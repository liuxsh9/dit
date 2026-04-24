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
