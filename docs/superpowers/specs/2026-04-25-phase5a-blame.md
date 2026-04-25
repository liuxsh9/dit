# Phase 5A: Blame

> **Parent:** Phase 5 (Operations & Observability)  
> **Date:** 2026-04-25  
> **Depends on:** Phase 1–4 (core object model, commit DAG, manifests, sidecars)  
> **Blocks:** None

---

## Overview

Add a `dit blame` CLI command, a server API endpoint, and a Web UI blame view that trace every row in a JSONL file back to the commit that introduced it. Blame operates at per-row granularity — each row in the manifest is annotated with the commit hash, author, and timestamp of the commit that added or last refreshed it.

Primary use cases:

- **Local audit**: `dit blame train.jsonl` shows who introduced each row and when.
- **Row provenance**: `dit blame train.jsonl --row 42` shows the full history of a single row across commits (additions, refreshes, removals).
- **Web UI integration**: the file detail view gains a blame panel showing per-row attribution.

---

## 1. CLI Command

### 1.1 `dit blame`

```
dit blame <FILE> [--ref REF] [--row N] [--format table|json]
```

| Flag / Arg | Default | Description |
|------------|---------|-------------|
| `FILE` | _(required)_ | File path (e.g. `train.jsonl`) within the commit tree |
| `--ref` | `main` | Branch name or 64-char commit hash to inspect |
| `--row N` | _(none)_ | Show history for a specific row index instead of full blame |
| `--format` | `table` | Output format: `table` (human-readable) or `json` (machine-readable) |

### 1.2 Default output (table, full blame)

Walks commit history backward from the target ref, tracking when each row in the file's manifest was introduced or last refreshed.

```
$ dit blame train.jsonl
Blame for train.jsonl at heads/main (commit abc12345)

 Row  Commit    Author    Date                  Content
──────────────────────────────────────────────────────────────────────────
   0  abc1234   alice     2026-04-20 10:00 UTC  {"messages":[{"role":"user","content":"hello...
   1  def5678   bob       2026-04-21 14:30 UTC  {"messages":[{"role":"user","content":"what i...
   2  abc1234   alice     2026-04-20 10:00 UTC  {"messages":[{"role":"user","content":"explai...
   3  ghi9012   alice     2026-04-22 09:15 UTC  {"messages":[{"role":"user","content":"how do...
──────────────────────────────────────────────────────────────────────────
4 rows, 3 commits, 2 authors
```

Notes:
- `Content` column shows the first 60 characters of the JSON-serialized row, truncated with `...`.
- Commit hashes are shown as 7-char abbreviations.
- Rows are listed in manifest order (index 0, 1, 2, ...).

### 1.3 Row history (`--row N`)

```
$ dit blame train.jsonl --row 1
History for train.jsonl row 1 at heads/main

  Commit    Author  Date                  Event     Content
─────────────────────────────────────────────────────────────────────────────
  def5678   bob     2026-04-21 14:30 UTC  refresh   {"messages":[{"role":"user","content":"what i...
  abc1234   alice   2026-04-20 10:00 UTC  added     {"messages":[{"role":"user","content":"what i...
─────────────────────────────────────────────────────────────────────────────
2 events (query_fingerprint: 9a3f...b2c1)
```

The `--row N` mode walks backward through the commit history and tracks all events related to the row at index N in the target commit's manifest. Events are:

| Event | Meaning |
|-------|---------|
| `added` | Row first appeared in this file (row_hash not in parent manifest) |
| `refresh` | Row content changed but query_fingerprint stayed the same (response was regenerated for the same prompt) |
| `removed` | Row was present in parent but absent in this commit (only shown in row history, not in full blame) |

When `--row N` is used, the function identifies the row by its `query_fingerprint` (if present) to follow it across refreshes. If the row has no query_fingerprint, it tracks by `row_hash` only and can only report `added` events.

### 1.4 JSON output

`--format json` produces machine-readable output. Shapes mirror the server API responses (Section 3).

---

## 2. Core Blame Module

### 2.1 New file: `src/dit/core/blame.py`

```python
@dataclass(frozen=True)
class BlameEntry:
    row_index: int
    row_hash: str
    commit_hash: str
    author: str
    timestamp: int
    query_fingerprint: str | None


def blame_file(
    store: ObjectStore,
    commit_hash: str,
    file_path: str,
) -> dict:
    """Attribute each row in a file's manifest to the commit that introduced it.

    Returns:
    {
      "commit_hash": "abc12345...",
      "file": "train.jsonl",
      "entries": [
        {
          "row_index": 0,
          "row_hash": "...",
          "commit_hash": "...",
          "author": "alice",
          "timestamp": 1713600000,
          "query_fingerprint": "..." or null,
        },
        ...
      ],
      "summary": {
        "total_rows": 4,
        "unique_commits": 3,
        "unique_authors": 2,
      }
    }
    """


def row_history(
    store: ObjectStore,
    commit_hash: str,
    file_path: str,
    row_index: int,
) -> dict:
    """Trace the history of a specific row across commits.

    Returns:
    {
      "commit_hash": "abc12345...",
      "file": "train.jsonl",
      "row_index": 3,
      "query_fingerprint": "..." or null,
      "events": [
        {
          "commit_hash": "...",
          "author": "bob",
          "timestamp": 1713686400,
          "event": "refresh",
          "row_hash": "...",
          "content_preview": "..."
        },
        {
          "commit_hash": "...",
          "author": "alice",
          "timestamp": 1713600000,
          "event": "added",
          "row_hash": "...",
          "content_preview": "..."
        },
      ]
    }
    """
```

### 2.2 Implementation details

**`blame_file`:**

1. Load the commit and flatten its tree. Find the manifest for `file_path`. Raise `FileNotFoundError` if commit or file not found.
2. Deserialize the manifest. Build `unattributed: set[str]` = set of all row_hashes in the manifest.
3. Initialize `blame_map: dict[str, BlameEntry]` = {}.
4. Set `current_hash = commit_hash`, `current_manifest = target_manifest`.
5. Walk backward:
   a. Load current commit.
   b. Get first parent. If no parent (root commit): attribute all remaining `unattributed` rows that exist in `current_manifest` to `current_hash`. Break.
   c. Load parent's manifest for the same file path. If the file doesn't exist in the parent: attribute all remaining `unattributed` rows that exist in `current_manifest` to `current_hash`. Break.
   d. Call `diff_manifests(parent_manifest, current_manifest)`.
   e. For each row in `diff.added`: if `row_hash` is in `unattributed`, create a `BlameEntry` with `current_hash`, remove from `unattributed`.
   f. For each `(old_hash, new_hash, qfp)` in `diff.refreshed`: if `new_hash` is in `unattributed`, create a `BlameEntry` with `current_hash`, remove from `unattributed`.
   g. If `unattributed` is empty: break (early exit).
   h. Set `current_hash = parent_hash`, `current_manifest = parent_manifest`.
6. Build result ordered by the target manifest's entry order.

**First-parent traversal**: Only follows `commit.parent_hashes[0]`. Merge commits are attributed as a single unit — if a row appeared via a merge, the merge commit gets blame. This matches `git blame`'s default behavior and avoids exponential DAG traversal.

**`row_history`:**

1. Load the manifest at `commit_hash` for `file_path`. Get the entry at `row_index`.
2. Extract `query_fingerprint` and `row_hash` from that entry.
3. Walk backward through commit history (first-parent):
   a. At each commit, load the manifest for the file.
   b. Compare with the child commit's manifest using `diff_manifests`.
   c. If the row's `row_hash` (or a previous row_hash sharing the same `query_fingerprint`) appears in `diff.added`: record an `added` event.
   d. If the row appears in `diff.refreshed`: record a `refresh` event and update the tracked `row_hash` to the old hash for further backward walking.
   e. If the row's `row_hash` is in `diff.removed` (present in parent but not in current): record a `removed` event. Continue walking.
   f. Stop when the file no longer exists in the parent or we reach a root commit.
4. Events are returned in reverse chronological order (newest first).

**Content preview**: For each blame entry / event, read the row from the object store (`store.read("rows", row_hash)`) and produce a truncated JSON string (first 60 chars + `...`).

### 2.3 Return types

Both functions return plain `dict` (consistent with `stats`, `search`, `validate` conventions). JSON-serializable without conversion.

### 2.4 Performance considerations

- **Early exit**: `blame_file` stops walking as soon as all rows are attributed. For a file that hasn't changed in many commits, only 1–2 commits need inspection.
- **Manifest caching**: Each commit requires loading the tree and finding the manifest. `flatten_tree` is called per commit. No additional caching in Phase 5A — acceptable for repos with hundreds of commits. Phase 5 GC/optimization can add a blame cache if needed.
- **Row content loading**: Content previews require reading row objects. This is one `store.read("rows", hash)` per row. For `blame_file`, all row hashes are known from the target manifest — content is loaded once at the end, not during the walk.

---

## 3. Server API

### 3.1 Blame endpoint

```
GET /api/v1/repos/{repo}/blame/{commit_hash}/{file_path:path}
```

Optional query params:

| Param | Default | Description |
|-------|---------|-------------|
| `row` | _(none)_ | If set to an integer, returns row history instead of full blame |

**Authentication:** `require_permission("read")`.

**Response (200) — full blame:**

```json
{
  "commit_hash": "abc1234567890abcd...",
  "file": "train.jsonl",
  "entries": [
    {
      "row_index": 0,
      "row_hash": "...",
      "commit_hash": "...",
      "author": "alice",
      "timestamp": 1713600000,
      "query_fingerprint": "...",
      "content_preview": "{\"messages\":[{\"role\":\"user\",\"content\":\"hello..."
    }
  ],
  "summary": {
    "total_rows": 4,
    "unique_commits": 3,
    "unique_authors": 2
  }
}
```

**Response (200) — row history (when `?row=N` is set):**

```json
{
  "commit_hash": "abc1234567890abcd...",
  "file": "train.jsonl",
  "row_index": 1,
  "query_fingerprint": "...",
  "events": [
    {
      "commit_hash": "...",
      "author": "bob",
      "timestamp": 1713686400,
      "event": "refresh",
      "row_hash": "...",
      "content_preview": "..."
    }
  ]
}
```

**Error cases:**

- `404` if commit hash not found, file path not found, or file is not a manifest.
- `400` if `row` param is out of range.

### 3.2 New file: `src/dit/server/routes/blame_api.py`

```python
router = APIRouter(prefix="/api/v1/repos", tags=["blame"])

@router.get("/{repo}/blame/{commit_hash}/{file_path:path}")
async def blame_endpoint(
    repo: str,
    commit_hash: str,
    file_path: str,
    row: Optional[int] = Query(default=None),
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    from dit.core.blame import blame_file, row_history
    ...
```

The handler:
1. Calls `_get_repo(repo, session)` to verify repo exists.
2. Builds `store = _store_for_repo(request, repo)`.
3. If `row` is not None: calls `row_history(store, commit_hash, file_path, row)`.
4. Otherwise: calls `blame_file(store, commit_hash, file_path)`.
5. Returns the dict directly.

Register the router in `src/dit/server/app.py` alongside existing routers.

---

## 4. Gateway + Web UI

### 4.1 Gateway route

Add to the `datahub` group in `routers/api/v1/api.go`:

```go
m.Get("/blame/{commit}/{file:*}", repo.DatahubGetBlame)
```

### 4.2 Handler in `routers/api/v1/repo/datahub.go`

```go
func DatahubGetBlame(ctx *context.APIContext) {
    commitHash := ctx.Params(":commit")
    filePath := ctx.Params(":file")
    rowParam := ctx.FormString("row")

    data, status, err := datahub.DefaultClient().GetBlame(
        ctx, ctx.Repo.Repository.Name, commitHash, filePath, rowParam,
    )
    proxyDatahubResponse(ctx, data, status, err)
}
```

### 4.3 Client method in `modules/datahub/client.go`

```go
func (c *Client) GetBlame(ctx context.Context, repoName, commitHash, filePath, row string) ([]byte, int, error) {
    path := "/api/v1/repos/" + repoName + "/blame/" + commitHash + "/" + filePath
    if row != "" {
        path += "?row=" + url.QueryEscape(row)
    }
    return c.do(ctx, http.MethodGet, path, nil)
}
```

### 4.4 Web UI: Blame view

Add a "Blame" button/link in the file detail view (when viewing a single manifest file). Clicking it loads blame data and shows a per-row annotated view.

**Template (in DataRepoHome.vue or a new component):**

The blame view renders as a table where each row shows:
- Commit hash (abbreviated, clickable)
- Author
- Date
- Row index
- Content preview (first 60 chars, truncated)

Rows from the same commit are visually grouped (alternating background color per commit block).

**Behavior:**
- Blame data is loaded lazily when the user clicks "Blame" on a file.
- Loading spinner during fetch.
- Error message if the file has no blame data or commit not found.
- Clicking a commit hash navigates to the commit detail view.
- Clicking a row index opens the row detail / row history panel.

**Row history sub-panel:**
When a user clicks a row in the blame view, a side panel or expandable section shows the row's full history (events: added, refresh, removed) across commits.

---

## 5. Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Traversal strategy | First-parent only | Matches `git blame` default; avoids exponential DAG traversal at merge commits; merge commits get blame for anything they introduce |
| Row identity for blame | `row_hash` (content-addressed) | Exact: a row_hash uniquely identifies content. If the exact same content appears across commits, it's the same row |
| Row identity for history | `query_fingerprint` (when available) | Allows tracking a row through refreshes (same prompt, different response). Falls back to `row_hash` if no query_fingerprint |
| Content preview | First 60 chars of canonical JSON | Enough to identify the row; keeps output compact; consistent with search highlight approach |
| Event types | added / refresh / removed | Maps directly to `diff_manifests` result: `added`, `refreshed`, `removed` |
| Blame storage | None — computed on-the-fly | Similar to Phase 4C stats; commit history is typically short (tens to hundreds); early exit makes this fast |
| Merge commit handling | Attribute to the merge commit itself | If a merge introduces a row (not in first-parent), the merge commit gets blame. Simpler than descending into both branches |
| Row history order | Reverse chronological (newest first) | Natural reading order: "what happened most recently?" |

---

## 6. Out of Scope

- **Cross-file blame**: Tracing a row_hash across different file paths (e.g., row moved from `train.jsonl` to `eval.jsonl`). Requires a global row_hash → file path index. Deferred.
- **Blame cache / pre-computation**: For very large repos, a pre-computed blame-per-commit could speed things up. Not needed at current scale.
- **Interactive blame (web UI row editing)**: The blame view is read-only.
- **Blame with range (`--row 0:10`)**: Only single row history for now. Multi-row range can be added later.
- **Second-parent blame for merges**: Walking into the second parent of a merge to find more precise attribution. `git blame` supports this with `-C` but it's complex and rarely needed.
