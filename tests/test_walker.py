"""Tests for walk_commit_objects and is_ancestor."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from dit.core.objects import (
    Commit,
    Manifest,
    ManifestEntry,
    Tree,
    TreeEntry,
    serialize_blob,
    serialize_commit,
    serialize_manifest,
    serialize_tree,
    object_hash,
)
from dit.core.store import ObjectStore
from dit.core.walker import is_ancestor, walk_commit_objects


def _make_store(tmp_path: Path) -> ObjectStore:
    return ObjectStore(tmp_path / "objects")


def _store_manifest(store: ObjectStore, row_hashes: list[str]) -> str:
    entries = [ManifestEntry(row_hash=rh, query_fingerprint=None) for rh in row_hashes]
    manifest = Manifest(entries=entries)
    data = serialize_manifest(manifest)
    return store.write("manifests", data)


def _store_row(store: ObjectStore, content: str) -> str:
    data = content.encode("utf-8")
    return store.write("rows", data)


def _store_tree(store: ObjectStore, entries: list[TreeEntry]) -> str:
    tree = Tree(entries=entries)
    data = serialize_tree(tree)
    return store.write("trees", data)


def _store_commit(
    store: ObjectStore,
    tree_hash: str,
    parent_hashes: list[str],
    message: str = "test",
) -> str:
    commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=parent_hashes,
        author="tester",
        message=message,
        timestamp=int(time.time()),
    )
    data = serialize_commit(commit)
    return store.write("commits", data)


def test_walk_single_commit(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    row_hash = _store_row(store, '{"a":1}')
    manifest_hash = _store_manifest(store, [row_hash])
    tree_hash = _store_tree(store, [TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash=manifest_hash)])
    commit_hash = _store_commit(store, tree_hash, [])

    result = walk_commit_objects(store, commit_hash)
    assert commit_hash in result["commits"]
    assert tree_hash in result["trees"]
    assert manifest_hash in result["manifests"]
    assert row_hash in result["rows"]


def test_walk_two_commits_shares_history(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    row1 = _store_row(store, '{"a":1}')
    mhash1 = _store_manifest(store, [row1])
    thash1 = _store_tree(store, [TreeEntry("data.jsonl", "manifest", mhash1)])
    commit1 = _store_commit(store, thash1, [])

    row2 = _store_row(store, '{"b":2}')
    mhash2 = _store_manifest(store, [row2])
    thash2 = _store_tree(store, [TreeEntry("data.jsonl", "manifest", mhash2)])
    commit2 = _store_commit(store, thash2, [commit1])

    result = walk_commit_objects(store, commit2)
    assert result["commits"] == {commit1, commit2}
    assert thash1 in result["trees"] and thash2 in result["trees"]
    assert mhash1 in result["manifests"] and mhash2 in result["manifests"]
    assert row1 in result["rows"] and row2 in result["rows"]


def test_walk_deduplicates_shared_objects(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    row = _store_row(store, '{"shared":true}')
    mhash = _store_manifest(store, [row])
    thash1 = _store_tree(store, [TreeEntry("x.jsonl", "manifest", mhash)])
    thash2 = _store_tree(store, [TreeEntry("x.jsonl", "manifest", mhash)])
    c1 = _store_commit(store, thash1, [])
    c2 = _store_commit(store, thash2, [c1])

    result = walk_commit_objects(store, c2)
    assert len(result["manifests"]) == 1
    assert len(result["rows"]) == 1


def test_is_ancestor_linear_chain(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    thash = _store_tree(store, [])
    ca = _store_commit(store, thash, [])
    cb = _store_commit(store, thash, [ca])
    cc = _store_commit(store, thash, [cb])
    assert is_ancestor(store, ca, cc) is True
    assert is_ancestor(store, cb, cc) is True
    assert is_ancestor(store, ca, cb) is True


def test_is_ancestor_same_hash(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    thash = _store_tree(store, [])
    ca = _store_commit(store, thash, [])
    assert is_ancestor(store, ca, ca) is True


def test_is_ancestor_non_ancestor(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    thash = _store_tree(store, [])
    ca = _store_commit(store, thash, [])
    cb = _store_commit(store, thash, [ca], message="branch-b")
    cc = _store_commit(store, thash, [ca], message="branch-c")
    assert is_ancestor(store, cb, cc) is False
    assert is_ancestor(store, cc, cb) is False


def test_is_ancestor_descendant_not_ancestor(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    thash = _store_tree(store, [])
    ca = _store_commit(store, thash, [])
    cb = _store_commit(store, thash, [ca])
    assert is_ancestor(store, cb, ca) is False


class TestWalkSidecars:
    def test_sidecar_hash_collected(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        row_hash = _store_row(store, '{"text":"hello world example"}')
        manifest_hash = _store_manifest(store, [row_hash])
        sidecar_hash = "sc" * 32
        tree_hash = _store_tree(store, [
            TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash=manifest_hash, sidecar_hash=sidecar_hash)
        ])
        commit_hash = _store_commit(store, tree_hash, [])

        result = walk_commit_objects(store, commit_hash)
        assert "sidecars" in result
        assert sidecar_hash in result["sidecars"]

    def test_no_sidecar_hash_not_in_result(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        row_hash = _store_row(store, '{"a":1}')
        manifest_hash = _store_manifest(store, [row_hash])
        tree_hash = _store_tree(store, [
            TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash=manifest_hash)
        ])
        commit_hash = _store_commit(store, tree_hash, [])

        result = walk_commit_objects(store, commit_hash)
        assert result.get("sidecars", set()) == set()

    def test_multiple_sidecar_hashes_all_collected(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        sc1 = "11" * 32
        sc2 = "22" * 32
        m1 = _store_manifest(store, [_store_row(store, '{"x":1}')])
        m2 = _store_manifest(store, [_store_row(store, '{"y":2}')])
        tree_hash = _store_tree(store, [
            TreeEntry(name="a.jsonl", obj_type="manifest", obj_hash=m1, sidecar_hash=sc1),
            TreeEntry(name="b.jsonl", obj_type="manifest", obj_hash=m2, sidecar_hash=sc2),
        ])
        commit_hash = _store_commit(store, tree_hash, [])

        result = walk_commit_objects(store, commit_hash)
        assert sc1 in result["sidecars"]
        assert sc2 in result["sidecars"]

    def test_sidecars_key_present_in_result_dict(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        tree_hash = _store_tree(store, [])
        commit_hash = _store_commit(store, tree_hash, [])

        result = walk_commit_objects(store, commit_hash)
        assert "sidecars" in result


# ── Blob helpers ──────────────────────────────────────────────────────────────

def _write_row(store: ObjectStore, content: str) -> str:
    """Write a raw row, return its hash."""
    data = content.encode("utf-8")
    return store.write("rows", data)


def _make_blob(store: ObjectStore, content: str) -> str:
    """Serialize and write a blob object, return its hash."""
    blob_bytes = serialize_blob(content.encode("utf-8"))
    return store.write("blobs", blob_bytes)


def _make_commit_with_blob(
    store: ObjectStore,
    blob_hash: str,
    parent_hashes: list[str] | None = None,
) -> str:
    """Create a commit whose tree contains a single blob entry."""
    tree_entries = [TreeEntry(name="file.bin", obj_type="blob", obj_hash=blob_hash)]
    tree = Tree(entries=tree_entries)
    tree_hash = store.write("trees", serialize_tree(tree))
    commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=parent_hashes or [],
        author="tester",
        message="blob commit",
        timestamp=int(time.time()),
    )
    return store.write("commits", serialize_commit(commit))


def _make_commit_with_manifest_and_blob(
    store: ObjectStore,
    rows: list[str],
    blob_content: str,
    parent_hashes: list[str] | None = None,
) -> str:
    """Create a commit whose tree has both a manifest entry and a blob entry."""
    row_hashes = [_write_row(store, r) for r in rows]
    manifest = Manifest(entries=[ManifestEntry(row_hash=rh, query_fingerprint=None) for rh in row_hashes])
    manifest_hash = store.write("manifests", serialize_manifest(manifest))
    blob_hash = _make_blob(store, blob_content)
    tree_entries = [
        TreeEntry(name="data.jsonl", obj_type="manifest", obj_hash=manifest_hash),
        TreeEntry(name="attachment.bin", obj_type="blob", obj_hash=blob_hash),
    ]
    tree = Tree(entries=tree_entries)
    tree_hash = store.write("trees", serialize_tree(tree))
    commit = Commit(
        tree_hash=tree_hash,
        parent_hashes=parent_hashes or [],
        author="tester",
        message="mixed commit",
        timestamp=int(time.time()),
    )
    return store.write("commits", serialize_commit(commit)), blob_hash


# ── Blob tests ────────────────────────────────────────────────────────────────

class TestWalkBlobs:
    def test_walk_includes_blobs_key(self, tmp_path: Path) -> None:
        """Result dict must always contain a 'blobs' key."""
        store = _make_store(tmp_path)
        tree_hash = _store_tree(store, [])
        commit_hash = _store_commit(store, tree_hash, [])

        result = walk_commit_objects(store, commit_hash)
        assert "blobs" in result

    def test_walk_collects_blob_hashes(self, tmp_path: Path) -> None:
        """Blob hashes from tree entries must appear in result['blobs']."""
        store = _make_store(tmp_path)
        blob_hash = _make_blob(store, "binary content here")
        commit_hash = _make_commit_with_blob(store, blob_hash)

        result = walk_commit_objects(store, commit_hash)
        assert blob_hash in result["blobs"]

    def test_walk_mixed_tree_collects_all_types(self, tmp_path: Path) -> None:
        """Commit with both manifest and blob entries collects all types."""
        store = _make_store(tmp_path)
        commit_hash, blob_hash = _make_commit_with_manifest_and_blob(
            store, ['{"x":1}'], "raw data"
        )

        result = walk_commit_objects(store, commit_hash)
        assert "blobs" in result
        assert blob_hash in result["blobs"]
        assert len(result["manifests"]) == 1
        assert len(result["rows"]) == 1

    def test_walk_no_blobs_returns_empty_set(self, tmp_path: Path) -> None:
        """Commit with only manifests has empty blobs set."""
        store = _make_store(tmp_path)
        row_hash = _store_row(store, '{"a":1}')
        manifest_hash = _store_manifest(store, [row_hash])
        tree_hash = _store_tree(store, [TreeEntry("d.jsonl", "manifest", manifest_hash)])
        commit_hash = _store_commit(store, tree_hash, [])

        result = walk_commit_objects(store, commit_hash)
        assert result["blobs"] == set()

    def test_walk_multiple_commits_collects_all_blobs(self, tmp_path: Path) -> None:
        """Walking a commit chain collects blobs from all commits."""
        store = _make_store(tmp_path)
        blob1_hash = _make_blob(store, "first blob")
        commit1_hash = _make_commit_with_blob(store, blob1_hash)

        blob2_hash = _make_blob(store, "second blob")
        commit2_hash = _make_commit_with_blob(store, blob2_hash, parent_hashes=[commit1_hash])

        result = walk_commit_objects(store, commit2_hash)
        assert blob1_hash in result["blobs"]
        assert blob2_hash in result["blobs"]


class TestIterativeWalker:
    def test_deep_commit_chain_no_recursion_error(self, tmp_path: Path) -> None:
        """A commit chain deeper than Python's recursion limit must not crash."""
        store = _make_store(tmp_path)
        tree_hash = _store_tree(store, [])
        prev = _store_commit(store, tree_hash, [])
        for i in range(1500):
            prev = _store_commit(store, tree_hash, [prev])
        # Should not raise RecursionError
        result = walk_commit_objects(store, prev)
        assert len(result["commits"]) == 1501  # 1 initial + 1500 loop

    def test_is_ancestor_deep_chain(self, tmp_path: Path) -> None:
        """is_ancestor must work on chains deeper than recursion limit."""
        store = _make_store(tmp_path)
        tree_hash = _store_tree(store, [])
        root = _store_commit(store, tree_hash, [])
        prev = root
        for i in range(1500):
            prev = _store_commit(store, tree_hash, [prev])
        assert is_ancestor(store, root, prev) is True
        assert is_ancestor(store, prev, root) is False
