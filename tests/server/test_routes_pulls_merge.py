import time

from dit.core.store import ObjectStore
from dit.core.objects import (
    Commit, Manifest, ManifestEntry, serialize_commit, serialize_manifest, deserialize_commit,
)
from dit.core.tree_builder import build_nested_tree


async def _setup_merge_test_repo(client, tmp_path, diverged=True):
    resp = await client.post("/api/v1/repos", json={"name": "merge-test"})
    assert resp.status_code == 201
    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / "merge-test" / "objects")

    row_a = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
    row_b = ManifestEntry(row_hash="b" * 64, query_fingerprint="q2")
    row_c = ManifestEntry(row_hash="c" * 64, query_fingerprint="q3")

    m_base = Manifest(entries=[row_a])
    m_base_hash = store.write("manifests", serialize_manifest(m_base))
    tree_base = build_nested_tree(store, {"data.jsonl": ("manifest", m_base_hash)})
    c_base = Commit(tree_hash=tree_base, parent_hashes=[], author="test", message="base", timestamp=int(time.time()))
    h_base = store.write("commits", serialize_commit(c_base))

    if diverged:
        m_main = Manifest(entries=[row_a, row_b])
        m_main_hash = store.write("manifests", serialize_manifest(m_main))
        tree_main = build_nested_tree(store, {"data.jsonl": ("manifest", m_main_hash)})
        c_main = Commit(tree_hash=tree_main, parent_hashes=[h_base], author="test", message="main commit", timestamp=int(time.time()))
        h_main = store.write("commits", serialize_commit(c_main))
    else:
        h_main = h_base

    m_feat = Manifest(entries=[row_a, row_c])
    m_feat_hash = store.write("manifests", serialize_manifest(m_feat))
    tree_feat = build_nested_tree(store, {"data.jsonl": ("manifest", m_feat_hash)})
    c_feat = Commit(tree_hash=tree_feat, parent_hashes=[h_base], author="test", message="feat commit", timestamp=int(time.time()))
    h_feat = store.write("commits", serialize_commit(c_feat))

    await client.post("/api/v1/repos/merge-test/refs/heads/main", json={"old": None, "new": h_main})
    await client.post("/api/v1/repos/merge-test/refs/heads/feature", json={"old": None, "new": h_feat})

    pr_resp = await client.post(
        "/api/v1/repos/merge-test/pulls",
        json={"title": "Merge test PR", "source_branch": "feature", "target_branch": "main", "author": "tester"},
    )
    assert pr_resp.status_code == 201
    return store, h_base, h_main, h_feat, pr_resp.json()


class TestPRMergeThreeWay:
    async def test_three_way_merge_creates_commit(self, client, tmp_path):
        store, h_base, h_main, h_feat, pr_data = await _setup_merge_test_repo(client, tmp_path, diverged=True)
        pr_id = pr_data["pull_request_id"]
        resp = await client.post(
            f"/api/v1/repos/merge-test/pulls/{pr_id}/merge",
            json={"message": "Merge feature", "author": "merger"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "merged"
        assert data["merge_commit"] is not None
        assert data["fast_forward"] is False
        assert len(data["merge_commit"]) == 64
        merge_data = store.read("commits", data["merge_commit"])
        merge_commit = deserialize_commit(merge_data)
        assert len(merge_commit.parent_hashes) == 2
        assert h_main in merge_commit.parent_hashes
        assert h_feat in merge_commit.parent_hashes
        ref_resp = await client.get("/api/v1/repos/merge-test/refs/heads/main")
        assert ref_resp.json()["target_hash"] == data["merge_commit"]


class TestPRMergeFastForward:
    async def test_fast_forward_merge(self, client, tmp_path):
        store, h_base, h_main, h_feat, pr_data = await _setup_merge_test_repo(client, tmp_path, diverged=False)
        pr_id = pr_data["pull_request_id"]
        resp = await client.post(
            f"/api/v1/repos/merge-test/pulls/{pr_id}/merge",
            json={"message": "FF merge", "author": "merger"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["fast_forward"] is True
        assert data["status"] == "merged"
        ref_resp = await client.get("/api/v1/repos/merge-test/refs/heads/main")
        assert ref_resp.json()["target_hash"] == h_feat


class TestPRMergeConflict:
    async def test_merge_with_conflict(self, client, tmp_path):
        await client.post("/api/v1/repos", json={"name": "conflict-repo"})
        data_dir = tmp_path / "data"
        store = ObjectStore(data_dir / "repos" / "conflict-repo" / "objects")

        row_base = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
        m_base = Manifest(entries=[row_base])
        m_base_hash = store.write("manifests", serialize_manifest(m_base))
        tree_base = build_nested_tree(store, {"data.jsonl": ("manifest", m_base_hash)})
        c_base = Commit(tree_hash=tree_base, parent_hashes=[], author="test", message="base", timestamp=1000)
        h_base = store.write("commits", serialize_commit(c_base))

        row_main = ManifestEntry(row_hash="b" * 64, query_fingerprint="q2")
        m_main = Manifest(entries=[row_main])
        m_main_hash = store.write("manifests", serialize_manifest(m_main))
        tree_main = build_nested_tree(store, {"other.jsonl": ("manifest", m_main_hash)})
        c_main = Commit(tree_hash=tree_main, parent_hashes=[h_base], author="test", message="main", timestamp=2000)
        h_main = store.write("commits", serialize_commit(c_main))

        row_feat = ManifestEntry(row_hash="c" * 64, query_fingerprint="q3")
        m_feat = Manifest(entries=[row_base, row_feat])
        m_feat_hash = store.write("manifests", serialize_manifest(m_feat))
        tree_feat = build_nested_tree(store, {"data.jsonl": ("manifest", m_feat_hash)})
        c_feat = Commit(tree_hash=tree_feat, parent_hashes=[h_base], author="test", message="feat", timestamp=2000)
        h_feat = store.write("commits", serialize_commit(c_feat))

        await client.post("/api/v1/repos/conflict-repo/refs/heads/main", json={"old": None, "new": h_main})
        await client.post("/api/v1/repos/conflict-repo/refs/heads/feature", json={"old": None, "new": h_feat})

        pr_resp = await client.post(
            "/api/v1/repos/conflict-repo/pulls",
            json={"title": "Conflict PR", "source_branch": "feature", "target_branch": "main", "author": "tester"},
        )
        pr_id = pr_resp.json()["pull_request_id"]

        merge_resp = await client.post(
            f"/api/v1/repos/conflict-repo/pulls/{pr_id}/merge",
            json={"message": "try merge", "author": "merger"},
        )
        assert merge_resp.status_code == 409
        detail = merge_resp.json()["detail"]
        assert "conflicts" in detail or "conflict" in str(detail).lower()


class TestPRMergeEdgeCases:
    async def test_merge_already_merged_pr(self, client, tmp_path):
        store, h_base, h_main, h_feat, pr_data = await _setup_merge_test_repo(client, tmp_path, diverged=True)
        pr_id = pr_data["pull_request_id"]
        resp1 = await client.post(f"/api/v1/repos/merge-test/pulls/{pr_id}/merge", json={"message": "merge", "author": "merger"})
        assert resp1.status_code == 200
        resp2 = await client.post(f"/api/v1/repos/merge-test/pulls/{pr_id}/merge", json={"message": "merge again", "author": "merger"})
        assert resp2.status_code == 400

    async def test_merge_closed_pr(self, client, tmp_path):
        store, h_base, h_main, h_feat, pr_data = await _setup_merge_test_repo(client, tmp_path, diverged=True)
        pr_id = pr_data["pull_request_id"]
        await client.patch(f"/api/v1/repos/merge-test/pulls/{pr_id}", json={"status": "closed"})
        resp = await client.post(f"/api/v1/repos/merge-test/pulls/{pr_id}/merge", json={"message": "merge", "author": "merger"})
        assert resp.status_code == 400
