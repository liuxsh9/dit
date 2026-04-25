# Phase 4B: Export Command

> **Parent:** Phase 4 (Metadata & Advanced Features)  
> **Date:** 2026-04-25  
> **Depends on:** Phase 4A (sidecar metadata system)  
> **Blocks:** None (independent leaf)

---

## Overview

Add a `dit export` CLI command that reconstructs JSONL files from the content-addressable object store and writes them to a local directory or S3 bucket. Optionally includes sidecar metadata as companion files. Also add a server API endpoint so the gateway can trigger exports.

---

## 1. CLI Command

### 1.1 `dit export`

```
dit export [--ref REF] [--file PATH] [--format jsonl|csv] [--include-meta] [--output PATH] [--s3 S3_URI]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--ref` | `heads/main` | Ref to export from (branch or commit hash) |
| `--file` | _(all files)_ | Export only a specific file |
| `--format` | `jsonl` | Output format: `jsonl` (one JSON object per line) or `csv` (flattened top-level keys) |
| `--include-meta` | `false` | Include sidecar summary as `<filename>.meta.json` alongside each exported file |
| `--output` | `.` (current dir) | Local directory to write exported files |
| `--s3` | _(none)_ | S3 URI (`s3://bucket/prefix/`); mutually exclusive with `--output` |

**Exactly one of `--output` or `--s3` must be specified** (error if both or neither — but `--output` defaults to `.` so in practice only `--s3` needs explicit flag).

### 1.2 Export logic

1. Resolve ref to commit hash (via `RefStore.resolve` for branches, or use directly if it looks like a hex hash)
2. Load commit, get tree hash
3. Flatten tree via `flatten_tree()`
4. For each manifest entry (or just the one matching `--file`):
   a. Read manifest from store
   b. For each row in manifest, read row bytes from store
   c. Write rows to output:
      - **jsonl**: one row per line, newline-terminated
      - **csv**: parse JSON, extract top-level keys, write CSV with header
   d. If `--include-meta` and entry has `sidecar_hash`:
      - Read sidecar from store
      - Compute summary (reuse `compute_summary` from `core/sidecar.py`)
      - Write as `<filename>.meta.json`

### 1.3 Output structure

```
<output>/
  train.jsonl
  train.jsonl.meta.json      (only if --include-meta)
  eval.jsonl
  eval.jsonl.meta.json       (only if --include-meta)
```

For nested trees, preserve directory structure:
```
<output>/
  subdir/
    data.jsonl
    data.jsonl.meta.json
```

### 1.4 S3 upload

When `--s3` is specified:
- Write files to a temp directory first, then upload via boto3
- Require `boto3` as optional dependency (import at runtime, error with helpful message if missing)
- Upload key = `<s3_prefix>/<relative_path>`
- Use `boto3.client('s3').upload_file()` with default credentials (AWS env vars or profile)

### 1.5 Output examples

```
$ dit export --output ./exported
Exporting from heads/main (commit abc123)
  train.jsonl (1500 rows)... done
  eval.jsonl (200 rows)... done
Exported 2 files to ./exported/

$ dit export --file train.jsonl --include-meta --output ./exported
Exporting from heads/main (commit abc123)
  train.jsonl (1500 rows)... done
  train.jsonl.meta.json... done
Exported 1 file to ./exported/

$ dit export --s3 s3://my-bucket/exports/v1/
Exporting from heads/main (commit abc123)
  train.jsonl (1500 rows)... done
  eval.jsonl (200 rows)... done
Uploaded 2 files to s3://my-bucket/exports/v1/
```

---

## 2. Core Export Module

### 2.1 New file: `core/export.py`

```python
def export_commit(
    store: ObjectStore,
    commit_hash: str,
    output_dir: Path,
    *,
    file_filter: str | None = None,
    fmt: str = "jsonl",
    include_meta: bool = False,
) -> list[dict]:
    """Export files from a commit to output_dir.
    
    Returns list of dicts: [{"path": "train.jsonl", "rows": 1500, "bytes": 12345}, ...]
    """
```

This is a pure function that:
1. Reads commit → tree → flattened entries
2. For each manifest, reads rows and writes to `output_dir / path`
3. Returns a report of what was exported

### 2.2 Format handlers

**JSONL (default):** Read each row from store, write bytes directly (they're already canonical JSON), append `\n`.

**CSV:** Parse each JSON row, collect all top-level keys across all rows (first pass), then write CSV with sorted headers. Nested values are JSON-serialized as strings.

### 2.3 Sidecar summary export

When `include_meta=True`, for each file with a sidecar:
```json
{
  "file": "train.jsonl",
  "manifest_hash": "abc123",
  "sidecar_hash": "def456",
  "row_count": 1500,
  "char_count": 4521000,
  "token_estimate": 1130250,
  "avg_fields": 5.2,
  "lang_distribution": {"zh": 0.82, "en": 0.18}
}
```

Reuse `compute_summary()` from `core/sidecar.py` (the same function used by the server summary endpoint).

---

## 3. Server API

### 3.1 Export endpoint

```
POST /api/v1/repos/{repo}/export
Body: {
  "ref": "heads/main",
  "file": "train.jsonl",       // optional
  "format": "jsonl",           // optional, default "jsonl"
  "include_meta": false        // optional, default false
}
Response: streaming JSONL of file contents
```

**Response format:** The response streams the exported data as a tar.gz archive:
- `Content-Type: application/gzip`
- The archive contains the same file structure as local export

This allows the gateway to proxy the response directly to the browser for download.

### 3.2 Alternative: simple file-by-file export

For simpler integration, also support single-file export:

```
GET /api/v1/repos/{repo}/export/{commit_hash}/{file_path:path}?format=jsonl
Response: raw JSONL file content
Content-Type: application/x-ndjson
```

This is more REST-friendly and allows the gateway to serve individual files without tar.

---

## 4. Gateway + Web UI

### 4.1 Gateway proxy

Add export routes to datahub group:
- `POST /datahub/export` → proxy to core export endpoint
- `GET /datahub/export/{commit}/{path}` → proxy to core single-file export

### 4.2 Gateway client methods

```go
func (c *Client) Export(ctx context.Context, repoName string, body []byte) ([]byte, int, error)
func (c *Client) ExportFile(ctx context.Context, repoName, commit, filePath string) ([]byte, int, error)
```

### 4.3 DataRepoHome.vue enhancement

Add an "Export" button in the file tree header. Clicking it downloads the current branch as JSONL files via the gateway export endpoint.

---

## 5. Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Output format | JSONL default, CSV optional | JSONL is native format; CSV useful for spreadsheet users |
| S3 support | boto3 optional dep | Keep core lightweight; S3 is opt-in |
| Meta format | Companion `.meta.json` | Simple, discoverable, no format coupling |
| Server response | tar.gz archive for batch, raw file for single | Efficient for bulk; simple for single-file |
| Temp dir for S3 | Yes | Simpler than streaming to S3 |

---

## 6. Out of Scope

- Parquet export (Phase 5 — needs pyarrow dependency)
- Streaming export for very large files (for now, read all rows into memory)
- Export filtering by row content (Phase 4D search provides this)
- Export scheduling / async jobs
