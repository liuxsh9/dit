# Phase 5E: Persistence Audit (fsck) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `dit fsck` command and server endpoint to verify object store integrity (hash verification + graph verification).

**Architecture:** Core `fsck()` function walks the store and ref graph, reporting issues as `FsckIssue` dataclasses. CLI and server API consume it.

**Tech Stack:** Python 3.12, FastAPI, pyzstd (for decompression verification)

---

### Task 1: Core Fsck Module

**Files:**
- Create: `src/dit/core/fsck.py`
- Test: `tests/test_fsck.py`

- [ ] **Step 1: Write tests**

Create `tests/test_fsck.py`:

```python
"""Tests for dit.core.fsck module."""
import hashlib
import json
import time

import pyzstd
import pytest

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


def _conv(user_msg: str, asst_msg: str) -> dict:
    return {"messages": [{"role": "user", "content": user_msg}, {"role": "assistant", "content": asst_msg}]}


def _make_commit(store: ObjectStore, files: dict[str, list[dict]], parent_hashes=None) -> str:
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


class TestFsck:
    def test_clean_store(self, tmp_path):
        from dit.core.fsck import fsck

        store = ObjectStore(tmp_path / "objects")
        c = _make_commit(store, {"train.jsonl": [_conv("q1", "a1"), _conv("q2", "a2")]})

        result = fsck(store, [c])
        assert result.total_errors == 0
        assert result.total_warnings == 0
        assert result.total_checked > 0
        assert result.checked_objects["commits"] >= 1
        assert result.checked_objects["rows"] >= 2

    def test_hash_mismatch_detected(self, tmp_path):
        from dit.core.fsck import fsck

        store = ObjectStore(tmp_path / "objects")
        c = _make_commit(store, {"train.jsonl": [_conv("q1", "a1")]})

        row_hash = compute_row_hash(_conv("q1", "a1"))
        obj_path = store._object_path("rows", row_hash)
        obj_path.write_bytes(pyzstd.compress(b"corrupted data"))

        result = fsck(store, [c])
        assert result.total_errors >= 1
        error_msgs = [e.message for e in result.errors]
        assert any("hash mismatch" in m for m in error_msgs)

    def test_corrupt_object_detected(self, tmp_path):
        from dit.core.fsck import fsck

        store = ObjectStore(tmp_path / "objects")
        c = _make_commit(store, {"train.jsonl": [_conv("q1", "a1")]})

        row_hash = compute_row_hash(_conv("q1", "a1"))
        obj_path = store._object_path("rows", row_hash)
        obj_path.write_bytes(b"not valid zstd data")

        result = fsck(store, [c])
        assert result.total_errors >= 1
        error_msgs = [e.message for e in result.errors]
        assert any("corrupt" in m.lower() or "decompression" in m.lower() for m in error_msgs)

    def test_missing_object_in_graph(self, tmp_path):
        from dit.core.fsck import fsck

        store = ObjectStore(tmp_path / "objects")
        c = _make_commit(store, {"train.jsonl": [_conv("q1", "a1")]})

        row_hash = compute_row_hash(_conv("q1", "a1"))
        obj_path = store._object_path("rows", row_hash)
        obj_path.unlink()

        result = fsck(store, [c], check_hashes=False)
        assert result.total_errors >= 1
        error_msgs = [e.message for e in result.errors]
        assert any("missing" in m.lower() for m in error_msgs)

    def test_dangling_ref(self, tmp_path):
        from dit.core.fsck import fsck

        store = ObjectStore(tmp_path / "objects")
        fake_hash = "a" * 64

        result = fsck(store, [fake_hash])
        assert result.total_errors >= 1
        error_msgs = [e.message for e in result.errors]
        assert any("dangling" in m.lower() or "missing" in m.lower() for m in error_msgs)

    def test_skip_hash_check(self, tmp_path):
        from dit.core.fsck import fsck

        store = ObjectStore(tmp_path / "objects")
        c = _make_commit(store, {"train.jsonl": [_conv("q1", "a1")]})

        result = fsck(store, [c], check_hashes=False)
        assert result.total_errors == 0

    def test_skip_graph_check(self, tmp_path):
        from dit.core.fsck import fsck

        store = ObjectStore(tmp_path / "objects")
        c = _make_commit(store, {"train.jsonl": [_conv("q1", "a1")]})

        result = fsck(store, [c], check_graph=False)
        assert result.total_errors == 0
        assert result.total_checked > 0

    def test_result_counts_match(self, tmp_path):
        from dit.core.fsck import fsck

        store = ObjectStore(tmp_path / "objects")
        c = _make_commit(store, {
            "a.jsonl": [_conv("q1", "a1")],
            "b.jsonl": [_conv("q2", "a2")],
        })

        result = fsck(store, [c])
        total_from_dict = sum(result.checked_objects.values())
        assert result.total_checked == total_from_dict

    def test_multiple_refs(self, tmp_path):
        from dit.core.fsck import fsck

        store = ObjectStore(tmp_path / "objects")
        c1 = _make_commit(store, {"a.jsonl": [_conv("q1", "a1")]})
        c2 = _make_commit(store, {"b.jsonl": [_conv("q2", "a2")]})

        result = fsck(store, [c1, c2])
        assert result.total_errors == 0
        assert result.checked_objects["commits"] >= 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/lxs/code/dit && uv run pytest tests/test_fsck.py -v`

Expected: FAIL — `ModuleNotFoundError: No module named 'dit.core.fsck'`.

- [ ] **Step 3: Implement `src/dit/core/fsck.py`**

```python
"""Object store integrity verification."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

import pyzstd

from dit.core.objects import (
    deserialize_commit,
    deserialize_manifest,
    deserialize_tree,
)
from dit.core.store import ObjectStore

OBJ_TYPES = ["commits", "trees", "manifests", "rows", "sidecars", "blobs"]


@dataclass(frozen=True)
class FsckIssue:
    severity: str
    obj_type: str
    obj_hash: str
    message: str


@dataclass
class FsckResult:
    checked_objects: dict[str, int] = field(default_factory=lambda: {t: 0 for t in OBJ_TYPES})
    errors: list[FsckIssue] = field(default_factory=list)
    warnings: list[FsckIssue] = field(default_factory=list)
    total_checked: int = 0
    total_errors: int = 0
    total_warnings: int = 0


def _verify_hashes(store: ObjectStore, result: FsckResult) -> None:
    for obj_type in OBJ_TYPES:
        type_dir = store.root / obj_type
        if not type_dir.exists():
            continue
        for shard1 in sorted(type_dir.iterdir()):
            if not shard1.is_dir():
                continue
            for shard2 in sorted(shard1.iterdir()):
                if not shard2.is_dir():
                    continue
                for obj_file in sorted(shard2.iterdir()):
                    if not obj_file.is_file():
                        continue
                    expected_hash = obj_file.name
                    result.checked_objects[obj_type] += 1
                    result.total_checked += 1

                    try:
                        compressed = obj_file.read_bytes()
                        data = pyzstd.decompress(compressed)
                    except Exception:
                        result.errors.append(FsckIssue(
                            severity="error",
                            obj_type=obj_type,
                            obj_hash=expected_hash,
                            message=f"corrupt object: decompression failed",
                        ))
                        result.total_errors += 1
                        continue

                    actual_hash = hashlib.sha256(data).hexdigest()
                    if actual_hash != expected_hash:
                        result.errors.append(FsckIssue(
                            severity="error",
                            obj_type=obj_type,
                            obj_hash=expected_hash,
                            message=f"hash mismatch: expected {expected_hash[:16]}..., got {actual_hash[:16]}...",
                        ))
                        result.total_errors += 1


def _verify_graph(store: ObjectStore, ref_hashes: list[str], result: FsckResult) -> None:
    visited_commits: set[str] = set()

    def _check_commit(commit_hash: str) -> None:
        if commit_hash in visited_commits:
            return
        visited_commits.add(commit_hash)

        data = store.read("commits", commit_hash)
        if data is None:
            result.errors.append(FsckIssue(
                severity="error",
                obj_type="commits",
                obj_hash=commit_hash,
                message=f"missing commit object (dangling reference)",
            ))
            result.total_errors += 1
            return

        commit = deserialize_commit(data)

        for parent in commit.parent_hashes:
            _check_commit(parent)

        _check_tree(commit.tree_hash)

    def _check_tree(tree_hash: str) -> None:
        data = store.read("trees", tree_hash)
        if data is None:
            result.errors.append(FsckIssue(
                severity="error",
                obj_type="trees",
                obj_hash=tree_hash,
                message="missing tree object",
            ))
            result.total_errors += 1
            return

        tree = deserialize_tree(data)
        for entry in tree.entries:
            if entry.obj_type == "tree":
                _check_tree(entry.obj_hash)
            elif entry.obj_type == "manifest":
                _check_manifest(entry.obj_hash)
            elif entry.obj_type == "blob":
                if store.read("blobs", entry.obj_hash) is None:
                    result.errors.append(FsckIssue(
                        severity="error",
                        obj_type="blobs",
                        obj_hash=entry.obj_hash,
                        message="missing blob object",
                    ))
                    result.total_errors += 1

            if hasattr(entry, "sidecar_hash") and entry.sidecar_hash:
                if store.read("sidecars", entry.sidecar_hash) is None:
                    result.errors.append(FsckIssue(
                        severity="error",
                        obj_type="sidecars",
                        obj_hash=entry.sidecar_hash,
                        message="missing sidecar object",
                    ))
                    result.total_errors += 1

    def _check_manifest(manifest_hash: str) -> None:
        data = store.read("manifests", manifest_hash)
        if data is None:
            result.errors.append(FsckIssue(
                severity="error",
                obj_type="manifests",
                obj_hash=manifest_hash,
                message="missing manifest object",
            ))
            result.total_errors += 1
            return

        manifest = deserialize_manifest(data)
        for entry in manifest.entries:
            if store.read("rows", entry.row_hash) is None:
                result.errors.append(FsckIssue(
                    severity="error",
                    obj_type="rows",
                    obj_hash=entry.row_hash,
                    message="missing row object",
                ))
                result.total_errors += 1

    for ref_hash in ref_hashes:
        _check_commit(ref_hash)


def fsck(
    store: ObjectStore,
    ref_hashes: list[str],
    check_hashes: bool = True,
    check_graph: bool = True,
) -> FsckResult:
    result = FsckResult()

    if check_hashes:
        _verify_hashes(store, result)

    if check_graph:
        _verify_graph(store, ref_hashes, result)

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/lxs/code/dit && uv run pytest tests/test_fsck.py -v`

Expected: All 9 tests PASS.

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/lxs/code/dit && uv run pytest tests/ -x -q`

- [ ] **Step 6: Commit**

```bash
cd /Users/lxs/code/dit
git add src/dit/core/fsck.py tests/test_fsck.py
git commit -m "feat: add core fsck module for object store integrity verification

Hash verification detects corrupt or tampered objects.
Graph verification detects missing objects and dangling references."
```

---

### Task 2: CLI `dit fsck` Command

**Files:**
- Modify: `src/dit/cli/main.py`
- Test: `tests/test_cli_fsck.py`

- [ ] **Step 1: Write tests**

Create `tests/test_cli_fsck.py`:

```python
"""Tests for dit fsck CLI command."""
import json
import time
from pathlib import Path

import pyzstd
from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.objects import (
    Commit, Manifest, ManifestEntry, Tree, TreeEntry,
    serialize_manifest, serialize_tree, serialize_commit,
)
from dit.core.refs import RefStore
from dit.core.store import ObjectStore
from dit.core.hash import row_hash as compute_row_hash, query_fingerprint as compute_qfp

runner = CliRunner()


def _setup_repo(tmp_path: Path) -> tuple[Path, ObjectStore, RefStore]:
    dot = tmp_path / ".dit"
    dot.mkdir()
    (dot / "objects").mkdir()
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)
    refs.init()
    return dot, store, refs


def _write_row(store, content):
    data = json.dumps(content, separators=(",", ":"), sort_keys=True).encode()
    return store.write("rows", data)


def _conv(user_msg, asst_msg):
    return {"messages": [{"role": "user", "content": user_msg}, {"role": "assistant", "content": asst_msg}]}


def _make_commit(store, files, parent_hashes=None):
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


class TestFsckCommand:
    def test_clean_repo(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, store, refs = _setup_repo(tmp_path)
        c = _make_commit(store, {"train.jsonl": [_conv("q1", "a1")]})
        refs.set_branch("main", c)

        result = runner.invoke(app, ["fsck"])
        assert result.exit_code == 0
        assert "No issues" in result.output or "0 error" in result.output

    def test_corrupt_object_exit_code(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, store, refs = _setup_repo(tmp_path)
        c = _make_commit(store, {"train.jsonl": [_conv("q1", "a1")]})
        refs.set_branch("main", c)

        row_hash = compute_row_hash(_conv("q1", "a1"))
        obj_path = store._object_path("rows", row_hash)
        obj_path.write_bytes(pyzstd.compress(b"corrupted"))

        result = runner.invoke(app, ["fsck"])
        assert result.exit_code == 1

    def test_json_format(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, store, refs = _setup_repo(tmp_path)
        c = _make_commit(store, {"train.jsonl": [_conv("q1", "a1")]})
        refs.set_branch("main", c)

        result = runner.invoke(app, ["fsck", "--format", "json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "checked_objects" in data
        assert "errors" in data
        assert "warnings" in data
        assert "total_checked" in data

    def test_no_hash_check_flag(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, store, refs = _setup_repo(tmp_path)
        c = _make_commit(store, {"train.jsonl": [_conv("q1", "a1")]})
        refs.set_branch("main", c)

        row_hash = compute_row_hash(_conv("q1", "a1"))
        obj_path = store._object_path("rows", row_hash)
        obj_path.write_bytes(pyzstd.compress(b"corrupted"))

        result = runner.invoke(app, ["fsck", "--no-hash-check"])
        assert result.exit_code == 0

    def test_no_commits_repo(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        dot, store, refs = _setup_repo(tmp_path)

        result = runner.invoke(app, ["fsck"])
        assert result.exit_code == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/lxs/code/dit && uv run pytest tests/test_cli_fsck.py -v`

- [ ] **Step 3: Add `fsck` command to `main.py`**

Append at the END of `src/dit/cli/main.py`:

```python
@app.command()
def fsck(
    no_hash_check: bool = typer.Option(False, "--no-hash-check", help="Skip hash verification"),
    no_graph_check: bool = typer.Option(False, "--no-graph-check", help="Skip graph verification"),
    format: str = typer.Option("table", "--format", help="Output format: table or json"),
):
    """Verify object store integrity."""
    import json as _json
    from dit.core.fsck import fsck as run_fsck

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    ref_hashes = []
    for _name, h in refs.list_branches().items():
        ref_hashes.append(h)
    for _name, h in refs.list_tags().items():
        ref_hashes.append(h)

    result = run_fsck(
        store,
        ref_hashes,
        check_hashes=not no_hash_check,
        check_graph=not no_graph_check,
    )

    if format == "json":
        typer.echo(_json.dumps({
            "checked_objects": result.checked_objects,
            "errors": [{"severity": e.severity, "obj_type": e.obj_type, "obj_hash": e.obj_hash, "message": e.message} for e in result.errors],
            "warnings": [{"severity": w.severity, "obj_type": w.obj_type, "obj_hash": w.obj_hash, "message": w.message} for w in result.warnings],
            "total_checked": result.total_checked,
            "total_errors": result.total_errors,
            "total_warnings": result.total_warnings,
        }, indent=2))
        raise typer.Exit(1 if result.total_errors > 0 else 0)

    typer.echo("Object store integrity check")
    typer.echo("")

    if not no_hash_check:
        typer.echo("Hash verification:")
        for obj_type in ["commits", "trees", "manifests", "rows", "sidecars", "blobs"]:
            count = result.checked_objects.get(obj_type, 0)
            type_errors = [e for e in result.errors if e.obj_type == obj_type and "hash" in e.message.lower() or "corrupt" in e.message.lower()]
            status = f"\u2717 {len(type_errors)} error(s)" if type_errors else "\u2713"
            typer.echo(f"  {obj_type:<14} {count:>4}  {status}")
        typer.echo("")

    if not no_graph_check:
        typer.echo("Graph verification:")
        graph_errors = [e for e in result.errors if "missing" in e.message.lower() or "dangling" in e.message.lower()]
        if graph_errors:
            typer.echo(f"  {len(graph_errors)} missing or dangling reference(s) found")
        else:
            typer.echo(f"  All references valid \u2713")
        typer.echo("")

    if result.total_errors > 0:
        typer.echo(f"ERRORS ({result.total_errors}):")
        for e in result.errors:
            typer.echo(f"  [{e.obj_type}] {e.obj_hash[:16]}...: {e.message}")
        typer.echo("")

    if result.total_warnings > 0:
        typer.echo(f"WARNINGS ({result.total_warnings}):")
        for w in result.warnings:
            typer.echo(f"  [{w.obj_type}] {w.obj_hash[:16]}...: {w.message}")
        typer.echo("")

    if result.total_errors == 0 and result.total_warnings == 0:
        typer.echo(f"\u2713 No issues found. {result.total_checked} objects checked.")
    else:
        typer.echo(f"\u2717 {result.total_errors} error(s), {result.total_warnings} warning(s). {result.total_checked} objects checked.")

    raise typer.Exit(1 if result.total_errors > 0 else 0)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/lxs/code/dit && uv run pytest tests/test_cli_fsck.py -v`

- [ ] **Step 5: Run full test suite**

Run: `cd /Users/lxs/code/dit && uv run pytest tests/ -x -q`

- [ ] **Step 6: Commit**

```bash
cd /Users/lxs/code/dit
git add src/dit/cli/main.py tests/test_cli_fsck.py
git commit -m "feat: add dit fsck CLI command

Verifies object store integrity with hash verification and
graph verification. Exit code 1 if errors found."
```

---

### Task 3: Server Fsck Endpoint

**Files:**
- Create: `src/dit/server/routes/fsck_api.py`
- Modify: `src/dit/server/app.py`
- Test: `tests/server/test_routes_fsck.py`

- [ ] **Step 1: Write tests**

Create `tests/server/test_routes_fsck.py`:

```python
"""Tests for fsck API endpoint."""
import json
import time

import pyzstd
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
async def test_fsck_clean(client: AsyncClient, session, tmp_path):
    repo = Repo(name="fsck-clean")
    session.add(repo)
    await session.commit()
    await session.refresh(repo)

    store = ObjectStore(tmp_path / "data" / "repos" / "fsck-clean" / "objects")
    c = _make_commit(store, {"f.jsonl": [_conv("q1", "a1")]})

    ref = Ref(repo_id=repo.id, name="heads/main", target_hash=c)
    session.add(ref)
    await session.commit()

    resp = await client.post(f"/api/v1/repos/fsck-clean/fsck", json={"check_hashes": True, "check_graph": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_errors"] == 0


@pytest.mark.asyncio
async def test_fsck_with_corruption(client: AsyncClient, session, tmp_path):
    from dit.core.hash import row_hash as compute_row_hash

    repo = Repo(name="fsck-corrupt")
    session.add(repo)
    await session.commit()
    await session.refresh(repo)

    store = ObjectStore(tmp_path / "data" / "repos" / "fsck-corrupt" / "objects")
    c = _make_commit(store, {"f.jsonl": [_conv("q1", "a1")]})

    ref = Ref(repo_id=repo.id, name="heads/main", target_hash=c)
    session.add(ref)
    await session.commit()

    row_hash = compute_row_hash(_conv("q1", "a1"))
    obj_path = store._object_path("rows", row_hash)
    obj_path.write_bytes(pyzstd.compress(b"corrupted"))

    resp = await client.post(f"/api/v1/repos/fsck-corrupt/fsck", json={"check_hashes": True, "check_graph": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_errors"] >= 1


@pytest.mark.asyncio
async def test_fsck_repo_not_found(client: AsyncClient):
    resp = await client.post(f"/api/v1/repos/nonexistent/fsck", json={})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_fsck_defaults(client: AsyncClient, session, tmp_path):
    repo = Repo(name="fsck-defaults")
    session.add(repo)
    await session.commit()
    await session.refresh(repo)

    store = ObjectStore(tmp_path / "data" / "repos" / "fsck-defaults" / "objects")
    c = _make_commit(store, {"f.jsonl": [_conv("q1", "a1")]})

    ref = Ref(repo_id=repo.id, name="heads/main", target_hash=c)
    session.add(ref)
    await session.commit()

    resp = await client.post(f"/api/v1/repos/fsck-defaults/fsck", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert "checked_objects" in data
    assert "total_checked" in data
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/lxs/code/dit && uv run pytest tests/server/test_routes_fsck.py -v`

- [ ] **Step 3: Create `src/dit/server/routes/fsck_api.py`**

```python
"""Fsck API endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import Ref
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos", tags=["fsck"])


def _store_for_repo(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    from pathlib import Path
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


class FsckRequest(BaseModel):
    check_hashes: bool = True
    check_graph: bool = True


@router.post("/{repo}/fsck")
async def fsck_endpoint(
    repo: str,
    body: FsckRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("admin")),
):
    from dit.core.fsck import fsck

    r = await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    result = await session.execute(
        select(Ref.target_hash).where(Ref.repo_id == r.id)
    )
    ref_hashes = [row[0] for row in result.all()]

    fsck_result = fsck(
        store,
        ref_hashes,
        check_hashes=body.check_hashes,
        check_graph=body.check_graph,
    )

    return {
        "checked_objects": fsck_result.checked_objects,
        "errors": [
            {"severity": e.severity, "obj_type": e.obj_type, "obj_hash": e.obj_hash, "message": e.message}
            for e in fsck_result.errors
        ],
        "warnings": [
            {"severity": w.severity, "obj_type": w.obj_type, "obj_hash": w.obj_hash, "message": w.message}
            for w in fsck_result.warnings
        ],
        "total_checked": fsck_result.total_checked,
        "total_errors": fsck_result.total_errors,
        "total_warnings": fsck_result.total_warnings,
    }
```

- [ ] **Step 4: Register in `app.py`**

Add after the dedup_router registration:

```python
    from dit.server.routes.fsck_api import router as fsck_router
    application.include_router(fsck_router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd /Users/lxs/code/dit && uv run pytest tests/server/test_routes_fsck.py -v`

- [ ] **Step 6: Run full test suite**

Run: `cd /Users/lxs/code/dit && uv run pytest tests/ -x -q`

- [ ] **Step 7: Commit**

```bash
cd /Users/lxs/code/dit
git add src/dit/server/routes/fsck_api.py src/dit/server/app.py tests/server/test_routes_fsck.py
git commit -m "feat: add server fsck API endpoint

POST /api/v1/repos/{repo}/fsck with admin auth.
Returns integrity check results."
```
