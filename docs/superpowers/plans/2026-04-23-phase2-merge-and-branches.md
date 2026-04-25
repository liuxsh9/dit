# Phase 2: Merge & Branches Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement branch management, three-way merge, cherry-pick, tag, server merge API, and webhook skeleton for the dit version control system.

**Architecture:** Phase 2 adds branching/merging on top of Phase 0+1's content-addressed object model. Core merge logic lives in `dit.core.merge` and `dit.core.merge_base`, shared by CLI and server. Conflict state is stored in `.dit/` files (MERGE_HEAD, CHERRY_PICK_HEAD, conflicts.json). Server gets merge-preview/merge APIs and webhook skeleton.

**Tech Stack:** Python 3.12, typer (CLI), FastAPI (server), SQLAlchemy 2.0 async, httpx (webhook POST), pyzstd (object store), pytest + pytest-asyncio (tests)

**Design Spec:** `docs/superpowers/specs/2026-04-23-phase2-merge-and-branches.md`

---

## File Structure

### New Files

| File | Responsibility |
|---|---|
| `src/dit/core/merge_base.py` | BFS merge-base algorithm: `find_merge_base()` |
| `src/dit/core/merge.py` | Three-way merge: `three_way_merge()`, `merge_manifests()` |
| `tests/test_merge_base.py` | merge-base algorithm unit tests |
| `tests/test_merge.py` | Three-way merge unit tests (all 8 file-level cases + row-level) |
| `tests/test_cli_branch.py` | CLI tests for branch, checkout, switch |
| `tests/test_cli_merge.py` | CLI integration tests for merge (ff, 3-way, conflict, continue, abort) |
| `tests/test_cli_cherry_pick.py` | CLI tests for cherry-pick (clean, conflict, continue, abort) |
| `tests/test_cli_tag.py` | CLI tests for tag (create, list, delete) |
| `src/dit/server/routes/merge.py` | Server merge-preview + merge API routes |
| `src/dit/server/routes/webhooks.py` | Webhook CRUD routes |
| `src/dit/server/webhooks.py` | Webhook event trigger logic |
| `src/dit/server/alembic/versions/002_webhooks.py` | Webhook table migration |
| `tests/server/test_routes_merge.py` | Server merge API route tests |
| `tests/server/test_routes_webhooks.py` | Webhook CRUD + trigger tests |

### Modified Files

| File | Changes |
|---|---|
| `src/dit/core/refs.py` | Add `delete_branch()`, tag methods (`get_tag`, `set_tag`, `delete_tag`, `list_tags`), `tags_dir` property |
| `src/dit/cli/main.py` | Add branch, checkout, switch, merge, cherry-pick, tag commands |
| `src/dit/server/models.py` | Add `Webhook` SQLAlchemy model |
| `src/dit/server/app.py` | Register merge_router and webhooks_router |

---

### Task 1: RefStore Extensions — delete_branch and tag methods

**Files:**
- Modify: `src/dit/core/refs.py`
- Test: `tests/test_refs.py` (create new)

- [ ] **Step 1: Write failing tests for delete_branch and tag methods**

```python
# tests/test_refs.py
from pathlib import Path
from dit.core.refs import RefStore


class TestDeleteBranch:
    def test_delete_existing_branch(self, tmp_path: Path):
        dot = tmp_path / ".dit"
        refs = RefStore(dot)
        refs.init()
        refs.set_branch("feature", "a" * 64)
        assert refs.delete_branch("feature") is True
        assert refs.get_branch("feature") is None

    def test_delete_nonexistent_branch(self, tmp_path: Path):
        dot = tmp_path / ".dit"
        refs = RefStore(dot)
        refs.init()
        assert refs.delete_branch("nope") is False

    def test_delete_branch_does_not_affect_others(self, tmp_path: Path):
        dot = tmp_path / ".dit"
        refs = RefStore(dot)
        refs.init()
        refs.set_branch("keep", "a" * 64)
        refs.set_branch("remove", "b" * 64)
        refs.delete_branch("remove")
        assert refs.get_branch("keep") == "a" * 64


class TestTags:
    def test_set_and_get_tag(self, tmp_path: Path):
        dot = tmp_path / ".dit"
        refs = RefStore(dot)
        refs.init()
        refs.set_tag("v1.0", "a" * 64)
        assert refs.get_tag("v1.0") == "a" * 64

    def test_get_nonexistent_tag(self, tmp_path: Path):
        dot = tmp_path / ".dit"
        refs = RefStore(dot)
        refs.init()
        assert refs.get_tag("nope") is None

    def test_delete_tag(self, tmp_path: Path):
        dot = tmp_path / ".dit"
        refs = RefStore(dot)
        refs.init()
        refs.set_tag("v1.0", "a" * 64)
        assert refs.delete_tag("v1.0") is True
        assert refs.get_tag("v1.0") is None

    def test_delete_nonexistent_tag(self, tmp_path: Path):
        dot = tmp_path / ".dit"
        refs = RefStore(dot)
        refs.init()
        assert refs.delete_tag("nope") is False

    def test_list_tags(self, tmp_path: Path):
        dot = tmp_path / ".dit"
        refs = RefStore(dot)
        refs.init()
        refs.set_tag("v1.0", "a" * 64)
        refs.set_tag("v2.0", "b" * 64)
        tags = refs.list_tags()
        assert tags == {"v1.0": "a" * 64, "v2.0": "b" * 64}

    def test_list_tags_empty(self, tmp_path: Path):
        dot = tmp_path / ".dit"
        refs = RefStore(dot)
        refs.init()
        assert refs.list_tags() == {}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_refs.py -v`
Expected: FAIL — `RefStore` has no `delete_branch`, `get_tag`, etc.

- [ ] **Step 3: Implement RefStore extensions**

Add to `src/dit/core/refs.py` — the `__init__` method needs `tags_dir`, and add the new methods:

```python
# In __init__, add after self.refs_dir line:
        self.tags_dir = dot_dit / "refs" / "tags"

# In init(), add after self.refs_dir.mkdir():
        self.tags_dir.mkdir(parents=True, exist_ok=True)

# New methods to add at the end of the class:

    def delete_branch(self, name: str) -> bool:
        path = self.refs_dir / name
        if not path.exists():
            return False
        path.unlink()
        return True

    def get_tag(self, name: str) -> str | None:
        path = self.tags_dir / name
        if not path.exists():
            return None
        return path.read_text().strip()

    def set_tag(self, name: str, commit_hash: str) -> None:
        self.tags_dir.mkdir(parents=True, exist_ok=True)
        path = self.tags_dir / name
        path.write_text(commit_hash + "\n")

    def delete_tag(self, name: str) -> bool:
        path = self.tags_dir / name
        if not path.exists():
            return False
        path.unlink()
        return True

    def list_tags(self) -> dict[str, str]:
        result = {}
        if self.tags_dir.exists():
            for f in self.tags_dir.iterdir():
                if f.is_file():
                    result[f.name] = f.read_text().strip()
        return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_refs.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `uv run pytest --tb=short -q`
Expected: All existing tests still pass

- [ ] **Step 6: Commit**

```bash
git add src/dit/core/refs.py tests/test_refs.py
git commit -m "feat: RefStore delete_branch and tag methods"
```

---

### Task 2: merge-base Algorithm

**Files:**
- Create: `src/dit/core/merge_base.py`
- Test: `tests/test_merge_base.py` (create new)

- [ ] **Step 1: Write failing tests for merge-base**

```python
# tests/test_merge_base.py
"""Tests for BFS merge-base algorithm."""
import time

from dit.core.merge_base import find_merge_base
from dit.core.objects import Commit, Tree, serialize_commit, serialize_tree, object_hash
from dit.core.store import ObjectStore


def _make_commit(store: ObjectStore, parent_hashes: list[str], msg: str = "c") -> str:
    tree = Tree(entries=[])
    tree_bytes = serialize_tree(tree)
    tree_hash = store.write("trees", tree_bytes)
    commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=parent_hashes,
        author="test",
        message=msg,
        timestamp=int(time.time()),
    )
    commit_bytes = serialize_commit(commit)
    return store.write("commits", commit_bytes)


class TestFindMergeBase:
    def test_linear_history(self, tmp_path):
        """A -- B -- C: merge_base(B, C) = B"""
        store = ObjectStore(tmp_path / "objects")
        a = _make_commit(store, [], "a")
        b = _make_commit(store, [a], "b")
        c = _make_commit(store, [b], "c")
        assert find_merge_base(store, b, c) == b

    def test_same_commit(self, tmp_path):
        store = ObjectStore(tmp_path / "objects")
        a = _make_commit(store, [], "a")
        assert find_merge_base(store, a, a) == a

    def test_a_is_ancestor_of_b(self, tmp_path):
        """A -- B -- C: merge_base(A, C) = A (fast-forward)"""
        store = ObjectStore(tmp_path / "objects")
        a = _make_commit(store, [], "a")
        b = _make_commit(store, [a], "b")
        c = _make_commit(store, [b], "c")
        assert find_merge_base(store, a, c) == a

    def test_b_is_ancestor_of_a(self, tmp_path):
        """A -- B -- C: merge_base(C, A) = A"""
        store = ObjectStore(tmp_path / "objects")
        a = _make_commit(store, [], "a")
        b = _make_commit(store, [a], "b")
        c = _make_commit(store, [b], "c")
        assert find_merge_base(store, c, a) == a

    def test_diamond(self, tmp_path):
        """
        A -- B -- D
         \\       /
          -- C --
        merge_base(D, C) = A  (or merge_base(B, C) = A)
        """
        store = ObjectStore(tmp_path / "objects")
        a = _make_commit(store, [], "a")
        b = _make_commit(store, [a], "b")
        c = _make_commit(store, [a], "c")
        d = _make_commit(store, [b, c], "d")
        assert find_merge_base(store, b, c) == a
        # Also test: merge_base of two branches from same fork
        assert find_merge_base(store, d, c) == c

    def test_no_common_ancestor(self, tmp_path):
        """Two independent histories."""
        store = ObjectStore(tmp_path / "objects")
        a = _make_commit(store, [], "a")
        b = _make_commit(store, [], "b")
        assert find_merge_base(store, a, b) is None

    def test_longer_diverged(self, tmp_path):
        """
        A -- B -- C -- D (branch1)
         \\
          -- E -- F     (branch2)
        merge_base(D, F) = A
        """
        store = ObjectStore(tmp_path / "objects")
        a = _make_commit(store, [], "a")
        b = _make_commit(store, [a], "b")
        c = _make_commit(store, [b], "c")
        d = _make_commit(store, [c], "d")
        e = _make_commit(store, [a], "e")
        f = _make_commit(store, [e], "f")
        assert find_merge_base(store, d, f) == a

    def test_criss_cross(self, tmp_path):
        """
        A -- B -- D
         \\   \\X  /
          -- C -- E
        B and C both merge each other: D=merge(B,C), E=merge(C,B)
        merge_base(D, E) should return B or C (both valid).
        """
        store = ObjectStore(tmp_path / "objects")
        a = _make_commit(store, [], "a")
        b = _make_commit(store, [a], "b")
        c = _make_commit(store, [a], "c")
        d = _make_commit(store, [b, c], "d")
        e = _make_commit(store, [c, b], "e")
        result = find_merge_base(store, d, e)
        assert result in (b, c)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_merge_base.py -v`
Expected: FAIL — module `dit.core.merge_base` does not exist

- [ ] **Step 3: Implement merge-base algorithm**

```python
# src/dit/core/merge_base.py
from __future__ import annotations

from collections import deque

from dit.core.objects import deserialize_commit
from dit.core.store import ObjectStore


def find_merge_base(store: ObjectStore, hash_a: str, hash_b: str) -> str | None:
    if hash_a == hash_b:
        return hash_a

    ancestors_a: set[str] = {hash_a}
    ancestors_b: set[str] = {hash_b}
    queue_a: deque[str] = deque([hash_a])
    queue_b: deque[str] = deque([hash_b])

    while queue_a or queue_b:
        if queue_a:
            current = queue_a.popleft()
            if current in ancestors_b:
                return current
            commit_data = store.read("commits", current)
            if commit_data is not None:
                commit = deserialize_commit(commit_data)
                for parent in commit.parent_hashes:
                    if parent not in ancestors_a:
                        ancestors_a.add(parent)
                        queue_a.append(parent)
                        if parent in ancestors_b:
                            return parent

        if queue_b:
            current = queue_b.popleft()
            if current in ancestors_a:
                return current
            commit_data = store.read("commits", current)
            if commit_data is not None:
                commit = deserialize_commit(commit_data)
                for parent in commit.parent_hashes:
                    if parent not in ancestors_b:
                        ancestors_b.add(parent)
                        queue_b.append(parent)
                        if parent in ancestors_a:
                            return parent

    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_merge_base.py -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/dit/core/merge_base.py tests/test_merge_base.py
git commit -m "feat: BFS merge-base algorithm"
```

---

### Task 3: Three-way Merge Algorithm — Data Types and File-Level Merge

**Files:**
- Create: `src/dit/core/merge.py`
- Test: `tests/test_merge.py` (create new)

This task implements the data types and the file-level (tree-level) merge dispatch — the logic that decides for each file whether to keep, delete, or invoke row-level merge. Row-level merge is implemented in Task 4.

- [ ] **Step 1: Write failing tests for file-level three-way merge**

```python
# tests/test_merge.py
"""Tests for three-way merge algorithm."""
import time

from dit.core.merge import (
    MergeConflict,
    MergeResult,
    three_way_merge,
    merge_manifests,
)
from dit.core.objects import (
    Commit,
    Manifest,
    ManifestEntry,
    Tree,
    TreeEntry,
    object_hash,
    serialize_commit,
    serialize_manifest,
    serialize_tree,
)
from dit.core.store import ObjectStore


def _write_manifest(store: ObjectStore, entries: list[ManifestEntry]) -> str:
    m = Manifest(entries=entries)
    data = serialize_manifest(m)
    return store.write("manifests", data)


def _write_tree(store: ObjectStore, file_entries: dict[str, str]) -> str:
    entries = [
        TreeEntry(name=name, obj_type="manifest", obj_hash=mhash)
        for name, mhash in file_entries.items()
    ]
    tree = Tree(entries=entries)
    data = serialize_tree(tree)
    return store.write("trees", data)


def _write_commit(store: ObjectStore, tree_hash: str, parents: list[str]) -> str:
    c = Commit(
        tree_hash=tree_hash,
        parent_hashes=parents,
        author="test",
        message="test",
        timestamp=int(time.time()),
    )
    data = serialize_commit(c)
    return store.write("commits", data)


class TestFileLevelMerge:
    """Tests for tree-level three-way merge dispatch (per-file decisions)."""

    def test_both_same_as_base(self, tmp_path):
        """File unchanged in both — keep as-is."""
        store = ObjectStore(tmp_path / "objects")
        entry = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
        mhash = _write_manifest(store, [entry])
        base_tree = _write_tree(store, {"f.jsonl": mhash})
        base = _write_commit(store, base_tree, [])
        ours = _write_commit(store, base_tree, [base])
        theirs = _write_commit(store, base_tree, [base])
        result = three_way_merge(store, base, ours, theirs)
        assert result.conflicts == []
        assert "f.jsonl" in result.merged_tree_entries

    def test_ours_modified_theirs_same(self, tmp_path):
        """File modified only in ours — take ours."""
        store = ObjectStore(tmp_path / "objects")
        base_e = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
        ours_e = ManifestEntry(row_hash="b" * 64, query_fingerprint="q1")
        base_mhash = _write_manifest(store, [base_e])
        ours_mhash = _write_manifest(store, [ours_e])
        base_tree = _write_tree(store, {"f.jsonl": base_mhash})
        ours_tree = _write_tree(store, {"f.jsonl": ours_mhash})
        base_c = _write_commit(store, base_tree, [])
        ours_c = _write_commit(store, ours_tree, [base_c])
        theirs_c = _write_commit(store, base_tree, [base_c])
        result = three_way_merge(store, base_c, ours_c, theirs_c)
        assert result.conflicts == []
        assert result.merged_tree_entries["f.jsonl"] == ours_mhash

    def test_theirs_modified_ours_same(self, tmp_path):
        """File modified only in theirs — take theirs."""
        store = ObjectStore(tmp_path / "objects")
        base_e = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
        theirs_e = ManifestEntry(row_hash="c" * 64, query_fingerprint="q1")
        base_mhash = _write_manifest(store, [base_e])
        theirs_mhash = _write_manifest(store, [theirs_e])
        base_tree = _write_tree(store, {"f.jsonl": base_mhash})
        theirs_tree = _write_tree(store, {"f.jsonl": theirs_mhash})
        base_c = _write_commit(store, base_tree, [])
        ours_c = _write_commit(store, base_tree, [base_c])
        theirs_c = _write_commit(store, theirs_tree, [base_c])
        result = three_way_merge(store, base_c, ours_c, theirs_c)
        assert result.conflicts == []
        assert result.merged_tree_entries["f.jsonl"] == theirs_mhash

    def test_ours_deleted_theirs_same(self, tmp_path):
        """File deleted in ours, unchanged in theirs — delete."""
        store = ObjectStore(tmp_path / "objects")
        base_e = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
        base_mhash = _write_manifest(store, [base_e])
        base_tree = _write_tree(store, {"f.jsonl": base_mhash})
        ours_tree = _write_tree(store, {})
        base_c = _write_commit(store, base_tree, [])
        ours_c = _write_commit(store, ours_tree, [base_c])
        theirs_c = _write_commit(store, base_tree, [base_c])
        result = three_way_merge(store, base_c, ours_c, theirs_c)
        assert result.conflicts == []
        assert "f.jsonl" not in result.merged_tree_entries

    def test_theirs_deleted_ours_same(self, tmp_path):
        """File deleted in theirs, unchanged in ours — delete."""
        store = ObjectStore(tmp_path / "objects")
        base_e = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
        base_mhash = _write_manifest(store, [base_e])
        base_tree = _write_tree(store, {"f.jsonl": base_mhash})
        theirs_tree = _write_tree(store, {})
        base_c = _write_commit(store, base_tree, [])
        ours_c = _write_commit(store, base_tree, [base_c])
        theirs_c = _write_commit(store, theirs_tree, [base_c])
        result = three_way_merge(store, base_c, ours_c, theirs_c)
        assert result.conflicts == []
        assert "f.jsonl" not in result.merged_tree_entries

    def test_both_deleted(self, tmp_path):
        """File deleted in both — delete."""
        store = ObjectStore(tmp_path / "objects")
        base_e = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
        base_mhash = _write_manifest(store, [base_e])
        base_tree = _write_tree(store, {"f.jsonl": base_mhash})
        empty_tree = _write_tree(store, {})
        base_c = _write_commit(store, base_tree, [])
        ours_c = _write_commit(store, empty_tree, [base_c])
        theirs_c = _write_commit(store, empty_tree, [base_c])
        result = three_way_merge(store, base_c, ours_c, theirs_c)
        assert result.conflicts == []
        assert "f.jsonl" not in result.merged_tree_entries

    def test_ours_new_file(self, tmp_path):
        """New file only in ours — add."""
        store = ObjectStore(tmp_path / "objects")
        new_e = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
        new_mhash = _write_manifest(store, [new_e])
        base_tree = _write_tree(store, {})
        ours_tree = _write_tree(store, {"new.jsonl": new_mhash})
        base_c = _write_commit(store, base_tree, [])
        ours_c = _write_commit(store, ours_tree, [base_c])
        theirs_c = _write_commit(store, base_tree, [base_c])
        result = three_way_merge(store, base_c, ours_c, theirs_c)
        assert result.conflicts == []
        assert result.merged_tree_entries["new.jsonl"] == new_mhash

    def test_theirs_new_file(self, tmp_path):
        """New file only in theirs — add."""
        store = ObjectStore(tmp_path / "objects")
        new_e = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
        new_mhash = _write_manifest(store, [new_e])
        base_tree = _write_tree(store, {})
        theirs_tree = _write_tree(store, {"new.jsonl": new_mhash})
        base_c = _write_commit(store, base_tree, [])
        ours_c = _write_commit(store, base_tree, [base_c])
        theirs_c = _write_commit(store, theirs_tree, [base_c])
        result = three_way_merge(store, base_c, ours_c, theirs_c)
        assert result.conflicts == []
        assert result.merged_tree_entries["new.jsonl"] == new_mhash

    def test_both_add_same_new_file(self, tmp_path):
        """Both add the same new file with same content — keep one."""
        store = ObjectStore(tmp_path / "objects")
        new_e = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
        new_mhash = _write_manifest(store, [new_e])
        base_tree = _write_tree(store, {})
        both_tree = _write_tree(store, {"new.jsonl": new_mhash})
        base_c = _write_commit(store, base_tree, [])
        ours_c = _write_commit(store, both_tree, [base_c])
        theirs_c = _write_commit(store, both_tree, [base_c])
        result = three_way_merge(store, base_c, ours_c, theirs_c)
        assert result.conflicts == []
        assert result.merged_tree_entries["new.jsonl"] == new_mhash

    def test_modify_delete_conflict(self, tmp_path):
        """Ours deletes, theirs modifies — conflict."""
        store = ObjectStore(tmp_path / "objects")
        base_e = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
        theirs_e = ManifestEntry(row_hash="b" * 64, query_fingerprint="q1")
        base_mhash = _write_manifest(store, [base_e])
        theirs_mhash = _write_manifest(store, [theirs_e])
        base_tree = _write_tree(store, {"f.jsonl": base_mhash})
        ours_tree = _write_tree(store, {})
        theirs_tree = _write_tree(store, {"f.jsonl": theirs_mhash})
        base_c = _write_commit(store, base_tree, [])
        ours_c = _write_commit(store, ours_tree, [base_c])
        theirs_c = _write_commit(store, theirs_tree, [base_c])
        result = three_way_merge(store, base_c, ours_c, theirs_c)
        assert len(result.conflicts) == 1
        assert result.conflicts[0].file_path == "f.jsonl"
        assert result.conflicts[0].conflict_type == "modify_delete"

    def test_both_add_different_new_file_conflict(self, tmp_path):
        """Both add same filename with different content — conflict."""
        store = ObjectStore(tmp_path / "objects")
        ours_e = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
        theirs_e = ManifestEntry(row_hash="b" * 64, query_fingerprint="q2")
        ours_mhash = _write_manifest(store, [ours_e])
        theirs_mhash = _write_manifest(store, [theirs_e])
        base_tree = _write_tree(store, {})
        ours_tree = _write_tree(store, {"new.jsonl": ours_mhash})
        theirs_tree = _write_tree(store, {"new.jsonl": theirs_mhash})
        base_c = _write_commit(store, base_tree, [])
        ours_c = _write_commit(store, ours_tree, [base_c])
        theirs_c = _write_commit(store, theirs_tree, [base_c])
        result = three_way_merge(store, base_c, ours_c, theirs_c)
        assert len(result.conflicts) == 1
        assert result.conflicts[0].file_path == "new.jsonl"
        assert result.conflicts[0].conflict_type == "both_added"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_merge.py -v`
Expected: FAIL — module `dit.core.merge` does not exist

- [ ] **Step 3: Implement data types and file-level merge**

```python
# src/dit/core/merge.py
from __future__ import annotations

from dataclasses import dataclass, field

from dit.core.objects import (
    Manifest,
    ManifestEntry,
    deserialize_commit,
    deserialize_manifest,
    deserialize_tree,
    object_hash,
    serialize_manifest,
)
from dit.core.store import ObjectStore


@dataclass
class MergeConflict:
    file_path: str
    conflict_type: str  # "both_modified" | "modify_delete" | "both_added"
    base_entries: list[ManifestEntry] | None = None
    ours_entries: list[ManifestEntry] | None = None
    theirs_entries: list[ManifestEntry] | None = None


@dataclass
class MergeResult:
    merged_tree_entries: dict[str, str] = field(default_factory=dict)  # file_path -> manifest_hash
    conflicts: list[MergeConflict] = field(default_factory=list)


def _load_tree_manifests(store: ObjectStore, commit_hash: str) -> dict[str, str]:
    commit_data = store.read("commits", commit_hash)
    commit = deserialize_commit(commit_data)
    tree_data = store.read("trees", commit.tree_hash)
    tree = deserialize_tree(tree_data)
    return {e.name: e.obj_hash for e in tree.entries if e.obj_type == "manifest"}


def three_way_merge(
    store: ObjectStore,
    base_hash: str | None,
    ours_hash: str,
    theirs_hash: str,
) -> MergeResult:
    base_files = _load_tree_manifests(store, base_hash) if base_hash else {}
    ours_files = _load_tree_manifests(store, ours_hash)
    theirs_files = _load_tree_manifests(store, theirs_hash)

    all_paths = sorted(set(list(base_files.keys()) + list(ours_files.keys()) + list(theirs_files.keys())))
    result = MergeResult()

    for path in all_paths:
        base_mhash = base_files.get(path)
        ours_mhash = ours_files.get(path)
        theirs_mhash = theirs_files.get(path)

        if base_mhash is not None:
            # File existed in base
            if ours_mhash == base_mhash and theirs_mhash == base_mhash:
                result.merged_tree_entries[path] = base_mhash
            elif ours_mhash == base_mhash and theirs_mhash is not None:
                result.merged_tree_entries[path] = theirs_mhash
            elif theirs_mhash == base_mhash and ours_mhash is not None:
                result.merged_tree_entries[path] = ours_mhash
            elif ours_mhash is None and theirs_mhash == base_mhash:
                pass  # ours deleted, theirs unchanged -> delete
            elif theirs_mhash is None and ours_mhash == base_mhash:
                pass  # theirs deleted, ours unchanged -> delete
            elif ours_mhash is None and theirs_mhash is None:
                pass  # both deleted
            elif ours_mhash is None and theirs_mhash != base_mhash:
                # ours deleted, theirs modified -> conflict
                theirs_m = deserialize_manifest(store.read("manifests", theirs_mhash))
                base_m = deserialize_manifest(store.read("manifests", base_mhash))
                result.conflicts.append(MergeConflict(
                    file_path=path,
                    conflict_type="modify_delete",
                    base_entries=base_m.entries,
                    ours_entries=None,
                    theirs_entries=theirs_m.entries,
                ))
            elif theirs_mhash is None and ours_mhash != base_mhash:
                # theirs deleted, ours modified -> conflict
                ours_m = deserialize_manifest(store.read("manifests", ours_mhash))
                base_m = deserialize_manifest(store.read("manifests", base_mhash))
                result.conflicts.append(MergeConflict(
                    file_path=path,
                    conflict_type="modify_delete",
                    base_entries=base_m.entries,
                    ours_entries=ours_m.entries,
                    theirs_entries=None,
                ))
            elif ours_mhash == theirs_mhash:
                result.merged_tree_entries[path] = ours_mhash
            else:
                # Both modified differently -> row-level merge
                base_m = deserialize_manifest(store.read("manifests", base_mhash))
                ours_m = deserialize_manifest(store.read("manifests", ours_mhash))
                theirs_m = deserialize_manifest(store.read("manifests", theirs_mhash))
                merged_entries, conflicts = merge_manifests(base_m, ours_m, theirs_m, path)
                if conflicts:
                    result.conflicts.extend(conflicts)
                merged_manifest = Manifest(entries=merged_entries)
                merged_bytes = serialize_manifest(merged_manifest)
                merged_hash = store.write("manifests", merged_bytes)
                result.merged_tree_entries[path] = merged_hash
        else:
            # File NOT in base (new file)
            if ours_mhash is not None and theirs_mhash is None:
                result.merged_tree_entries[path] = ours_mhash
            elif ours_mhash is None and theirs_mhash is not None:
                result.merged_tree_entries[path] = theirs_mhash
            elif ours_mhash == theirs_mhash:
                result.merged_tree_entries[path] = ours_mhash
            else:
                # Both added different content -> conflict
                ours_m = deserialize_manifest(store.read("manifests", ours_mhash))
                theirs_m = deserialize_manifest(store.read("manifests", theirs_mhash))
                result.conflicts.append(MergeConflict(
                    file_path=path,
                    conflict_type="both_added",
                    base_entries=None,
                    ours_entries=ours_m.entries,
                    theirs_entries=theirs_m.entries,
                ))

    return result


def merge_manifests(
    base: Manifest,
    ours: Manifest,
    theirs: Manifest,
    file_path: str,
) -> tuple[list[ManifestEntry], list[MergeConflict]]:
    # Placeholder — implemented in Task 4
    raise NotImplementedError("merge_manifests not yet implemented")
```

- [ ] **Step 4: Run file-level tests to verify they pass**

Run: `uv run pytest tests/test_merge.py::TestFileLevelMerge -v`
Expected: All 11 tests PASS (the "both_modified" case that calls merge_manifests is tested separately in Task 4)

- [ ] **Step 5: Commit**

```bash
git add src/dit/core/merge.py tests/test_merge.py
git commit -m "feat: three-way merge — file-level dispatch"
```

---

### Task 4: Three-way Merge Algorithm — Row-Level Merge

**Files:**
- Modify: `src/dit/core/merge.py` (replace `merge_manifests` placeholder)
- Modify: `tests/test_merge.py` (add row-level tests)

- [ ] **Step 1: Write failing tests for row-level merge**

Add to `tests/test_merge.py`:

```python
class TestMergeManifests:
    """Row-level three-way merge tests."""

    def _e(self, rh: str, qfp: str | None = None) -> ManifestEntry:
        return ManifestEntry(row_hash=rh, query_fingerprint=qfp)

    def test_all_same(self):
        """Base/ours/theirs identical — no changes."""
        base = Manifest(entries=[self._e("a1", "q1"), self._e("a2", "q2")])
        ours = Manifest(entries=[self._e("a1", "q1"), self._e("a2", "q2")])
        theirs = Manifest(entries=[self._e("a1", "q1"), self._e("a2", "q2")])
        merged, conflicts = merge_manifests(base, ours, theirs, "f.jsonl")
        assert conflicts == []
        assert [e.row_hash for e in merged] == ["a1", "a2"]

    def test_ours_adds_row(self):
        """Ours adds a new row — included in merged."""
        base = Manifest(entries=[self._e("a1", "q1")])
        ours = Manifest(entries=[self._e("a1", "q1"), self._e("a2", "q2")])
        theirs = Manifest(entries=[self._e("a1", "q1")])
        merged, conflicts = merge_manifests(base, ours, theirs, "f.jsonl")
        assert conflicts == []
        assert len(merged) == 2
        hashes = [e.row_hash for e in merged]
        assert "a1" in hashes and "a2" in hashes

    def test_theirs_adds_row(self):
        """Theirs adds a new row — appended to end."""
        base = Manifest(entries=[self._e("a1", "q1")])
        ours = Manifest(entries=[self._e("a1", "q1")])
        theirs = Manifest(entries=[self._e("a1", "q1"), self._e("b1", "q2")])
        merged, conflicts = merge_manifests(base, ours, theirs, "f.jsonl")
        assert conflicts == []
        assert len(merged) == 2
        assert merged[0].row_hash == "a1"
        assert merged[1].row_hash == "b1"

    def test_ours_deletes_row(self):
        """Ours deletes a row — removed from merged."""
        base = Manifest(entries=[self._e("a1", "q1"), self._e("a2", "q2")])
        ours = Manifest(entries=[self._e("a1", "q1")])
        theirs = Manifest(entries=[self._e("a1", "q1"), self._e("a2", "q2")])
        merged, conflicts = merge_manifests(base, ours, theirs, "f.jsonl")
        assert conflicts == []
        assert [e.row_hash for e in merged] == ["a1"]

    def test_theirs_deletes_row(self):
        """Theirs deletes a row — removed from merged."""
        base = Manifest(entries=[self._e("a1", "q1"), self._e("a2", "q2")])
        ours = Manifest(entries=[self._e("a1", "q1"), self._e("a2", "q2")])
        theirs = Manifest(entries=[self._e("a1", "q1")])
        merged, conflicts = merge_manifests(base, ours, theirs, "f.jsonl")
        assert conflicts == []
        assert [e.row_hash for e in merged] == ["a1"]

    def test_ours_refreshes_row(self):
        """Ours refreshes a row (same qfp, different row_hash) — take ours."""
        base = Manifest(entries=[self._e("old_rh", "q1")])
        ours = Manifest(entries=[self._e("new_rh_ours", "q1")])
        theirs = Manifest(entries=[self._e("old_rh", "q1")])
        merged, conflicts = merge_manifests(base, ours, theirs, "f.jsonl")
        assert conflicts == []
        assert merged[0].row_hash == "new_rh_ours"

    def test_theirs_refreshes_row(self):
        """Theirs refreshes a row — take theirs."""
        base = Manifest(entries=[self._e("old_rh", "q1")])
        ours = Manifest(entries=[self._e("old_rh", "q1")])
        theirs = Manifest(entries=[self._e("new_rh_theirs", "q1")])
        merged, conflicts = merge_manifests(base, ours, theirs, "f.jsonl")
        assert conflicts == []
        assert merged[0].row_hash == "new_rh_theirs"

    def test_both_refresh_same_result(self):
        """Both refresh same row to same result — no conflict."""
        base = Manifest(entries=[self._e("old_rh", "q1")])
        ours = Manifest(entries=[self._e("new_rh", "q1")])
        theirs = Manifest(entries=[self._e("new_rh", "q1")])
        merged, conflicts = merge_manifests(base, ours, theirs, "f.jsonl")
        assert conflicts == []
        assert merged[0].row_hash == "new_rh"

    def test_both_refresh_different_result_conflict(self):
        """Both refresh same row to different results — conflict."""
        base = Manifest(entries=[self._e("old_rh", "q1")])
        ours = Manifest(entries=[self._e("ours_rh", "q1")])
        theirs = Manifest(entries=[self._e("theirs_rh", "q1")])
        merged, conflicts = merge_manifests(base, ours, theirs, "f.jsonl")
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type == "both_modified"
        assert conflicts[0].file_path == "f.jsonl"

    def test_both_add_same_new_row(self):
        """Both add the same new row — keep one copy."""
        base = Manifest(entries=[])
        ours = Manifest(entries=[self._e("new_rh", "q1")])
        theirs = Manifest(entries=[self._e("new_rh", "q1")])
        merged, conflicts = merge_manifests(base, ours, theirs, "f.jsonl")
        assert conflicts == []
        assert len(merged) == 1
        assert merged[0].row_hash == "new_rh"

    def test_row_ordering_ours_skeleton(self):
        """Ours order is used as skeleton, theirs additions appended."""
        base = Manifest(entries=[self._e("a1", "q1"), self._e("a2", "q2")])
        ours = Manifest(entries=[self._e("a2", "q2"), self._e("a1", "q1"), self._e("a3", "q3")])
        theirs = Manifest(entries=[self._e("a1", "q1"), self._e("a2", "q2"), self._e("b1", "q4")])
        merged, conflicts = merge_manifests(base, ours, theirs, "f.jsonl")
        assert conflicts == []
        hashes = [e.row_hash for e in merged]
        assert hashes == ["a2", "a1", "a3", "b1"]

    def test_complex_mixed_operations(self):
        """Mix of add, delete, refresh across both sides."""
        base = Manifest(entries=[
            self._e("r1", "q1"),
            self._e("r2", "q2"),
            self._e("r3", "q3"),
        ])
        ours = Manifest(entries=[
            self._e("r1", "q1"),       # keep
            self._e("r2_new", "q2"),    # refresh r2
            # r3 deleted
            self._e("r4", "q4"),        # new
        ])
        theirs = Manifest(entries=[
            self._e("r1", "q1"),        # keep
            self._e("r2", "q2"),        # unchanged
            self._e("r3", "q3"),        # unchanged
            self._e("r5", "q5"),        # new
        ])
        merged, conflicts = merge_manifests(base, ours, theirs, "f.jsonl")
        assert conflicts == []
        hashes = [e.row_hash for e in merged]
        assert "r1" in hashes
        assert "r2_new" in hashes  # ours refresh wins
        assert "r3" not in hashes  # ours deleted
        assert "r4" in hashes      # ours new
        assert "r5" in hashes      # theirs new

    def test_row_without_query_fingerprint(self):
        """Rows with qfp=None: treated as independent, no refresh detection."""
        base = Manifest(entries=[self._e("r1", None)])
        ours = Manifest(entries=[self._e("r1", None)])
        theirs = Manifest(entries=[self._e("r2", None)])
        merged, conflicts = merge_manifests(base, ours, theirs, "f.jsonl")
        assert conflicts == []
        # r1 deleted by theirs (not in theirs), r2 added by theirs
        assert len(merged) == 1
        assert merged[0].row_hash == "r2"

    def test_pure_reorder_no_conflict(self):
        """Ours reorders rows, theirs unchanged — ours order preserved."""
        base = Manifest(entries=[self._e("a1", "q1"), self._e("a2", "q2"), self._e("a3", "q3")])
        ours = Manifest(entries=[self._e("a3", "q3"), self._e("a1", "q1"), self._e("a2", "q2")])
        theirs = Manifest(entries=[self._e("a1", "q1"), self._e("a2", "q2"), self._e("a3", "q3")])
        merged, conflicts = merge_manifests(base, ours, theirs, "f.jsonl")
        assert conflicts == []
        assert [e.row_hash for e in merged] == ["a3", "a1", "a2"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_merge.py::TestMergeManifests -v`
Expected: FAIL — `merge_manifests` raises `NotImplementedError`

- [ ] **Step 3: Implement merge_manifests**

Replace the placeholder `merge_manifests` in `src/dit/core/merge.py`:

```python
def merge_manifests(
    base: Manifest,
    ours: Manifest,
    theirs: Manifest,
    file_path: str,
) -> tuple[list[ManifestEntry], list[MergeConflict]]:
    base_hashes = {e.row_hash for e in base.entries}
    ours_hashes = {e.row_hash for e in ours.entries}
    theirs_hashes = {e.row_hash for e in theirs.entries}

    # Index by query_fingerprint for refresh detection
    base_by_qfp: dict[str, ManifestEntry] = {}
    for e in base.entries:
        if e.query_fingerprint:
            base_by_qfp[e.query_fingerprint] = e

    ours_by_qfp: dict[str, ManifestEntry] = {}
    for e in ours.entries:
        if e.query_fingerprint:
            ours_by_qfp[e.query_fingerprint] = e

    theirs_by_qfp: dict[str, ManifestEntry] = {}
    for e in theirs.entries:
        if e.query_fingerprint:
            theirs_by_qfp[e.query_fingerprint] = e

    # Detect refreshes: base qfp present in ours/theirs but with different row_hash
    ours_refreshed: dict[str, ManifestEntry] = {}   # qfp -> new entry in ours
    theirs_refreshed: dict[str, ManifestEntry] = {}  # qfp -> new entry in theirs

    for qfp, base_entry in base_by_qfp.items():
        if base_entry.row_hash not in ours_hashes and qfp in ours_by_qfp:
            ours_refreshed[qfp] = ours_by_qfp[qfp]
        if base_entry.row_hash not in theirs_hashes and qfp in theirs_by_qfp:
            theirs_refreshed[qfp] = theirs_by_qfp[qfp]

    conflicts: list[MergeConflict] = []
    # Track which row_hashes are consumed by refresh conflict resolution
    conflict_ours_hashes: set[str] = set()
    conflict_theirs_hashes: set[str] = set()

    # Resolve refresh conflicts
    refresh_resolved: dict[str, ManifestEntry] = {}  # qfp -> winning entry
    for qfp in set(list(ours_refreshed.keys()) + list(theirs_refreshed.keys())):
        o = ours_refreshed.get(qfp)
        t = theirs_refreshed.get(qfp)
        if o and t:
            if o.row_hash == t.row_hash:
                refresh_resolved[qfp] = o
            else:
                conflicts.append(MergeConflict(
                    file_path=file_path,
                    conflict_type="both_modified",
                    base_entries=[base_by_qfp[qfp]],
                    ours_entries=[o],
                    theirs_entries=[t],
                ))
                conflict_ours_hashes.add(o.row_hash)
                conflict_theirs_hashes.add(t.row_hash)
        elif o:
            refresh_resolved[qfp] = o
        elif t:
            refresh_resolved[qfp] = t

    # Determine which base rows are deleted
    deleted_base_hashes: set[str] = set()
    for e in base.entries:
        in_ours = e.row_hash in ours_hashes or (e.query_fingerprint and e.query_fingerprint in ours_refreshed)
        in_theirs = e.row_hash in theirs_hashes or (e.query_fingerprint and e.query_fingerprint in theirs_refreshed)
        if not in_ours or not in_theirs:
            deleted_base_hashes.add(e.row_hash)

    # Collect theirs-only new rows (not in base, not in ours)
    theirs_only_new: list[ManifestEntry] = []
    ours_all_hashes = ours_hashes | conflict_ours_hashes
    theirs_refreshed_hashes = {e.row_hash for e in theirs_refreshed.values()}
    for e in theirs.entries:
        if e.row_hash not in base_hashes and e.row_hash not in ours_all_hashes and e.row_hash not in conflict_theirs_hashes and e.row_hash not in theirs_refreshed_hashes:
            theirs_only_new.append(e)

    # Build merged result: ours as skeleton
    merged: list[ManifestEntry] = []
    seen_hashes: set[str] = set()

    for e in ours.entries:
        if e.row_hash in conflict_ours_hashes:
            continue

        # Check if this is a refresh
        if e.query_fingerprint and e.query_fingerprint in refresh_resolved:
            resolved = refresh_resolved[e.query_fingerprint]
            if resolved.row_hash not in seen_hashes:
                merged.append(resolved)
                seen_hashes.add(resolved.row_hash)
            continue

        # Check if this row was deleted by theirs
        if e.row_hash in base_hashes and e.row_hash not in theirs_hashes:
            if not (e.query_fingerprint and e.query_fingerprint in theirs_refreshed):
                continue  # theirs deleted this row

        if e.row_hash in deleted_base_hashes:
            continue

        if e.row_hash not in seen_hashes:
            merged.append(e)
            seen_hashes.add(e.row_hash)

    # Append theirs-only new rows
    for e in theirs_only_new:
        if e.row_hash not in seen_hashes:
            merged.append(e)
            seen_hashes.add(e.row_hash)

    return merged, conflicts
```

- [ ] **Step 4: Run all merge tests**

Run: `uv run pytest tests/test_merge.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/dit/core/merge.py tests/test_merge.py
git commit -m "feat: row-level three-way merge with refresh detection"
```

---

### Task 5: Branch CLI Commands (branch, checkout, switch)

**Files:**
- Modify: `src/dit/cli/main.py`
- Create: `tests/test_cli_branch.py`

- [ ] **Step 1: Write failing tests for branch commands**

```python
# tests/test_cli_branch.py
import json
import os
from pathlib import Path

from typer.testing import CliRunner

from dit.cli.main import app

runner = CliRunner()


def _init_and_commit(tmp_path: Path):
    os.chdir(tmp_path)
    runner.invoke(app, ["init"], catch_exceptions=False)
    (tmp_path / "data.jsonl").write_text(
        json.dumps({"messages": [{"role": "user", "content": "hello"}]}) + "\n"
    )
    runner.invoke(app, ["add", "."], catch_exceptions=False)
    runner.invoke(app, ["commit", "-m", "initial"], catch_exceptions=False)


class TestBranch:
    def test_list_branches(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["branch"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "main" in result.output

    def test_list_marks_current(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["branch"], catch_exceptions=False)
        assert "* main" in result.output

    def test_create_branch(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        assert result.exit_code == 0
        # Branch created but HEAD still on main
        result = runner.invoke(app, ["branch"], catch_exceptions=False)
        assert "feature" in result.output
        assert "* main" in result.output

    def test_create_existing_branch_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        result = runner.invoke(app, ["branch", "feature"])
        assert result.exit_code != 0

    def test_delete_branch(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        result = runner.invoke(app, ["branch", "-d", "feature"], catch_exceptions=False)
        assert result.exit_code == 0
        result = runner.invoke(app, ["branch"], catch_exceptions=False)
        assert "feature" not in result.output

    def test_delete_current_branch_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["branch", "-d", "main"])
        assert result.exit_code != 0

    def test_delete_nonexistent_branch_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["branch", "-d", "nope"])
        assert result.exit_code != 0


class TestCheckout:
    def test_checkout_existing_branch(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        result = runner.invoke(app, ["checkout", "feature"], catch_exceptions=False)
        assert result.exit_code == 0
        head = (tmp_path / ".dit" / "HEAD").read_text().strip()
        assert head == "ref:feature"

    def test_checkout_creates_new_branch(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        assert result.exit_code == 0
        head = (tmp_path / ".dit" / "HEAD").read_text().strip()
        assert head == "ref:feature"

    def test_checkout_nonexistent_branch_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["checkout", "nope"])
        assert result.exit_code != 0

    def test_checkout_b_existing_branch_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        result = runner.invoke(app, ["checkout", "-b", "feature"])
        assert result.exit_code != 0

    def test_checkout_materializes_files(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "new content"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "change on feature"], catch_exceptions=False)
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        content = (tmp_path / "data.jsonl").read_text()
        assert "hello" in content
        assert "new content" not in content

    def test_checkout_with_uncommitted_changes_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text('{"messages":[{"role":"user","content":"dirty"}]}\n')
        result = runner.invoke(app, ["checkout", "feature"])
        assert result.exit_code != 0
        assert "uncommitted" in result.output.lower()

    def test_checkout_removes_files_not_in_target(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "extra.jsonl").write_text('{"x":1}\n')
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "add extra"], catch_exceptions=False)
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        assert not (tmp_path / "extra.jsonl").exists()


class TestSwitch:
    def test_switch_to_existing_branch(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        result = runner.invoke(app, ["switch", "feature"], catch_exceptions=False)
        assert result.exit_code == 0
        head = (tmp_path / ".dit" / "HEAD").read_text().strip()
        assert head == "ref:feature"

    def test_switch_nonexistent_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["switch", "nope"])
        assert result.exit_code != 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli_branch.py -v`
Expected: FAIL — commands `branch`, `checkout`, `switch` do not exist

- [ ] **Step 3: Implement branch, checkout, switch commands**

Add to `src/dit/cli/main.py` (after the `status` command, before `serve`):

Note: Add `from typing import Optional` to the imports at the top of `main.py` if it is not already present.

```python
@app.command()
def branch(
    name: Optional[str] = typer.Argument(None, help="Branch name to create"),
    delete: str = typer.Option("", "-d", help="Branch name to delete"),
):
    """List, create, or delete branches."""
    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    refs = RefStore(dot)

    if delete:
        current = refs.current_branch()
        if delete == current:
            typer.echo(f"error: cannot delete current branch '{delete}'", err=True)
            raise typer.Exit(1)
        if not refs.delete_branch(delete):
            typer.echo(f"error: branch '{delete}' not found", err=True)
            raise typer.Exit(1)
        typer.echo(f"Deleted branch '{delete}'.")
        return

    if name is not None:
        if refs.get_branch(name) is not None:
            typer.echo(f"fatal: branch '{name}' already exists", err=True)
            raise typer.Exit(1)
        head_hash = refs.resolve_head()
        if head_hash is None:
            typer.echo("fatal: no commits yet", err=True)
            raise typer.Exit(1)
        refs.set_branch(name, head_hash)
        typer.echo(f"Created branch '{name}' at {head_hash[:8]}.")
        return

    # List branches
    current = refs.current_branch()
    branches = refs.list_branches()
    for bname in sorted(branches.keys()):
        prefix = "* " if bname == current else "  "
        typer.echo(f"{prefix}{bname} {branches[bname][:8]}")


def _has_uncommitted_changes(repo_root: Path, dot: Path, store: ObjectStore, refs: RefStore) -> bool:
    head_hash = refs.resolve_head()
    if head_hash is None:
        return len(find_jsonl_files(repo_root)) > 0

    head_manifests: dict[str, str] = {}
    commit_data = store.read("commits", head_hash)
    head_commit = deserialize_commit(commit_data)
    tree_data = store.read("trees", head_commit.tree_hash)
    tree = deserialize_tree(tree_data)
    for entry in tree.entries:
        if entry.obj_type == "manifest":
            head_manifests[entry.name] = entry.obj_hash

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


def _materialize_tree(repo_root: Path, store: ObjectStore, tree_hash: str, old_tree_hash: str | None = None):
    """Materialize working directory from tree, optimizing by skipping unchanged files."""
    from dit.core.workspace import materialize_file

    tree_data = store.read("trees", tree_hash)
    tree = deserialize_tree(tree_data)
    new_files = {e.name: e.obj_hash for e in tree.entries if e.obj_type == "manifest"}

    old_files: dict[str, str] = {}
    if old_tree_hash:
        old_tree_data = store.read("trees", old_tree_hash)
        old_tree = deserialize_tree(old_tree_data)
        old_files = {e.name: e.obj_hash for e in old_tree.entries if e.obj_type == "manifest"}

    # Materialize changed or new files
    for name, mhash in new_files.items():
        if old_files.get(name) != mhash:
            m_data = store.read("manifests", mhash)
            manifest = deserialize_manifest(m_data)
            materialize_file(repo_root, name, manifest, store)

    # Remove files that exist in old but not in new
    for name in old_files:
        if name not in new_files:
            file_path = repo_root / name
            if file_path.exists():
                file_path.unlink()
                # Clean up empty parent directories
                parent = file_path.parent
                while parent != repo_root and not any(parent.iterdir()):
                    parent.rmdir()
                    parent = parent.parent


@app.command()
def checkout(
    target: str = typer.Argument(..., help="Branch name to checkout"),
    create: bool = typer.Option(False, "-b", help="Create a new branch and switch to it"),
):
    """Switch branches or create a new branch."""
    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)
    index = StagingIndex(dot / "index")

    if create:
        if refs.get_branch(target) is not None:
            typer.echo(f"fatal: branch '{target}' already exists", err=True)
            raise typer.Exit(1)
        head_hash = refs.resolve_head()
        if head_hash is None:
            typer.echo("fatal: no commits yet", err=True)
            raise typer.Exit(1)
        refs.set_branch(target, head_hash)
        refs.head_file.write_text(f"ref:{target}\n")
        typer.echo(f"Switched to new branch '{target}'.")
        return

    target_hash = refs.get_branch(target)
    if target_hash is None:
        typer.echo(f"error: branch '{target}' not found", err=True)
        raise typer.Exit(1)

    if _has_uncommitted_changes(repo_root, dot, store, refs):
        typer.echo("error: working directory has uncommitted changes", err=True)
        raise typer.Exit(1)

    if index.entries():
        typer.echo("error: staging area is not empty — please commit or reset first", err=True)
        raise typer.Exit(1)

    current_hash = refs.resolve_head()
    current_commit = deserialize_commit(store.read("commits", current_hash)) if current_hash else None
    target_commit = deserialize_commit(store.read("commits", target_hash))

    old_tree_hash = current_commit.tree_hash if current_commit else None
    _materialize_tree(repo_root, store, target_commit.tree_hash, old_tree_hash)

    refs.head_file.write_text(f"ref:{target}\n")
    typer.echo(f"Switched to branch '{target}'.")


@app.command()
def switch(
    target: str = typer.Argument(..., help="Branch name to switch to"),
):
    """Switch to an existing branch."""
    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)
    index = StagingIndex(dot / "index")

    target_hash = refs.get_branch(target)
    if target_hash is None:
        typer.echo(f"error: branch '{target}' not found", err=True)
        raise typer.Exit(1)

    if _has_uncommitted_changes(repo_root, dot, store, refs):
        typer.echo("error: working directory has uncommitted changes", err=True)
        raise typer.Exit(1)

    if index.entries():
        typer.echo("error: staging area is not empty — please commit or reset first", err=True)
        raise typer.Exit(1)

    current_hash = refs.resolve_head()
    current_commit = deserialize_commit(store.read("commits", current_hash)) if current_hash else None
    target_commit = deserialize_commit(store.read("commits", target_hash))

    old_tree_hash = current_commit.tree_hash if current_commit else None
    _materialize_tree(repo_root, store, target_commit.tree_hash, old_tree_hash)

    refs.head_file.write_text(f"ref:{target}\n")
    typer.echo(f"Switched to branch '{target}'.")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_branch.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest --tb=short -q`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add src/dit/cli/main.py tests/test_cli_branch.py
git commit -m "feat: branch, checkout, switch CLI commands"
```

---

### Task 6: CLI Merge Command — Fast-Forward and Clean Three-Way Merge

**Files:**
- Modify: `src/dit/cli/main.py`
- Create: `tests/test_cli_merge.py`

- [ ] **Step 1: Write failing tests for merge command (non-conflict cases)**

```python
# tests/test_cli_merge.py
import json
import os
from pathlib import Path

from typer.testing import CliRunner

from dit.cli.main import app

runner = CliRunner()


def _init_and_commit(tmp_path: Path, filename: str = "data.jsonl", content: str | None = None):
    os.chdir(tmp_path)
    runner.invoke(app, ["init"], catch_exceptions=False)
    if content is None:
        content = json.dumps({"messages": [{"role": "user", "content": "hello"}]}) + "\n"
    (tmp_path / filename).write_text(content)
    runner.invoke(app, ["add", "."], catch_exceptions=False)
    runner.invoke(app, ["commit", "-m", "initial"], catch_exceptions=False)


class TestMergeFastForward:
    def test_fast_forward(self, tmp_path):
        """Feature branch is ahead of main — fast-forward."""
        _init_and_commit(tmp_path)
        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "updated"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "feature change"], catch_exceptions=False)
        feature_hash = (tmp_path / ".dit" / "refs" / "heads" / "feature").read_text().strip()
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        result = runner.invoke(app, ["merge", "feature"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "fast-forward" in result.output.lower()
        main_hash = (tmp_path / ".dit" / "refs" / "heads" / "main").read_text().strip()
        assert main_hash == feature_hash

    def test_already_up_to_date(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        result = runner.invoke(app, ["merge", "feature"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "up to date" in result.output.lower()


class TestMergeThreeWay:
    def test_clean_three_way_merge(self, tmp_path):
        """Both branches have changes to different files — clean merge."""
        _init_and_commit(tmp_path)
        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "feature.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "feature data"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "add feature file"], catch_exceptions=False)
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        (tmp_path / "main.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "main data"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "add main file"], catch_exceptions=False)
        result = runner.invoke(app, ["merge", "feature"], catch_exceptions=False)
        assert result.exit_code == 0
        assert (tmp_path / "feature.jsonl").exists()
        assert (tmp_path / "main.jsonl").exists()
        # Verify merge commit has two parents
        from dit.core.store import ObjectStore
        from dit.core.objects import deserialize_commit
        from dit.core.refs import RefStore
        dot = tmp_path / ".dit"
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)
        head_hash = refs.resolve_head()
        commit = deserialize_commit(store.read("commits", head_hash))
        assert len(commit.parent_hashes) == 2

    def test_merge_same_branch_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["merge", "main"])
        assert result.exit_code != 0

    def test_merge_nonexistent_branch_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["merge", "nope"])
        assert result.exit_code != 0

    def test_merge_with_staged_files_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["branch", "feature"], catch_exceptions=False)
        (tmp_path / "new.jsonl").write_text('{"x":1}\n')
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        result = runner.invoke(app, ["merge", "feature"])
        assert result.exit_code != 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli_merge.py -v`
Expected: FAIL — `merge` command does not exist

- [ ] **Step 3: Implement merge command**

Add to `src/dit/cli/main.py`:

```python
@app.command()
def merge(
    source: str = typer.Argument("", help="Branch to merge into current branch"),
    continue_merge: bool = typer.Option(False, "--continue", help="Continue after resolving conflicts"),
    abort: bool = typer.Option(False, "--abort", help="Abort current merge"),
    message: str = typer.Option("", "-m", help="Merge commit message"),
):
    """Merge a branch into the current branch."""
    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)
    index = StagingIndex(dot / "index")

    merge_head_file = dot / "MERGE_HEAD"
    merge_msg_file = dot / "MERGE_MSG"
    conflicts_file = dot / "conflicts.json"

    if abort:
        if not merge_head_file.exists():
            typer.echo("error: no merge in progress", err=True)
            raise typer.Exit(1)
        conflicts_data = json.loads(conflicts_file.read_text()) if conflicts_file.exists() else {}
        ours_hash = conflicts_data.get("ours_commit") or refs.resolve_head()
        if ours_hash:
            ours_commit = deserialize_commit(store.read("commits", ours_hash))
            _materialize_tree(repo_root, store, ours_commit.tree_hash)
        merge_head_file.unlink(missing_ok=True)
        merge_msg_file.unlink(missing_ok=True)
        conflicts_file.unlink(missing_ok=True)
        index.clear()
        typer.echo("Merge aborted.")
        return

    if continue_merge:
        if not merge_head_file.exists():
            typer.echo("error: no merge in progress", err=True)
            raise typer.Exit(1)
        staged = index.entries()
        if not staged:
            typer.echo("error: nothing staged — resolve conflicts and dit add first", err=True)
            raise typer.Exit(1)
        theirs_hash = merge_head_file.read_text().strip()
        ours_hash = refs.resolve_head()
        merge_msg = message or (merge_msg_file.read_text().strip() if merge_msg_file.exists() else "merge commit")
        head_commit_hash = refs.resolve_head()
        existing_tree_entries: dict[str, TreeEntry] = {}
        if head_commit_hash:
            commit_data = store.read("commits", head_commit_hash)
            old_commit = deserialize_commit(commit_data)
            tree_data = store.read("trees", old_commit.tree_hash)
            old_tree = deserialize_tree(tree_data)
            for e in old_tree.entries:
                existing_tree_entries[e.name] = e
        for rel_path, manifest_hash in staged.items():
            existing_tree_entries[rel_path] = TreeEntry(
                name=rel_path, obj_type="manifest", obj_hash=manifest_hash
            )
        tree = Tree(entries=list(existing_tree_entries.values()))
        tree_bytes = serialize_tree(tree)
        tree_hash = store.write("trees", tree_bytes)
        c = Commit(
            tree_hash=tree_hash,
            parent_hashes=[ours_hash, theirs_hash],
            author=_get_author(),
            message=merge_msg,
            timestamp=int(time.time()),
        )
        commit_bytes = serialize_commit(c)
        commit_hash = store.write("commits", commit_bytes)
        branch_name = refs.current_branch()
        refs.set_branch(branch_name, commit_hash)
        index.clear()
        merge_head_file.unlink(missing_ok=True)
        merge_msg_file.unlink(missing_ok=True)
        conflicts_file.unlink(missing_ok=True)
        typer.echo(f"[{branch_name} {commit_hash[:8]}] {merge_msg}")
        return

    # Normal merge
    if not source:
        typer.echo("error: specify a branch to merge", err=True)
        raise typer.Exit(1)

    current_branch = refs.current_branch()
    if source == current_branch:
        typer.echo("error: cannot merge a branch into itself", err=True)
        raise typer.Exit(1)

    theirs_hash = refs.get_branch(source)
    if theirs_hash is None:
        typer.echo(f"error: branch '{source}' not found", err=True)
        raise typer.Exit(1)

    if merge_head_file.exists():
        typer.echo("error: merge already in progress (use --continue or --abort)", err=True)
        raise typer.Exit(1)

    staged = index.entries()
    if staged:
        typer.echo("error: staging area is not empty — please commit first", err=True)
        raise typer.Exit(1)

    ours_hash = refs.resolve_head()
    if ours_hash is None:
        typer.echo("fatal: no commits on current branch", err=True)
        raise typer.Exit(1)

    from dit.core.merge_base import find_merge_base
    base_hash = find_merge_base(store, ours_hash, theirs_hash)

    # Fast-forward
    if base_hash == ours_hash:
        theirs_commit = deserialize_commit(store.read("commits", theirs_hash))
        ours_commit = deserialize_commit(store.read("commits", ours_hash))
        _materialize_tree(repo_root, store, theirs_commit.tree_hash, ours_commit.tree_hash)
        refs.set_branch(current_branch, theirs_hash)
        typer.echo(f"Fast-forward to {theirs_hash[:8]}.")
        return

    # Already up to date
    if base_hash == theirs_hash:
        typer.echo("Already up to date.")
        return

    # Three-way merge
    from dit.core.merge import three_way_merge
    merge_result = three_way_merge(store, base_hash, ours_hash, theirs_hash)

    if merge_result.conflicts:
        # Write conflict state
        ours_commit = deserialize_commit(store.read("commits", ours_hash))
        # Write non-conflict files to working directory
        for path, mhash in merge_result.merged_tree_entries.items():
            m_data = store.read("manifests", mhash)
            manifest = deserialize_manifest(m_data)
            from dit.core.workspace import materialize_file
            materialize_file(repo_root, path, manifest, store)
        merge_head_file.write_text(theirs_hash + "\n")
        merge_msg = message or f"Merge branch '{source}' into {current_branch}"
        merge_msg_file.write_text(merge_msg + "\n")
        conflict_data = {
            "base_commit": base_hash,
            "ours_commit": ours_hash,
            "theirs_commit": theirs_hash,
            "conflicts": [
                {
                    "file_path": c.file_path,
                    "conflict_type": c.conflict_type,
                }
                for c in merge_result.conflicts
            ],
        }
        conflicts_file.write_text(json.dumps(conflict_data, indent=2))
        typer.echo(f"CONFLICT: {len(merge_result.conflicts)} file(s) have conflicts.")
        for c in merge_result.conflicts:
            typer.echo(f"  {c.file_path} ({c.conflict_type})")
        typer.echo("\nResolve conflicts, then: dit add <files> && dit merge --continue")
        raise typer.Exit(1)

    # No conflicts — create merge commit
    merged_tree_entries = [
        TreeEntry(name=name, obj_type="manifest", obj_hash=mhash)
        for name, mhash in merge_result.merged_tree_entries.items()
    ]
    tree = Tree(entries=merged_tree_entries)
    tree_bytes = serialize_tree(tree)
    tree_hash = store.write("trees", tree_bytes)

    merge_msg = message or f"Merge branch '{source}' into {current_branch}"
    c = Commit(
        tree_hash=tree_hash,
        parent_hashes=[ours_hash, theirs_hash],
        author=_get_author(),
        message=merge_msg,
        timestamp=int(time.time()),
    )
    commit_bytes = serialize_commit(c)
    commit_hash = store.write("commits", commit_bytes)

    ours_commit = deserialize_commit(store.read("commits", ours_hash))
    _materialize_tree(repo_root, store, tree_hash, ours_commit.tree_hash)
    refs.set_branch(current_branch, commit_hash)
    typer.echo(f"Merge made: [{current_branch} {commit_hash[:8]}] {merge_msg}")
```

Note: The `import json` and `import time` are already at the top of `main.py`.

> **Known limitation:** `merge --continue` cannot stage file deletions as conflict resolution.
> If the user wants to delete a file during merge resolution, they must manually remove it
> and it will persist from HEAD's tree. This will be addressed when `dit rm` is implemented.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_merge.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/dit/cli/main.py tests/test_cli_merge.py
git commit -m "feat: dit merge command — fast-forward and three-way merge"
```

---

### Task 7: CLI Merge Command — Conflict, Continue, and Abort

**Files:**
- Modify: `tests/test_cli_merge.py` (add conflict tests)

- [ ] **Step 1: Write conflict/continue/abort tests**

Add to `tests/test_cli_merge.py`:

```python
class TestMergeConflict:
    def test_conflict_creates_state_files(self, tmp_path):
        """Both branches modify the same row differently — conflict."""
        # Both branches change the assistant response for the same user query.
        # Since query_fingerprint is derived from the user turn (same on both branches),
        # and both produce different row_hashes, this triggers a "both_modified" conflict
        # via the refresh detection path in merge_manifests.
        _init_and_commit(tmp_path)
        initial_row = {"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "original"}]}
        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "feature answer"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "feature refresh"], catch_exceptions=False)
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "main answer"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "main refresh"], catch_exceptions=False)
        result = runner.invoke(app, ["merge", "feature"])
        assert result.exit_code != 0
        assert "conflict" in result.output.lower()
        assert (tmp_path / ".dit" / "MERGE_HEAD").exists()
        assert (tmp_path / ".dit" / "MERGE_MSG").exists()
        assert (tmp_path / ".dit" / "conflicts.json").exists()

    def test_merge_abort(self, tmp_path):
        """Abort restores working directory."""
        _init_and_commit(tmp_path)
        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "feature"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "feature"], catch_exceptions=False)
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "main"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "main"], catch_exceptions=False)
        runner.invoke(app, ["merge", "feature"])
        result = runner.invoke(app, ["merge", "--abort"], catch_exceptions=False)
        assert result.exit_code == 0
        assert not (tmp_path / ".dit" / "MERGE_HEAD").exists()
        content = (tmp_path / "data.jsonl").read_text()
        assert "main" in content

    def test_merge_continue(self, tmp_path):
        """Resolve conflict and continue creates merge commit."""
        _init_and_commit(tmp_path)
        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "feature"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "feature"], catch_exceptions=False)
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "main"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "main"], catch_exceptions=False)
        runner.invoke(app, ["merge", "feature"])
        # Resolve: pick one version
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "resolved"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        result = runner.invoke(app, ["merge", "--continue"], catch_exceptions=False)
        assert result.exit_code == 0
        assert not (tmp_path / ".dit" / "MERGE_HEAD").exists()
        # Verify merge commit has two parents
        from dit.core.store import ObjectStore
        from dit.core.objects import deserialize_commit
        from dit.core.refs import RefStore
        dot = tmp_path / ".dit"
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)
        head_hash = refs.resolve_head()
        commit = deserialize_commit(store.read("commits", head_hash))
        assert len(commit.parent_hashes) == 2

    def test_abort_no_merge_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["merge", "--abort"])
        assert result.exit_code != 0

    def test_continue_no_merge_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["merge", "--continue"])
        assert result.exit_code != 0
```

- [ ] **Step 2: Run tests to verify they pass (they should since merge command is already implemented)**

Run: `uv run pytest tests/test_cli_merge.py -v`
Expected: All tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_cli_merge.py
git commit -m "test: merge conflict, continue, and abort CLI tests"
```

---

### Task 8: Cherry-pick Command

**Files:**
- Modify: `src/dit/cli/main.py`
- Create: `tests/test_cli_cherry_pick.py`

- [ ] **Step 1: Write failing tests for cherry-pick**

```python
# tests/test_cli_cherry_pick.py
import json
import os
from pathlib import Path

from typer.testing import CliRunner

from dit.cli.main import app

runner = CliRunner()


def _init_and_commit(tmp_path: Path):
    os.chdir(tmp_path)
    runner.invoke(app, ["init"], catch_exceptions=False)
    (tmp_path / "data.jsonl").write_text(
        json.dumps({"messages": [{"role": "user", "content": "hello"}]}) + "\n"
    )
    runner.invoke(app, ["add", "."], catch_exceptions=False)
    runner.invoke(app, ["commit", "-m", "initial"], catch_exceptions=False)


class TestCherryPick:
    def test_clean_cherry_pick(self, tmp_path):
        """Cherry-pick a commit that adds a new file."""
        _init_and_commit(tmp_path)
        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "feature.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "feature data"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "add feature file"], catch_exceptions=False)
        feature_hash = (tmp_path / ".dit" / "refs" / "heads" / "feature").read_text().strip()
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        result = runner.invoke(app, ["cherry-pick", feature_hash], catch_exceptions=False)
        assert result.exit_code == 0
        assert (tmp_path / "feature.jsonl").exists()
        # Verify single parent (not merge commit)
        from dit.core.store import ObjectStore
        from dit.core.objects import deserialize_commit
        from dit.core.refs import RefStore
        dot = tmp_path / ".dit"
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)
        head_hash = refs.resolve_head()
        commit = deserialize_commit(store.read("commits", head_hash))
        assert len(commit.parent_hashes) == 1
        assert "cherry-pick" in commit.message.lower()

    def test_cherry_pick_conflict(self, tmp_path):
        """Cherry-pick that conflicts creates CHERRY_PICK_HEAD."""
        _init_and_commit(tmp_path)
        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "feature"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "feature change"], catch_exceptions=False)
        feature_hash = (tmp_path / ".dit" / "refs" / "heads" / "feature").read_text().strip()
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "main"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "main change"], catch_exceptions=False)
        result = runner.invoke(app, ["cherry-pick", feature_hash])
        assert result.exit_code != 0
        assert (tmp_path / ".dit" / "CHERRY_PICK_HEAD").exists()
        assert not (tmp_path / ".dit" / "MERGE_HEAD").exists()

    def test_cherry_pick_continue(self, tmp_path):
        """Resolve cherry-pick conflict and continue."""
        _init_and_commit(tmp_path)
        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "feature"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "feature change"], catch_exceptions=False)
        feature_hash = (tmp_path / ".dit" / "refs" / "heads" / "feature").read_text().strip()
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "main"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "main change"], catch_exceptions=False)
        runner.invoke(app, ["cherry-pick", feature_hash])
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "resolved"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        result = runner.invoke(app, ["cherry-pick", "--continue"], catch_exceptions=False)
        assert result.exit_code == 0
        assert not (tmp_path / ".dit" / "CHERRY_PICK_HEAD").exists()
        from dit.core.store import ObjectStore
        from dit.core.objects import deserialize_commit
        from dit.core.refs import RefStore
        dot = tmp_path / ".dit"
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)
        head_hash = refs.resolve_head()
        commit = deserialize_commit(store.read("commits", head_hash))
        assert len(commit.parent_hashes) == 1  # Not a merge commit

    def test_cherry_pick_abort(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "feature"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "feature change"], catch_exceptions=False)
        feature_hash = (tmp_path / ".dit" / "refs" / "heads" / "feature").read_text().strip()
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "main"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "main change"], catch_exceptions=False)
        runner.invoke(app, ["cherry-pick", feature_hash])
        result = runner.invoke(app, ["cherry-pick", "--abort"], catch_exceptions=False)
        assert result.exit_code == 0
        assert not (tmp_path / ".dit" / "CHERRY_PICK_HEAD").exists()
        content = (tmp_path / "data.jsonl").read_text()
        assert "main" in content

    def test_cherry_pick_invalid_hash_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["cherry-pick", "0" * 64])
        assert result.exit_code != 0

    def test_merge_and_cherry_pick_mutually_exclusive(self, tmp_path):
        """Cannot cherry-pick while merge is in progress."""
        _init_and_commit(tmp_path)
        # Create MERGE_HEAD manually to simulate in-progress merge
        (tmp_path / ".dit" / "MERGE_HEAD").write_text("a" * 64 + "\n")
        result = runner.invoke(app, ["cherry-pick", "b" * 64])
        assert result.exit_code != 0
        assert "merge" in result.output.lower()
        (tmp_path / ".dit" / "MERGE_HEAD").unlink()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli_cherry_pick.py -v`
Expected: FAIL — `cherry-pick` command does not exist

- [ ] **Step 3: Implement cherry-pick command**

Add to `src/dit/cli/main.py`, using the `cherry_pick` function name (typer will expose it as `cherry-pick`):

```python
@app.command("cherry-pick")
def cherry_pick(
    commit_hash: str = typer.Argument("", help="Commit hash to cherry-pick"),
    continue_pick: bool = typer.Option(False, "--continue", help="Continue after resolving conflicts"),
    abort: bool = typer.Option(False, "--abort", help="Abort current cherry-pick"),
    message: str = typer.Option("", "-m", help="Override commit message"),
):
    """Apply a single commit to the current branch."""
    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    store = ObjectStore(dot / "objects")
    refs = RefStore(dot)
    index = StagingIndex(dot / "index")

    cherry_pick_head_file = dot / "CHERRY_PICK_HEAD"
    merge_head_file = dot / "MERGE_HEAD"
    merge_msg_file = dot / "MERGE_MSG"
    conflicts_file = dot / "conflicts.json"

    if abort:
        if not cherry_pick_head_file.exists():
            typer.echo("error: no cherry-pick in progress", err=True)
            raise typer.Exit(1)
        conflicts_data = json.loads(conflicts_file.read_text()) if conflicts_file.exists() else {}
        ours_hash = conflicts_data.get("ours_commit") or refs.resolve_head()
        if ours_hash:
            ours_commit = deserialize_commit(store.read("commits", ours_hash))
            _materialize_tree(repo_root, store, ours_commit.tree_hash)
        cherry_pick_head_file.unlink(missing_ok=True)
        merge_msg_file.unlink(missing_ok=True)
        conflicts_file.unlink(missing_ok=True)
        index.clear()
        typer.echo("Cherry-pick aborted.")
        return

    if continue_pick:
        if not cherry_pick_head_file.exists():
            typer.echo("error: no cherry-pick in progress", err=True)
            raise typer.Exit(1)
        staged = index.entries()
        if not staged:
            typer.echo("error: nothing staged — resolve conflicts and dit add first", err=True)
            raise typer.Exit(1)
        pick_msg = message or (merge_msg_file.read_text().strip() if merge_msg_file.exists() else "cherry-pick commit")
        head_commit_hash = refs.resolve_head()
        existing_tree_entries: dict[str, TreeEntry] = {}
        if head_commit_hash:
            commit_data = store.read("commits", head_commit_hash)
            old_commit = deserialize_commit(commit_data)
            tree_data = store.read("trees", old_commit.tree_hash)
            old_tree = deserialize_tree(tree_data)
            for e in old_tree.entries:
                existing_tree_entries[e.name] = e
        for rel_path, manifest_hash in staged.items():
            existing_tree_entries[rel_path] = TreeEntry(
                name=rel_path, obj_type="manifest", obj_hash=manifest_hash
            )
        tree = Tree(entries=list(existing_tree_entries.values()))
        tree_bytes = serialize_tree(tree)
        tree_hash = store.write("trees", tree_bytes)
        c = Commit(
            tree_hash=tree_hash,
            parent_hashes=[head_commit_hash],
            author=_get_author(),
            message=pick_msg,
            timestamp=int(time.time()),
        )
        commit_bytes = serialize_commit(c)
        new_hash = store.write("commits", commit_bytes)
        branch_name = refs.current_branch()
        refs.set_branch(branch_name, new_hash)
        index.clear()
        cherry_pick_head_file.unlink(missing_ok=True)
        merge_msg_file.unlink(missing_ok=True)
        conflicts_file.unlink(missing_ok=True)
        typer.echo(f"[{branch_name} {new_hash[:8]}] {pick_msg}")
        return

    # Normal cherry-pick
    if not commit_hash:
        typer.echo("error: specify a commit hash to cherry-pick", err=True)
        raise typer.Exit(1)

    if merge_head_file.exists():
        typer.echo("error: merge in progress — finish or abort it first", err=True)
        raise typer.Exit(1)

    if cherry_pick_head_file.exists():
        typer.echo("error: cherry-pick already in progress (use --continue or --abort)", err=True)
        raise typer.Exit(1)

    target_data = store.read("commits", commit_hash)
    if target_data is None:
        typer.echo(f"error: commit '{commit_hash[:8]}' not found", err=True)
        raise typer.Exit(1)

    target_commit = deserialize_commit(target_data)
    if not target_commit.parent_hashes:
        typer.echo("error: cannot cherry-pick a root commit", err=True)
        raise typer.Exit(1)

    base_hash = target_commit.parent_hashes[0]
    ours_hash = refs.resolve_head()

    from dit.core.merge import three_way_merge
    merge_result = three_way_merge(store, base_hash, ours_hash, commit_hash)

    if merge_result.conflicts:
        for path, mhash in merge_result.merged_tree_entries.items():
            m_data = store.read("manifests", mhash)
            manifest = deserialize_manifest(m_data)
            from dit.core.workspace import materialize_file
            materialize_file(repo_root, path, manifest, store)
        cherry_pick_head_file.write_text(commit_hash + "\n")
        pick_msg = message or f"cherry-pick: {target_commit.message}"
        merge_msg_file.write_text(pick_msg + "\n")
        conflict_data = {
            "base_commit": base_hash,
            "ours_commit": ours_hash,
            "theirs_commit": commit_hash,
            "conflicts": [
                {"file_path": c.file_path, "conflict_type": c.conflict_type}
                for c in merge_result.conflicts
            ],
        }
        conflicts_file.write_text(json.dumps(conflict_data, indent=2))
        typer.echo(f"CONFLICT: {len(merge_result.conflicts)} file(s) have conflicts.")
        for c in merge_result.conflicts:
            typer.echo(f"  {c.file_path} ({c.conflict_type})")
        typer.echo("\nResolve conflicts, then: dit add <files> && dit cherry-pick --continue")
        raise typer.Exit(1)

    # No conflicts
    merged_tree_entries = [
        TreeEntry(name=name, obj_type="manifest", obj_hash=mhash)
        for name, mhash in merge_result.merged_tree_entries.items()
    ]
    tree = Tree(entries=merged_tree_entries)
    tree_bytes = serialize_tree(tree)
    tree_hash = store.write("trees", tree_bytes)

    pick_msg = message or f"cherry-pick: {target_commit.message}"
    c = Commit(
        tree_hash=tree_hash,
        parent_hashes=[ours_hash],
        author=_get_author(),
        message=pick_msg,
        timestamp=int(time.time()),
    )
    commit_bytes = serialize_commit(c)
    new_hash = store.write("commits", commit_bytes)

    branch_name = refs.current_branch()
    ours_commit = deserialize_commit(store.read("commits", ours_hash))
    _materialize_tree(repo_root, store, tree_hash, ours_commit.tree_hash)
    refs.set_branch(branch_name, new_hash)
    typer.echo(f"[{branch_name} {new_hash[:8]}] {pick_msg}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_cherry_pick.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run full test suite**

Run: `uv run pytest --tb=short -q`
Expected: All tests pass

- [ ] **Step 6: Commit**

```bash
git add src/dit/cli/main.py tests/test_cli_cherry_pick.py
git commit -m "feat: dit cherry-pick command with conflict handling"
```

---

### Task 9: Tag CLI Command

**Files:**
- Modify: `src/dit/cli/main.py`
- Create: `tests/test_cli_tag.py`

- [ ] **Step 1: Write failing tests for tag command**

```python
# tests/test_cli_tag.py
import json
import os
from pathlib import Path

from typer.testing import CliRunner

from dit.cli.main import app

runner = CliRunner()


def _init_and_commit(tmp_path: Path):
    os.chdir(tmp_path)
    runner.invoke(app, ["init"], catch_exceptions=False)
    (tmp_path / "data.jsonl").write_text(
        json.dumps({"messages": [{"role": "user", "content": "hello"}]}) + "\n"
    )
    runner.invoke(app, ["add", "."], catch_exceptions=False)
    runner.invoke(app, ["commit", "-m", "initial"], catch_exceptions=False)


class TestTag:
    def test_create_tag(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["tag", "v1.0"], catch_exceptions=False)
        assert result.exit_code == 0
        assert (tmp_path / ".dit" / "refs" / "tags" / "v1.0").exists()

    def test_list_tags(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["tag", "v1.0"], catch_exceptions=False)
        runner.invoke(app, ["tag", "v2.0"], catch_exceptions=False)
        result = runner.invoke(app, ["tag"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "v1.0" in result.output
        assert "v2.0" in result.output

    def test_list_tags_empty(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["tag"], catch_exceptions=False)
        assert result.exit_code == 0
        assert "no tags" in result.output.lower()

    def test_delete_tag(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["tag", "v1.0"], catch_exceptions=False)
        result = runner.invoke(app, ["tag", "-d", "v1.0"], catch_exceptions=False)
        assert result.exit_code == 0
        assert not (tmp_path / ".dit" / "refs" / "tags" / "v1.0").exists()

    def test_create_existing_tag_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        runner.invoke(app, ["tag", "v1.0"], catch_exceptions=False)
        result = runner.invoke(app, ["tag", "v1.0"])
        assert result.exit_code != 0

    def test_delete_nonexistent_tag_fails(self, tmp_path):
        _init_and_commit(tmp_path)
        result = runner.invoke(app, ["tag", "-d", "nope"])
        assert result.exit_code != 0

    def test_tag_before_any_commits_fails(self, tmp_path):
        os.chdir(tmp_path)
        runner.invoke(app, ["init"], catch_exceptions=False)
        result = runner.invoke(app, ["tag", "v1.0"])
        assert result.exit_code != 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli_tag.py -v`
Expected: FAIL — `tag` command does not exist

- [ ] **Step 3: Implement tag command**

Add to `src/dit/cli/main.py`:

```python
@app.command()
def tag(
    name: Optional[str] = typer.Argument(None, help="Tag name to create"),
    delete: str = typer.Option("", "-d", help="Tag name to delete"),
):
    """List, create, or delete tags."""
    repo_root = find_repo_root()
    dot = get_dot(repo_root)
    refs = RefStore(dot)

    if delete:
        if not refs.delete_tag(delete):
            typer.echo(f"error: tag '{delete}' not found", err=True)
            raise typer.Exit(1)
        typer.echo(f"Deleted tag '{delete}'.")
        return

    if name is not None:
        if refs.get_tag(name) is not None:
            typer.echo(f"fatal: tag '{name}' already exists", err=True)
            raise typer.Exit(1)
        head_hash = refs.resolve_head()
        if head_hash is None:
            typer.echo("fatal: no commits yet", err=True)
            raise typer.Exit(1)
        refs.set_tag(name, head_hash)
        typer.echo(f"Created tag '{name}' at {head_hash[:8]}.")
        return

    # List tags
    tags = refs.list_tags()
    if not tags:
        typer.echo("No tags.")
        return
    for tname in sorted(tags.keys()):
        typer.echo(f"  {tname} {tags[tname][:8]}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_cli_tag.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/dit/cli/main.py tests/test_cli_tag.py
git commit -m "feat: dit tag command — create, list, delete"
```

---

### Task 10: Webhook Database Model and Migration

**Files:**
- Modify: `src/dit/server/models.py`
- Create: `src/dit/server/alembic/versions/002_webhooks.py`

- [ ] **Step 1: Add Webhook model to models.py**

Add to `src/dit/server/models.py`:

```python
class Webhook(Base):
    __tablename__ = "webhooks"
    __table_args__ = {"schema": "dit"}

    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("dit.repos.id"), nullable=False)
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    secret: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    events: Mapped[str] = mapped_column(String(256), nullable=False)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
```

- [ ] **Step 2: Create alembic migration**

```python
# src/dit/server/alembic/versions/002_webhooks.py
"""Add webhooks table

Revision ID: 002
Revises: 001
Create Date: 2026-04-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "webhooks",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("repo_id", sa.Integer, sa.ForeignKey("dit.repos.id"), nullable=False),
        sa.Column("url", sa.String(512), nullable=False),
        sa.Column("secret", sa.String(128), nullable=False, server_default=""),
        sa.Column("events", sa.String(256), nullable=False),
        sa.Column("active", sa.Boolean, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        schema="dit",
    )


def downgrade() -> None:
    op.drop_table("webhooks", schema="dit")
```

- [ ] **Step 3: Verify model works with existing conftest**

Run: `uv run pytest tests/server/ --tb=short -q`
Expected: All existing server tests still pass (conftest creates all tables from metadata, which now includes webhooks)

- [ ] **Step 4: Commit**

```bash
git add src/dit/server/models.py src/dit/server/alembic/versions/002_webhooks.py
git commit -m "feat: Webhook database model and migration"
```

---

### Task 11: Webhook CRUD Routes

**Files:**
- Create: `src/dit/server/routes/webhooks.py`
- Modify: `src/dit/server/app.py`
- Create: `tests/server/test_routes_webhooks.py`

- [ ] **Step 1: Write failing tests for webhook CRUD**

```python
# tests/server/test_routes_webhooks.py
import pytest


class TestWebhookRoutes:
    async def _create_repo(self, client, name="test-repo"):
        resp = await client.post("/api/v1/repos", json={"name": name})
        assert resp.status_code == 201

    async def test_create_webhook(self, client):
        await self._create_repo(client)
        resp = await client.post(
            "/api/v1/repos/test-repo/webhooks",
            json={
                "url": "https://example.com/hook",
                "secret": "mysecret",
                "events": "ref_update,branch_create",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["url"] == "https://example.com/hook"
        assert data["events"] == "ref_update,branch_create"
        assert data["active"] is True

    async def test_list_webhooks(self, client):
        await self._create_repo(client)
        await client.post(
            "/api/v1/repos/test-repo/webhooks",
            json={"url": "https://a.com/hook", "secret": "", "events": "ref_update"},
        )
        await client.post(
            "/api/v1/repos/test-repo/webhooks",
            json={"url": "https://b.com/hook", "secret": "", "events": "branch_create"},
        )
        resp = await client.get("/api/v1/repos/test-repo/webhooks")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_delete_webhook(self, client):
        await self._create_repo(client)
        create_resp = await client.post(
            "/api/v1/repos/test-repo/webhooks",
            json={"url": "https://a.com/hook", "secret": "", "events": "ref_update"},
        )
        wh_id = create_resp.json()["id"]
        resp = await client.delete(f"/api/v1/repos/test-repo/webhooks/{wh_id}")
        assert resp.status_code == 200
        list_resp = await client.get("/api/v1/repos/test-repo/webhooks")
        assert len(list_resp.json()) == 0

    async def test_delete_nonexistent_webhook(self, client):
        await self._create_repo(client)
        resp = await client.delete("/api/v1/repos/test-repo/webhooks/999")
        assert resp.status_code == 404

    async def test_webhook_repo_not_found(self, client):
        resp = await client.get("/api/v1/repos/nope/webhooks")
        assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/server/test_routes_webhooks.py -v`
Expected: FAIL — route not registered

- [ ] **Step 3: Implement webhook CRUD routes**

> **Note (I5 — shared helper):** `_get_repo` is also needed in `routes/refs.py`. To avoid
> duplication, first create `src/dit/server/routes/_helpers.py` with the shared implementation,
> then import from it in both route files. Also update `routes/refs.py` to import from the shared
> helper instead of defining its own copy (see Task 14 for the refs.py touch-up).
>
> ```python
> # src/dit/server/routes/_helpers.py
> from fastapi import HTTPException
> from sqlalchemy import select
> from sqlalchemy.ext.asyncio import AsyncSession
>
> from dit.server.models import Repo
>
>
> async def _get_repo(repo: str, session: AsyncSession) -> Repo:
>     result = await session.execute(select(Repo).where(Repo.name == repo))
>     r = result.scalar_one_or_none()
>     if r is None:
>         raise HTTPException(status_code=404, detail=f"Repo '{repo}' not found")
>     return r
> ```

```python
# src/dit/server/routes/webhooks.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import Repo, Webhook
from dit.server.routes._helpers import _get_repo

router = APIRouter(prefix="/api/v1/repos/{repo}", tags=["webhooks"])


class CreateWebhookRequest(BaseModel):
    url: str
    secret: str = ""
    events: str


@router.post("/webhooks", status_code=201)
async def create_webhook(
    repo: str,
    body: CreateWebhookRequest,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("admin")),
):
    r = await _get_repo(repo, session)
    wh = Webhook(repo_id=r.id, url=body.url, secret=body.secret, events=body.events)
    session.add(wh)
    await session.commit()
    await session.refresh(wh)
    return {"id": wh.id, "url": wh.url, "events": wh.events, "active": wh.active}


@router.get("/webhooks")
async def list_webhooks(
    repo: str,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("admin")),
):
    r = await _get_repo(repo, session)
    result = await session.execute(select(Webhook).where(Webhook.repo_id == r.id))
    hooks = result.scalars().all()
    return [{"id": h.id, "url": h.url, "events": h.events, "active": h.active} for h in hooks]


@router.delete("/webhooks/{webhook_id}")
async def delete_webhook(
    repo: str,
    webhook_id: int,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("admin")),
):
    r = await _get_repo(repo, session)
    result = await session.execute(
        select(Webhook).where(Webhook.repo_id == r.id, Webhook.id == webhook_id)
    )
    wh = result.scalar_one_or_none()
    if wh is None:
        raise HTTPException(status_code=404, detail="Webhook not found")
    await session.delete(wh)
    await session.commit()
    return {"status": "deleted"}
```

- [ ] **Step 4: Register router in app.py**

Add to `src/dit/server/app.py` in `create_app()`, after the tokens_router line:

```python
    from dit.server.routes.webhooks import router as webhooks_router
    application.include_router(webhooks_router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/server/test_routes_webhooks.py -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add src/dit/server/routes/_helpers.py src/dit/server/routes/webhooks.py src/dit/server/app.py tests/server/test_routes_webhooks.py
git commit -m "feat: webhook CRUD API routes"
```

---

### Task 12: Webhook Event Trigger Logic

**Files:**
- Create: `src/dit/server/webhooks.py`
- Modify: `src/dit/server/routes/refs.py` (trigger after CAS update; also import `_get_repo` from shared `_helpers.py`)
- Create: `tests/server/test_webhooks.py`

- [ ] **Step 1: Write failing tests for webhook trigger**

```python
# tests/server/test_webhooks.py
# Note: no @pytest.mark.asyncio — project uses asyncio_mode = "auto" in pyproject.toml
import hashlib
import hmac
import json
from unittest.mock import AsyncMock, patch

from dit.server.webhooks import load_webhooks, fire_webhook_payloads, WebhookEvent


class TestFireWebhooks:
    async def test_fire_sends_post(self, session):
        from dit.server.models import Repo, Webhook
        repo = Repo(name="test-repo")
        session.add(repo)
        await session.commit()
        await session.refresh(repo)

        wh = Webhook(repo_id=repo.id, url="https://example.com/hook", secret="s3cret", events="ref_update")
        session.add(wh)
        await session.commit()

        hooks = await load_webhooks(session, repo.id, WebhookEvent.REF_UPDATE)
        assert len(hooks) == 1

        with patch("dit.server.webhooks.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock()

            await fire_webhook_payloads(
                hooks=hooks,
                event=WebhookEvent.REF_UPDATE,
                payload={"ref": "heads/main", "old_hash": "a" * 64, "new_hash": "b" * 64},
            )

            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert call_args[0][0] == "https://example.com/hook"
            body = call_args[1]["content"]
            headers = call_args[1]["headers"]
            assert "X-Dit-Signature" in headers

    async def test_fire_skips_inactive(self, session):
        from dit.server.models import Repo, Webhook
        repo = Repo(name="test-repo")
        session.add(repo)
        await session.commit()
        await session.refresh(repo)

        wh = Webhook(repo_id=repo.id, url="https://example.com/hook", secret="", events="ref_update", active=False)
        session.add(wh)
        await session.commit()

        hooks = await load_webhooks(session, repo.id, WebhookEvent.REF_UPDATE)
        assert len(hooks) == 0  # inactive hook filtered out at load time

        with patch("dit.server.webhooks.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await fire_webhook_payloads(hooks=hooks, event=WebhookEvent.REF_UPDATE, payload={})
            mock_client.post.assert_not_called()

    async def test_fire_skips_non_matching_event(self, session):
        from dit.server.models import Repo, Webhook
        repo = Repo(name="test-repo")
        session.add(repo)
        await session.commit()
        await session.refresh(repo)

        wh = Webhook(repo_id=repo.id, url="https://example.com/hook", secret="", events="branch_create")
        session.add(wh)
        await session.commit()

        hooks = await load_webhooks(session, repo.id, WebhookEvent.REF_UPDATE)
        assert len(hooks) == 0  # event mismatch filtered out at load time

        with patch("dit.server.webhooks.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            await fire_webhook_payloads(hooks=hooks, event=WebhookEvent.REF_UPDATE, payload={})
            mock_client.post.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/server/test_webhooks.py -v`
Expected: FAIL — module `dit.server.webhooks` does not exist

- [ ] **Step 3: Implement webhook event trigger**

```python
# src/dit/server/webhooks.py
from __future__ import annotations

import enum
import hashlib
import hmac
import json
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.models import Webhook


class WebhookEvent(str, enum.Enum):
    REF_UPDATE = "ref_update"
    BRANCH_CREATE = "branch_create"
    BRANCH_DELETE = "branch_delete"


async def load_webhooks(
    session: AsyncSession,
    repo_id: int,
    event: WebhookEvent,
) -> list[dict]:
    """Query DB for active webhooks subscribed to `event`. Returns list of {url, secret} dicts.

    Called inside the request while the session is still open. The returned dicts are
    plain data — no ORM objects — so they are safe to hand off to fire_webhook_payloads
    after the session closes.
    """
    result = await session.execute(
        select(Webhook).where(Webhook.repo_id == repo_id, Webhook.active == True)
    )
    hooks = result.scalars().all()
    subscribed = [
        {"url": h.url, "secret": h.secret}
        for h in hooks
        if event.value in {e.strip() for e in h.events.split(",")}
    ]
    return subscribed


async def fire_webhook_payloads(
    hooks: list[dict],
    event: WebhookEvent,
    payload: dict,
) -> None:
    """Send HTTP POST to each hook URL. No DB access — safe to call fire-and-forget.

    `hooks` is the list returned by load_webhooks (plain {url, secret} dicts).
    """
    if not hooks:
        return

    payload["event"] = event.value
    payload["timestamp"] = datetime.now(timezone.utc).isoformat()
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True)

    async with httpx.AsyncClient(timeout=10.0) as client:
        for hook in hooks:
            signature = hmac.new(
                hook["secret"].encode() if hook["secret"] else b"",
                body.encode(),
                hashlib.sha256,
            ).hexdigest()
            try:
                await client.post(
                    hook["url"],
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Dit-Signature": signature,
                        "X-Dit-Event": event.value,
                    },
                )
            except Exception:
                pass  # Fire-and-forget
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/server/test_webhooks.py -v`
Expected: All tests PASS

- [ ] **Step 5: Wire webhook trigger into refs CAS update**

Modify `src/dit/server/routes/refs.py` — add the webhook fire call after successful CAS update and after new ref creation.

At the end of the `cas_update_ref` function, before the final `return`, add webhook firing for both branches (INSERT and CAS UPDATE).

**Important (C2 — session lifetime):** The `session` dependency is closed when the request ends.
`asyncio.ensure_future` schedules the coroutine to run *after* the response is returned, so the
session will already be closed by the time it executes. To avoid this, load the hook data
*synchronously within the request* using `load_webhooks`, then schedule only the HTTP-posting
function (which needs no session) as fire-and-forget.

Add to the import block:
```python
import asyncio
from dit.server.webhooks import load_webhooks, fire_webhook_payloads, WebhookEvent
```

For the INSERT branch (after `await session.commit()`):
```python
        _hooks = await load_webhooks(session, r.id, WebhookEvent.REF_UPDATE)
        asyncio.ensure_future(fire_webhook_payloads(
            hooks=_hooks,
            event=WebhookEvent.REF_UPDATE,
            payload={"repo": repo, "ref": ref_name, "old_hash": None, "new_hash": body.new},
        ))
```

For the CAS UPDATE branch (after `await session.commit()`):
```python
        _hooks = await load_webhooks(session, r.id, WebhookEvent.REF_UPDATE)
        asyncio.ensure_future(fire_webhook_payloads(
            hooks=_hooks,
            event=WebhookEvent.REF_UPDATE,
            payload={"repo": repo, "ref": ref_name, "old_hash": body.old, "new_hash": body.new},
        ))
```

`load_webhooks` is awaited inside the request (session still open). `fire_webhook_payloads`
receives only plain dicts and does no DB access, so it is safe to run after the session closes.

- [ ] **Step 6: Run all server tests**

Run: `uv run pytest tests/server/ --tb=short -q`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add src/dit/server/webhooks.py src/dit/server/routes/refs.py tests/server/test_webhooks.py
git commit -m "feat: webhook event trigger with HMAC signature"
```

---

### Task 13: Server Merge API Routes

**Files:**
- Create: `src/dit/server/routes/merge.py`
- Modify: `src/dit/server/app.py`
- Create: `tests/server/test_routes_merge.py`

- [ ] **Step 1: Write failing tests for merge API**

```python
# tests/server/test_routes_merge.py
"""Tests for server merge-preview and merge API routes."""
import json
import time

import pytest

from dit.core.objects import (
    Commit,
    Manifest,
    ManifestEntry,
    Tree,
    TreeEntry,
    serialize_commit,
    serialize_manifest,
    serialize_tree,
)
from dit.core.store import ObjectStore


async def _setup_diverged_repo(client, tmp_path):
    """Create a repo with two diverged branches on the server.

    Returns (store, base_hash, main_hash, feature_hash).

    NOTE: This is async — callers must ``await`` it.  The helper was made
    async so it can directly await ``client`` calls without resorting to
    ``asyncio.get_event_loop().run_until_complete()``, which raises
    ``RuntimeError: This event loop is already running`` inside
    pytest-asyncio tests (fix C3).
    """
    resp = await client.post("/api/v1/repos", json={"name": "test-repo"})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / "test-repo" / "objects")

    # Use consistent 64-char hex hashes for ManifestEntry.row_hash (fix C4).
    # The merge algorithm only compares hashes from manifests — it never
    # reads actual row blobs — so we do not write row objects to the store.
    BASE_ROW_HASH = "a" * 64
    MAIN_ROW_HASH = "b" * 64
    FEAT_ROW_HASH = "c" * 64

    # Create base commit
    base_row = ManifestEntry(row_hash=BASE_ROW_HASH, query_fingerprint="q1")
    base_m = Manifest(entries=[base_row])
    base_m_hash = store.write("manifests", serialize_manifest(base_m))

    base_tree = Tree(entries=[TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash=base_m_hash)])
    base_tree_hash = store.write("trees", serialize_tree(base_tree))
    base_commit = Commit(tree_hash=base_tree_hash, parent_hashes=[], author="test", message="base", timestamp=int(time.time()))
    base_hash = store.write("commits", serialize_commit(base_commit))

    # Create main commit (adds main_row)
    main_row = ManifestEntry(row_hash=MAIN_ROW_HASH, query_fingerprint="q2")
    main_m = Manifest(entries=[base_row, main_row])
    main_m_hash = store.write("manifests", serialize_manifest(main_m))

    main_tree = Tree(entries=[TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash=main_m_hash)])
    main_tree_hash = store.write("trees", serialize_tree(main_tree))
    main_commit = Commit(tree_hash=main_tree_hash, parent_hashes=[base_hash], author="test", message="main change", timestamp=int(time.time()))
    main_hash = store.write("commits", serialize_commit(main_commit))

    # Create feature commit (adds feat_row — diverges from main)
    feat_row = ManifestEntry(row_hash=FEAT_ROW_HASH, query_fingerprint="q3")
    feat_m = Manifest(entries=[base_row, feat_row])
    feat_m_hash = store.write("manifests", serialize_manifest(feat_m))

    feat_tree = Tree(entries=[TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash=feat_m_hash)])
    feat_tree_hash = store.write("trees", serialize_tree(feat_tree))
    feat_commit = Commit(tree_hash=feat_tree_hash, parent_hashes=[base_hash], author="test", message="feature change", timestamp=int(time.time()))
    feat_hash = store.write("commits", serialize_commit(feat_commit))

    return store, base_hash, main_hash, feat_hash


class TestMergePreview:
    async def test_mergeable(self, client, tmp_path):
        store, base_hash, main_hash, feat_hash = await _setup_diverged_repo(client, tmp_path)
        # Set up refs
        await client.post(
            "/api/v1/repos/test-repo/refs/heads/main",
            json={"old": None, "new": main_hash},
        )
        await client.post(
            "/api/v1/repos/test-repo/refs/heads/feature",
            json={"old": None, "new": feat_hash},
        )
        resp = await client.post(
            "/api/v1/repos/test-repo/merge-preview",
            json={"source_branch": "feature", "target_branch": "main"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["mergeable"] is True
        assert data["conflicts"] == []

    async def test_branch_not_found(self, client):
        resp = await client.post("/api/v1/repos", json={"name": "test-repo"})
        resp = await client.post(
            "/api/v1/repos/test-repo/merge-preview",
            json={"source_branch": "nope", "target_branch": "main"},
        )
        assert resp.status_code == 404


class TestMerge:
    async def test_clean_merge(self, client, tmp_path):
        store, base_hash, main_hash, feat_hash = await _setup_diverged_repo(client, tmp_path)
        await client.post(
            "/api/v1/repos/test-repo/refs/heads/main",
            json={"old": None, "new": main_hash},
        )
        await client.post(
            "/api/v1/repos/test-repo/refs/heads/feature",
            json={"old": None, "new": feat_hash},
        )
        resp = await client.post(
            "/api/v1/repos/test-repo/merge",
            json={
                "source_branch": "feature",
                "target_branch": "main",
                "message": "Merge feature into main",
                "author": "tester",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "commit_hash" in data
        assert len(data["commit_hash"]) == 64
        # Verify main ref updated
        ref_resp = await client.get("/api/v1/repos/test-repo/refs/heads/main")
        assert ref_resp.json()["target_hash"] == data["commit_hash"]

    async def test_merge_branch_not_found(self, client):
        resp = await client.post("/api/v1/repos", json={"name": "test-repo"})
        resp = await client.post(
            "/api/v1/repos/test-repo/merge",
            json={
                "source_branch": "nope",
                "target_branch": "main",
                "message": "m",
                "author": "t",
            },
        )
        assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/server/test_routes_merge.py -v`
Expected: FAIL — routes not registered

- [ ] **Step 3: Implement server merge routes**

```python
# src/dit/server/routes/merge.py
from __future__ import annotations

import time
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.auth import get_session, require_permission
from dit.server.models import Ref, Repo

router = APIRouter(prefix="/api/v1/repos/{repo}", tags=["merge"])


class MergePreviewRequest(BaseModel):
    source_branch: str
    target_branch: str


class MergeRequest(BaseModel):
    source_branch: str
    target_branch: str
    message: str
    author: str


async def _get_repo(repo: str, session: AsyncSession) -> Repo:
    result = await session.execute(select(Repo).where(Repo.name == repo))
    r = result.scalar_one_or_none()
    if r is None:
        raise HTTPException(status_code=404, detail=f"Repo '{repo}' not found")
    return r


def _get_store(request: Request, repo_name: str):
    from dit.core.store import ObjectStore
    data_dir: Path = request.app.state.data_dir
    return ObjectStore(data_dir / "repos" / repo_name / "objects")


async def _resolve_branch(session: AsyncSession, repo_id: int, branch: str) -> str:
    ref_name = f"heads/{branch}"
    result = await session.execute(
        select(Ref).where(Ref.repo_id == repo_id, Ref.name == ref_name)
    )
    ref = result.scalar_one_or_none()
    if ref is None:
        raise HTTPException(status_code=404, detail=f"Branch '{branch}' not found")
    return ref.target_hash


@router.post("/merge-preview")
async def merge_preview(
    repo: str,
    body: MergePreviewRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("read")),
):
    r = await _get_repo(repo, session)
    store = _get_store(request, repo)

    source_hash = await _resolve_branch(session, r.id, body.source_branch)
    target_hash = await _resolve_branch(session, r.id, body.target_branch)

    from dit.core.merge_base import find_merge_base
    from dit.core.merge import three_way_merge

    base_hash = find_merge_base(store, target_hash, source_hash)
    merge_result = three_way_merge(store, base_hash, target_hash, source_hash)

    return {
        "mergeable": len(merge_result.conflicts) == 0,
        "merge_base": base_hash,
        "conflicts": [
            {"file_path": c.file_path, "conflict_type": c.conflict_type}
            for c in merge_result.conflicts
        ],
    }


@router.post("/merge")
async def merge(
    repo: str,
    body: MergeRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("push")),
):
    from dit.core.merge_base import find_merge_base
    from dit.core.merge import three_way_merge
    from dit.core.objects import (
        Commit,
        Tree,
        TreeEntry,
        serialize_commit,
        serialize_tree,
    )

    r = await _get_repo(repo, session)
    store = _get_store(request, repo)

    source_hash = await _resolve_branch(session, r.id, body.source_branch)
    target_hash = await _resolve_branch(session, r.id, body.target_branch)

    base_hash = find_merge_base(store, target_hash, source_hash)

    # Fast-forward check
    if base_hash == target_hash:
        # Update target ref to source
        target_ref_name = f"heads/{body.target_branch}"
        result = await session.execute(
            select(Ref).where(Ref.repo_id == r.id, Ref.name == target_ref_name)
        )
        ref = result.scalar_one_or_none()
        if ref is None:
            raise HTTPException(status_code=404, detail="Target branch ref not found")
        ref.target_hash = source_hash
        await session.commit()
        # TODO (fix I4): fire ref_update webhook here, same pattern as the
        # non-ff path below.  Per spec §11.4, all ref updates must trigger
        # ref_update — use load_webhooks + fire_webhook_payloads established
        # by Task 12 once that module is available.
        return {"commit_hash": source_hash, "fast_forward": True}

    merge_result = three_way_merge(store, base_hash, target_hash, source_hash)

    if merge_result.conflicts:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Merge conflicts — cannot auto-merge",
                "conflicts": [
                    {"file_path": c.file_path, "conflict_type": c.conflict_type}
                    for c in merge_result.conflicts
                ],
            },
        )

    # Create merge commit
    tree_entries = [
        TreeEntry(name=name, obj_type="manifest", obj_hash=mhash)
        for name, mhash in merge_result.merged_tree_entries.items()
    ]
    tree = Tree(entries=tree_entries)
    tree_bytes = serialize_tree(tree)
    tree_hash = store.write("trees", tree_bytes)

    commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=[target_hash, source_hash],
        author=body.author,
        message=body.message,
        timestamp=int(time.time()),
    )
    commit_bytes = serialize_commit(commit)
    commit_hash = store.write("commits", commit_bytes)

    # CAS update target branch — atomic UPDATE WHERE to avoid read-then-write
    # race (fix I3, mirrors the pattern in routes/refs.py cas_update_ref).
    target_ref_name = f"heads/{body.target_branch}"
    cas_result = await session.execute(
        update(Ref)
        .where(Ref.repo_id == r.id, Ref.name == target_ref_name, Ref.target_hash == target_hash)
        .values(target_hash=commit_hash)
        .returning(Ref.id)
    )
    if cas_result.scalar_one_or_none() is None:
        # Either the ref doesn't exist or it was updated concurrently.
        check = await session.execute(
            select(Ref).where(Ref.repo_id == r.id, Ref.name == target_ref_name)
        )
        if check.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Target branch ref not found")
        raise HTTPException(status_code=409, detail="Target branch was updated concurrently")
    await session.commit()

    return {"commit_hash": commit_hash, "fast_forward": False}
```

- [ ] **Step 4: Register merge router in app.py**

Add to `src/dit/server/app.py` in `create_app()`:

```python
    from dit.server.routes.merge import router as merge_router
    application.include_router(merge_router)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/server/test_routes_merge.py -v`
Expected: All tests PASS

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest --tb=short -q`
Expected: All tests pass

- [ ] **Step 7: Commit**

```bash
git add src/dit/server/routes/merge.py src/dit/server/app.py tests/server/test_routes_merge.py
git commit -m "feat: server merge-preview and merge API routes"
```

---

### Task 14: Server Tag Routes

**Files:**
- Modify: `src/dit/server/routes/refs.py`
- Modify: `tests/server/test_routes_refs.py` (add tag tests)

The existing refs router already handles `GET /refs/{ref_type}/{name}` and `POST /refs/{ref_type}/{name}` which work for both `heads/*` and `tags/*`. We only need to add a `DELETE` route for tags and ensure existing tests pass.

- [ ] **Step 1: Write tag route tests**

Add to `tests/server/test_routes_refs.py`:

```python
class TestTagRoutes:
    async def _create_repo(self, client, name="test-repo"):
        resp = await client.post("/api/v1/repos", json={"name": name})
        assert resp.status_code == 201

    async def test_create_tag(self, client):
        await self._create_repo(client)
        resp = await client.post(
            "/api/v1/repos/test-repo/refs/tags/v1.0",
            json={"old": None, "new": "a" * 64},
        )
        assert resp.status_code == 200
        assert resp.json()["target_hash"] == "a" * 64

    async def test_get_tag(self, client):
        await self._create_repo(client)
        await client.post(
            "/api/v1/repos/test-repo/refs/tags/v1.0",
            json={"old": None, "new": "a" * 64},
        )
        resp = await client.get("/api/v1/repos/test-repo/refs/tags/v1.0")
        assert resp.status_code == 200
        assert resp.json()["target_hash"] == "a" * 64

    async def test_delete_tag(self, client):
        await self._create_repo(client)
        await client.post(
            "/api/v1/repos/test-repo/refs/tags/v1.0",
            json={"old": None, "new": "a" * 64},
        )
        resp = await client.delete("/api/v1/repos/test-repo/refs/tags/v1.0")
        assert resp.status_code == 200
        get_resp = await client.get("/api/v1/repos/test-repo/refs/tags/v1.0")
        assert get_resp.status_code == 404

    async def test_delete_nonexistent_tag(self, client):
        await self._create_repo(client)
        resp = await client.delete("/api/v1/repos/test-repo/refs/tags/nope")
        assert resp.status_code == 404
```

- [ ] **Step 2: Run tests to verify they fail (DELETE route missing)**

Run: `uv run pytest tests/server/test_routes_refs.py::TestTagRoutes -v`
Expected: FAIL — DELETE method not allowed on refs endpoint

- [ ] **Step 3: Add DELETE ref route**

Add to `src/dit/server/routes/refs.py`:

```python
@router.delete("/refs/{ref_type}/{name}")
async def delete_ref(
    repo: str,
    ref_type: str,
    name: str,
    session: AsyncSession = Depends(get_session),
    _token=Depends(require_permission("admin")),
):
    r = await _get_repo(repo, session)
    ref_name = f"{ref_type}/{name}"
    result = await session.execute(
        select(Ref).where(Ref.repo_id == r.id, Ref.name == ref_name)
    )
    ref = result.scalar_one_or_none()
    if ref is None:
        raise HTTPException(status_code=404, detail=f"Ref '{ref_name}' not found")
    await session.delete(ref)
    await session.commit()
    return {"status": "deleted"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/server/test_routes_refs.py -v`
Expected: All tests PASS (including original tests)

- [ ] **Step 5: Commit**

```bash
git add src/dit/server/routes/refs.py tests/server/test_routes_refs.py
git commit -m "feat: server tag routes (create, get, delete via refs)"
```

---

### Task 15: Full Integration Test

**Files:**
- Create: `tests/test_integration_merge.py`

This task creates an end-to-end integration test that exercises the full merge workflow: init, branch, commit on both branches, merge, verify result.

- [ ] **Step 1: Write integration test**

```python
# tests/test_integration_merge.py
"""End-to-end integration test for the full merge workflow."""
import json
import os
from pathlib import Path

from typer.testing import CliRunner

from dit.cli.main import app
from dit.core.objects import deserialize_commit
from dit.core.refs import RefStore
from dit.core.store import ObjectStore

runner = CliRunner()


class TestFullMergeWorkflow:
    def test_branch_diverge_merge_resolve(self, tmp_path: Path):
        """Full workflow: init -> branch -> diverge -> merge -> resolve -> verify."""
        os.chdir(tmp_path)

        # Init and initial commit
        runner.invoke(app, ["init"], catch_exceptions=False)
        rows = [
            {"messages": [{"role": "user", "content": f"q{i}"}, {"role": "assistant", "content": f"a{i}"}]}
            for i in range(3)
        ]
        (tmp_path / "data.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "initial 3 rows"], catch_exceptions=False)

        # Create feature branch and add rows
        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "feature.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "feature q"}, {"role": "assistant", "content": "feature a"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "add feature data"], catch_exceptions=False)

        # Back to main, add different file
        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        assert not (tmp_path / "feature.jsonl").exists()
        (tmp_path / "main-extra.jsonl").write_text(
            json.dumps({"messages": [{"role": "user", "content": "main q"}, {"role": "assistant", "content": "main a"}]}) + "\n"
        )
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "add main data"], catch_exceptions=False)

        # Merge feature into main
        result = runner.invoke(app, ["merge", "feature"], catch_exceptions=False)
        assert result.exit_code == 0

        # Verify all files exist
        assert (tmp_path / "data.jsonl").exists()
        assert (tmp_path / "feature.jsonl").exists()
        assert (tmp_path / "main-extra.jsonl").exists()

        # Verify merge commit
        dot = tmp_path / ".dit"
        store = ObjectStore(dot / "objects")
        refs = RefStore(dot)
        head_hash = refs.resolve_head()
        commit = deserialize_commit(store.read("commits", head_hash))
        assert len(commit.parent_hashes) == 2
        assert "merge" in commit.message.lower()

        # Verify log shows merge
        log_result = runner.invoke(app, ["log"], catch_exceptions=False)
        assert "merge" in log_result.output.lower()

    def test_tag_on_merge_commit(self, tmp_path: Path):
        """Create a tag on a merge commit."""
        os.chdir(tmp_path)
        runner.invoke(app, ["init"], catch_exceptions=False)
        (tmp_path / "data.jsonl").write_text('{"messages":[{"role":"user","content":"hi"}]}\n')
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "initial"], catch_exceptions=False)

        runner.invoke(app, ["checkout", "-b", "feature"], catch_exceptions=False)
        (tmp_path / "new.jsonl").write_text('{"messages":[{"role":"user","content":"new"}]}\n')
        runner.invoke(app, ["add", "."], catch_exceptions=False)
        runner.invoke(app, ["commit", "-m", "feature"], catch_exceptions=False)

        runner.invoke(app, ["checkout", "main"], catch_exceptions=False)
        runner.invoke(app, ["merge", "feature"], catch_exceptions=False)

        result = runner.invoke(app, ["tag", "v1.0"], catch_exceptions=False)
        assert result.exit_code == 0

        result = runner.invoke(app, ["tag"], catch_exceptions=False)
        assert "v1.0" in result.output
```

- [ ] **Step 2: Run integration tests**

Run: `uv run pytest tests/test_integration_merge.py -v`
Expected: All tests PASS

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest --tb=short -q`
Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration_merge.py
git commit -m "test: end-to-end merge workflow integration tests"
```

---

## Summary

| Task | Description | New Files | Modified Files |
|---|---|---|---|
| 1 | RefStore extensions | `tests/test_refs.py` | `src/dit/core/refs.py` |
| 2 | merge-base algorithm | `src/dit/core/merge_base.py`, `tests/test_merge_base.py` | — |
| 3 | Three-way merge (file-level) | `src/dit/core/merge.py`, `tests/test_merge.py` | — |
| 4 | Three-way merge (row-level) | — | `src/dit/core/merge.py`, `tests/test_merge.py` |
| 5 | Branch/checkout/switch CLI | `tests/test_cli_branch.py` | `src/dit/cli/main.py` |
| 6 | Merge CLI (ff + clean 3-way) | `tests/test_cli_merge.py` | `src/dit/cli/main.py` |
| 7 | Merge CLI (conflict/continue/abort) | — | `tests/test_cli_merge.py` |
| 8 | Cherry-pick CLI | `tests/test_cli_cherry_pick.py` | `src/dit/cli/main.py` |
| 9 | Tag CLI | `tests/test_cli_tag.py` | `src/dit/cli/main.py` |
| 10 | Webhook DB model + migration | `src/dit/server/alembic/versions/002_webhooks.py` | `src/dit/server/models.py` |
| 11 | Webhook CRUD routes | `src/dit/server/routes/webhooks.py`, `tests/server/test_routes_webhooks.py` | `src/dit/server/app.py` |
| 12 | Webhook event trigger | `src/dit/server/webhooks.py`, `tests/server/test_webhooks.py` | `src/dit/server/routes/refs.py` |
| 13 | Server merge API | `src/dit/server/routes/merge.py`, `tests/server/test_routes_merge.py` | `src/dit/server/app.py` |
| 14 | Server tag routes | — | `src/dit/server/routes/refs.py`, `tests/server/test_routes_refs.py` |
| 15 | Full integration test | `tests/test_integration_merge.py` | — |
