# Phase 3A: Data Repository Foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend dit-core with blob support, nested trees, new APIs (tree/manifest/log), enhanced diff, atomic CAS, service token auth, and CLI proxy adaptation — the Python foundation for Phase 3 Web UI.

**Architecture:** The dit-core FastAPI server gains six new or fixed capabilities without breaking any existing Phase 0-2 CLI behavior. Atomic CAS fixes a race condition in refs and merge routes. Nested tree support refactors how `dit add`/`dit commit` build Tree objects (subdirectory → sub-Tree) while remaining backward-compatible with existing flat-tree repos. New read-only API endpoints (tree, manifest, log, enhanced diff) expose structured data to the Forgejo proxy layer. Service token middleware threads into the existing `require_permission` dependency chain via a short-circuit check before database token lookup.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 async, typer (CLI), httpx, pyzstd, pytest + pytest-asyncio

**Design Spec:** `docs/superpowers/specs/2026-04-24-phase3-web-ui-design.md` §5

---

## File Structure

```
src/dit/
  core/
    objects.py          # add serialize_blob / deserialize_blob; blob TreeEntry type
    store.py            # unchanged (already generic by obj_type)
    workspace.py        # add find_all_files(); blob write in build_manifest_for_file
    diff.py             # unchanged (manifest-level, unaffected)
    tree_builder.py     # NEW: build_nested_tree(repo_root, staged) → tree_hash
    tree_walker.py      # NEW: flatten_tree(store, tree_hash) → dict[path, (type, hash)]
  server/
    auth.py             # add service_token_or_permission() dependency
    config.py           # add service_token field
    routes/
      refs.py           # fix CAS UPDATE atomicity
      merge.py          # fix CAS UPDATE atomicity
      tree.py           # NEW: GET /v1/repos/{repo}/tree/{commit_hash}/{path}
      manifest_api.py   # NEW: GET /v1/repos/{repo}/manifest/{commit_hash}/{path}
      log.py            # NEW: GET /v1/repos/{repo}/log
      diff_api.py       # NEW: POST /v1/repos/{repo}/diff (enhanced)
    app.py              # register 4 new routers
  cli/
    main.py             # adapt RemoteClient URL + auth; add `dit auth login`
  core/
    remote.py           # new base_url format; Authorization: token <t>

tests/
  test_objects.py           # extend with blob roundtrip
  test_tree_builder.py      # NEW
  test_tree_walker.py       # NEW
  server/
    test_routes_refs.py     # add atomic CAS concurrency test
    test_routes_merge.py    # add concurrent merge conflict test
    test_routes_tree.py     # NEW
    test_routes_manifest.py # NEW
    test_routes_log.py      # NEW
    test_routes_diff_api.py # NEW
    test_auth_service_token.py # NEW
  test_cli_auth.py          # NEW: auth login command
  test_cli_remote_proxy.py  # NEW: proxy URL format
```

---

## Task 1: Atomic CAS Ref Update (refs.py + merge.py)

**Files:**
- `src/dit/server/routes/refs.py`
- `src/dit/server/routes/merge.py`
- `tests/server/test_routes_refs.py`

### Steps

- [ ] **1.1** Add a concurrent-update test to `tests/server/test_routes_refs.py`. This test creates a ref at `"a"*64`, then fires two simultaneous CAS requests updating from `"a"*64` → `"b"*64` and `"a"*64` → `"c"*64` using `asyncio.gather`. Exactly one must succeed (200) and one must conflict (409).

```python
# tests/server/test_routes_refs.py  — append to existing TestRefsRoutes class

import asyncio

async def test_cas_atomic_concurrent(self, client):
    await self._create_repo(client)
    await client.post(
        "/api/v1/repos/test-repo/refs/heads/main",
        json={"old": None, "new": "a" * 64},
    )
    results = await asyncio.gather(
        client.post(
            "/api/v1/repos/test-repo/refs/heads/main",
            json={"old": "a" * 64, "new": "b" * 64},
        ),
        client.post(
            "/api/v1/repos/test-repo/refs/heads/main",
            json={"old": "a" * 64, "new": "c" * 64},
        ),
        return_exceptions=False,
    )
    statuses = sorted([r.status_code for r in results])
    assert statuses == [200, 409], f"Expected one 200 and one 409, got {statuses}"
```

- [ ] **1.2** Run the test to confirm it is currently flaky or passes for the wrong reason (the SELECT→UPDATE pattern may not race under aiosqlite in tests, but the logic is non-atomic):

```bash
uv run pytest tests/server/test_routes_refs.py::TestRefsRoutes::test_cas_atomic_concurrent -v
```

- [ ] **1.3** Rewrite the CAS UPDATE branch in `src/dit/server/routes/refs.py`. Replace the SELECT → Python compare → ORM-mutate pattern with a single `UPDATE ... WHERE target_hash = :old` and check `rowcount`:

```python
# src/dit/server/routes/refs.py
# Replace the entire `else:` branch (CAS UPDATE) of cas_update_ref with:

    else:
        # Atomic CAS: single UPDATE with WHERE clause on current hash
        from sqlalchemy import update as sa_update
        stmt = (
            sa_update(Ref)
            .where(
                Ref.repo_id == r.id,
                Ref.name == ref_name,
                Ref.target_hash == body.old,
            )
            .values(target_hash=body.new)
            .execution_options(synchronize_session=False)
        )
        result = await session.execute(stmt)
        if result.rowcount == 0:
            # Either ref doesn't exist, or target_hash didn't match
            check = await session.execute(
                select(Ref).where(Ref.repo_id == r.id, Ref.name == ref_name)
            )
            if check.scalar_one_or_none() is None:
                raise HTTPException(status_code=404, detail=f"Ref '{ref_name}' not found")
            raise HTTPException(
                status_code=409,
                detail=f"CAS conflict: expected {body.old[:8]}...",
            )
        await session.commit()
        _hooks = await load_webhooks(session, r.id, WebhookEvent.REF_UPDATE)
        asyncio.ensure_future(fire_webhook_payloads(
            hooks=_hooks,
            event=WebhookEvent.REF_UPDATE,
            payload={"repo": repo, "ref": ref_name, "old_hash": body.old, "new_hash": body.new},
        ))
        return {"name": ref_name, "target_hash": body.new}
```

- [ ] **1.4** Rewrite the non-fast-forward CAS update in `src/dit/server/routes/merge.py`. Find the section after `# CAS update target branch` and replace:

```python
# src/dit/server/routes/merge.py
# Replace the block from "# CAS update target branch: SELECT then UPDATE pattern"
# through "await session.commit()" with:

    from sqlalchemy import update as sa_update

    target_ref_name = f"heads/{body.target_branch}"
    stmt = (
        sa_update(Ref)
        .where(
            Ref.repo_id == r.id,
            Ref.name == target_ref_name,
            Ref.target_hash == target_hash,
        )
        .values(target_hash=commit_hash)
        .execution_options(synchronize_session=False)
    )
    result = await session.execute(stmt)
    if result.rowcount == 0:
        raise HTTPException(status_code=409, detail="Target branch was updated concurrently")
    await session.commit()
```

- [ ] **1.5** Run all refs and merge tests to confirm nothing regressed:

```bash
uv run pytest tests/server/test_routes_refs.py tests/server/test_routes_merge.py -v
```

Expected: all previously passing tests pass, new concurrent test passes.

- [ ] **1.6** Commit:

```bash
cd /Users/lxs/code/dit && git add src/dit/server/routes/refs.py src/dit/server/routes/merge.py tests/server/test_routes_refs.py && git commit -m "fix: atomic CAS ref update via single UPDATE WHERE rowcount check"
```

---

## Task 2: Blob Type Support

**Files:**
- `src/dit/core/objects.py`
- `tests/test_objects.py`

### Steps

- [ ] **2.1** Add blob serialize/deserialize tests to `tests/test_objects.py`:

```python
# tests/test_objects.py — append new class

from dit.core.objects import serialize_blob, deserialize_blob

class TestBlob:
    def test_roundtrip_text(self):
        content = b"# README\n\nThis is a data repository.\n"
        data = serialize_blob(content)
        assert deserialize_blob(data) == content

    def test_roundtrip_binary(self):
        content = bytes(range(256))
        data = serialize_blob(content)
        assert deserialize_blob(data) == content

    def test_hash_deterministic(self):
        content = b"hello world"
        data1 = serialize_blob(content)
        data2 = serialize_blob(content)
        assert object_hash(data1) == object_hash(data2)

    def test_different_content_different_hash(self):
        data1 = serialize_blob(b"foo")
        data2 = serialize_blob(b"bar")
        assert object_hash(data1) != object_hash(data2)
```

- [ ] **2.2** Run to confirm failure:

```bash
uv run pytest tests/test_objects.py::TestBlob -v
```

Expected: `ImportError` or `AttributeError` — `serialize_blob` does not exist yet.

- [ ] **2.3** Add `serialize_blob` and `deserialize_blob` to `src/dit/core/objects.py`. Blob storage wraps raw bytes in a minimal JSON envelope so the object store's zstd compression and hash verification work uniformly. Also add `"blob"` as a documented valid `obj_type` in `TreeEntry` docstring:

```python
# src/dit/core/objects.py — append after deserialize_commit()

def serialize_blob(content: bytes) -> bytes:
    """Wrap raw blob content for storage in the object store.

    Uses a length-prefixed envelope so the store can verify integrity.
    The envelope is: 8-byte big-endian length + raw bytes.
    """
    import struct
    return struct.pack(">Q", len(content)) + content


def deserialize_blob(data: bytes) -> bytes:
    """Extract raw blob content from store envelope."""
    import struct
    if len(data) < 8:
        raise ValueError("Blob data too short to contain length prefix")
    (length,) = struct.unpack(">Q", data[:8])
    payload = data[8:]
    if len(payload) != length:
        raise ValueError(f"Blob length mismatch: expected {length}, got {len(payload)}")
    return payload
```

- [ ] **2.4** Run blob tests to confirm they pass:

```bash
uv run pytest tests/test_objects.py::TestBlob -v
```

Expected: 4 passed.

- [ ] **2.5** Update `src/dit/core/workspace.py` to write blob objects for non-`.jsonl` files. Add `find_all_files()` (returns both `.jsonl` and other files) and `build_blob_for_file()`:

```python
# src/dit/core/workspace.py — add after find_jsonl_files()

def find_all_files(root: Path) -> tuple[list[Path], list[Path]]:
    """Return (jsonl_files, blob_files) under root, excluding .dit/.

    jsonl_files: all *.jsonl paths
    blob_files: all other regular files
    """
    jsonl: list[Path] = []
    blobs: list[Path] = []
    for p in sorted(root.rglob("*")):
        if ".dit" in p.parts:
            continue
        if not p.is_file():
            continue
        if p.suffix == ".jsonl":
            jsonl.append(p)
        else:
            blobs.append(p)
    return jsonl, blobs


def build_blob_for_file(path: Path) -> bytes:
    """Read a non-JSONL file and return its raw content for blob storage."""
    return path.read_bytes()
```

- [ ] **2.6** Run full test suite to confirm nothing broke:

```bash
uv run pytest tests/test_objects.py tests/test_workspace.py -v
```

Expected: all pass.

- [ ] **2.7** Commit:

```bash
cd /Users/lxs/code/dit && git add src/dit/core/objects.py src/dit/core/workspace.py tests/test_objects.py && git commit -m "feat: blob type support — serialize_blob/deserialize_blob + find_all_files"
```

---

## Task 3: Nested Tree Builder and Walker

**Files:**
- `src/dit/core/tree_builder.py` (new)
- `src/dit/core/tree_walker.py` (new)
- `tests/test_tree_builder.py` (new)
- `tests/test_tree_walker.py` (new)

### Steps

- [ ] **3.1** Write `tests/test_tree_builder.py`:

```python
# tests/test_tree_builder.py
import pytest
from pathlib import Path

from dit.core.store import ObjectStore
from dit.core.tree_builder import build_nested_tree
from dit.core.objects import deserialize_tree, deserialize_manifest


class TestBuildNestedTree:
    def test_flat_files(self, tmp_path):
        """Single-level files produce a flat root tree."""
        store = ObjectStore(tmp_path / "objects")
        # staged: rel_path -> (obj_type, obj_hash)
        staged = {
            "a.jsonl": ("manifest", "a" * 64),
            "b.jsonl": ("manifest", "b" * 64),
        }
        tree_hash = build_nested_tree(store, staged)
        data = store.read("trees", tree_hash)
        assert data is not None
        tree = deserialize_tree(data)
        names = {e.name for e in tree.entries}
        assert names == {"a.jsonl", "b.jsonl"}
        assert all(e.obj_type == "manifest" for e in tree.entries)

    def test_nested_files(self, tmp_path):
        """Files in subdirectories produce nested trees."""
        store = ObjectStore(tmp_path / "objects")
        staged = {
            "train/sft.jsonl": ("manifest", "a" * 64),
            "eval/bench.jsonl": ("manifest", "b" * 64),
            "README.md": ("blob", "c" * 64),
        }
        tree_hash = build_nested_tree(store, staged)
        data = store.read("trees", tree_hash)
        root = deserialize_tree(data)
        entry_map = {e.name: e for e in root.entries}
        assert "README.md" in entry_map
        assert entry_map["README.md"].obj_type == "blob"
        assert "train" in entry_map
        assert entry_map["train"].obj_type == "tree"
        assert "eval" in entry_map
        assert entry_map["eval"].obj_type == "tree"

        train_data = store.read("trees", entry_map["train"].obj_hash)
        assert train_data is not None
        train_tree = deserialize_tree(train_data)
        assert len(train_tree.entries) == 1
        assert train_tree.entries[0].name == "sft.jsonl"
        assert train_tree.entries[0].obj_type == "manifest"

    def test_deep_nesting(self, tmp_path):
        """Three-level nesting resolves correctly."""
        store = ObjectStore(tmp_path / "objects")
        staged = {
            "a/b/c.jsonl": ("manifest", "d" * 64),
        }
        tree_hash = build_nested_tree(store, staged)
        root = deserialize_tree(store.read("trees", tree_hash))
        assert len(root.entries) == 1
        assert root.entries[0].name == "a"
        assert root.entries[0].obj_type == "tree"

        a_tree = deserialize_tree(store.read("trees", root.entries[0].obj_hash))
        assert a_tree.entries[0].name == "b"
        assert a_tree.entries[0].obj_type == "tree"

        b_tree = deserialize_tree(store.read("trees", a_tree.entries[0].obj_hash))
        assert b_tree.entries[0].name == "c.jsonl"

    def test_deterministic_hash(self, tmp_path):
        """Same staged inputs always produce the same tree hash."""
        store = ObjectStore(tmp_path / "objects")
        staged = {
            "x.jsonl": ("manifest", "e" * 64),
            "sub/y.jsonl": ("manifest", "f" * 64),
        }
        h1 = build_nested_tree(store, staged)
        h2 = build_nested_tree(store, staged)
        assert h1 == h2

    def test_empty_staged(self, tmp_path):
        """Empty staged map produces an empty root tree."""
        store = ObjectStore(tmp_path / "objects")
        tree_hash = build_nested_tree(store, {})
        root = deserialize_tree(store.read("trees", tree_hash))
        assert root.entries == []
```

- [ ] **3.2** Run to confirm failure:

```bash
uv run pytest tests/test_tree_builder.py -v
```

Expected: `ModuleNotFoundError` — `tree_builder` does not exist.

- [ ] **3.3** Create `src/dit/core/tree_builder.py`:

```python
# src/dit/core/tree_builder.py
"""Build nested Tree objects from a flat staged map.

staged: dict mapping rel_path (POSIX slash-separated) to (obj_type, obj_hash).
        obj_type is "manifest" or "blob".
        The path separator is always "/" regardless of OS.

Returns the hash of the root Tree object written into the store.
"""
from __future__ import annotations

from collections import defaultdict

from dit.core.objects import Tree, TreeEntry, serialize_tree
from dit.core.store import ObjectStore


def build_nested_tree(
    store: ObjectStore,
    staged: dict[str, tuple[str, str]],
) -> str:
    """Recursively build nested Tree objects and write them to store.

    Args:
        store: ObjectStore to write tree objects into.
        staged: Flat map of POSIX-relative path → (obj_type, obj_hash).
                obj_type must be "manifest" or "blob".

    Returns:
        SHA-256 hex hash of the root Tree object.
    """
    return _build_subtree(store, staged, prefix="")


def _build_subtree(
    store: ObjectStore,
    staged: dict[str, tuple[str, str]],
    prefix: str,
) -> str:
    """Build a Tree for entries whose paths start with `prefix`.

    prefix="" means root. prefix="subdir/" means the subdir node.
    """
    # Separate direct children (no further slash after prefix) from deeper entries
    direct: dict[str, tuple[str, str]] = {}       # name → (type, hash)
    subdirs: dict[str, dict[str, tuple[str, str]]] = defaultdict(dict)

    prefix_len = len(prefix)
    for path, (obj_type, obj_hash) in staged.items():
        if not path.startswith(prefix):
            continue
        rest = path[prefix_len:]
        if "/" not in rest:
            # Direct child file
            direct[rest] = (obj_type, obj_hash)
        else:
            # Belongs to a subdirectory
            subdir_name, sub_rest = rest.split("/", 1)
            subdirs[subdir_name][prefix + subdir_name + "/" + sub_rest] = (obj_type, obj_hash)

    entries: list[TreeEntry] = []

    # Add direct file entries
    for name, (obj_type, obj_hash) in direct.items():
        entries.append(TreeEntry(name=name, obj_type=obj_type, obj_hash=obj_hash))

    # Recursively build sub-trees
    for subdir_name, sub_staged in subdirs.items():
        sub_tree_hash = _build_subtree(store, sub_staged, prefix=prefix + subdir_name + "/")
        entries.append(TreeEntry(name=subdir_name, obj_type="tree", obj_hash=sub_tree_hash))

    tree = Tree(entries=entries)
    tree_bytes = serialize_tree(tree)
    return store.write("trees", tree_bytes)
```

- [ ] **3.4** Run tree builder tests:

```bash
uv run pytest tests/test_tree_builder.py -v
```

Expected: 5 passed.

- [ ] **3.5** Write `tests/test_tree_walker.py`:

```python
# tests/test_tree_walker.py
import pytest
from dit.core.store import ObjectStore
from dit.core.tree_builder import build_nested_tree
from dit.core.tree_walker import flatten_tree, resolve_path


class TestFlattenTree:
    def test_flat_repo(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        staged = {
            "a.jsonl": ("manifest", "a" * 64),
            "b.jsonl": ("manifest", "b" * 64),
        }
        tree_hash = build_nested_tree(store, staged)
        result = flatten_tree(store, tree_hash)
        assert result == {
            "a.jsonl": ("manifest", "a" * 64),
            "b.jsonl": ("manifest", "b" * 64),
        }

    def test_nested_repo(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        staged = {
            "train/sft.jsonl": ("manifest", "a" * 64),
            "eval/bench.jsonl": ("manifest", "b" * 64),
            "README.md": ("blob", "c" * 64),
        }
        tree_hash = build_nested_tree(store, staged)
        result = flatten_tree(store, tree_hash)
        assert result == staged

    def test_deep_nesting(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        staged = {"a/b/c.jsonl": ("manifest", "d" * 64)}
        tree_hash = build_nested_tree(store, staged)
        result = flatten_tree(store, tree_hash)
        assert result == staged

    def test_empty_tree(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        tree_hash = build_nested_tree(store, {})
        result = flatten_tree(store, tree_hash)
        assert result == {}


class TestResolvePath:
    def test_root_path(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        staged = {
            "train/sft.jsonl": ("manifest", "a" * 64),
            "README.md": ("blob", "c" * 64),
        }
        tree_hash = build_nested_tree(store, staged)
        entries = resolve_path(store, tree_hash, "")
        names = {e["name"] for e in entries}
        assert "train" in names
        assert "README.md" in names

    def test_subdir_path(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        staged = {"train/sft.jsonl": ("manifest", "a" * 64)}
        tree_hash = build_nested_tree(store, staged)
        entries = resolve_path(store, tree_hash, "train")
        assert len(entries) == 1
        assert entries[0]["name"] == "sft.jsonl"
        assert entries[0]["obj_type"] == "manifest"

    def test_missing_path_returns_none(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        staged = {"a.jsonl": ("manifest", "a" * 64)}
        tree_hash = build_nested_tree(store, staged)
        result = resolve_path(store, tree_hash, "nonexistent")
        assert result is None
```

- [ ] **3.6** Run to confirm failure:

```bash
uv run pytest tests/test_tree_walker.py -v
```

- [ ] **3.7** Create `src/dit/core/tree_walker.py`:

```python
# src/dit/core/tree_walker.py
"""Walk nested Tree objects to produce flat path maps or resolve sub-paths."""
from __future__ import annotations

from dit.core.objects import TreeEntry, deserialize_tree
from dit.core.store import ObjectStore


def flatten_tree(
    store: ObjectStore,
    tree_hash: str,
    prefix: str = "",
) -> dict[str, tuple[str, str]]:
    """Recursively expand a Tree into a flat map of path → (obj_type, obj_hash).

    Tree-type entries are descended recursively; manifest and blob entries are
    included as leaves with their full relative path.

    Args:
        store: ObjectStore to read tree objects from.
        tree_hash: SHA-256 hash of the root Tree object.
        prefix: Internal use — path prefix accumulated during recursion.

    Returns:
        Dict mapping POSIX-style relative path to (obj_type, obj_hash).
        obj_type will be "manifest" or "blob" (never "tree").
    """
    data = store.read("trees", tree_hash)
    if data is None:
        return {}
    tree = deserialize_tree(data)
    result: dict[str, tuple[str, str]] = {}
    for entry in tree.entries:
        full_path = f"{prefix}{entry.name}" if not prefix else f"{prefix}/{entry.name}"
        if entry.obj_type == "tree":
            result.update(flatten_tree(store, entry.obj_hash, prefix=full_path))
        else:
            result[full_path] = (entry.obj_type, entry.obj_hash)
    return result


def resolve_path(
    store: ObjectStore,
    tree_hash: str,
    path: str,
) -> list[dict] | None:
    """Navigate a nested tree to the given path and return its directory listing.

    Args:
        store: ObjectStore to read tree objects from.
        tree_hash: SHA-256 hash of the root Tree object.
        path: POSIX-style path relative to repo root, e.g. "" for root,
              "subdir" for one level, "a/b" for two levels.

    Returns:
        List of entry dicts with keys: name, obj_type, obj_hash.
        Returns None if the path does not exist or points to a non-tree entry.
    """
    data = store.read("trees", tree_hash)
    if data is None:
        return None

    if path == "" or path == ".":
        tree = deserialize_tree(data)
        return [
            {"name": e.name, "obj_type": e.obj_type, "obj_hash": e.obj_hash}
            for e in tree.entries
        ]

    parts = path.strip("/").split("/")
    current_hash = tree_hash
    for i, part in enumerate(parts):
        node_data = store.read("trees", current_hash)
        if node_data is None:
            return None
        node = deserialize_tree(node_data)
        found: TreeEntry | None = None
        for entry in node.entries:
            if entry.name == part:
                found = entry
                break
        if found is None:
            return None
        if i == len(parts) - 1:
            # Last segment — if it's a tree, return its listing; otherwise None
            if found.obj_type != "tree":
                return None
            leaf_data = store.read("trees", found.obj_hash)
            if leaf_data is None:
                return None
            leaf = deserialize_tree(leaf_data)
            return [
                {"name": e.name, "obj_type": e.obj_type, "obj_hash": e.obj_hash}
                for e in leaf.entries
            ]
        else:
            if found.obj_type != "tree":
                return None
            current_hash = found.obj_hash
    return None
```

- [ ] **3.8** Run walker tests:

```bash
uv run pytest tests/test_tree_walker.py -v
```

Expected: 7 passed.

- [ ] **3.9** Commit:

```bash
cd /Users/lxs/code/dit && git add src/dit/core/tree_builder.py src/dit/core/tree_walker.py tests/test_tree_builder.py tests/test_tree_walker.py && git commit -m "feat: nested tree builder and walker — build_nested_tree, flatten_tree, resolve_path"
```

---

## Task 4: Update dit add/commit to Use Nested Trees

**Files:**
- `src/dit/cli/main.py`
- `tests/test_cli.py`

### Steps

- [ ] **4.1** Add a nested-tree commit test to `tests/test_cli.py`. This test creates files in subdirectories, stages them, commits, and verifies the on-disk tree objects are nested (not flat):

```python
# tests/test_cli.py — append new class

from dit.core.store import ObjectStore
from dit.core.objects import deserialize_commit, deserialize_tree
from dit.core.refs import RefStore

class TestNestedTreeCommit:
    def test_add_and_commit_nested(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from typer.testing import CliRunner
        from dit.cli.main import app
        runner = CliRunner()

        # init
        runner.invoke(app, ["init"])

        # Create nested structure
        (tmp_path / "train").mkdir()
        (tmp_path / "eval").mkdir()
        (tmp_path / "train" / "sft.jsonl").write_text(
            '{"messages": [{"role": "user", "content": "hi"}]}\n'
        )
        (tmp_path / "eval" / "bench.jsonl").write_text(
            '{"messages": [{"role": "user", "content": "test"}]}\n'
        )

        result = runner.invoke(app, ["add", "."])
        assert result.exit_code == 0

        result = runner.invoke(app, ["commit", "-m", "nested commit"])
        assert result.exit_code == 0

        dot = tmp_path / ".dit"
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)
        commit_hash = refs.resolve_head()
        assert commit_hash is not None

        commit_data = store.read("commits", commit_hash)
        commit = deserialize_commit(commit_data)
        root_tree = deserialize_tree(store.read("trees", commit.tree_hash))

        entry_names = {e.name for e in root_tree.entries}
        # Should have "train" and "eval" as tree entries, not flat paths
        assert "train" in entry_names, f"Expected 'train' in root tree, got {entry_names}"
        assert "eval" in entry_names, f"Expected 'eval' in root tree, got {entry_names}"
        assert "train/sft.jsonl" not in entry_names, "Root tree must not have flat slash paths"

        train_entry = next(e for e in root_tree.entries if e.name == "train")
        assert train_entry.obj_type == "tree"

    def test_blob_files_staged(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from typer.testing import CliRunner
        from dit.cli.main import app
        runner = CliRunner()

        runner.invoke(app, ["init"])
        (tmp_path / "README.md").write_text("# My Dataset\n")
        (tmp_path / "data.jsonl").write_text(
            '{"messages": [{"role": "user", "content": "q"}]}\n'
        )

        result = runner.invoke(app, ["add", "."])
        assert result.exit_code == 0

        result = runner.invoke(app, ["commit", "-m", "with readme"])
        assert result.exit_code == 0

        dot = tmp_path / ".dit"
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)
        commit_hash = refs.resolve_head()
        commit = deserialize_commit(store.read("commits", commit_hash))
        root_tree = deserialize_tree(store.read("trees", commit.tree_hash))
        entry_map = {e.name: e for e in root_tree.entries}

        assert "README.md" in entry_map
        assert entry_map["README.md"].obj_type == "blob"
        assert "data.jsonl" in entry_map
        assert entry_map["data.jsonl"].obj_type == "manifest"
```

- [ ] **4.2** Run to confirm failure:

```bash
uv run pytest tests/test_cli.py::TestNestedTreeCommit -v
```

- [ ] **4.3** Update the `add` command in `src/dit/cli/main.py` to handle blob files. Replace the `add` command body:

```python
# src/dit/cli/main.py — replace the add() command

@app.command()
def add(paths: list[str] = typer.Argument(..., help="Files or directories to stage")):
    """Stage JSONL and other files for the next commit."""
    from dit.core.workspace import find_all_files, build_blob_for_file
    from dit.core.objects import serialize_blob

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    index = StagingIndex(dot / "index")

    for path_str in paths:
        target = Path(path_str).resolve()
        if path_str == ".":
            jsonl_files, blob_files = find_all_files(repo_root)
        elif target.is_dir():
            jsonl_files, blob_files = find_all_files(target)
        elif target.is_file() and target.suffix == ".jsonl":
            jsonl_files, blob_files = [target], []
        elif target.is_file():
            jsonl_files, blob_files = [], [target]
        else:
            typer.echo(f"fatal: pathspec '{path_str}' did not match any files", err=True)
            raise typer.Exit(1)

        for fp in jsonl_files:
            manifest, row_data = build_manifest_for_file(fp)
            for rh, data in row_data.items():
                store.write("rows", data)
            manifest_bytes = serialize_manifest(manifest)
            manifest_hash = store.write("manifests", manifest_bytes)
            rel = str(fp.relative_to(repo_root))
            index.stage(rel, manifest_hash, obj_type="manifest")
            typer.echo(f"  staged {rel} ({len(manifest.entries)} rows)")

        for fp in blob_files:
            content = build_blob_for_file(fp)
            blob_bytes = serialize_blob(content)
            blob_hash = store.write("blobs", blob_bytes)
            rel = str(fp.relative_to(repo_root))
            index.stage(rel, blob_hash, obj_type="blob")
            typer.echo(f"  staged {rel} (blob)")
```

- [ ] **4.4** The `StagingIndex` must support an `obj_type` parameter. Check `src/dit/core/index.py` and update it:

```bash
cat /Users/lxs/code/dit/src/dit/core/index.py
```

- [ ] **4.5** Read the current index implementation and update `stage()` to accept and store `obj_type`. The index file is a simple JSON map; extend values to `{"hash": ..., "type": ...}` while reading back old format (plain string hash) for backward compatibility:

```python
# src/dit/core/index.py — full replacement
import json
from pathlib import Path


class StagingIndex:
    def __init__(self, path: Path):
        self.path = path

    def _load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        raw = json.loads(self.path.read_text())
        # Backward compat: old format stored plain string hashes
        result = {}
        for k, v in raw.items():
            if isinstance(v, str):
                result[k] = {"hash": v, "type": "manifest"}
            else:
                result[k] = v
        return result

    def stage(self, rel_path: str, obj_hash: str, obj_type: str = "manifest") -> None:
        data = self._load()
        data[rel_path] = {"hash": obj_hash, "type": obj_type}
        self.path.write_text(json.dumps(data, indent=2))

    def entries(self) -> dict[str, str]:
        """Return rel_path → obj_hash (backward compat, manifests only for commit)."""
        return {k: v["hash"] for k, v in self._load().items()}

    def entries_typed(self) -> dict[str, tuple[str, str]]:
        """Return rel_path → (obj_type, obj_hash)."""
        return {k: (v["type"], v["hash"]) for k, v in self._load().items()}

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
```

- [ ] **4.6** Update the `commit` command in `src/dit/cli/main.py` to use `build_nested_tree`:

```python
# src/dit/cli/main.py — replace the commit() command

@app.command()
def commit(message: str = typer.Option(..., "-m", help="Commit message")):
    """Create a commit from staged files."""
    from dit.core.tree_builder import build_nested_tree

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    index = StagingIndex(dot / "index")
    refs = RefStore(dot)

    staged_typed = index.entries_typed()
    if not staged_typed:
        typer.echo("nothing to commit (staging area is empty)", err=True)
        raise typer.Exit(1)

    # Load existing tree entries from HEAD (for files not in staged)
    head_commit_hash = refs.resolve_head()
    existing_entries: dict[str, tuple[str, str]] = {}
    if head_commit_hash:
        from dit.core.tree_walker import flatten_tree
        commit_data = store.read("commits", head_commit_hash)
        old_commit = deserialize_commit(commit_data)
        existing_entries = flatten_tree(store, old_commit.tree_hash)

    # Merge: staged entries override existing
    merged: dict[str, tuple[str, str]] = {**existing_entries, **staged_typed}

    tree_hash = build_nested_tree(store, merged)

    parent_hashes = [head_commit_hash] if head_commit_hash else []
    c = Commit(
        tree_hash=tree_hash,
        parent_hashes=parent_hashes,
        author=_get_author(),
        message=message,
        timestamp=int(time.time()),
    )
    commit_bytes = serialize_commit(c)
    commit_hash = store.write("commits", commit_bytes)

    branch = refs.current_branch()
    refs.set_branch(branch, commit_hash)
    index.clear()
    typer.echo(f"[{branch} {commit_hash[:8]}] {message}")
```

- [ ] **4.7** Run the nested tree tests:

```bash
uv run pytest tests/test_cli.py::TestNestedTreeCommit -v
```

Expected: 2 passed.

- [ ] **4.8** Run the full CLI test suite to check for regressions:

```bash
uv run pytest tests/test_cli.py tests/test_index.py -v
```

- [ ] **4.9** Commit:

```bash
cd /Users/lxs/code/dit && git add src/dit/core/index.py src/dit/cli/main.py tests/test_cli.py && git commit -m "feat: nested tree commit — dit add/commit build nested Trees, blob support in index"
```

---

## Task 5: Update diff/status to Use Nested Tree Walker

**Files:**
- `src/dit/cli/main.py` (diff, status, _has_uncommitted_changes, _materialize_tree)
- `tests/test_cli.py`

### Steps

- [ ] **5.1** Add regression tests for diff and status with nested trees:

```python
# tests/test_cli.py — append to TestNestedTreeCommit or new class

class TestNestedTreeDiffStatus:
    def _init_nested_repo(self, tmp_path, runner, app):
        runner.invoke(app, ["init"])
        (tmp_path / "train").mkdir()
        (tmp_path / "train" / "sft.jsonl").write_text(
            '{"messages": [{"role": "user", "content": "hello"}]}\n'
        )
        runner.invoke(app, ["add", "."])
        runner.invoke(app, ["commit", "-m", "initial"])

    def test_diff_nested_no_changes(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from typer.testing import CliRunner
        from dit.cli.main import app
        runner = CliRunner()
        self._init_nested_repo(tmp_path, runner, app)
        result = runner.invoke(app, ["diff"])
        assert result.exit_code == 0
        assert "No changes" in result.output

    def test_diff_nested_shows_change(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from typer.testing import CliRunner
        from dit.cli.main import app
        runner = CliRunner()
        self._init_nested_repo(tmp_path, runner, app)
        (tmp_path / "train" / "sft.jsonl").write_text(
            '{"messages": [{"role": "user", "content": "hello"}]}\n'
            '{"messages": [{"role": "user", "content": "world"}]}\n'
        )
        result = runner.invoke(app, ["diff"])
        assert result.exit_code == 0
        assert "train/sft.jsonl" in result.output

    def test_status_nested(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from typer.testing import CliRunner
        from dit.cli.main import app
        runner = CliRunner()
        self._init_nested_repo(tmp_path, runner, app)
        (tmp_path / "train" / "sft.jsonl").write_text(
            '{"messages": [{"role": "user", "content": "modified"}]}\n'
        )
        result = runner.invoke(app, ["status"])
        assert result.exit_code == 0
        assert "train/sft.jsonl" in result.output
```

- [ ] **5.2** Run to confirm current diff/status might fail with nested trees (since they use the old flat tree traversal):

```bash
uv run pytest tests/test_cli.py::TestNestedTreeDiffStatus -v
```

- [ ] **5.3** Update `diff` command in `src/dit/cli/main.py` to use `flatten_tree`:

```python
# src/dit/cli/main.py — replace the diff() command

@app.command()
def diff():
    """Show changes between working directory and HEAD."""
    from dit.core.workspace import find_all_files, build_blob_for_file
    from dit.core.tree_walker import flatten_tree
    from dit.core.objects import serialize_blob

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)

    # Current working directory state (JSONL files only for row-level diff)
    current_files: dict[str, Manifest] = {}
    for fp in find_jsonl_files(repo_root):
        rel = str(fp.relative_to(repo_root))
        manifest, _ = build_manifest_for_file(fp)
        current_files[rel] = manifest

    # HEAD state
    head_files: dict[str, Manifest] = {}
    head_hash = refs.resolve_head()
    if head_hash:
        commit_data = store.read("commits", head_hash)
        head_commit = deserialize_commit(commit_data)
        flat = flatten_tree(store, head_commit.tree_hash)
        for path, (obj_type, obj_hash) in flat.items():
            if obj_type == "manifest":
                m_data = store.read("manifests", obj_hash)
                if m_data:
                    head_files[path] = deserialize_manifest(m_data)

    all_files = sorted(set(list(current_files.keys()) + list(head_files.keys())))
    any_changes = False

    for rel in all_files:
        old_m = head_files.get(rel, Manifest(entries=[]))
        new_m = current_files.get(rel, Manifest(entries=[]))
        result = diff_manifests(old_m, new_m)

        if not result.added and not result.removed and not result.refreshed:
            continue

        any_changes = True
        old_count = len(old_m.entries)
        new_count = len(new_m.entries)

        if rel not in head_files:
            typer.echo(f"{rel}: new file ({new_count} rows)")
        elif rel not in current_files:
            typer.echo(f"{rel}: deleted ({old_count} rows)")
        else:
            typer.echo(f"{rel}: {old_count} → {new_count} rows ({result.summary()})")

        if result.refreshed:
            typer.echo(f"  Likely refreshed: {len(result.refreshed)} rows")

    if not any_changes:
        typer.echo("No changes.")
```

- [ ] **5.4** Update `status` command and `_has_uncommitted_changes` helper to use `flatten_tree`:

```python
# src/dit/cli/main.py — replace status() command

@app.command()
def status():
    """Show working directory status."""
    from dit.core.tree_walker import flatten_tree

    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    index = StagingIndex(dot / "index")
    refs = RefStore(dot)

    branch = refs.current_branch() or "HEAD"
    typer.echo(f"On branch {branch}")

    staged = index.entries()
    if staged:
        typer.echo("\nStaged files:")
        for rel in sorted(staged.keys()):
            typer.echo(f"  {rel}")

    head_manifests: dict[str, str] = {}
    head_hash = refs.resolve_head()
    if head_hash:
        commit_data = store.read("commits", head_hash)
        head_commit = deserialize_commit(commit_data)
        flat = flatten_tree(store, head_commit.tree_hash)
        head_manifests = {
            path: obj_hash
            for path, (obj_type, obj_hash) in flat.items()
            if obj_type == "manifest"
        }

    current_files = find_jsonl_files(repo_root)
    current_rels = {str(f.relative_to(repo_root)) for f in current_files}
    head_rels = set(head_manifests.keys())

    modified = []
    new_files = []
    deleted = sorted(head_rels - current_rels)

    for fp in current_files:
        rel = str(fp.relative_to(repo_root))
        if rel not in head_manifests:
            new_files.append(rel)
        else:
            manifest, _ = build_manifest_for_file(fp)
            current_hash = object_hash(serialize_manifest(manifest))
            if current_hash != head_manifests[rel]:
                modified.append(rel)

    has_changes = modified or new_files or deleted
    if not staged and not has_changes:
        typer.echo("\nNothing to commit, working directory clean.")
        return

    if modified or new_files or deleted:
        typer.echo("\nUnstaged changes:")
        for rel in sorted(modified):
            typer.echo(f"  modified: {rel}")
        for rel in sorted(new_files):
            typer.echo(f"  new file: {rel}")
        for rel in deleted:
            typer.echo(f"  deleted:  {rel}")
```

- [ ] **5.5** Update `_has_uncommitted_changes` helper:

```python
# src/dit/cli/main.py — replace _has_uncommitted_changes()

def _has_uncommitted_changes(repo_root: Path, dot: Path, store: ObjectStore, refs: RefStore) -> bool:
    from dit.core.tree_walker import flatten_tree

    head_hash = refs.resolve_head()
    if head_hash is None:
        return len(find_jsonl_files(repo_root)) > 0

    commit_data = store.read("commits", head_hash)
    head_commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, head_commit.tree_hash)
    head_manifests = {
        path: obj_hash
        for path, (obj_type, obj_hash) in flat.items()
        if obj_type == "manifest"
    }

    current_files = find_jsonl_files(repo_root)
    current_rels = {str(f.relative_to(repo_root)) for f in current_files}
    head_rels = set(head_manifests.keys())

    if current_rels != head_rels:
        return True

    for fp in current_files:
        rel = str(fp.relative_to(repo_root))
        if rel in head_manifests:
            manifest, _ = build_manifest_for_file(fp)
            current_hash = object_hash(serialize_manifest(manifest))
            if current_hash != head_manifests[rel]:
                return True

    return False
```

- [ ] **5.6** Update `_materialize_tree` to use `flatten_tree`:

```python
# src/dit/cli/main.py — replace _materialize_tree()

def _materialize_tree(repo_root: Path, store: ObjectStore, tree_hash: str, old_tree_hash: str | None = None):
    """Materialize working directory from tree, optimizing by skipping unchanged files."""
    from dit.core.workspace import materialize_file
    from dit.core.tree_walker import flatten_tree

    new_flat = flatten_tree(store, tree_hash)
    new_files = {path: obj_hash for path, (obj_type, obj_hash) in new_flat.items() if obj_type == "manifest"}

    old_files: dict[str, str] = {}
    if old_tree_hash:
        old_flat = flatten_tree(store, old_tree_hash)
        old_files = {path: obj_hash for path, (obj_type, obj_hash) in old_flat.items() if obj_type == "manifest"}

    for name, mhash in new_files.items():
        if old_files.get(name) != mhash:
            m_data = store.read("manifests", mhash)
            manifest = deserialize_manifest(m_data)
            materialize_file(repo_root, name, manifest, store)

    for name in old_files:
        if name not in new_files:
            file_path = repo_root / name
            if file_path.exists():
                file_path.unlink()
                parent = file_path.parent
                while parent != repo_root and not any(parent.iterdir()):
                    parent.rmdir()
                    parent = parent.parent
```

- [ ] **5.7** Run nested diff/status tests:

```bash
uv run pytest tests/test_cli.py::TestNestedTreeDiffStatus -v
```

Expected: 3 passed.

- [ ] **5.8** Run full CLI test suite:

```bash
uv run pytest tests/ -v --ignore=tests/server -x
```

- [ ] **5.9** Commit:

```bash
cd /Users/lxs/code/dit && git add src/dit/cli/main.py && git commit -m "feat: update diff/status/materialize to use flatten_tree for nested tree support"
```

---

## Task 6: Tree API Endpoint

**Files:**
- `src/dit/server/routes/tree.py` (new)
- `src/dit/server/app.py`
- `tests/server/test_routes_tree.py` (new)

### Steps

- [ ] **6.1** Write `tests/server/test_routes_tree.py`:

```python
# tests/server/test_routes_tree.py
import time
import pytest
from pathlib import Path

from dit.core.store import ObjectStore
from dit.core.objects import (
    Commit, Tree, TreeEntry, Manifest, ManifestEntry,
    serialize_commit, serialize_tree, serialize_manifest,
)
from dit.core.tree_builder import build_nested_tree


async def _setup_repo_with_tree(client, tmp_path):
    resp = await client.post("/api/v1/repos", json={"name": "tree-repo"})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / "tree-repo" / "objects")

    # Build nested staged map
    m1 = Manifest(entries=[ManifestEntry(row_hash="a" * 64, query_fingerprint=None)])
    m1_hash = store.write("manifests", serialize_manifest(m1))
    m2 = Manifest(entries=[ManifestEntry(row_hash="b" * 64, query_fingerprint=None)])
    m2_hash = store.write("manifests", serialize_manifest(m2))

    staged = {
        "train/sft.jsonl": ("manifest", m1_hash),
        "eval/bench.jsonl": ("manifest", m2_hash),
        "README.md": ("blob", "c" * 64),
    }
    tree_hash = build_nested_tree(store, staged)
    commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=[],
        author="tester",
        message="initial",
        timestamp=int(time.time()),
    )
    commit_hash = store.write("commits", serialize_commit(commit))
    return store, commit_hash


class TestTreeRoute:
    async def test_root_tree(self, client, tmp_path):
        store, commit_hash = await _setup_repo_with_tree(client, tmp_path)
        resp = await client.get(f"/api/v1/repos/tree-repo/tree/{commit_hash}/")
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        names = {e["name"] for e in data["entries"]}
        assert "train" in names
        assert "eval" in names
        assert "README.md" in names

    async def test_subtree_path(self, client, tmp_path):
        store, commit_hash = await _setup_repo_with_tree(client, tmp_path)
        resp = await client.get(f"/api/v1/repos/tree-repo/tree/{commit_hash}/train")
        assert resp.status_code == 200
        data = resp.json()
        entries = data["entries"]
        assert len(entries) == 1
        assert entries[0]["name"] == "sft.jsonl"
        assert entries[0]["obj_type"] == "manifest"

    async def test_invalid_commit(self, client, tmp_path):
        await _setup_repo_with_tree(client, tmp_path)
        resp = await client.get(f"/api/v1/repos/tree-repo/tree/{'z' * 64}/")
        assert resp.status_code == 404

    async def test_invalid_path(self, client, tmp_path):
        store, commit_hash = await _setup_repo_with_tree(client, tmp_path)
        resp = await client.get(f"/api/v1/repos/tree-repo/tree/{commit_hash}/nonexistent")
        assert resp.status_code == 404

    async def test_repo_not_found(self, client, tmp_path):
        resp = await client.get(f"/api/v1/repos/no-such-repo/tree/{'a' * 64}/")
        assert resp.status_code == 404
```

- [ ] **6.2** Run to confirm failure:

```bash
uv run pytest tests/server/test_routes_tree.py -v
```

- [ ] **6.3** Create `src/dit/server/routes/tree.py`:

```python
# src/dit/server/routes/tree.py
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos", tags=["tree"])


def _store_for_repo(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


@router.get("/{repo}/tree/{commit_hash}/{path:path}")
async def get_tree(
    repo: str,
    commit_hash: str,
    path: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    """Return the directory listing for a tree path within a commit.

    path="" or path="." returns the root tree.
    path="subdir" returns entries inside subdir.
    Returns 404 if commit or path does not exist.
    """
    await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    from dit.core.objects import deserialize_commit
    commit_data = store.read("commits", commit_hash)
    if commit_data is None:
        raise HTTPException(status_code=404, detail="Commit not found")

    commit = deserialize_commit(commit_data)

    from dit.core.tree_walker import resolve_path
    entries = resolve_path(store, commit.tree_hash, path.strip("/"))
    if entries is None:
        raise HTTPException(status_code=404, detail=f"Path '{path}' not found in tree")

    return {"commit_hash": commit_hash, "path": path.strip("/"), "entries": entries}
```

- [ ] **6.4** Register the router in `src/dit/server/app.py`. Add after the existing merge router:

```python
# src/dit/server/app.py — add inside create_app(), after merge router

    from dit.server.routes.tree import router as tree_router
    application.include_router(tree_router)
```

- [ ] **6.5** Run tree route tests:

```bash
uv run pytest tests/server/test_routes_tree.py -v
```

Expected: 5 passed.

- [ ] **6.6** Commit:

```bash
cd /Users/lxs/code/dit && git add src/dit/server/routes/tree.py src/dit/server/app.py tests/server/test_routes_tree.py && git commit -m "feat: GET /v1/repos/{repo}/tree/{commit}/{path} — nested tree API endpoint"
```

---

## Task 7: Manifest API Endpoint (with pagination)

**Files:**
- `src/dit/server/routes/manifest_api.py` (new)
- `src/dit/server/app.py`
- `tests/server/test_routes_manifest.py` (new)

### Steps

- [ ] **7.1** Write `tests/server/test_routes_manifest.py`:

```python
# tests/server/test_routes_manifest.py
import time
import pytest
from pathlib import Path

from dit.core.store import ObjectStore
from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    serialize_commit, serialize_manifest,
)
from dit.core.tree_builder import build_nested_tree


async def _setup_manifest_repo(client, tmp_path, n_rows: int = 10):
    resp = await client.post("/api/v1/repos", json={"name": "manifest-repo"})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / "manifest-repo" / "objects")

    entries = [
        ManifestEntry(row_hash=f"{i:064x}", query_fingerprint=f"q{i}")
        for i in range(n_rows)
    ]
    manifest = Manifest(entries=entries)
    m_hash = store.write("manifests", serialize_manifest(manifest))

    staged = {"train/data.jsonl": ("manifest", m_hash)}
    tree_hash = build_nested_tree(store, staged)
    commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=[],
        author="tester",
        message="initial",
        timestamp=int(time.time()),
    )
    commit_hash = store.write("commits", serialize_commit(commit))
    return store, commit_hash, m_hash, entries


class TestManifestRoute:
    async def test_full_manifest(self, client, tmp_path):
        store, commit_hash, m_hash, entries = await _setup_manifest_repo(client, tmp_path, n_rows=5)
        resp = await client.get(
            f"/api/v1/repos/manifest-repo/manifest/{commit_hash}/train/data.jsonl"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert len(data["entries"]) == 5
        assert data["offset"] == 0
        assert data["limit"] == 5

    async def test_pagination_offset(self, client, tmp_path):
        store, commit_hash, m_hash, entries = await _setup_manifest_repo(client, tmp_path, n_rows=10)
        resp = await client.get(
            f"/api/v1/repos/manifest-repo/manifest/{commit_hash}/train/data.jsonl"
            "?offset=5&limit=3"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["offset"] == 5
        assert data["limit"] == 3
        assert len(data["entries"]) == 3
        assert data["entries"][0]["row_hash"] == f"{5:064x}"

    async def test_pagination_limit_clamp(self, client, tmp_path):
        store, commit_hash, m_hash, entries = await _setup_manifest_repo(client, tmp_path, n_rows=5)
        resp = await client.get(
            f"/api/v1/repos/manifest-repo/manifest/{commit_hash}/train/data.jsonl"
            "?offset=0&limit=1000"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["entries"]) == 5

    async def test_commit_not_found(self, client, tmp_path):
        await client.post("/api/v1/repos", json={"name": "manifest-repo2"})
        resp = await client.get(
            f"/api/v1/repos/manifest-repo2/manifest/{'z' * 64}/data.jsonl"
        )
        assert resp.status_code == 404

    async def test_path_not_manifest(self, client, tmp_path):
        store, commit_hash, m_hash, entries = await _setup_manifest_repo(client, tmp_path, n_rows=3)
        resp = await client.get(
            f"/api/v1/repos/manifest-repo/manifest/{commit_hash}/train"
        )
        assert resp.status_code == 404
```

- [ ] **7.2** Run to confirm failure:

```bash
uv run pytest tests/server/test_routes_manifest.py -v
```

- [ ] **7.3** Create `src/dit/server/routes/manifest_api.py`:

```python
# src/dit/server/routes/manifest_api.py
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos", tags=["manifest"])

_MAX_LIMIT = 500
_DEFAULT_LIMIT = 50


def _store_for_repo(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


@router.get("/{repo}/manifest/{commit_hash}/{path:path}")
async def get_manifest(
    repo: str,
    commit_hash: str,
    path: str,
    request: Request,
    offset: int = Query(default=0, ge=0, description="Row offset for pagination"),
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT, description="Max rows to return"),
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    """Return paginated manifest entries for a JSONL file at the given commit + path.

    Query params:
        offset: starting row index (0-based)
        limit: max rows to return (1-500, default 50)
    """
    await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    from dit.core.objects import deserialize_commit, deserialize_manifest
    from dit.core.tree_walker import flatten_tree

    commit_data = store.read("commits", commit_hash)
    if commit_data is None:
        raise HTTPException(status_code=404, detail="Commit not found")

    commit = deserialize_commit(commit_data)
    flat = flatten_tree(store, commit.tree_hash)

    clean_path = path.strip("/")
    if clean_path not in flat:
        raise HTTPException(status_code=404, detail=f"Path '{clean_path}' not found")

    obj_type, obj_hash = flat[clean_path]
    if obj_type != "manifest":
        raise HTTPException(
            status_code=404,
            detail=f"Path '{clean_path}' is not a manifest (type={obj_type})",
        )

    manifest_data = store.read("manifests", obj_hash)
    if manifest_data is None:
        raise HTTPException(status_code=404, detail="Manifest object not found in store")

    manifest = deserialize_manifest(manifest_data)
    total = len(manifest.entries)
    page = manifest.entries[offset: offset + limit]

    return {
        "commit_hash": commit_hash,
        "path": clean_path,
        "total": total,
        "offset": offset,
        "limit": len(page),
        "entries": [
            {"row_hash": e.row_hash, "query_fingerprint": e.query_fingerprint}
            for e in page
        ],
    }
```

- [ ] **7.4** Register in `src/dit/server/app.py`:

```python
# src/dit/server/app.py — add after tree router

    from dit.server.routes.manifest_api import router as manifest_router
    application.include_router(manifest_router)
```

- [ ] **7.5** Run manifest tests:

```bash
uv run pytest tests/server/test_routes_manifest.py -v
```

Expected: 5 passed.

- [ ] **7.6** Commit:

```bash
cd /Users/lxs/code/dit && git add src/dit/server/routes/manifest_api.py src/dit/server/app.py tests/server/test_routes_manifest.py && git commit -m "feat: GET /v1/repos/{repo}/manifest/{commit}/{path} — paginated manifest API"
```

---

## Task 8: Log API Endpoint

**Files:**
- `src/dit/server/routes/log.py` (new)
- `src/dit/server/app.py`
- `tests/server/test_routes_log.py` (new)

### Steps

- [ ] **8.1** Write `tests/server/test_routes_log.py`:

```python
# tests/server/test_routes_log.py
import time
import pytest
from pathlib import Path

from dit.core.store import ObjectStore
from dit.core.objects import (
    Commit, Tree,
    serialize_commit, serialize_tree,
)
from dit.core.tree_builder import build_nested_tree


async def _setup_log_repo(client, tmp_path):
    resp = await client.post("/api/v1/repos", json={"name": "log-repo"})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / "log-repo" / "objects")

    # 3-commit linear history
    tree_hash = build_nested_tree(store, {})

    c1 = Commit(tree_hash=tree_hash, parent_hashes=[], author="a", message="first", timestamp=1000)
    h1 = store.write("commits", serialize_commit(c1))

    c2 = Commit(tree_hash=tree_hash, parent_hashes=[h1], author="a", message="second", timestamp=2000)
    h2 = store.write("commits", serialize_commit(c2))

    c3 = Commit(tree_hash=tree_hash, parent_hashes=[h2], author="b", message="third", timestamp=3000)
    h3 = store.write("commits", serialize_commit(c3))

    # Create ref heads/main pointing to h3
    await client.post(
        "/api/v1/repos/log-repo/refs/heads/main",
        json={"old": None, "new": h3},
    )
    return store, h1, h2, h3


class TestLogRoute:
    async def test_default_log(self, client, tmp_path):
        store, h1, h2, h3 = await _setup_log_repo(client, tmp_path)
        resp = await client.get("/api/v1/repos/log-repo/log?ref=heads/main")
        assert resp.status_code == 200
        data = resp.json()
        assert "commits" in data
        assert len(data["commits"]) == 3
        assert data["commits"][0]["commit_hash"] == h3
        assert data["commits"][1]["commit_hash"] == h2
        assert data["commits"][2]["commit_hash"] == h1

    async def test_log_pagination(self, client, tmp_path):
        store, h1, h2, h3 = await _setup_log_repo(client, tmp_path)
        resp = await client.get("/api/v1/repos/log-repo/log?ref=heads/main&limit=2&offset=0")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["commits"]) == 2
        assert data["commits"][0]["commit_hash"] == h3

    async def test_log_offset(self, client, tmp_path):
        store, h1, h2, h3 = await _setup_log_repo(client, tmp_path)
        resp = await client.get("/api/v1/repos/log-repo/log?ref=heads/main&limit=10&offset=2")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["commits"]) == 1
        assert data["commits"][0]["commit_hash"] == h1

    async def test_log_commit_fields(self, client, tmp_path):
        store, h1, h2, h3 = await _setup_log_repo(client, tmp_path)
        resp = await client.get("/api/v1/repos/log-repo/log?ref=heads/main&limit=1")
        assert resp.status_code == 200
        commit = resp.json()["commits"][0]
        assert "commit_hash" in commit
        assert "author" in commit
        assert "message" in commit
        assert "timestamp" in commit
        assert "parent_hashes" in commit

    async def test_log_ref_not_found(self, client, tmp_path):
        await client.post("/api/v1/repos", json={"name": "log-repo2"})
        resp = await client.get("/api/v1/repos/log-repo2/log?ref=heads/nosuchbranch")
        assert resp.status_code == 404

    async def test_log_repo_not_found(self, client, tmp_path):
        resp = await client.get("/api/v1/repos/no-such-repo/log?ref=heads/main")
        assert resp.status_code == 404
```

- [ ] **8.2** Run to confirm failure:

```bash
uv run pytest tests/server/test_routes_log.py -v
```

- [ ] **8.3** Create `src/dit/server/routes/log.py`:

```python
# src/dit/server/routes/log.py
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import Ref
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos", tags=["log"])

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 200


def _store_for_repo(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


@router.get("/{repo}/log")
async def get_log(
    repo: str,
    request: Request,
    ref: str = Query(..., description="Ref name, e.g. 'heads/main' or a commit hash"),
    limit: int = Query(default=_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    """Return paginated commit history starting from the given ref.

    `ref` may be a ref name (e.g. "heads/main") or a bare commit hash.
    Commits are returned newest-first (following parent_hashes[0]).
    """
    r = await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    from dit.core.objects import deserialize_commit

    # Resolve ref to commit hash
    result = await session.execute(
        select(Ref).where(Ref.repo_id == r.id, Ref.name == ref)
    )
    ref_obj = result.scalar_one_or_none()
    if ref_obj is None:
        # Try treating `ref` directly as a commit hash
        if store.read("commits", ref) is None:
            raise HTTPException(status_code=404, detail=f"Ref '{ref}' not found")
        start_hash = ref
    else:
        start_hash = ref_obj.target_hash

    # Walk commit graph up to offset + limit steps, then slice
    commits = []
    visited: set[str] = set()
    queue = [start_hash]

    while queue and len(commits) < offset + limit:
        chash = queue.pop(0)
        if chash in visited:
            continue
        visited.add(chash)
        data = store.read("commits", chash)
        if data is None:
            break
        commit = deserialize_commit(data)
        commits.append({
            "commit_hash": chash,
            "tree_hash": commit.tree_hash,
            "parent_hashes": commit.parent_hashes,
            "author": commit.author,
            "message": commit.message,
            "timestamp": commit.timestamp,
        })
        # Follow first parent only (linear history for log)
        if commit.parent_hashes:
            queue.append(commit.parent_hashes[0])

    page = commits[offset: offset + limit]
    return {
        "ref": ref,
        "total_fetched": len(commits),
        "offset": offset,
        "limit": len(page),
        "commits": page,
    }
```

- [ ] **8.4** Register in `src/dit/server/app.py`:

```python
# src/dit/server/app.py — add after manifest router

    from dit.server.routes.log import router as log_router
    application.include_router(log_router)
```

- [ ] **8.5** Run log tests:

```bash
uv run pytest tests/server/test_routes_log.py -v
```

Expected: 6 passed.

- [ ] **8.6** Commit:

```bash
cd /Users/lxs/code/dit && git add src/dit/server/routes/log.py src/dit/server/app.py tests/server/test_routes_log.py && git commit -m "feat: GET /v1/repos/{repo}/log — paginated commit history API"
```

---

## Task 9: Enhanced Diff API

**Files:**
- `src/dit/server/routes/diff_api.py` (new)
- `src/dit/server/app.py`
- `tests/server/test_routes_diff_api.py` (new)

### Steps

- [ ] **9.1** Write `tests/server/test_routes_diff_api.py`:

```python
# tests/server/test_routes_diff_api.py
import json
import time
import pytest
from pathlib import Path

from dit.core.store import ObjectStore
from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    serialize_commit, serialize_manifest,
)
from dit.core.tree_builder import build_nested_tree
from dit.core.hash import canonical_json, row_hash


async def _setup_diff_repo(client, tmp_path):
    resp = await client.post("/api/v1/repos", json={"name": "diff-repo"})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / "diff-repo" / "objects")

    def make_row(content: str) -> ManifestEntry:
        row = {"messages": [{"role": "user", "content": content}]}
        canon = canonical_json(row)
        rh = row_hash(row)
        store.write("rows", canon)
        return ManifestEntry(row_hash=rh, query_fingerprint=None)

    row_a = make_row("hello")
    row_b = make_row("world")
    row_c = make_row("new")

    m_old = Manifest(entries=[row_a, row_b])
    m_old_hash = store.write("manifests", serialize_manifest(m_old))

    m_new = Manifest(entries=[row_b, row_c])
    m_new_hash = store.write("manifests", serialize_manifest(m_new))

    staged_old = {"data.jsonl": ("manifest", m_old_hash)}
    staged_new = {"data.jsonl": ("manifest", m_new_hash)}

    tree_old_hash = build_nested_tree(store, staged_old)
    tree_new_hash = build_nested_tree(store, staged_new)

    c_old = Commit(tree_hash=tree_old_hash, parent_hashes=[], author="t", message="old", timestamp=1000)
    h_old = store.write("commits", serialize_commit(c_old))

    c_new = Commit(tree_hash=tree_new_hash, parent_hashes=[h_old], author="t", message="new", timestamp=2000)
    h_new = store.write("commits", serialize_commit(c_new))

    return store, h_old, h_new, row_a, row_b, row_c


class TestDiffApi:
    async def test_diff_between_commits(self, client, tmp_path):
        store, h_old, h_new, row_a, row_b, row_c = await _setup_diff_repo(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/diff-repo/diff",
            json={"old_commit": h_old, "new_commit": h_new},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "files" in data
        assert len(data["files"]) == 1
        f = data["files"][0]
        assert f["path"] == "data.jsonl"
        assert f["added"] >= 1
        assert f["removed"] >= 1

    async def test_diff_per_file_rows(self, client, tmp_path):
        store, h_old, h_new, row_a, row_b, row_c = await _setup_diff_repo(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/diff-repo/diff",
            json={
                "old_commit": h_old,
                "new_commit": h_new,
                "include_rows": True,
                "path": "data.jsonl",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "files" in data
        f = data["files"][0]
        assert "added_rows" in f
        assert "removed_rows" in f
        # row_a was removed, row_c was added
        added_hashes = {r["row_hash"] for r in f["added_rows"]}
        removed_hashes = {r["row_hash"] for r in f["removed_rows"]}
        assert row_c.row_hash in added_hashes
        assert row_a.row_hash in removed_hashes

    async def test_diff_commit_not_found(self, client, tmp_path):
        await client.post("/api/v1/repos", json={"name": "diff-repo2"})
        resp = await client.post(
            "/api/v1/repos/diff-repo2/diff",
            json={"old_commit": "z" * 64, "new_commit": "y" * 64},
        )
        assert resp.status_code == 404

    async def test_diff_no_changes(self, client, tmp_path):
        store, h_old, h_new, row_a, row_b, row_c = await _setup_diff_repo(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/diff-repo/diff",
            json={"old_commit": h_old, "new_commit": h_old},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["files"] == []
```

- [ ] **9.2** Run to confirm failure:

```bash
uv run pytest tests/server/test_routes_diff_api.py -v
```

- [ ] **9.3** Create `src/dit/server/routes/diff_api.py`:

```python
# src/dit/server/routes/diff_api.py
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos", tags=["diff"])


def _store_for_repo(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(Path(data_dir) / "repos" / repo_name / "objects")


class DiffRequest(BaseModel):
    old_commit: str
    new_commit: str
    path: Optional[str] = None        # if set, only diff this file
    include_rows: bool = False         # if True, include added_rows/removed_rows content
    offset: int = 0
    limit: int = 100


@router.post("/{repo}/diff")
async def diff_commits(
    repo: str,
    body: DiffRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    """Compute diff between two commits.

    Returns per-file summary (added/removed row counts).
    With include_rows=True and a specific path, also returns row hashes and content.
    """
    await _get_repo(repo, session)
    store = _store_for_repo(request, repo)

    from dit.core.objects import deserialize_commit, deserialize_manifest
    from dit.core.diff import diff_manifests
    from dit.core.tree_walker import flatten_tree

    old_commit_data = store.read("commits", body.old_commit)
    if old_commit_data is None:
        raise HTTPException(status_code=404, detail=f"Old commit '{body.old_commit[:8]}' not found")

    new_commit_data = store.read("commits", body.new_commit)
    if new_commit_data is None:
        raise HTTPException(status_code=404, detail=f"New commit '{body.new_commit[:8]}' not found")

    old_commit = deserialize_commit(old_commit_data)
    new_commit = deserialize_commit(new_commit_data)

    old_flat = flatten_tree(store, old_commit.tree_hash)
    new_flat = flatten_tree(store, new_commit.tree_hash)

    # Filter to manifest entries only; optionally limit to a single path
    def manifest_map(flat: dict) -> dict[str, str]:
        return {
            path: obj_hash
            for path, (obj_type, obj_hash) in flat.items()
            if obj_type == "manifest"
        }

    old_manifests = manifest_map(old_flat)
    new_manifests = manifest_map(new_flat)

    all_paths = sorted(set(old_manifests) | set(new_manifests))
    if body.path:
        clean = body.path.strip("/")
        all_paths = [p for p in all_paths if p == clean]

    file_diffs = []
    for path in all_paths:
        old_m_hash = old_manifests.get(path)
        new_m_hash = new_manifests.get(path)

        if old_m_hash == new_m_hash:
            continue

        if old_m_hash:
            old_m_data = store.read("manifests", old_m_hash)
            from dit.core.objects import Manifest
            old_manifest = deserialize_manifest(old_m_data) if old_m_data else Manifest(entries=[])
        else:
            from dit.core.objects import Manifest
            old_manifest = Manifest(entries=[])

        if new_m_hash:
            new_m_data = store.read("manifests", new_m_hash)
            from dit.core.objects import Manifest
            new_manifest = deserialize_manifest(new_m_data) if new_m_data else Manifest(entries=[])
        else:
            from dit.core.objects import Manifest
            new_manifest = Manifest(entries=[])

        result = diff_manifests(old_manifest, new_manifest)

        file_entry: dict = {
            "path": path,
            "added": len(result.added),
            "removed": len(result.removed),
            "refreshed": len(result.refreshed),
            "old_total": len(old_manifest.entries),
            "new_total": len(new_manifest.entries),
        }

        if body.include_rows:
            def _row_entry(row_hash: str, position: int) -> dict:
                content = None
                raw = store.read("rows", row_hash)
                if raw is not None:
                    import json as _json
                    try:
                        content = _json.loads(raw)
                    except Exception:
                        content = None
                return {"row_hash": row_hash, "position": position, "content": content}

            added_page = result.added[body.offset: body.offset + body.limit]
            removed_page = result.removed[body.offset: body.offset + body.limit]

            file_entry["added_rows"] = [
                _row_entry(e.row_hash, i + body.offset)
                for i, e in enumerate(added_page)
            ]
            file_entry["removed_rows"] = [
                _row_entry(e.row_hash, i + body.offset)
                for i, e in enumerate(removed_page)
            ]
            file_entry["refreshed_rows"] = [
                {
                    "old_row_hash": old_rh,
                    "new_row_hash": new_rh,
                    "query_fingerprint": qfp,
                }
                for old_rh, new_rh, qfp in result.refreshed[body.offset: body.offset + body.limit]
            ]

        file_diffs.append(file_entry)

    return {
        "old_commit": body.old_commit,
        "new_commit": body.new_commit,
        "files": file_diffs,
    }
```

- [ ] **9.4** Register in `src/dit/server/app.py`:

```python
# src/dit/server/app.py — add after log router

    from dit.server.routes.diff_api import router as diff_api_router
    application.include_router(diff_api_router)
```

- [ ] **9.5** Run diff API tests:

```bash
uv run pytest tests/server/test_routes_diff_api.py -v
```

Expected: 4 passed.

- [ ] **9.6** Commit:

```bash
cd /Users/lxs/code/dit && git add src/dit/server/routes/diff_api.py src/dit/server/app.py tests/server/test_routes_diff_api.py && git commit -m "feat: POST /v1/repos/{repo}/diff — enhanced diff API with per-file row content"
```

---

## Task 10: Service Token Auth Middleware

**Files:**
- `src/dit/server/auth.py`
- `src/dit/server/config.py`
- `tests/server/test_auth_service_token.py` (new)

### Steps

- [ ] **10.1** Write `tests/server/test_auth_service_token.py`:

```python
# tests/server/test_auth_service_token.py
import hashlib
import pytest
from httpx import AsyncClient, ASGITransport

from dit.server.app import create_app
from dit.server.auth import get_session
from dit.server.config import ServerSettings
from dit.server.database import create_db_engine, create_session_factory
from dit.server.models import Base, Token


SERVICE_TOKEN = "internal-service-secret-xyz"


@pytest.fixture
async def service_token_engine():
    eng = await create_db_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        for table in Base.metadata.tables.values():
            table.schema = None
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    for table in Base.metadata.tables.values():
        table.schema = "dit"
    await eng.dispose()


@pytest.fixture
async def service_client(service_token_engine, tmp_path):
    settings = ServerSettings(
        database_url="sqlite+aiosqlite:///:memory:",
        data_dir=str(tmp_path / "data"),
        service_token=SERVICE_TOKEN,
    )
    app = create_app(settings=settings)
    factory = create_session_factory(service_token_engine)

    async def override_get_session():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session

    # Create a regular admin token for comparison
    async with factory() as s:
        t = Token(
            token_hash=hashlib.sha256(b"regular-token").hexdigest(),
            label="regular",
            permissions="admin",
        )
        s.add(t)
        await s.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestServiceTokenAuth:
    async def test_service_token_grants_admin_access(self, service_client):
        """X-Service-Token bypasses Bearer token check and grants admin permission."""
        resp = await service_client.get(
            "/api/v1/repos",
            headers={"X-Service-Token": SERVICE_TOKEN},
        )
        assert resp.status_code == 200

    async def test_service_token_wrong_secret_rejected(self, service_client):
        resp = await service_client.get(
            "/api/v1/repos",
            headers={"X-Service-Token": "wrong-secret"},
        )
        assert resp.status_code == 401

    async def test_service_token_no_header_falls_through_to_bearer(self, service_client):
        """Without X-Service-Token, regular Bearer auth still works."""
        resp = await service_client.get(
            "/api/v1/repos",
            headers={"Authorization": "Bearer regular-token"},
        )
        assert resp.status_code == 200

    async def test_service_token_no_auth_rejected(self, service_client):
        resp = await service_client.get("/api/v1/repos")
        assert resp.status_code == 401

    async def test_service_token_not_configured_ignores_header(self, tmp_path):
        """When service_token is empty, X-Service-Token header is ignored."""
        from dit.server.database import create_db_engine, create_session_factory
        eng = await create_db_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as conn:
            for table in Base.metadata.tables.values():
                table.schema = None
            await conn.run_sync(Base.metadata.create_all)
        settings = ServerSettings(
            database_url="sqlite+aiosqlite:///:memory:",
            data_dir=str(tmp_path / "data2"),
            service_token="",
        )
        app = create_app(settings=settings)
        factory = create_session_factory(eng)

        async def override():
            async with factory() as s:
                yield s

        app.dependency_overrides[get_session] = override
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(
                "/api/v1/repos",
                headers={"X-Service-Token": "any-value"},
            )
            assert resp.status_code == 401
        for table in Base.metadata.tables.values():
            table.schema = "dit"
        await eng.dispose()
```

- [ ] **10.2** Run to confirm failure:

```bash
uv run pytest tests/server/test_auth_service_token.py -v
```

- [ ] **10.3** Add `service_token` field to `src/dit/server/config.py`:

```python
# src/dit/server/config.py — full replacement
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerSettings(BaseSettings):
    database_url: str = "postgresql+asyncpg://localhost/dit"
    data_dir: str = "/data/dit"
    host: str = "0.0.0.0"
    port: int = 8000
    service_token: str = ""  # Shared secret for Forgejo → dit-core calls

    model_config = SettingsConfigDict(env_prefix="DIT_SERVER_")
```

- [ ] **10.4** Update `src/dit/server/auth.py` to add service token short-circuit. The strategy: add a `verify_token_or_service` function that checks `X-Service-Token` first; if it matches the app's `service_token` setting, return a synthetic admin Token without hitting the database. The existing `require_permission` dependency stays unchanged — it simply calls `verify_token` which now delegates:

```python
# src/dit/server/auth.py — full replacement
import hashlib
import secrets
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.models import Token

api_key_header = APIKeyHeader(name="Authorization", auto_error=False)
service_token_header = APIKeyHeader(name="X-Service-Token", auto_error=False)


async def get_session() -> AsyncSession:
    raise NotImplementedError("Must be overridden via dependency_overrides")


def _synthetic_admin_token() -> Token:
    """Return an in-memory Token with admin permissions for service-token auth."""
    t = Token.__new__(Token)
    t.id = -1
    t.token_hash = ""
    t.label = "service-token"
    t.repo_scope = None
    t.permissions = "admin"
    t.created_at = datetime.now(timezone.utc)
    t.expires_at = None
    return t


async def verify_token(
    request: Request,
    authorization: str | None = Depends(api_key_header),
    x_service_token: str | None = Depends(service_token_header),
    session: AsyncSession = Depends(get_session),
) -> Token:
    # Check service token first (short-circuit — no DB access needed)
    configured_service_token: str = getattr(
        getattr(request.app.state, "settings", None), "service_token", ""
    )
    if x_service_token and configured_service_token:
        if secrets.compare_digest(x_service_token, configured_service_token):
            return _synthetic_admin_token()
        raise HTTPException(status_code=401, detail="Invalid service token")

    # Fall through to Bearer token auth
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing authorization header")

    raw_token = authorization.removeprefix("Bearer ").removeprefix("token ").strip()
    if not raw_token:
        raise HTTPException(status_code=401, detail="Empty token")

    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    result = await session.execute(select(Token).where(Token.token_hash == token_hash))
    token = result.scalar_one_or_none()

    if token is None:
        raise HTTPException(status_code=401, detail="Invalid token")

    if token.expires_at and token.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status_code=403, detail="Token expired")

    return token


def require_permission(required: str):
    """Dependency factory that checks token has required permission level."""
    permission_levels = {"read": 0, "push": 1, "admin": 2}

    async def check(token: Token = Depends(verify_token)) -> Token:
        token_level = permission_levels.get(token.permissions, 0)
        required_level = permission_levels.get(required, 0)
        if token_level < required_level:
            raise HTTPException(status_code=403, detail=f"Requires {required} permission")
        return token

    return check
```

- [ ] **10.5** Run service token tests:

```bash
uv run pytest tests/server/test_auth_service_token.py -v
```

Expected: 5 passed.

- [ ] **10.6** Run full server test suite to check no regressions:

```bash
uv run pytest tests/server/ -v
```

- [ ] **10.7** Commit:

```bash
cd /Users/lxs/code/dit && git add src/dit/server/auth.py src/dit/server/config.py tests/server/test_auth_service_token.py && git commit -m "feat: service token auth — X-Service-Token header bypasses Bearer check with constant-time compare"
```

---

## Task 11: CLI Adaptation — RemoteClient Proxy URL + Forgejo Auth

**Files:**
- `src/dit/core/remote.py`
- `src/dit/cli/main.py`
- `tests/test_cli_auth.py` (new)
- `tests/test_remote.py`

### Steps

- [ ] **11.1** Write `tests/test_cli_auth.py`:

```python
# tests/test_cli_auth.py
import json
from pathlib import Path
from typer.testing import CliRunner
from dit.cli.main import app


class TestAuthLogin:
    def test_auth_login_stores_credentials(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()

        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0

        result = runner.invoke(
            app,
            ["auth", "login", "--url", "http://forgejo:3000", "--token", "mytoken123"],
        )
        assert result.exit_code == 0, result.output
        assert "Logged in" in result.output or "credentials saved" in result.output.lower()

        creds_path = tmp_path / ".dit" / "credentials"
        assert creds_path.exists()
        data = json.loads(creds_path.read_text())
        assert data["url"] == "http://forgejo:3000"
        assert data["token"] == "mytoken123"

    def test_auth_login_updates_existing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        runner = CliRunner()
        runner.invoke(app, ["init"])

        runner.invoke(app, ["auth", "login", "--url", "http://forgejo:3000", "--token", "old"])
        runner.invoke(app, ["auth", "login", "--url", "http://forgejo:3000", "--token", "new"])

        creds_path = tmp_path / ".dit" / "credentials"
        data = json.loads(creds_path.read_text())
        assert data["token"] == "new"
```

- [ ] **11.2** Write `tests/test_cli_remote_proxy.py`:

```python
# tests/test_cli_remote_proxy.py
from dit.core.remote import RemoteClient


class TestRemoteClientProxyURL:
    def test_forgejo_proxy_url_format(self):
        """RemoteClient correctly builds proxy paths for Forgejo."""
        rc = RemoteClient(
            base_url="http://forgejo:3000",
            token="mytoken",
            repo="alice/mydata",
        )
        assert rc._refs_url("heads", "main") == (
            "http://forgejo:3000/api/v1/repos/alice/mydata/dit/refs/heads/main"
        )

    def test_auth_header_is_token_format(self):
        """Authorization header uses 'token <t>' format, not 'Bearer <t>'."""
        rc = RemoteClient(
            base_url="http://forgejo:3000",
            token="abc123",
            repo="alice/repo",
        )
        assert rc.client.headers["Authorization"] == "token abc123"

    def test_objects_url(self):
        rc = RemoteClient(
            base_url="http://forgejo:3000",
            token="t",
            repo="owner/repo",
        )
        assert rc._objects_url("rows", "a" * 64) == (
            f"http://forgejo:3000/api/v1/repos/owner/repo/dit/objects/rows/{'a' * 64}"
        )
```

- [ ] **11.3** Run tests to confirm failure:

```bash
uv run pytest tests/test_cli_auth.py tests/test_cli_remote_proxy.py -v
```

- [ ] **11.4** Update `src/dit/core/remote.py` to use Forgejo proxy URL format and `token` auth header:

```python
# src/dit/core/remote.py — full replacement
from __future__ import annotations

import httpx


class RemoteClient:
    """Synchronous HTTP client for the Dit server API via Forgejo proxy.

    URL format: base_url is the Forgejo root (e.g. http://forgejo:3000).
    repo is the owner/repo path (e.g. "alice/mydata").
    All Dit API calls go through /api/v1/repos/{owner}/{repo}/dit/*.
    Auth uses Forgejo API token format: Authorization: token <token>.
    """

    def __init__(self, base_url: str, token: str = "", repo: str = "") -> None:
        self.base_url = base_url.rstrip("/")
        self.repo = repo  # "owner/repo" or plain "repo" for backward compat
        self.client = httpx.Client(
            headers={"Authorization": f"token {token}"},
        )

    def _dit_prefix(self) -> str:
        return f"{self.base_url}/api/v1/repos/{self.repo}/dit"

    def _refs_url(self, ref_type: str, name: str) -> str:
        return f"{self._dit_prefix()}/refs/{ref_type}/{name}"

    def _objects_url(self, obj_type: str, hash_hex: str) -> str:
        return f"{self._dit_prefix()}/objects/{obj_type}/{hash_hex}"

    def create_repo(self, name: str) -> dict:
        response = self.client.post(
            f"{self.base_url}/api/v1/repos", json={"name": name}
        )
        response.raise_for_status()
        return response.json()

    def list_repos(self) -> list[dict]:
        response = self.client.get(f"{self.base_url}/api/v1/repos")
        response.raise_for_status()
        return response.json()

    def get_ref(self, ref_type: str, name: str) -> str | None:
        response = self.client.get(self._refs_url(ref_type, name))
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()["target_hash"]

    def list_refs(self) -> list[dict]:
        response = self.client.get(f"{self._dit_prefix()}/refs")
        response.raise_for_status()
        return response.json()

    def cas_ref(self, ref_type: str, name: str, old: str | None, new: str) -> bool:
        response = self.client.post(
            self._refs_url(ref_type, name),
            json={"old": old, "new": new},
        )
        if response.status_code == 409:
            return False
        response.raise_for_status()
        return True

    def upload_object(self, obj_type: str, hash_hex: str, data: bytes) -> None:
        response = self.client.post(
            self._objects_url(obj_type, hash_hex),
            content=data,
        )
        response.raise_for_status()

    def download_object(self, obj_type: str, hash_hex: str) -> bytes | None:
        response = self.client.get(self._objects_url(obj_type, hash_hex))
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.content

    def batch_exists(self, obj_type: str, hashes: list[str]) -> dict[str, bool]:
        response = self.client.post(
            f"{self._dit_prefix()}/objects/batch-exists",
            json={"obj_type": obj_type, "hashes": hashes},
        )
        response.raise_for_status()
        return response.json()["exists"]
```

- [ ] **11.5** Add `dit auth login` command to `src/dit/cli/main.py`. Append to the `auth_app` section (after `auth_set_token`):

```python
# src/dit/cli/main.py — append to auth_app section

@auth_app.command("login")
def auth_login(
    url: str = typer.Option(..., help="Forgejo base URL, e.g. http://forgejo:3000"),
    token: str = typer.Option(..., help="Forgejo API token"),
):
    """Store Forgejo credentials in .dit/credentials."""
    import json as _json

    # Try to find repo root; if not in a repo, store in ~/.dit/credentials
    try:
        repo_root = find_repo_root()
        creds_path = get_dot(repo_root) / "credentials"
    except SystemExit:
        home_dot = Path.home() / ".dit"
        home_dot.mkdir(parents=True, exist_ok=True)
        creds_path = home_dot / "credentials"

    existing: dict = {}
    if creds_path.exists():
        try:
            existing = _json.loads(creds_path.read_text())
        except Exception:
            existing = {}

    existing["url"] = url.rstrip("/")
    existing["token"] = token
    creds_path.write_text(_json.dumps(existing, indent=2))
    typer.echo(f"Credentials saved to {creds_path}")
    typer.echo(f"Logged in to {url}")
```

- [ ] **11.6** Update `_build_remote_client` in `src/dit/cli/main.py` to handle Forgejo-style URLs where the repo includes owner (e.g. `http://forgejo:3000/alice/mydata`). The owner/repo is encoded in the path after the host:

```python
# src/dit/cli/main.py — replace _build_remote_client()

def _build_remote_client(dot: Path, remote_name: str = "origin") -> "RemoteClient":
    from dit.core.config import get_remote
    from dit.core.remote import RemoteClient

    cfg = get_remote(dot, remote_name)
    if cfg is None:
        # Fallback: try credentials file
        creds_path = dot / "credentials"
        if creds_path.exists():
            import json as _json
            try:
                creds = _json.loads(creds_path.read_text())
                base_url = creds.get("url", "")
                token = creds.get("token", "")
                # repo must still come from remote config or be inferred
                typer.echo(f"fatal: remote '{remote_name}' not configured", err=True)
                raise typer.Exit(1)
            except Exception:
                pass
        typer.echo(f"fatal: remote '{remote_name}' not configured", err=True)
        raise typer.Exit(1)

    url: str = cfg["url"]
    token: str = cfg.get("token", "")

    # Forgejo URL format: http://host:port/owner/repo
    # Legacy format: http://host:port/repo  (no owner)
    # base_url = everything up to and including host:port
    # repo = owner/repo or plain repo
    from urllib.parse import urlparse
    parsed = urlparse(url)
    path_parts = parsed.path.strip("/").split("/")

    if len(path_parts) >= 2:
        # Forgejo format: owner/repo
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        repo_name = "/".join(path_parts)
    else:
        # Legacy: plain repo name
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        repo_name = path_parts[0] if path_parts else url

    return RemoteClient(base_url=base_url, token=token, repo=repo_name)
```

- [ ] **11.7** Run auth and remote proxy tests:

```bash
uv run pytest tests/test_cli_auth.py tests/test_cli_remote_proxy.py -v
```

Expected: all pass.

- [ ] **11.8** Run the existing remote tests to check for regressions:

```bash
uv run pytest tests/test_remote.py -v
```

- [ ] **11.9** Run full test suite:

```bash
uv run pytest tests/ -v
```

Expected: all tests pass (new and existing).

- [ ] **11.10** Commit:

```bash
cd /Users/lxs/code/dit && git add src/dit/core/remote.py src/dit/cli/main.py tests/test_cli_auth.py tests/test_cli_remote_proxy.py && git commit -m "feat: CLI adaptation — Forgejo proxy URLs, token auth format, dit auth login command"
```

---

## Final Verification

- [ ] **12.1** Run the complete test suite one final time:

```bash
uv run pytest tests/ -v 2>&1 | tail -20
```

Expected output ends with something like:
```
========== N passed in X.XXs ==========
```
with zero failures.

- [ ] **12.2** Confirm all new files are tracked:

```bash
cd /Users/lxs/code/dit && git status
```

Expected: `nothing to commit, working tree clean` (all new files committed in prior steps).

- [ ] **12.3** Confirm the API surface is registered:

```bash
cd /Users/lxs/code/dit && uv run python -c "
from dit.server.app import create_app
app = create_app()
routes = [(r.methods, r.path) for r in app.routes if hasattr(r, 'methods')]
for m, p in sorted(routes, key=lambda x: x[1]):
    print(m, p)
"
```

Expected output includes:
```
{'GET'} /api/v1/repos/{repo}/tree/{commit_hash}/{path}
{'GET'} /api/v1/repos/{repo}/manifest/{commit_hash}/{path}
{'GET'} /api/v1/repos/{repo}/log
{'POST'} /api/v1/repos/{repo}/diff
```
