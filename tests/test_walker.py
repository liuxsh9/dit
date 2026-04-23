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
    serialize_commit,
    serialize_manifest,
    serialize_tree,
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
