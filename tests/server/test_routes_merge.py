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
    """Create a repo with two diverged branches on the server."""
    resp = await client.post("/api/v1/repos", json={"name": "test-repo"})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / "test-repo" / "objects")

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
