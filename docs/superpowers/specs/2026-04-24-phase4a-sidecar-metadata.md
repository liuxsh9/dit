# Phase 4A: Sidecar Metadata System

> **Parent:** Phase 4 (Metadata & Advanced Features)  
> **Date:** 2026-04-24  
> **Depends on:** Phase 1-3 (core object store, server API, Web UI)  
> **Blocks:** Phase 4B (Export), 4C (Stats), 4D (Search)

---

## Overview

Add a content-addressable **sidecar** object type that stores per-row metadata (character count, token estimate, field count, language) alongside manifest objects. Sidecar generation is **lazy** — computed on demand via CLI command or server endpoint, not during `dit add` / `dit commit`.

---

## 1. Data Model

### 1.1 New dataclasses (`core/objects.py`)

```python
@dataclass(frozen=True)
class SidecarEntry:
    row_hash: str                    # matches ManifestEntry.row_hash
    char_count: int                  # len(row_text.decode("utf-8")) — character count, not byte count
    token_estimate: int              # char_count // 4 (rough token estimate)
    field_count: int                 # number of top-level JSON keys
    lang: Optional[str]              # detected language of longest string field (None if undetermined)

@dataclass(frozen=True)
class Sidecar:
    manifest_hash: str               # the manifest this sidecar describes
    entries: list[SidecarEntry]      # same order and length as manifest.entries
```

**Invariant:** `len(sidecar.entries) == len(manifest.entries)` and `sidecar.entries[i].row_hash == manifest.entries[i].row_hash` for all `i`.

### 1.2 TreeEntry extension

```python
@dataclass(frozen=True)
class TreeEntry:
    name: str
    obj_type: str             # "manifest" or "tree" (unchanged)
    obj_hash: str
    sidecar_hash: Optional[str] = None   # present only when obj_type == "manifest"
```

**Backward compatibility:** `deserialize_tree` uses `.get("sidecar_hash")` with default `None`. Old trees without sidecar_hash parse correctly. `serialize_tree` omits `sidecar_hash` when it is `None` — old clients ignore unknown fields.

**Hash stability requirement:** A tree with all `sidecar_hash=None` MUST serialize to byte-identical output as the old format (no `sidecar_hash` key in JSON). Tests must verify that `serialize_tree(deserialize_tree(old_bytes))` produces the same hash.

### 1.3 Object store layout

Sidecar objects live under a new subdirectory:

```
.dit/objects/sidecars/<hash[:2]>/<hash[2:4]>/<hash>    (zstd compressed)
```

Follows the existing 3-level sharding scheme used by all other object types.

The `obj_type` string for store operations is `"sidecars"`.

---

## 2. Serialization

### 2.1 serialize_sidecar / deserialize_sidecar (`core/objects.py`)

```python
def serialize_sidecar(s: Sidecar) -> bytes:
    data = {
        "type": "sidecar",
        "manifest_hash": s.manifest_hash,
        "entries": [
            {
                "char_count": e.char_count,
                "field_count": e.field_count,
                "lang": e.lang,
                "row_hash": e.row_hash,
                "token_estimate": e.token_estimate,
            }
            for e in s.entries
        ],
    }
    return json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
```

**Determinism guarantee:** Because all fields are derived purely from row content (no timestamps, no random values), the same manifest always produces the same sidecar bytes and therefore the same hash. Re-running `dit meta compute` is a no-op.

Uses the same deterministic JSON encoding as manifest/tree/commit.

### 2.2 serialize_tree extension

When `sidecar_hash` is not None, include it in the entry dict:

```python
entry_dict = {"name": e.name, "obj_hash": e.obj_hash, "obj_type": e.obj_type}
if e.sidecar_hash is not None:
    entry_dict["sidecar_hash"] = e.sidecar_hash
```

### 2.3 deserialize_tree extension

```python
TreeEntry(
    name=raw["name"],
    obj_type=raw["obj_type"],
    obj_hash=raw["obj_hash"],
    sidecar_hash=raw.get("sidecar_hash"),
)
```

---

## 3. Sidecar Computation

### 3.1 Compute function (`core/sidecar.py` — new file)

```python
def compute_sidecar(store: ObjectStore, manifest_hash: str) -> Sidecar:
    """Read a manifest, load each row, compute metadata, return Sidecar."""
```

Per row:
1. Read row data from store: `store.read("rows", entry.row_hash)`
2. Decode to string: `row_text = row_bytes.decode("utf-8")`
3. Parse as JSON (it's a single JSONL line, canonical JSON)
4. Compute:
   - `char_count = len(row_text)` (character count of decoded UTF-8 string)
   - `token_estimate = char_count // 4`
   - `field_count = len(parsed_json)` (top-level keys)
   - `lang = detect_lang(parsed_json)` (see 3.2)

**Edge case:** If manifest has 0 entries, return `Sidecar(manifest_hash=..., entries=[])`. Summary endpoint must handle this (return zeros, not division-by-zero).

### 3.2 Language detection heuristic (`core/sidecar.py`)

Simple, zero-dependency:
1. Find the longest string value in the parsed JSON (recursively check nested dicts/lists)
2. Check for CJK Unicode ranges → `"zh"`
3. Check for Cyrillic → `"ru"`
4. Check for Arabic → `"ar"`
5. Otherwise → `"en"` (default)
6. If longest string < 10 chars → `None` (too short to detect)

This is intentionally crude. Can be replaced with a real detector later without API changes.

### 3.3 Attach to tree

After computing a sidecar:
1. `sidecar_bytes = serialize_sidecar(sidecar)`
2. `sidecar_hash = store.write("sidecars", sidecar_bytes)`
3. Update the TreeEntry for the manifest: set `sidecar_hash = sidecar_hash`
4. Rebuild the tree and create a new commit (same tree structure, just sidecar_hash added)

**tree_builder update:** `build_nested_tree` currently takes `staged: dict[str, tuple[str, str]]` (name → (obj_type, obj_hash)). Extend to accept an optional 3rd element: `dict[str, tuple[str, str, Optional[str]]]` where the 3rd element is `sidecar_hash`. When building TreeEntry, pass it through. For backward compat, 2-tuples still work (sidecar_hash defaults to None).

This means `dit meta compute` creates a **new commit** that is identical to the current HEAD except tree entries now have `sidecar_hash` populated.

---

## 4. CLI Commands

### 4.1 `dit meta compute` (`cli/main.py`)

```
dit meta compute [--file PATH]
```

- Without `--file`: compute sidecars for ALL manifests in current HEAD that lack sidecar_hash
- With `--file`: compute sidecar for a specific file only
- Creates a new commit with updated tree entries
- Idempotent: if sidecar already exists, skips

Output:
```
Computing metadata for train.jsonl (1500 rows)... done (sidecar: abc123)
Computing metadata for eval.jsonl (200 rows)... done (sidecar: def456)
Created commit: 789abc "meta: compute sidecar metadata"
```

### 4.2 `dit meta show` (`cli/main.py`)

```
dit meta show <file> [--format json|table]
```

- Reads the sidecar for the given file from current HEAD
- Default: table format with summary stats
- `--format json`: raw sidecar JSON

Output (table):
```
File: train.jsonl (1500 rows)
Sidecar: abc123

  Total chars:    4,521,000
  Token estimate: 1,130,250
  Avg fields/row: 5.2
  Languages:      zh (82%), en (18%)
```

### 4.3 `dit meta diff` (`cli/main.py`)

```
dit meta diff <commit1> <commit2> [--file PATH]
```

Compare sidecar stats between two commits. Useful for PR review.

Output:
```
train.jsonl:
  Rows:           1500 → 1620 (+120)
  Token estimate:  1.13M → 1.22M (+90K)
  Languages:       zh 82%→80%, en 18%→20%
```

---

## 5. Server API

All endpoints under `/api/v1/repos/{repo}/`.

### 5.1 Compute sidecar

```
POST /meta/compute
Body: {"file": "train.jsonl"}   (optional — omit for all files)
Response: {"commit_hash": "...", "sidecars": [{"file": "...", "sidecar_hash": "..."}]}
```

### 5.2 Get sidecar

```
GET /meta/{commit_hash}/{file_path:path}
Response: full Sidecar JSON
```

Note: `{file_path:path}` uses FastAPI's path converter to support nested paths like `subdir/train.jsonl`.

### 5.3 Get sidecar summary

```
GET /meta/{commit_hash}/{file_path:path}/summary
Response: {"char_count": ..., "token_estimate": ..., "avg_fields": ..., "lang_distribution": {...}, "row_count": ...}
```

### 5.4 Meta diff

```
GET /meta/diff/{old_commit}/{new_commit}?file={path}
Response: {"files": [{"path": "...", "old_stats": {...}, "new_stats": {...}, "delta": {...}}]}
```

---

## 6. Gateway Proxy + Web UI

### 6.1 Gateway routes (`routers/api/v1/repo/dit.go`)

Add 4 new proxy routes to the existing dit group:

| Method | Path | Handler |
|--------|------|---------|
| POST | `/dit/meta/compute` | DatahubMetaCompute |
| GET | `/dit/meta/{commit}/{path}` | DatahubMetaGet |
| GET | `/dit/meta/{commit}/{path}/summary` | DatahubMetaSummary |
| GET | `/dit/meta/diff/{old}/{new}` | DatahubMetaDiff |

### 6.2 Gateway client (`modules/dit/client.go`)

Add 4 methods: `MetaCompute`, `MetaGet`, `MetaSummary`, `MetaDiff`.

### 6.3 DataRepoHome.vue enhancement

Add metadata stats row below the file table when sidecar data is available:

```
| File       | Rows | Size  | Tokens    | Lang    |
|------------|------|-------|-----------|---------|
| train.jsonl| 1500 | 12 MB | ~1.13M   | zh 82%  |
| eval.jsonl | 200  | 1.2MB | ~150K    | en 100% |
```

Fetch from summary endpoint. If sidecar not computed yet, show "—" with a "Compute" button.

### 6.4 DataDiffView.vue enhancement

When viewing a PR diff, if both commits have sidecars, show a metadata delta header:

```
train.jsonl: +120 rows, +90K tokens
```

---

## 7. Push/Pull Sync

### 7.1 Walker extension (`core/walker.py`)

`walk_commit_objects` currently traverses: commits → trees → manifests → rows.

Add: when a TreeEntry has `sidecar_hash`, include it in the `"sidecars"` set.

```python
if entry.sidecar_hash:
    result.setdefault("sidecars", set()).add(entry.sidecar_hash)
```

### 7.2 Push order

Current: rows → manifests → trees → commits.

New: rows → manifests → **sidecars** → trees → commits.

Sidecars must be pushed before trees because trees reference sidecar hashes.

### 7.3 Pull / Clone

Same logic in reverse — `walk_commit_objects` on remote commit, fetch missing objects including sidecars.

**Important:** `clone` and `_fetch_objects_since` in `cli/main.py` do their own manual tree traversal (not via `walk_commit_objects`). These must ALSO be updated to check `entry.sidecar_hash` and fetch sidecar objects. Missing sidecar is non-fatal — if download fails, log a warning and set `sidecar_hash=None` on the local TreeEntry. The data remains usable without metadata.

### 7.4 Server batch-exists

The existing `POST /{repo}/objects/batch-exists` endpoint already accepts `obj_type` as a parameter, so `"sidecars"` works without changes to the endpoint — just need the store to handle the new subdirectory.

---

## 8. Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Storage | Content-addressable sidecar objects | Consistent with existing model, auto-syncs via push/pull |
| Token counting | `char_count // 4` | Zero dependency, good enough for dashboard, swappable later |
| Language detection | Unicode range heuristic | Zero dependency, covers CJK/Cyrillic/Arabic vs English |
| Computation timing | Lazy (explicit command) | Doesn't slow down commit; user controls when to compute |
| Tree format change | Optional sidecar_hash field | Backward compatible; old clients ignore new field |
| New commit on compute | Yes | Keeps history clean; sidecar attachment is a tracked change |

---

## 9. Out of Scope (deferred)

- User-defined metadata tags (Phase 4 extension, later)
- Full-text search on sidecar fields (Phase 4D)
- Aggregated stats dashboard (Phase 4C — will read sidecar data)
- Export with metadata (Phase 4B — will include sidecar in export)
- Precise tokenizer (tiktoken etc.) — can replace the estimate function later
