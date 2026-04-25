# Phase 5E: Persistence Audit (fsck)

> **Parent:** Phase 5 (Operations & Observability)  
> **Date:** 2026-04-25  
> **Depends on:** Phase 1–4 (object store, refs, walker), Phase 5B (GC / blob walker fix)  
> **Blocks:** None

---

## Overview

Add a `dit fsck` CLI command and a server API endpoint that verify the integrity of the object store and reference graph. Two levels of verification:

1. **Hash verification**: Every stored object's content matches its filename hash after decompression.
2. **Graph verification**: Every reference (branch, tag) points to a valid commit, and every commit/tree/manifest references valid objects that exist in the store.

Reports problems but never modifies data.

---

## 1. Core Fsck Module

### 1.1 New file: `src/dit/core/fsck.py`

```python
@dataclass(frozen=True)
class FsckIssue:
    severity: str        # "error" or "warning"
    obj_type: str        # object type where issue was found
    obj_hash: str        # hash of the problematic object (or ref name)
    message: str         # human-readable description

@dataclass
class FsckResult:
    checked_objects: dict[str, int]   # count by type
    errors: list[FsckIssue]          # hash mismatches, missing refs, corrupt objects
    warnings: list[FsckIssue]        # orphans, stale tmp files
    total_checked: int
    total_errors: int
    total_warnings: int


def fsck(
    store: ObjectStore,
    ref_hashes: list[str],
    check_hashes: bool = True,
    check_graph: bool = True,
) -> FsckResult:
    """Full integrity check: hash verification + graph verification."""
```

### 1.2 Implementation details

**Phase 1: Hash verification** (`check_hashes=True`)

1. For each object type in `["commits", "trees", "manifests", "rows", "sidecars", "blobs"]`:
   - Walk the shard hierarchy: `objects/{type}/{xx}/{yy}/{hash}`
   - For each file:
     a. Extract expected hash from filename
     b. Read and decompress the file (`pyzstd.decompress`)
     c. Compute `sha256(decompressed)` and compare to expected hash
     d. If mismatch: `FsckIssue(severity="error", message="hash mismatch: expected {expected}, got {actual}")`
     e. If decompression fails: `FsckIssue(severity="error", message="corrupt object: decompression failed")`
   - Count each object in `checked_objects`

**Phase 2: Graph verification** (`check_graph=True`)

1. For each `ref_hash` in `ref_hashes`:
   - Verify the commit exists in the store. If not: error.
   - Deserialize the commit. Walk parent_hashes — verify each parent commit exists.
   - Walk `tree_hash` → deserialize tree → for each entry:
     - Verify the referenced object exists (manifest, blob, or sub-tree)
     - If manifest: deserialize, verify each `row_hash` exists in the store
     - If sidecar_hash is set: verify it exists
   - Use `walk_commit_objects(store, ref_hash)` to collect the full reachable set, then spot-check that every hash in the set actually resolves.

2. Dangling reference detection: If a ref points to a nonexistent commit, that's an error.

### 1.3 Severity levels

- **error**: Data corruption or missing objects (hash mismatch, decompression failure, dangling reference, missing object in graph)
- **warning**: Non-critical issues (stale tmp files, unreachable objects — overlaps with GC)

### 1.4 Performance

Hash verification is O(n) in total objects and requires reading every file. For large stores this could be slow. The `check_hashes` flag allows skipping this for quick graph-only checks.

---

## 2. CLI Command

### 2.1 `dit fsck`

```
dit fsck [--no-hash-check] [--no-graph-check] [--format table|json]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--no-hash-check` | `False` | Skip hash verification (faster) |
| `--no-graph-check` | `False` | Skip graph verification |
| `--format` | `table` | Output format |

### 2.2 Table output

```
$ dit fsck
Object store integrity check

Hash verification:
  commits     5  ✓
  trees       5  ✓
  manifests   3  ✓
  rows       15  ✓
  sidecars    3  ✓
  blobs       1  ✓

Graph verification:
  Refs checked: 2 (1 branch, 1 tag)
  Commits reachable: 5
  All references valid ✓

✓ No issues found. 32 objects checked.
```

With errors:
```
$ dit fsck
Object store integrity check

Hash verification:
  ...
  rows       15  ✗ 1 error

Graph verification:
  ...

ERRORS (1):
  [rows] abc123...: hash mismatch: expected abc123..., got def456...

✗ 1 error, 0 warnings. 32 objects checked.
```

### 2.3 Exit codes

- `0`: no issues
- `1`: errors found (data corruption)
- `0`: warnings only (non-critical)

---

## 3. Server API

### 3.1 Endpoint

```
POST /api/v1/repos/{repo}/fsck
```

Request body:
```json
{
  "check_hashes": true,
  "check_graph": true
}
```

**Authentication:** `require_permission("admin")` — only admins can run fsck (reads every object).

**Response (200):**
```json
{
  "checked_objects": {"commits": 5, "trees": 5, "manifests": 3, "rows": 15, "sidecars": 3, "blobs": 1},
  "errors": [{"severity": "error", "obj_type": "rows", "obj_hash": "abc...", "message": "hash mismatch..."}],
  "warnings": [],
  "total_checked": 32,
  "total_errors": 1,
  "total_warnings": 0
}
```

### 3.2 New file: `src/dit/server/routes/fsck_api.py`

Same pattern as `gc_api.py`. Register in `app.py`.

---

## 4. Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Scope | Read-only verification | Never modify data during integrity check |
| Hash check | SHA256(decompressed) vs filename | Matches store.write() which hashes before compression |
| Graph check | Walk from refs | Same as GC's mark phase — reuse walk_commit_objects |
| Severity | error vs warning | Errors = data corruption. Warnings = advisory |
| Auth | Admin only | Reads every object — potential I/O impact |
| Default | Both checks enabled | Full verification by default, flags to skip |
| Orphan detection | Warning, not error | Orphans are harmless — GC handles them |

---

## 5. Out of Scope

- **Auto-repair**: No fixing corrupt objects. User must restore from backup.
- **Cross-repo checks**: Each repo checked independently.
- **Database integrity**: PostgreSQL has its own integrity tools.
- **Gateway proxy**: Low priority — fsck is primarily a CLI/admin tool.
