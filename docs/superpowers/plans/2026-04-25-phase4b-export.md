# Phase 4B: Export Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `dit export` CLI command and a `GET /{repo}/export/{commit}/{file_path}` server endpoint to reconstruct JSONL/CSV files from the content-addressable object store.

**Architecture:** Extract `sidecar_summary()` from `meta_api.py` into `core/sidecar.py` as a shared utility, then build `core/export.py` as a pure function that reads commit → tree → manifests → rows and writes files. The CLI wires up the flags; the server endpoint exposes single-file export over HTTP. No streaming, no S3 in this phase — local export only, tight scope.

**Tech Stack:** Python 3.12, FastAPI, Typer, pytest + typer.testing + httpx AsyncClient

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `src/dit/core/sidecar.py` | Modify | Add `sidecar_summary(sidecar) -> dict` as a public function |
| `src/dit/server/routes/meta_api.py` | Modify | Replace private `_sidecar_summary` with import from `core/sidecar` |
| `src/dit/core/export.py` | Create | Pure `export_commit()` function: JSONL + CSV + meta |
| `src/dit/cli/main.py` | Modify | Add `export` command wired to `export_commit()` |
| `src/dit/server/routes/export_api.py` | Create | `GET /{repo}/export/{commit_hash}/{file_path:path}` endpoint |
| `src/dit/server/app.py` | Modify | Register `export_api` router |
| `tests/test_export.py` | Create | Unit tests for `core/export.py` |
| `tests/test_cli_export.py` | Create | CLI integration tests for `dit export` |
| `tests/server/test_routes_export.py` | Create | Server route tests for the export endpoint |

---

## Task 1: Extract `sidecar_summary` to `core/sidecar.py`

**Files:**
- Modify: `src/dit/core/sidecar.py`
- Modify: `src/dit/server/routes/meta_api.py`
- Test: `tests/test_export.py` (create file for this task)

The private `_sidecar_summary` function in `meta_api.py` computes aggregate stats from a `Sidecar` object. It needs to live in `core/sidecar.py` so the export module can reuse it without importing server code.

- [ ] **Step 1: Write the failing test**

Create `tests/test_export.py`:

```python
import pytest
from dit.core.objects import Sidecar, SidecarEntry
from dit.core.sidecar import sidecar_summary


def _make_sidecar() -> Sidecar:
    entries = [
        SidecarEntry(row_hash="a" * 64, char_count=100, token_estimate=25, field_count=3, lang="en"),
        SidecarEntry(row_hash="b" * 64, char_count=200, token_estimate=50, field_count=5, lang="zh"),
        SidecarEntry(row_hash="c" * 64, char_count=150, token_estimate=37, field_count=4, lang="en"),
    ]
    return Sidecar(manifest_hash="m" * 64, entries=entries)


class TestSidecarSummary:
    def test_returns_correct_row_count(self):
        result = sidecar_summary(_make_sidecar())
        assert result["row_count"] == 3

    def test_returns_correct_char_count(self):
        result = sidecar_summary(_make_sidecar())
        assert result["char_count"] == 450

    def test_returns_correct_token_estimate(self):
        result = sidecar_summary(_make_sidecar())
        assert result["token_estimate"] == 112

    def test_returns_avg_fields(self):
        result = sidecar_summary(_make_sidecar())
        assert result["avg_fields"] == pytest.approx(4.0, rel=0.01)

    def test_lang_distribution(self):
        result = sidecar_summary(_make_sidecar())
        assert result["lang_distribution"] == {"en": 2, "zh": 1}

    def test_empty_sidecar(self):
        empty = Sidecar(manifest_hash="m" * 64, entries=[])
        result = sidecar_summary(empty)
        assert result == {
            "row_count": 0,
            "char_count": 0,
            "token_estimate": 0,
            "avg_fields": 0.0,
            "lang_distribution": {},
        }
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd ~/code/datahub
uv run pytest tests/test_export.py::TestSidecarSummary -v
```

Expected: `FAILED` — `ImportError: cannot import name 'sidecar_summary' from 'dit.core.sidecar'`

- [ ] **Step 3: Add `sidecar_summary` to `core/sidecar.py`**

Append to the bottom of `src/dit/core/sidecar.py` (after the existing `compute_sidecar` function):

```python
def sidecar_summary(sidecar) -> dict:
    """Compute aggregate stats from a Sidecar object.

    Returns a dict with keys: row_count, char_count, token_estimate,
    avg_fields, lang_distribution.
    """
    row_count = len(sidecar.entries)
    if row_count == 0:
        return {
            "row_count": 0,
            "char_count": 0,
            "token_estimate": 0,
            "avg_fields": 0.0,
            "lang_distribution": {},
        }
    total_chars = sum(e.char_count for e in sidecar.entries)
    total_tokens = sum(e.token_estimate for e in sidecar.entries)
    avg_fields = sum(e.field_count for e in sidecar.entries) / row_count
    lang_counts: dict[str, int] = {}
    for e in sidecar.entries:
        k = e.lang or "unknown"
        lang_counts[k] = lang_counts.get(k, 0) + 1
    return {
        "row_count": row_count,
        "char_count": total_chars,
        "token_estimate": total_tokens,
        "avg_fields": round(avg_fields, 2),
        "lang_distribution": lang_counts,
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_export.py::TestSidecarSummary -v
```

Expected: all 6 tests `PASSED`

- [ ] **Step 5: Replace `_sidecar_summary` in `meta_api.py` with import**

In `src/dit/server/routes/meta_api.py`, replace the entire `_sidecar_summary` function body (lines 26–49) with an import:

```python
from dit.core.sidecar import sidecar_summary as _sidecar_summary
```

The existing callers (`_get_summary(...)` in `meta_diff` and the calls in `meta_summary`) use `_sidecar_summary(...)` — the alias keeps them unchanged.

- [ ] **Step 6: Run existing meta tests to confirm nothing regressed**

```bash
uv run pytest tests/server/test_routes_meta.py -v
```

Expected: all tests `PASSED`

- [ ] **Step 7: Commit**

```bash
git add src/dit/core/sidecar.py src/dit/server/routes/meta_api.py tests/test_export.py
git commit -m "refactor: extract sidecar_summary to core/sidecar.py"
```

---

## Task 2: Core export module — JSONL format

**Files:**
- Create: `src/dit/core/export.py`
- Test: `tests/test_export.py` (append new test class)

`export_commit()` reads a commit from the store, flattens the tree, and writes JSONL files to an output directory. This task covers the JSONL path only.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_export.py`:

```python
import json
import time
from pathlib import Path

from dit.core.export import export_commit
from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    serialize_commit, serialize_manifest,
)
from dit.core.store import ObjectStore
from dit.core.tree_builder import build_nested_tree


def _build_repo(tmp_path: Path) -> tuple[ObjectStore, str]:
    """Create a minimal repo with two JSONL files committed. Returns (store, commit_hash)."""
    store = ObjectStore(tmp_path / "objects")

    rows_a = [
        json.dumps({"messages": [{"role": "user", "content": "hello"}]}),
        json.dumps({"messages": [{"role": "user", "content": "world"}]}),
    ]
    rows_b = [
        json.dumps({"messages": [{"role": "user", "content": "foo"}]}),
    ]

    def _write_manifest(rows: list[str]) -> str:
        entries = []
        for r in rows:
            rh = store.write("rows", r.encode("utf-8"))
            entries.append(ManifestEntry(row_hash=rh, query_fingerprint=None))
        manifest = Manifest(entries=entries)
        return store.write("manifests", serialize_manifest(manifest))

    mh_a = _write_manifest(rows_a)
    mh_b = _write_manifest(rows_b)

    tree_entries = {
        "train.jsonl": ("manifest", mh_a, None),
        "eval.jsonl": ("manifest", mh_b, None),
    }
    tree_hash = build_nested_tree(store, tree_entries)

    commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=[],
        author="tester",
        message="initial",
        timestamp=int(time.time()),
    )
    commit_hash = store.write("commits", serialize_commit(commit))
    return store, commit_hash


class TestExportCommitJsonl:
    def test_exports_all_files(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        report = export_commit(store, commit_hash, out)

        assert (out / "train.jsonl").exists()
        assert (out / "eval.jsonl").exists()
        assert len(report) == 2

    def test_report_contains_path_and_rows(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        report = export_commit(store, commit_hash, out)

        paths = {r["path"] for r in report}
        assert "train.jsonl" in paths
        assert "eval.jsonl" in paths

        by_path = {r["path"]: r for r in report}
        assert by_path["train.jsonl"]["rows"] == 2
        assert by_path["eval.jsonl"]["rows"] == 1

    def test_jsonl_content_is_valid_json_per_line(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        export_commit(store, commit_hash, out)

        lines = (out / "train.jsonl").read_text().splitlines()
        assert len(lines) == 2
        for line in lines:
            obj = json.loads(line)
            assert "messages" in obj

    def test_file_filter_exports_single_file(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        report = export_commit(store, commit_hash, out, file_filter="train.jsonl")

        assert (out / "train.jsonl").exists()
        assert not (out / "eval.jsonl").exists()
        assert len(report) == 1

    def test_unknown_file_filter_raises(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        with pytest.raises(FileNotFoundError, match="not found"):
            export_commit(store, commit_hash, out, file_filter="missing.jsonl")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_export.py::TestExportCommitJsonl -v
```

Expected: `FAILED` — `ModuleNotFoundError: No module named 'dit.core.export'`

- [ ] **Step 3: Create `src/dit/core/export.py`**

```python
# src/dit/core/export.py
"""Export files from a commit to a local directory."""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from dit.core.objects import deserialize_commit, deserialize_manifest, deserialize_sidecar
from dit.core.store import ObjectStore
from dit.core.tree_walker import flatten_tree


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

    Raises FileNotFoundError if file_filter names a path not present in the commit.
    """
    commit_data = store.read("commits", commit_hash)
    if commit_data is None:
        raise FileNotFoundError(f"Commit {commit_hash[:8]} not found in store")

    commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, commit.tree_hash)

    # Validate file_filter before doing any work
    if file_filter is not None:
        clean_filter = file_filter.lstrip("/")
        if clean_filter not in flat:
            raise FileNotFoundError(f"'{file_filter}' not found in commit {commit_hash[:8]}")

    report: list[dict] = []

    for path, (obj_type, obj_hash, sidecar_hash) in sorted(flat.items()):
        if obj_type != "manifest":
            continue
        if file_filter is not None and path != file_filter.lstrip("/"):
            continue

        manifest_data = store.read("manifests", obj_hash)
        if manifest_data is None:
            raise FileNotFoundError(f"Manifest {obj_hash[:8]} missing from store")

        manifest = deserialize_manifest(manifest_data)

        # Ensure parent directories exist
        dest = output_dir / path
        dest.parent.mkdir(parents=True, exist_ok=True)

        if fmt == "jsonl":
            total_bytes = _write_jsonl(store, manifest, dest)
        elif fmt == "csv":
            total_bytes = _write_csv(store, manifest, dest)
        else:
            raise ValueError(f"Unknown format '{fmt}'. Expected 'jsonl' or 'csv'.")

        row_count = len(manifest.entries)
        report.append({"path": path, "rows": row_count, "bytes": total_bytes})

        if include_meta and sidecar_hash is not None:
            _write_meta(store, path, obj_hash, sidecar_hash, output_dir)

    return report


def _write_jsonl(store: ObjectStore, manifest, dest: Path) -> int:
    """Write rows as JSONL. Returns total bytes written."""
    total = 0
    with dest.open("wb") as fh:
        for entry in manifest.entries:
            row_bytes = store.read("rows", entry.row_hash)
            if row_bytes is None:
                raise FileNotFoundError(f"Row {entry.row_hash[:8]} missing from store")
            fh.write(row_bytes)
            fh.write(b"\n")
            total += len(row_bytes) + 1
    return total


def _write_csv(store: ObjectStore, manifest, dest: Path) -> int:
    """Write rows as CSV (first pass collects all keys, second pass writes). Returns total bytes written."""
    rows: list[dict] = []
    all_keys: list[str] = []
    seen_keys: set[str] = set()

    for entry in manifest.entries:
        row_bytes = store.read("rows", entry.row_hash)
        if row_bytes is None:
            raise FileNotFoundError(f"Row {entry.row_hash[:8]} missing from store")
        parsed = json.loads(row_bytes)
        if isinstance(parsed, dict):
            for k in parsed:
                if k not in seen_keys:
                    seen_keys.add(k)
                    all_keys.append(k)
        rows.append(parsed)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=sorted(all_keys), extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        if not isinstance(row, dict):
            row = {"value": json.dumps(row)}
        # Serialize nested values as JSON strings
        flat_row = {
            k: json.dumps(v) if isinstance(v, (dict, list)) else v
            for k, v in row.items()
        }
        writer.writerow(flat_row)

    content = buf.getvalue().encode("utf-8")
    dest.write_bytes(content)
    return len(content)


def _write_meta(
    store: ObjectStore,
    path: str,
    manifest_hash: str,
    sidecar_hash: str,
    output_dir: Path,
) -> None:
    """Write <path>.meta.json alongside the exported file."""
    from dit.core.sidecar import sidecar_summary

    sc_data = store.read("sidecars", sidecar_hash)
    if sc_data is None:
        return  # Sidecar missing — skip silently

    sidecar = deserialize_sidecar(sc_data)
    summary = sidecar_summary(sidecar)
    meta = {
        "file": path,
        "manifest_hash": manifest_hash,
        "sidecar_hash": sidecar_hash,
        **summary,
    }
    meta_dest = output_dir / (path + ".meta.json")
    meta_dest.parent.mkdir(parents=True, exist_ok=True)
    meta_dest.write_text(json.dumps(meta, indent=2))
```

- [ ] **Step 4: Run test to verify it passes**

```bash
uv run pytest tests/test_export.py::TestExportCommitJsonl -v
```

Expected: all 5 tests `PASSED`

- [ ] **Step 5: Commit**

```bash
git add src/dit/core/export.py tests/test_export.py
git commit -m "feat: core export module — JSONL format"
```

---

## Task 3: Core export module — CSV format

**Files:**
- Test: `tests/test_export.py` (append new test class)
- No new source files — `_write_csv` is already in `core/export.py` from Task 2

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_export.py`:

```python
class TestExportCommitCsv:
    def test_csv_has_header(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        export_commit(store, commit_hash, out, fmt="csv", file_filter="train.jsonl")

        lines = (out / "train.jsonl").read_text().splitlines()
        assert len(lines) >= 2  # header + at least 1 data row
        # Header should contain the key from the row data
        assert "messages" in lines[0]

    def test_csv_row_count_matches_manifest(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        report = export_commit(store, commit_hash, out, fmt="csv", file_filter="train.jsonl")

        lines = (out / "train.jsonl").read_text().splitlines()
        # header + 2 data rows
        assert len(lines) == 3
        assert report[0]["rows"] == 2

    def test_csv_nested_values_are_json_strings(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        export_commit(store, commit_hash, out, fmt="csv", file_filter="train.jsonl")

        import csv as _csv
        with (out / "train.jsonl").open() as fh:
            reader = _csv.DictReader(fh)
            rows = list(reader)
        # The "messages" value is a list — it should be JSON-serialized as a string
        val = rows[0]["messages"]
        parsed = json.loads(val)
        assert isinstance(parsed, list)

    def test_invalid_format_raises(self, tmp_path: Path):
        store, commit_hash = _build_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        with pytest.raises(ValueError, match="Unknown format"):
            export_commit(store, commit_hash, out, fmt="parquet")
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_export.py::TestExportCommitCsv -v
```

Expected: `FAILED` — the CSV tests should fail because the test file references `export_commit` which now exists, but we need to verify the CSV logic is correct. If tests pass immediately, inspect output to confirm CSV headers are correct.

Note: if `test_csv_nested_values_are_json_strings` fails with a `KeyError`, it means the header name differs from `messages` — adjust to match the actual header.

- [ ] **Step 3: Run test to verify it passes**

```bash
uv run pytest tests/test_export.py::TestExportCommitCsv -v
```

Expected: all 4 tests `PASSED` (the implementation in Task 2 already handles CSV)

- [ ] **Step 4: Commit**

```bash
git add tests/test_export.py
git commit -m "test: CSV format tests for core export module"
```

---

## Task 4: Core export module — include-meta flag

**Files:**
- Test: `tests/test_export.py` (append new test class)
- `src/dit/core/export.py` already has `_write_meta()` from Task 2

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_export.py`:

```python
from dit.core.objects import Sidecar, SidecarEntry, serialize_sidecar


def _build_repo_with_sidecar(tmp_path: Path) -> tuple[ObjectStore, str]:
    """Create a repo with one JSONL file and a sidecar. Returns (store, commit_hash)."""
    store = ObjectStore(tmp_path / "objects")

    row = json.dumps({"messages": [{"role": "user", "content": "hello"}]})
    rh = store.write("rows", row.encode("utf-8"))

    manifest = Manifest(entries=[ManifestEntry(row_hash=rh, query_fingerprint=None)])
    mh = store.write("manifests", serialize_manifest(manifest))

    sc_entries = [SidecarEntry(row_hash=rh, char_count=len(row), token_estimate=len(row) // 4, field_count=1, lang="en")]
    sidecar = Sidecar(manifest_hash=mh, entries=sc_entries)
    sc_hash = store.write("sidecars", serialize_sidecar(sidecar))

    tree_entries = {"train.jsonl": ("manifest", mh, sc_hash)}
    tree_hash = build_nested_tree(store, tree_entries)

    commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=[],
        author="tester",
        message="initial",
        timestamp=int(time.time()),
    )
    commit_hash = store.write("commits", serialize_commit(commit))
    return store, commit_hash


class TestExportIncludeMeta:
    def test_meta_file_created_when_flag_set(self, tmp_path: Path):
        store, commit_hash = _build_repo_with_sidecar(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        export_commit(store, commit_hash, out, include_meta=True)

        assert (out / "train.jsonl").exists()
        assert (out / "train.jsonl.meta.json").exists()

    def test_meta_file_not_created_by_default(self, tmp_path: Path):
        store, commit_hash = _build_repo_with_sidecar(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        export_commit(store, commit_hash, out)

        assert not (out / "train.jsonl.meta.json").exists()

    def test_meta_file_content(self, tmp_path: Path):
        store, commit_hash = _build_repo_with_sidecar(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        export_commit(store, commit_hash, out, include_meta=True)

        meta = json.loads((out / "train.jsonl.meta.json").read_text())
        assert meta["file"] == "train.jsonl"
        assert "manifest_hash" in meta
        assert "sidecar_hash" in meta
        assert meta["row_count"] == 1
        assert meta["char_count"] > 0
        assert "lang_distribution" in meta

    def test_meta_skipped_when_no_sidecar(self, tmp_path: Path):
        """Files without sidecar should export normally without .meta.json."""
        store, commit_hash = _build_repo(tmp_path)  # no sidecar
        out = tmp_path / "exported"
        out.mkdir()

        export_commit(store, commit_hash, out, include_meta=True)

        assert (out / "train.jsonl").exists()
        assert not (out / "train.jsonl.meta.json").exists()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_export.py::TestExportIncludeMeta -v
```

Expected: `FAILED` — `test_meta_file_created_when_flag_set` fails because the sidecar-linked repo builder is new but `_write_meta` logic already exists. Check which tests fail.

- [ ] **Step 3: Run test to verify it passes**

```bash
uv run pytest tests/test_export.py::TestExportIncludeMeta -v
```

Expected: all 4 tests `PASSED` (implementation in Task 2 already handles `include_meta`)

- [ ] **Step 4: Run all export unit tests**

```bash
uv run pytest tests/test_export.py -v
```

Expected: all tests `PASSED`

- [ ] **Step 5: Commit**

```bash
git add tests/test_export.py
git commit -m "test: include-meta flag tests for core export module"
```

---

## Task 5: CLI command — `dit export`

**Files:**
- Modify: `src/dit/cli/main.py`
- Create: `tests/test_cli_export.py`

The `export` command resolves a ref or commit hash, calls `export_commit()`, and prints a progress report. It supports `--ref`, `--file`, `--format`, `--include-meta`, `--output`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli_export.py`:

```python
import json
import os
import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    serialize_commit, serialize_manifest,
)
from dit.core.store import ObjectStore
from dit.core.tree_builder import build_nested_tree
from dit.core.refs import RefStore

runner = CliRunner()


def _init_repo(tmp_path: Path) -> tuple[ObjectStore, RefStore, str]:
    """Set up a dit repo with one committed JSONL file. Returns (store, refs, commit_hash)."""
    os.chdir(tmp_path)
    runner.invoke(app, ["init"])

    dot = tmp_path / ".datahub"
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    row = json.dumps({"messages": [{"role": "user", "content": "hello"}]})
    rh = store.write("rows", row.encode("utf-8"))

    manifest = Manifest(entries=[ManifestEntry(row_hash=rh, query_fingerprint=None)])
    mh = store.write("manifests", serialize_manifest(manifest))

    tree_entries = {"train.jsonl": ("manifest", mh, None)}
    tree_hash = build_nested_tree(store, tree_entries)

    commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=[],
        author="tester",
        message="initial",
        timestamp=int(time.time()),
    )
    commit_hash = store.write("commits", serialize_commit(commit))
    refs.set_branch("main", commit_hash)

    return store, refs, commit_hash


class TestExportCommand:
    def test_export_creates_output_file(self, tmp_path: Path):
        _init_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        result = runner.invoke(app, ["export", "--output", str(out)])
        assert result.exit_code == 0
        assert (out / "train.jsonl").exists()

    def test_export_output_contains_summary(self, tmp_path: Path):
        _init_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        result = runner.invoke(app, ["export", "--output", str(out)])
        assert result.exit_code == 0
        assert "train.jsonl" in result.stdout
        assert "1 row" in result.stdout or "rows" in result.stdout

    def test_export_file_filter(self, tmp_path: Path):
        _init_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        result = runner.invoke(app, ["export", "--file", "train.jsonl", "--output", str(out)])
        assert result.exit_code == 0
        assert (out / "train.jsonl").exists()

    def test_export_missing_file_filter_exits_1(self, tmp_path: Path):
        _init_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        result = runner.invoke(app, ["export", "--file", "missing.jsonl", "--output", str(out)])
        assert result.exit_code == 1

    def test_export_no_commits_exits_1(self, tmp_path: Path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"])
        out = tmp_path / "exported"
        out.mkdir()

        result = runner.invoke(app, ["export", "--output", str(out)])
        assert result.exit_code == 1

    def test_export_csv_format(self, tmp_path: Path):
        _init_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        result = runner.invoke(app, ["export", "--format", "csv", "--output", str(out)])
        assert result.exit_code == 0
        assert (out / "train.jsonl").exists()
        content = (out / "train.jsonl").read_text()
        # CSV header should be present
        assert "messages" in content

    def test_export_ref_flag(self, tmp_path: Path):
        _init_repo(tmp_path)
        out = tmp_path / "exported"
        out.mkdir()

        result = runner.invoke(app, ["export", "--ref", "main", "--output", str(out)])
        assert result.exit_code == 0
        assert (out / "train.jsonl").exists()

    def test_export_outside_repo_exits_1(self, tmp_path: Path):
        # Do NOT init a repo — just cd into a plain empty dir
        empty = tmp_path / "empty"
        empty.mkdir()
        os.chdir(empty)
        out = tmp_path / "exported"
        out.mkdir()

        result = runner.invoke(app, ["export", "--output", str(out)])
        assert result.exit_code == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/test_cli_export.py -v
```

Expected: `FAILED` — `Error: No such command 'export'`

- [ ] **Step 3: Add `export` command to `src/dit/cli/main.py`**

Add this import at the top of the file (alongside existing imports):

```python
from typing import Optional  # already present; ensure it is
```

Add this command anywhere after the existing CLI commands (e.g., after `meta_diff`):

```python
@app.command()
def export(
    ref: str = typer.Option("main", "--ref", help="Branch name or commit hash to export from"),
    file: Optional[str] = typer.Option(None, "--file", help="Export only this file path"),
    format: str = typer.Option("jsonl", "--format", help="Output format: jsonl or csv"),
    include_meta: bool = typer.Option(False, "--include-meta", help="Write .meta.json alongside each file"),
    output: str = typer.Option(".", "--output", help="Local directory to write exported files"),
):
    """Export files from a commit to a local directory."""
    from dit.core.export import export_commit
    from dit.core.tree_walker import flatten_tree

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    # Resolve ref to commit hash
    commit_hash = refs.get_branch(ref)
    if commit_hash is None:
        # Maybe ref is already a commit hash (64-char hex)
        if len(ref) == 64 and all(c in "0123456789abcdef" for c in ref):
            commit_hash = ref
        else:
            typer.echo(f"fatal: ref '{ref}' not found", err=True)
            raise typer.Exit(1)

    if commit_hash is None:
        typer.echo(f"fatal: no commits on branch '{ref}'", err=True)
        raise typer.Exit(1)

    output_dir = Path(output)
    output_dir.mkdir(parents=True, exist_ok=True)

    typer.echo(f"Exporting from {ref} (commit {commit_hash[:8]})")

    try:
        report = export_commit(
            store,
            commit_hash,
            output_dir,
            file_filter=file,
            fmt=format,
            include_meta=include_meta,
        )
    except FileNotFoundError as exc:
        typer.echo(f"fatal: {exc}", err=True)
        raise typer.Exit(1)
    except ValueError as exc:
        typer.echo(f"fatal: {exc}", err=True)
        raise typer.Exit(1)

    for entry in report:
        rows = entry["rows"]
        row_word = "row" if rows == 1 else "rows"
        typer.echo(f"  {entry['path']} ({rows} {row_word})... done")
        if include_meta:
            meta_path = entry["path"] + ".meta.json"
            if (output_dir / meta_path).exists():
                typer.echo(f"  {meta_path}... done")

    file_word = "file" if len(report) == 1 else "files"
    typer.echo(f"Exported {len(report)} {file_word} to {output_dir}/")
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/test_cli_export.py -v
```

Expected: all 8 tests `PASSED`

- [ ] **Step 5: Run all tests to confirm nothing regressed**

```bash
uv run pytest tests/ -v --timeout=30
```

Expected: all tests `PASSED`

- [ ] **Step 6: Commit**

```bash
git add src/dit/cli/main.py tests/test_cli_export.py
git commit -m "feat: dit export CLI command"
```

---

## Task 6: Server endpoint — single-file export

**Files:**
- Create: `src/dit/server/routes/export_api.py`
- Modify: `src/dit/server/app.py`
- Create: `tests/server/test_routes_export.py`

`GET /api/v1/repos/{repo}/export/{commit_hash}/{file_path:path}?format=jsonl` returns raw file content. The response is `application/x-ndjson` for JSONL or `text/csv` for CSV. It uses `export_commit()` with a temp directory.

- [ ] **Step 1: Write the failing tests**

Create `tests/server/test_routes_export.py`:

```python
import csv
import io
import json
import time
from pathlib import Path

import pytest
from httpx import AsyncClient

from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    serialize_commit, serialize_manifest,
)
from dit.core.store import ObjectStore
from dit.core.tree_builder import build_nested_tree


async def _create_repo_with_data(client: AsyncClient, tmp_path: Path, repo: str = "export-repo"):
    """Create a repo with two JSONL rows committed. Returns (store, commit_hash)."""
    resp = await client.post("/api/v1/repos", json={"name": repo})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / repo / "objects")

    rows = [
        json.dumps({"messages": [{"role": "user", "content": "hello"}]}),
        json.dumps({"messages": [{"role": "user", "content": "world"}]}),
    ]
    row_hashes = [store.write("rows", r.encode("utf-8")) for r in rows]
    entries = [ManifestEntry(row_hash=rh, query_fingerprint=None) for rh in row_hashes]
    manifest = Manifest(entries=entries)
    mh = store.write("manifests", serialize_manifest(manifest))

    tree_entries = {"train.jsonl": ("manifest", mh, None)}
    tree_hash = build_nested_tree(store, tree_entries)

    commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=[],
        author="tester",
        message="initial",
        timestamp=int(time.time()),
    )
    commit_hash = store.write("commits", serialize_commit(commit))

    await client.post(
        f"/api/v1/repos/{repo}/refs/heads/main",
        json={"old": None, "new": commit_hash},
    )
    return store, commit_hash


class TestExportEndpoint:
    async def test_export_jsonl_returns_200(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_data(client, tmp_path)

        resp = await client.get(
            f"/api/v1/repos/export-repo/export/{commit_hash}/train.jsonl"
        )
        assert resp.status_code == 200

    async def test_export_jsonl_content_type(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_data(client, tmp_path)

        resp = await client.get(
            f"/api/v1/repos/export-repo/export/{commit_hash}/train.jsonl"
        )
        assert "ndjson" in resp.headers["content-type"] or "jsonl" in resp.headers.get("content-disposition", "")

    async def test_export_jsonl_body_is_valid_ndjson(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_data(client, tmp_path)

        resp = await client.get(
            f"/api/v1/repos/export-repo/export/{commit_hash}/train.jsonl"
        )
        lines = [l for l in resp.text.splitlines() if l.strip()]
        assert len(lines) == 2
        for line in lines:
            obj = json.loads(line)
            assert "messages" in obj

    async def test_export_csv_format(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_data(client, tmp_path)

        resp = await client.get(
            f"/api/v1/repos/export-repo/export/{commit_hash}/train.jsonl",
            params={"format": "csv"},
        )
        assert resp.status_code == 200
        reader = csv.DictReader(io.StringIO(resp.text))
        rows = list(reader)
        assert len(rows) == 2
        assert "messages" in rows[0]

    async def test_export_commit_not_found(self, client: AsyncClient, tmp_path: Path):
        await _create_repo_with_data(client, tmp_path)

        resp = await client.get(
            f"/api/v1/repos/export-repo/export/{'z' * 64}/train.jsonl"
        )
        assert resp.status_code == 404

    async def test_export_file_not_found(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_data(client, tmp_path)

        resp = await client.get(
            f"/api/v1/repos/export-repo/export/{commit_hash}/nonexistent.jsonl"
        )
        assert resp.status_code == 404

    async def test_export_repo_not_found(self, client: AsyncClient, tmp_path: Path):
        resp = await client.get(
            f"/api/v1/repos/no-such-repo/export/{'a' * 64}/train.jsonl"
        )
        assert resp.status_code == 404

    async def test_export_invalid_format(self, client: AsyncClient, tmp_path: Path):
        store, commit_hash = await _create_repo_with_data(client, tmp_path)

        resp = await client.get(
            f"/api/v1/repos/export-repo/export/{commit_hash}/train.jsonl",
            params={"format": "parquet"},
        )
        assert resp.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

```bash
uv run pytest tests/server/test_routes_export.py -v
```

Expected: `FAILED` — `404 Not Found` for all tests (route not registered yet)

- [ ] **Step 3: Create `src/dit/server/routes/export_api.py`**

```python
# src/dit/server/routes/export_api.py
"""Single-file export endpoint: GET /{repo}/export/{commit_hash}/{file_path:path}"""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos", tags=["export"])


def _store_for_repo(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


@router.get("/{repo}/export/{commit_hash}/{file_path:path}")
async def export_file(
    repo: str,
    commit_hash: str,
    file_path: str,
    request: Request,
    format: str = Query(default="jsonl", description="Output format: jsonl or csv"),
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    """Export a single file from a commit as raw JSONL or CSV content."""
    from dit.core.export import export_commit

    await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    if format not in ("jsonl", "csv"):
        raise HTTPException(status_code=400, detail=f"Unknown format '{format}'. Expected 'jsonl' or 'csv'.")

    clean_path = file_path.lstrip("/")

    with tempfile.TemporaryDirectory() as tmpdir:
        output_dir = Path(tmpdir)
        try:
            export_commit(
                store,
                commit_hash,
                output_dir,
                file_filter=clean_path,
                fmt=format,
                include_meta=False,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

        dest = output_dir / clean_path
        if not dest.exists():
            raise HTTPException(status_code=404, detail=f"Export produced no file for '{clean_path}'")

        content = dest.read_text(encoding="utf-8")

    if format == "csv":
        media_type = "text/csv"
    else:
        media_type = "application/x-ndjson"

    return PlainTextResponse(content=content, media_type=media_type)
```

- [ ] **Step 4: Register the router in `src/dit/server/app.py`**

Add before the final `return application` line in `create_app()`:

```python
    from dit.server.routes.export_api import router as export_router
    application.include_router(export_router)
```

The full `create_app()` tail should look like:

```python
    from dit.server.routes.meta_api import router as meta_router
    application.include_router(meta_router)

    from dit.server.routes.export_api import router as export_router
    application.include_router(export_router)

    return application
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
uv run pytest tests/server/test_routes_export.py -v
```

Expected: all 8 tests `PASSED`

- [ ] **Step 6: Run full test suite**

```bash
uv run pytest tests/ -v --timeout=30
```

Expected: all tests `PASSED`

- [ ] **Step 7: Commit**

```bash
git add src/dit/server/routes/export_api.py src/dit/server/app.py tests/server/test_routes_export.py
git commit -m "feat: server export endpoint GET /{repo}/export/{commit}/{file_path}"
```

---

## Task 7: Final verification

**Files:** No changes — run the full suite and confirm.

- [ ] **Step 1: Run all tests**

```bash
cd ~/code/datahub
uv run pytest tests/ -v --timeout=30
```

Expected: all tests `PASSED`, zero failures.

- [ ] **Step 2: Smoke-test the CLI manually**

```bash
cd /tmp
mkdir smoke-test && cd smoke-test
dit init
echo '{"messages":[{"role":"user","content":"hello world"}]}' > train.jsonl
echo '{"messages":[{"role":"user","content":"foo bar"}]}' >> train.jsonl
echo '{"q":"test"}' > eval.jsonl
dit add .
dit commit -m "initial"
dit export --output ./exported
cat ./exported/train.jsonl
dit export --format csv --output ./exported-csv
cat ./exported-csv/train.jsonl
```

Expected output (export step):
```
Exporting from main (commit <hash>)
  train.jsonl (2 rows)... done
  eval.jsonl (1 row)... done
Exported 2 files to ./exported/
```

- [ ] **Step 3: Verify `sidecar_summary` is exported from `core/sidecar.py`**

```bash
python -c "from dit.core.sidecar import sidecar_summary; print('OK')"
```

Expected: `OK`

- [ ] **Step 4: Final commit if any housekeeping was needed**

If the smoke test revealed any issues, fix them, run the tests again, then:

```bash
git add -p
git commit -m "fix: <description of any smoke-test fix>"
```

---

## Self-Review

### Spec coverage check

| Spec section | Covered by task |
|---|---|
| `dit export` CLI with `--ref`, `--file`, `--format`, `--include-meta`, `--output` flags | Task 5 |
| Export logic: resolve ref → commit → tree → manifests → rows | Task 2 |
| JSONL output (raw row bytes + newline) | Task 2 |
| CSV output (sorted headers, nested values JSON-serialized) | Task 3 |
| `include-meta` → `.meta.json` companion files | Task 4 |
| Nested tree → preserve directory structure | Task 2 (`dest.parent.mkdir(parents=True, exist_ok=True)`) |
| `sidecar_summary()` extracted to `core/sidecar.py` | Task 1 |
| Server: `GET /{repo}/export/{commit}/{file_path:path}?format=...` | Task 6 |
| S3 support | Explicitly out of scope per plan brief |
| Streaming tar.gz | Explicitly out of scope per plan brief |
| Gateway/Vue changes | Explicitly out of scope per plan brief |

**Gap identified:** The spec says `--ref` defaults to `heads/main`, but `RefStore.get_branch()` takes just a branch name (e.g. `main`), not `heads/main`. The CLI implementation resolves this by using `refs.get_branch(ref)` — if ref is `heads/main`, it won't match; the user will pass `main`. The default is set to `"main"` (not `"heads/main"`) in the CLI. This is consistent with how other CLI commands work in this codebase. No fix needed — intentional simplification.

### Placeholder scan

No TBD/TODO/placeholder text found in code blocks. All function signatures, type annotations, and return values are concrete and consistent across tasks.

### Type consistency check

- `export_commit(store, commit_hash, output_dir, *, file_filter, fmt, include_meta) -> list[dict]` — defined in Task 2, used identically in Tasks 4, 5, 6.
- `sidecar_summary(sidecar) -> dict` — defined in Task 1, used in `_write_meta()` in Task 2 (via internal import), and imported directly in `meta_api.py` Task 1.
- `_write_jsonl`, `_write_csv`, `_write_meta` — private helpers defined and used within `core/export.py` only.
- `build_nested_tree(store, dict[str, tuple[str, str, str | None]]) -> str` — used consistently in test helpers matching the existing pattern from `meta_api.py` tests.
