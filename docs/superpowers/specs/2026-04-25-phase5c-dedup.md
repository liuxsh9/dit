# Phase 5C: Duplicate Detection

> **Parent:** Phase 5 (Operations & Observability)  
> **Date:** 2026-04-25  
> **Depends on:** Phase 1–4 (object store, manifests, query_fingerprint, tree walker)  
> **Blocks:** None

---

## Overview

Add a `dit dedup` CLI command and a server API endpoint that detect and report duplicate rows across all files in a commit. Dedup is **detection and statistics only** — it never deletes, cleans, or modifies any data.

Two levels of duplication:

1. **Exact duplicates** (same `row_hash`): Identical full content (query + response). **Severity: WARNING** — these are almost certainly unintentional.
2. **Query duplicates** (same `query_fingerprint`, different `row_hash`): Same prompt, different response. **Severity: INFO** — this is normal and expected in distillation workflows where multiple response variants are generated for the same query.

---

## 1. Core Dedup Module

### 1.1 New file: `src/dit/core/dedup.py`

```python
@dataclass(frozen=True)
class DupGroup:
    fingerprint: str          # row_hash for exact dups, query_fingerprint for query dups
    dup_type: str             # "exact" or "query"
    occurrences: list[dict]   # [{file, row_index, row_hash, content_preview}]


def detect_duplicates(
    store: ObjectStore,
    commit_hash: str,
    path_prefix: str | None = None,
) -> dict:
    """Detect duplicate rows across all manifest files in a commit.

    Returns:
    {
      "commit_hash": "abc12345...",
      "exact_duplicates": [
        {
          "row_hash": "...",
          "count": 3,
          "occurrences": [
            {"file": "train.jsonl", "row_index": 0, "content_preview": "..."},
            {"file": "eval.jsonl", "row_index": 2, "content_preview": "..."},
            {"file": "train.jsonl", "row_index": 5, "content_preview": "..."},
          ]
        }
      ],
      "query_duplicates": [
        {
          "query_fingerprint": "...",
          "count": 2,
          "row_hashes": ["hash1", "hash2"],
          "occurrences": [
            {"file": "train.jsonl", "row_index": 0, "row_hash": "...", "content_preview": "..."},
            {"file": "train.jsonl", "row_index": 3, "row_hash": "...", "content_preview": "..."},
          ]
        }
      ],
      "summary": {
        "total_rows": 100,
        "total_files": 5,
        "exact_dup_groups": 2,
        "exact_dup_rows": 6,
        "query_dup_groups": 3,
        "query_dup_rows": 8,
        "severity": "warning"    # "clean" | "info" | "warning"
      }
    }
    """
```

### 1.2 Implementation details

1. Load the commit, `flatten_tree(store, commit.tree_hash)`.
2. Filter entries where `obj_type == "manifest"`. If `path_prefix` is set, also filter by path prefix.
3. For each manifest, deserialize and iterate entries. Build two indexes:
   - `row_hash_index: dict[str, list[dict]]` — maps `row_hash` → list of `{file, row_index, content_preview}`
   - `qfp_index: dict[str, list[dict]]` — maps `query_fingerprint` → list of `{file, row_index, row_hash, content_preview}` (skip entries where `query_fingerprint is None`)
4. **Exact duplicates**: entries in `row_hash_index` where `len(occurrences) > 1`. Sorted by count descending.
5. **Query duplicates**: entries in `qfp_index` where `len(set(row_hashes)) > 1` AND `len(occurrences) > 1`. This means: same query_fingerprint but at least 2 distinct row_hash values (different responses). Groups where all occurrences share the same row_hash are exact duplicates, already reported above.
6. **Severity**:
   - `"clean"`: no duplicates at all
   - `"info"`: only query duplicates (normal distillation)
   - `"warning"`: any exact duplicates exist

**Content preview**: First 60 chars of the row's canonical JSON, truncated with `...`. Same pattern as blame.

### 1.3 Cross-file detection

Dedup operates across ALL manifest files in the commit tree. A row_hash appearing in `train.jsonl` and `eval.jsonl` is reported as an exact duplicate. This is the primary cross-file detection use case.

---

## 2. CLI Command

### 2.1 `dit dedup`

```
dit dedup [--ref REF] [--path PREFIX] [--format table|json] [--exact-only] [--query-only]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--ref` | `main` | Branch name or 64-char commit hash |
| `--path` | _(none)_ | Only check files matching this prefix |
| `--format` | `table` | Output format |
| `--exact-only` | `False` | Only show exact duplicates |
| `--query-only` | `False` | Only show query duplicates |

### 2.2 Table output

```
$ dit dedup
Duplicate detection for heads/main (commit abc12345)

⚠ EXACT DUPLICATES (2 groups, 6 rows) — identical content
─────────────────────────────────────────────────────────
  row_hash    Count  Files
  a1b2c3d4    3      train.jsonl (×2), eval.jsonl (×1)
  e5f6g7h8    3      train.jsonl (×3)

ℹ QUERY DUPLICATES (3 groups, 8 rows) — same query, different response
─────────────────────────────────────────────────────────
  fingerprint  Variants  Files
  9a3fb2c1     2         train.jsonl (×2)
  d4e5f6a7     3         train.jsonl (×2), eval.jsonl (×1)
  b8c9d0e1     2         train.jsonl (×1), eval.jsonl (×1)

Summary: 100 rows across 5 files
  Exact duplicates: 2 groups (6 rows) ⚠ WARNING
  Query duplicates: 3 groups (8 rows) ℹ INFO
```

When clean:
```
$ dit dedup
Duplicate detection for heads/main (commit abc12345)

✓ No duplicates found. 100 rows across 5 files.
```

### 2.3 Exit codes

- `0`: clean or only query duplicates (info)
- `1`: exact duplicates found (warning) — usable in CI scripts

---

## 3. Server API

### 3.1 Endpoint

```
GET /api/v1/repos/{repo}/dedup/{commit_hash}
```

Optional query params:

| Param | Default | Description |
|-------|---------|-------------|
| `path` | _(none)_ | Path prefix filter |

**Authentication:** `require_permission("read")`.

**Response (200):** Same structure as `detect_duplicates()` return value.

**Errors:**
- `404` if commit not found.

### 3.2 New file: `src/dit/server/routes/dedup_api.py`

Follows the same pattern as `stats_api.py`. Register in `app.py`.

---

## 4. Gateway + Web UI

### 4.1 Gateway route

```go
m.Get("/dedup/{commit}", repo.DatahubGetDedup)
```

### 4.2 Handler

```go
func DatahubGetDedup(ctx *context.APIContext) {
    // proxy GET to datahub-core /api/v1/repos/{repo}/dedup/{commit}?path=...
}
```

### 4.3 Client method

```go
func (c *Client) GetDedup(ctx context.Context, repoName, commitHash, pathFilter string) ([]byte, int, error) {
    path := "/api/v1/repos/" + repoName + "/dedup/" + commitHash
    if pathFilter != "" {
        path += "?path=" + url.QueryEscape(pathFilter)
    }
    return c.do(ctx, http.MethodGet, path, nil)
}
```

### 4.4 Web UI

Optional — add a "Dedup Check" section to the repo overview or a button in the file listing. Shows summary (severity badge) and allows expanding to see duplicate groups. Can be deferred if not needed immediately.

---

## 5. Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Scope | Detection + statistics only | Many datasets intentionally have same-query multi-response pairs (distillation). Auto-cleanup would destroy valid data |
| Exact duplicate definition | Same `row_hash` | Content-addressed: byte-for-byte identical canonical JSON |
| Query duplicate definition | Same `query_fingerprint`, different `row_hash` | Same user messages, different assistant response — normal for distillation |
| Severity levels | clean / info / warning | Info = query dups (expected). Warning = exact dups (suspicious) |
| Cross-file | Yes, always | A row appearing in both train.jsonl and eval.jsonl IS a real duplicate worth flagging |
| Exit code | 0 for clean/info, 1 for warning | Makes `dit dedup` usable in CI pipelines as a quality gate |
| Content preview | 60 chars | Consistent with blame and search |
| Query-only groups | Exclude same-hash groups | If all occurrences have the same row_hash, that's an exact dup, not a query dup |
| Storage | None — computed on-the-fly | Same as blame, stats, validate — commit history is small |

---

## 6. Out of Scope

- **Automatic cleanup / dedup**: No delete, prune, or merge functionality. User decides.
- **Fuzzy matching**: Only exact hash equality. No embedding similarity or edit distance.
- **Semantic dedup**: No NLP-based duplicate detection. Would require embedding infrastructure.
- **Historical dedup**: Only checks a single commit snapshot. No cross-commit tracking.
- **Dedup suggestions**: No recommended actions (e.g., "remove row X from file Y").
