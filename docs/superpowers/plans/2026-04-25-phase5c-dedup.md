# Phase 5C: Duplicate Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add duplicate detection (exact + query-level) that reports statistics without modifying data.

**Architecture:** Core `detect_duplicates()` function builds row_hash and query_fingerprint indexes across all manifest files, then reports groups. CLI (`dit dedup`) and server API (`GET /repos/{repo}/dedup/{commit}`) consume the core function. Gateway proxies.

**Tech Stack:** Python 3.12, FastAPI, Typer CLI, Go (Forgejo gateway proxy)

---

### Task 1: Core Dedup Module

**Files:**
- Create: `src/dit/core/dedup.py`
- Test: `tests/test_dedup.py`

- [ ] **Step 1: Write tests**

Create `tests/test_dedup.py`:

```python
"""Tests for dit.core.dedup module."""
import json
import time

from dit.core.objects import (
    Commit, Manifest, ManifestEntry, Tree, TreeEntry,
    serialize_manifest, serialize_tree, serialize_commit,
    object_hash,
)
from dit.core.store import ObjectStore
from dit.core.hash import row_hash as compute_row_hash, query_fingerprint as compute_qfp


def _write_row(store: ObjectStore, content: dict) -> str:
    data = json.dumps(content, separators=(",", ":"), sort_keys=True).encode()
    return store.write("rows", data)


def _make_commit(
    store: ObjectStore,
    files: dict[str, list[dict]],
    parent_hashes: list[str] | None = None,
) -> str:
    tree_entries = []
    for fname, rows in files.items():
        entries = []
        for row in rows:
            rh = compute_row_hash(row)
            _write_row(store, row)
            qfp = compute_qfp(row) if "messages" in row else None
            entries.append(ManifestEntry(row_hash=rh, query_fingerprint=qfp))
        manifest = Manifest(entries=entries)
        m_hash = store.write("manifests", serialize_manifest(manifest))
        tree_entries.append(TreeEntry(name=fname, obj_type="manifest", obj_hash=m_hash))
    tree = Tree(entries=tree_entries)
    t_hash = store.write("trees", serialize_tree(tree))
    c = Commit(
        tree_hash=t_hash,
        parent_hashes=parent_hashes or [],
        author="alice",
        message="test",
        timestamp=int(time.time()),
    )
    return store.write("commits", serialize_commit(c))


def _conv(user_msg: str, asst_msg: str) -> dict:
    return {"messages": [{"role": "user", "content": user_msg}, {"role": "assistant", "content": asst_msg}]}


class TestDetectDuplicates:
    def test_no_duplicates(self, tmp_path):
        from dit.core.dedup import detect_duplicates

        store = ObjectStore(tmp_path / "objects")
        c = _make_commit(store, {"train.jsonl": [
            _conv("q1", "a1"),
            _conv("q2", "a2"),
        ]})

        result = detect_duplicates(store, c)
        assert result["summary"]["severity"] == "clean"
        assert result["summary"]["exact_dup_groups"] == 0
        assert result["summary"]["query_dup_groups"] == 0

    def test_exact_duplicates_same_file(self, tmp_path):
        from dit.core.dedup import detect_duplicates

        store = ObjectStore(tmp_path / "objects")
        row = _conv("q1", "a1")
        c = _make_commit(store, {"train.jsonl": [row, row, _conv("q2", "a2")]})

        result = detect_duplicates(store, c)
        assert result["summary"]["severity"] == "warning"
        assert result["summary"]["exact_dup_groups"] == 1
        assert result["summary"]["exact_dup_rows"] == 2

    def test_exact_duplicates_cross_file(self, tmp_path):
        from dit.core.dedup import detect_duplicates

        store = ObjectStore(tmp_path / "objects")
        row = _conv("q1", "a1")
        c = _make_commit(store, {
            "train.jsonl": [row],
            "eval.jsonl": [row],
        })

        result = detect_duplicates(store, c)
        assert result["summary"]["severity"] == "warning"
        assert result["summary"]["exact_dup_groups"] == 1
        assert len(result["exact_duplicates"][0]["occurrences"]) == 2
        files = {o["file"] for o in result["exact_duplicates"][0]["occurrences"]}
        assert files == {"train.jsonl", "eval.jsonl"}

    def test_query_duplicates_different_responses(self, tmp_path):
        from dit.core.dedup import detect_duplicates

        store = ObjectStore(tmp_path / "objects")
        c = _make_commit(store, {"train.jsonl": [
            _conv("same query", "response A"),
            _conv("same query", "response B"),
            _conv("different query", "response C"),
        ]})

        result = detect_duplicates(store, c)
        assert result["summary"]["severity"] == "info"
        assert result["summary"]["exact_dup_groups"] == 0
        assert result["summary"]["query_dup_groups"] == 1
        group = result["query_duplicates"][0]
        assert group["count"] == 2
        assert len(group["row_hashes"]) == 2

    def test_both_exact_and_query_duplicates(self, tmp_path):
        from dit.core.dedup import detect_duplicates

        store = ObjectStore(tmp_path / "objects")
        exact_row = _conv("q1", "a1")
        c = _make_commit(store, {"train.jsonl": [
            exact_row,
            exact_row,
            _conv("q2", "response X"),
            _conv("q2", "response Y"),
        ]})

        result = detect_duplicates(store, c)
        assert result["summary"]["severity"] == "warning"
        assert result["summary"]["exact_dup_groups"] == 1
        assert result["summary"]["query_dup_groups"] == 1

    def test_path_prefix_filter(self, tmp_path):
        from dit.core.dedup import detect_duplicates

        store = ObjectStore(tmp_path / "objects")
        row = _conv("q1", "a1")
        c = _make_commit(store, {
            "train.jsonl": [row],
            "eval.jsonl": [row],
        })

        result = detect_duplicates(store, c, path_prefix="train")
        assert result["summary"]["total_files"] == 1
        assert result["summary"]["exact_dup_groups"] == 0

    def test_commit_not_found(self, tmp_path):
        import pytest
        from dit.core.dedup import detect_duplicates

        store = ObjectStore(tmp_path / "objects")
        with pytest.raises(FileNotFoundError):
            detect_duplicates(store, "0" * 64)

    def test_content_preview_in_occurrences(self, tmp_path):
        from dit.core.dedup import detect_duplicates

        store = ObjectStore(tmp_path / "objects")
        row = _conv("q1", "a1")
        c = _make_commit(store, {"train.jsonl": [row, row]})

        result = detect_duplicates(store, c)
        occ = result["exact_duplicates"][0]["occurrences"][0]
        assert "content_preview" in occ
        assert len(occ["content_preview"]) <= 63

    def test_summary_counts(self, tmp_path):
        from dit.core.dedup import detect_duplicates

        store = ObjectStore(tmp_path / "objects")
        c = _make_commit(store, {
            "a.jsonl": [_conv("q1", "a1"), _conv("q2", "a2")],
            "b.jsonl": [_conv("q3", "a3")],
        })

        result = detect_duplicates(store, c)
        assert result["summary"]["total_rows"] == 3
        assert result["summary"]["total_files"] == 2

    def test_no_query_fingerprint_rows(self, tmp_path):
        from dit.core.dedup import detect_duplicates

        store = ObjectStore(tmp_path / "objects")
        row_no_qfp = {"data": "no messages field"}
        c = _make_commit(store, {"data.jsonl": [row_no_qfp, row_no_qfp]})

        result = detect_duplicates(store, c)
        assert result["summary"]["exact_dup_groups"] == 1
        assert result["summary"]["query_dup_groups"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/lxs/code/dit && uv run pytest tests/test_dedup.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'dit.core.dedup'`.

- [ ] **Step 3: Implement `src/dit/core/dedup.py`**

```python
"""Duplicate detection across manifest files in a commit."""
from __future__ import annotations

import json

from dit.core.objects import deserialize_commit, deserialize_manifest
from dit.core.store import ObjectStore
from dit.core.tree_walker import flatten_tree


def _content_preview(store: ObjectStore, row_hash: str) -> str:
    data = store.read("rows", row_hash)
    if data is None:
        return ""
    text = data.decode("utf-8", errors="replace")
    if len(text) > 60:
        return text[:60] + "..."
    return text


def detect_duplicates(
    store: ObjectStore,
    commit_hash: str,
    path_prefix: str | None = None,
) -> dict:
    commit_data = store.read("commits", commit_hash)
    if commit_data is None:
        raise FileNotFoundError(f"Commit {commit_hash} not found")

    commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, commit.tree_hash)

    row_hash_index: dict[str, list[dict]] = {}
    qfp_index: dict[str, list[dict]] = {}
    total_rows = 0
    total_files = 0

    for path, (obj_type, obj_hash, _sidecar) in flat.items():
        if obj_type != "manifest":
            continue
        if path_prefix and not path.startswith(path_prefix):
            continue

        total_files += 1
        m_data = store.read("manifests", obj_hash)
        if m_data is None:
            continue
        manifest = deserialize_manifest(m_data)

        for idx, entry in enumerate(manifest.entries):
            total_rows += 1
            occ = {"file": path, "row_index": idx}

            row_hash_index.setdefault(entry.row_hash, []).append(occ)

            if entry.query_fingerprint:
                qfp_occ = {
                    "file": path,
                    "row_index": idx,
                    "row_hash": entry.row_hash,
                }
                qfp_index.setdefault(entry.query_fingerprint, []).append(qfp_occ)

    exact_duplicates = []
    for rh, occs in sorted(row_hash_index.items(), key=lambda x: -len(x[1])):
        if len(occs) <= 1:
            continue
        for o in occs:
            o["content_preview"] = _content_preview(store, rh)
        exact_duplicates.append({
            "row_hash": rh,
            "count": len(occs),
            "occurrences": occs,
        })

    query_duplicates = []
    for qfp, occs in sorted(qfp_index.items(), key=lambda x: -len(x[1])):
        if len(occs) <= 1:
            continue
        distinct_hashes = list(set(o["row_hash"] for o in occs))
        if len(distinct_hashes) <= 1:
            continue
        for o in occs:
            o["content_preview"] = _content_preview(store, o["row_hash"])
        query_duplicates.append({
            "query_fingerprint": qfp,
            "count": len(occs),
            "row_hashes": distinct_hashes,
            "occurrences": occs,
        })

    exact_dup_rows = sum(g["count"] for g in exact_duplicates)
    query_dup_rows = sum(g["count"] for g in query_duplicates)

    if exact_duplicates:
        severity = "warning"
    elif query_duplicates:
        severity = "info"
    else:
        severity = "clean"

    return {
        "commit_hash": commit_hash,
        "exact_duplicates": exact_duplicates,
        "query_duplicates": query_duplicates,
        "summary": {
            "total_rows": total_rows,
            "total_files": total_files,
            "exact_dup_groups": len(exact_duplicates),
            "exact_dup_rows": exact_dup_rows,
            "query_dup_groups": len(query_duplicates),
            "query_dup_rows": query_dup_rows,
            "severity": severity,
        },
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/lxs/code/dit && uv run pytest tests/test_dedup.py -v`

Expected: All 10 tests PASS.

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/lxs/code/dit && uv run pytest tests/ -x -q`

- [ ] **Step 6: Commit**

```bash
cd /Users/lxs/code/dit
git add src/dit/core/dedup.py tests/test_dedup.py
git commit -m "feat: add core dedup module for duplicate detection

Detects exact duplicates (same row_hash) and query duplicates
(same query_fingerprint, different row_hash) across all manifest
files. Detection only — never modifies data."
```

---

### Task 2: CLI `dit dedup` Command

**Files:**
- Modify: `src/dit/cli/main.py`
- Test: `tests/test_cli_dedup.py`

- [ ] **Step 1: Write tests**

Create `tests/test_cli_dedup.py`:

```python
"""Tests for dit dedup CLI command."""
import json
import os
import time
from pathlib import Path

from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.objects import (
    Commit, Manifest, ManifestEntry, Tree, TreeEntry,
    serialize_manifest, serialize_tree, serialize_commit,
)
from dit.core.refs import RefStore
from dit.core.store import ObjectStore

runner = CliRunner()


def _setup_repo(tmp_path: Path) -> tuple[Path, ObjectStore, RefStore]:
    dot = tmp_path / ".dit"
    dot.mkdir()
    (dot / "objects").mkdir()
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)
    refs.init()
    return dot, store, refs


def _write_row(store: ObjectStore, content: dict) -> str:
    data = json.dumps(content, separators=(",", ":"), sort_keys=True).encode()
    return store.write("rows", data)


def _conv(user_msg: str, asst_msg: str) -> dict:
    return {"messages": [{"role": "user", "content": user_msg}, {"role": "assistant", "content": asst_msg}]}


def _make_commit(store: ObjectStore, files: dict[str, list[dict]], parent_hashes=None) -> str:
    from dit.core.hash import row_hash as compute_row_hash, query_fingerprint as compute_qfp

    tree_entries = []
    for fname, rows in files.items():
        entries = []
        for row in rows:
            rh = compute_row_hash(row)
            _write_row(store, row)
            qfp = compute_qfp(row) if "messages" in row else None
            entries.append(ManifestEntry(row_hash=rh, query_fingerprint=qfp))
        manifest = Manifest(entries=entries)
        m_hash = store.write("manifests", serialize_manifest(manifest))
        tree_entries.append(TreeEntry(name=fname, obj_type="manifest", obj_hash=m_hash))
    tree = Tree(entries=tree_entries)
    t_hash = store.write("trees", serialize_tree(tree))
    c = Commit(tree_hash=t_hash, parent_hashes=parent_hashes or [], author="alice", message="test", timestamp=int(time.time()))
    return store.write("commits", serialize_commit(c))


class TestDedupCommand:
    def test_clean_table(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, store, refs = _setup_repo(tmp_path)
        c = _make_commit(store, {"train.jsonl": [_conv("q1", "a1"), _conv("q2", "a2")]})
        refs.set_branch("main", c)

        result = runner.invoke(app, ["dedup"])
        assert result.exit_code == 0
        assert "No duplicates" in result.output or "clean" in result.output.lower()

    def test_exact_dup_warning_exit_code(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, store, refs = _setup_repo(tmp_path)
        row = _conv("q1", "a1")
        c = _make_commit(store, {"train.jsonl": [row, row]})
        refs.set_branch("main", c)

        result = runner.invoke(app, ["dedup"])
        assert result.exit_code == 1
        assert "EXACT" in result.output or "exact" in result.output.lower() or "WARNING" in result.output

    def test_query_dup_info_exit_code_zero(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, store, refs = _setup_repo(tmp_path)
        c = _make_commit(store, {"train.jsonl": [
            _conv("same q", "resp A"),
            _conv("same q", "resp B"),
        ]})
        refs.set_branch("main", c)

        result = runner.invoke(app, ["dedup"])
        assert result.exit_code == 0
        assert "QUERY" in result.output or "query" in result.output.lower()

    def test_json_format(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, store, refs = _setup_repo(tmp_path)
        row = _conv("q1", "a1")
        c = _make_commit(store, {"train.jsonl": [row, row]})
        refs.set_branch("main", c)

        result = runner.invoke(app, ["dedup", "--format", "json"])
        assert result.exit_code == 1
        data = json.loads(result.output)
        assert "exact_duplicates" in data
        assert "query_duplicates" in data
        assert "summary" in data

    def test_ref_option(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, store, refs = _setup_repo(tmp_path)
        c = _make_commit(store, {"train.jsonl": [_conv("q1", "a1")]})
        refs.set_branch("dev", c)

        result = runner.invoke(app, ["dedup", "--ref", "dev"])
        assert result.exit_code == 0

    def test_exact_only_flag(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, store, refs = _setup_repo(tmp_path)
        row = _conv("q1", "a1")
        c = _make_commit(store, {"train.jsonl": [
            row, row,
            _conv("q2", "resp A"),
            _conv("q2", "resp B"),
        ]})
        refs.set_branch("main", c)

        result = runner.invoke(app, ["dedup", "--exact-only", "--format", "json"])
        data = json.loads(result.output)
        assert len(data["exact_duplicates"]) >= 1
        assert len(data["query_duplicates"]) == 0

    def test_query_only_flag(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, store, refs = _setup_repo(tmp_path)
        row = _conv("q1", "a1")
        c = _make_commit(store, {"train.jsonl": [
            row, row,
            _conv("q2", "resp A"),
            _conv("q2", "resp B"),
        ]})
        refs.set_branch("main", c)

        result = runner.invoke(app, ["dedup", "--query-only", "--format", "json"])
        data = json.loads(result.output)
        assert len(data["exact_duplicates"]) == 0
        assert len(data["query_duplicates"]) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/lxs/code/dit && uv run pytest tests/test_cli_dedup.py -v`

- [ ] **Step 3: Add `dedup` command to `main.py`**

Add after the `gc` command in `src/dit/cli/main.py`:

```python
@app.command()
def dedup(
    ref: str = typer.Option("main", "--ref", help="Branch name or commit hash"),
    path: Optional[str] = typer.Option(None, "--path", help="Path prefix filter"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
    exact_only: bool = typer.Option(False, "--exact-only", help="Only show exact duplicates"),
    query_only: bool = typer.Option(False, "--query-only", help="Only show query duplicates"),
):
    """Detect duplicate rows across files."""
    import json as _json
    from dit.core.dedup import detect_duplicates

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs_store = RefStore(dot)

    commit_hash = refs_store.get_branch(ref)
    if commit_hash is None:
        if len(ref) == 64 and all(c in "0123456789abcdef" for c in ref):
            commit_hash = ref
        else:
            typer.echo(f"fatal: ref '{ref}' not found", err=True)
            raise typer.Exit(1)

    try:
        result = detect_duplicates(store, commit_hash, path_prefix=path)
    except FileNotFoundError as exc:
        typer.echo(f"fatal: {exc}", err=True)
        raise typer.Exit(1)

    if exact_only:
        result["query_duplicates"] = []
        result["summary"]["query_dup_groups"] = 0
        result["summary"]["query_dup_rows"] = 0
        if not result["exact_duplicates"]:
            result["summary"]["severity"] = "clean"
    if query_only:
        result["exact_duplicates"] = []
        result["summary"]["exact_dup_groups"] = 0
        result["summary"]["exact_dup_rows"] = 0
        if result["summary"]["severity"] == "warning" and not result["exact_duplicates"]:
            result["summary"]["severity"] = "info" if result["query_duplicates"] else "clean"

    severity = result["summary"]["severity"]
    exit_code = 1 if severity == "warning" else 0

    if format == "json":
        typer.echo(_json.dumps(result, indent=2))
        raise typer.Exit(exit_code)

    s = result["summary"]
    ref_display = f"heads/{ref}" if refs_store.get_branch(ref) else ref
    typer.echo(f"Duplicate detection for {ref_display} (commit {commit_hash[:8]})")
    typer.echo("")

    if severity == "clean":
        typer.echo(f"No duplicates found. {s['total_rows']} rows across {s['total_files']} files.")
        raise typer.Exit(0)

    if result["exact_duplicates"]:
        typer.echo(f"EXACT DUPLICATES ({s['exact_dup_groups']} groups, {s['exact_dup_rows']} rows) — identical content")
        typer.echo("\u2500" * 60)
        for group in result["exact_duplicates"]:
            file_counts: dict[str, int] = {}
            for occ in group["occurrences"]:
                file_counts[occ["file"]] = file_counts.get(occ["file"], 0) + 1
            files_str = ", ".join(f"{f} (\u00d7{c})" for f, c in file_counts.items())
            typer.echo(f"  {group['row_hash'][:8]}    {group['count']:>3}x   {files_str}")
        typer.echo("")

    if result["query_duplicates"]:
        typer.echo(f"QUERY DUPLICATES ({s['query_dup_groups']} groups, {s['query_dup_rows']} rows) — same query, different response")
        typer.echo("\u2500" * 60)
        for group in result["query_duplicates"]:
            file_counts: dict[str, int] = {}
            for occ in group["occurrences"]:
                file_counts[occ["file"]] = file_counts.get(occ["file"], 0) + 1
            files_str = ", ".join(f"{f} (\u00d7{c})" for f, c in file_counts.items())
            typer.echo(f"  {group['query_fingerprint'][:8]}    {len(group['row_hashes'])} variants   {files_str}")
        typer.echo("")

    typer.echo(f"Summary: {s['total_rows']} rows across {s['total_files']} files")
    if s["exact_dup_groups"] > 0:
        typer.echo(f"  Exact duplicates: {s['exact_dup_groups']} groups ({s['exact_dup_rows']} rows) WARNING")
    if s["query_dup_groups"] > 0:
        typer.echo(f"  Query duplicates: {s['query_dup_groups']} groups ({s['query_dup_rows']} rows) INFO")

    raise typer.Exit(exit_code)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/lxs/code/dit && uv run pytest tests/test_cli_dedup.py -v`

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/lxs/code/dit && uv run pytest tests/ -x -q`

- [ ] **Step 6: Commit**

```bash
cd /Users/lxs/code/dit
git add src/dit/cli/main.py tests/test_cli_dedup.py
git commit -m "feat: add dit dedup CLI command

Detects and reports exact and query duplicates. Exit code 1 for
exact duplicates (warning), 0 for clean or info-only."
```

---

### Task 3: Server Dedup Endpoint

**Files:**
- Create: `src/dit/server/routes/dedup_api.py`
- Modify: `src/dit/server/app.py`
- Test: `tests/server/test_routes_dedup.py`

- [ ] **Step 1: Write server tests**

Create `tests/server/test_routes_dedup.py`:

```python
"""Tests for dedup API endpoint."""
import json
import time

import pytest
from httpx import AsyncClient

from dit.core.objects import (
    Commit, Manifest, ManifestEntry, Tree, TreeEntry,
    serialize_manifest, serialize_tree, serialize_commit,
)
from dit.core.store import ObjectStore
from dit.server.models import Ref, Repo


def _write_row(store, content):
    data = json.dumps(content, separators=(",", ":"), sort_keys=True).encode()
    return store.write("rows", data)


def _conv(user_msg, asst_msg):
    return {"messages": [{"role": "user", "content": user_msg}, {"role": "assistant", "content": asst_msg}]}


def _make_commit(store, files, parent_hashes=None):
    from dit.core.hash import row_hash as compute_row_hash, query_fingerprint as compute_qfp

    tree_entries = []
    for fname, rows in files.items():
        entries = []
        for row in rows:
            rh = compute_row_hash(row)
            _write_row(store, row)
            qfp = compute_qfp(row) if "messages" in row else None
            entries.append(ManifestEntry(row_hash=rh, query_fingerprint=qfp))
        manifest = Manifest(entries=entries)
        m_hash = store.write("manifests", serialize_manifest(manifest))
        tree_entries.append(TreeEntry(name=fname, obj_type="manifest", obj_hash=m_hash))
    tree = Tree(entries=tree_entries)
    t_hash = store.write("trees", serialize_tree(tree))
    c = Commit(tree_hash=t_hash, parent_hashes=parent_hashes or [], author="alice", message="test", timestamp=int(time.time()))
    return store.write("commits", serialize_commit(c))


@pytest.mark.asyncio
async def test_dedup_clean(client: AsyncClient, session, tmp_path):
    repo = Repo(name="dedup-clean")
    session.add(repo)
    await session.commit()
    await session.refresh(repo)

    store = ObjectStore(tmp_path / "data" / "repos" / "dedup-clean" / "objects")
    c = _make_commit(store, {"f.jsonl": [_conv("q1", "a1"), _conv("q2", "a2")]})

    resp = await client.get(f"/api/v1/repos/dedup-clean/dedup/{c}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["severity"] == "clean"


@pytest.mark.asyncio
async def test_dedup_exact_duplicates(client: AsyncClient, session, tmp_path):
    repo = Repo(name="dedup-exact")
    session.add(repo)
    await session.commit()
    await session.refresh(repo)

    store = ObjectStore(tmp_path / "data" / "repos" / "dedup-exact" / "objects")
    row = _conv("q1", "a1")
    c = _make_commit(store, {"f.jsonl": [row, row]})

    resp = await client.get(f"/api/v1/repos/dedup-exact/dedup/{c}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["severity"] == "warning"
    assert data["summary"]["exact_dup_groups"] == 1


@pytest.mark.asyncio
async def test_dedup_with_path_filter(client: AsyncClient, session, tmp_path):
    repo = Repo(name="dedup-path")
    session.add(repo)
    await session.commit()
    await session.refresh(repo)

    store = ObjectStore(tmp_path / "data" / "repos" / "dedup-path" / "objects")
    row = _conv("q1", "a1")
    c = _make_commit(store, {"train.jsonl": [row], "eval.jsonl": [row]})

    resp = await client.get(f"/api/v1/repos/dedup-path/dedup/{c}?path=train")
    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["total_files"] == 1
    assert data["summary"]["exact_dup_groups"] == 0


@pytest.mark.asyncio
async def test_dedup_commit_not_found(client: AsyncClient, session, tmp_path):
    repo = Repo(name="dedup-404")
    session.add(repo)
    await session.commit()

    resp = await client.get(f"/api/v1/repos/dedup-404/dedup/{'0'*64}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_dedup_repo_not_found(client: AsyncClient):
    resp = await client.get(f"/api/v1/repos/nonexistent/dedup/{'0'*64}")
    assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/lxs/code/dit && uv run pytest tests/server/test_routes_dedup.py -v`

- [ ] **Step 3: Create `src/dit/server/routes/dedup_api.py`**

```python
"""Dedup API endpoint."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos", tags=["dedup"])


def _store_for_repo(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    from pathlib import Path
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


@router.get("/{repo}/dedup/{commit_hash}")
async def dedup_endpoint(
    repo: str,
    commit_hash: str,
    path: Optional[str] = Query(default=None),
    request: Request = None,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    from dit.core.dedup import detect_duplicates

    await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    try:
        return detect_duplicates(store, commit_hash, path_prefix=path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
```

- [ ] **Step 4: Register in `app.py`**

Add after gc_router:
```python
    from dit.server.routes.dedup_api import router as dedup_router
    application.include_router(dedup_router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/lxs/code/dit && uv run pytest tests/server/test_routes_dedup.py -v`

- [ ] **Step 6: Run full test suite**

Run: `cd /Users/lxs/code/dit && uv run pytest tests/ -x -q`

- [ ] **Step 7: Commit**

```bash
cd /Users/lxs/code/dit
git add src/dit/server/routes/dedup_api.py src/dit/server/app.py tests/server/test_routes_dedup.py
git commit -m "feat: add server dedup API endpoint

GET /api/v1/repos/{repo}/dedup/{commit} with read auth.
Optional path prefix filter."
```

---

### Task 4: Gateway Proxy Route (Go)

**Files:**
- Modify: `~/code/datahub-gateway/modules/dit/client.go`
- Modify: `~/code/datahub-gateway/routers/api/v1/repo/dit.go`
- Modify: `~/code/datahub-gateway/routers/api/v1/api.go`

- [ ] **Step 1: Add `GetDedup` method to `client.go`**

Add after `RunGC`:

```go
func (c *Client) GetDedup(ctx context.Context, repoName, commitHash, pathFilter string) ([]byte, int, error) {
	path := "/api/v1/repos/" + repoName + "/dedup/" + commitHash
	if pathFilter != "" {
		path += "?path=" + url.QueryEscape(pathFilter)
	}
	return c.do(ctx, http.MethodGet, path, nil)
}
```

- [ ] **Step 2: Add handler to `dit.go`**

```go
func DatahubGetDedup(ctx *context.APIContext) {
	proxyToDatahub(ctx, func() ([]byte, int, error) {
		return dit.DefaultClient().GetDedup(
			ctx,
			ctx.Repo.Repository.Name,
			ctx.Params(":commit"),
			ctx.FormString("path"),
		)
	})
}
```

- [ ] **Step 3: Register route in `api.go`**

After the gc route:
```go
					m.Get("/dedup/{commit}", repo.DatahubGetDedup)
```

- [ ] **Step 4: Verify Go build**

Run: `cd /Users/lxs/code/datahub-gateway && go build ./...`

- [ ] **Step 5: Commit**

```bash
cd /Users/lxs/code/datahub-gateway
git add modules/dit/client.go routers/api/v1/repo/dit.go routers/api/v1/api.go
git commit -m "feat: add dedup proxy route to dit gateway

GET /dedup/{commit} proxied to dit-core for duplicate detection."
```
