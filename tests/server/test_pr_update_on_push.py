import time

from dit.core.store import ObjectStore
from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    serialize_commit, serialize_manifest,
)
from dit.core.tree_builder import build_nested_tree


async def _setup_push_pr_repo(client, tmp_path):
    resp = await client.post("/api/v1/repos", json={"name": "push-pr-repo"})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / "push-pr-repo" / "objects")

    row_a = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
    row_b = ManifestEntry(row_hash="b" * 64, query_fingerprint="q2")

    m_base = Manifest(entries=[row_a])
    m_base_hash = store.write("manifests", serialize_manifest(m_base))
    tree_base = build_nested_tree(store, {"data.jsonl": ("manifest", m_base_hash)})
    c_base = Commit(
        tree_hash=tree_base, parent_hashes=[], author="test", message="base",
        timestamp=int(time.time()),
    )
    h_base = store.write("commits", serialize_commit(c_base))

    m_feat = Manifest(entries=[row_a, row_b])
    m_feat_hash = store.write("manifests", serialize_manifest(m_feat))
    tree_feat = build_nested_tree(store, {"data.jsonl": ("manifest", m_feat_hash)})
    c_feat = Commit(
        tree_hash=tree_feat, parent_hashes=[h_base], author="test", message="feat",
        timestamp=int(time.time()),
    )
    h_feat = store.write("commits", serialize_commit(c_feat))

    await client.post(
        "/api/v1/repos/push-pr-repo/refs/heads/main",
        json={"old": None, "new": h_base},
    )
    await client.post(
        "/api/v1/repos/push-pr-repo/refs/heads/feature",
        json={"old": None, "new": h_feat},
    )

    pr_resp = await client.post(
        "/api/v1/repos/push-pr-repo/pulls",
        json={
            "title": "Feature PR",
            "source_branch": "feature",
            "target_branch": "main",
            "author": "tester",
        },
    )
    assert pr_resp.status_code == 201
    pr_data = pr_resp.json()

    return store, h_base, h_feat, pr_data


class TestPRUpdateOnPush:
    async def test_push_to_source_updates_pr_stats(self, client, tmp_path):
        store, h_base, h_feat, pr_data = await _setup_push_pr_repo(client, tmp_path)
        pr_id = pr_data["pull_request_id"]
        old_added = pr_data["stats_added"]

        row_c = ManifestEntry(row_hash="c" * 64, query_fingerprint="q3")
        row_a = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
        row_b = ManifestEntry(row_hash="b" * 64, query_fingerprint="q2")
        m_new = Manifest(entries=[row_a, row_b, row_c])
        m_new_hash = store.write("manifests", serialize_manifest(m_new))
        tree_new = build_nested_tree(store, {"data.jsonl": ("manifest", m_new_hash)})
        c_new = Commit(
            tree_hash=tree_new, parent_hashes=[h_feat], author="test",
            message="more rows", timestamp=int(time.time()),
        )
        h_new = store.write("commits", serialize_commit(c_new))

        resp = await client.post(
            "/api/v1/repos/push-pr-repo/refs/heads/feature",
            json={"old": h_feat, "new": h_new},
        )
        assert resp.status_code == 200

        pr_resp = await client.get(f"/api/v1/repos/push-pr-repo/pulls/{pr_id}")
        assert pr_resp.status_code == 200
        updated = pr_resp.json()
        assert updated["source_commit"] == h_new
        assert updated["stats_added"] >= old_added

    async def test_push_to_unrelated_branch_no_update(self, client, tmp_path):
        store, h_base, h_feat, pr_data = await _setup_push_pr_repo(client, tmp_path)
        pr_id = pr_data["pull_request_id"]
        original_source = pr_data["source_commit"]

        await client.post(
            "/api/v1/repos/push-pr-repo/refs/heads/other",
            json={"old": None, "new": h_base},
        )

        pr_resp = await client.get(f"/api/v1/repos/push-pr-repo/pulls/{pr_id}")
        assert pr_resp.json()["source_commit"] == original_source

    async def test_push_to_target_updates_mergeability(self, client, tmp_path):
        store, h_base, h_feat, pr_data = await _setup_push_pr_repo(client, tmp_path)
        pr_id = pr_data["pull_request_id"]

        row_d = ManifestEntry(row_hash="d" * 64, query_fingerprint="q4")
        row_a = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
        m_main_new = Manifest(entries=[row_a, row_d])
        m_main_hash = store.write("manifests", serialize_manifest(m_main_new))
        tree_main_new = build_nested_tree(store, {"data.jsonl": ("manifest", m_main_hash)})
        c_main_new = Commit(
            tree_hash=tree_main_new, parent_hashes=[h_base], author="test",
            message="main update", timestamp=int(time.time()),
        )
        h_main_new = store.write("commits", serialize_commit(c_main_new))

        resp = await client.post(
            "/api/v1/repos/push-pr-repo/refs/heads/main",
            json={"old": h_base, "new": h_main_new},
        )
        assert resp.status_code == 200

        pr_resp = await client.get(f"/api/v1/repos/push-pr-repo/pulls/{pr_id}")
        updated = pr_resp.json()
        assert updated["target_commit"] == h_main_new
        assert updated["is_mergeable"] is not None
