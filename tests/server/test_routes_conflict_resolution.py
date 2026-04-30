
from dit.core.store import ObjectStore
from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    serialize_commit, serialize_manifest,
)
from dit.core.tree_builder import build_nested_tree


async def _setup_conflict_repo(client, tmp_path):
    resp = await client.post("/api/v1/repos", json={"name": "resolve-repo"})
    assert resp.status_code == 201
    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / "resolve-repo" / "objects")

    row_base = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
    m_base = Manifest(entries=[row_base])
    m_base_hash = store.write("manifests", serialize_manifest(m_base))
    tree_base = build_nested_tree(store, {"data.jsonl": ("manifest", m_base_hash)})
    c_base = Commit(tree_hash=tree_base, parent_hashes=[], author="test", message="base", timestamp=1000)
    h_base = store.write("commits", serialize_commit(c_base))

    row_main = ManifestEntry(row_hash="b" * 64, query_fingerprint="q1")
    m_main = Manifest(entries=[row_main])
    m_main_hash = store.write("manifests", serialize_manifest(m_main))
    tree_main = build_nested_tree(store, {"data.jsonl": ("manifest", m_main_hash)})
    c_main = Commit(tree_hash=tree_main, parent_hashes=[h_base], author="test", message="main refresh", timestamp=2000)
    h_main = store.write("commits", serialize_commit(c_main))

    row_feat = ManifestEntry(row_hash="c" * 64, query_fingerprint="q1")
    m_feat = Manifest(entries=[row_feat])
    m_feat_hash = store.write("manifests", serialize_manifest(m_feat))
    tree_feat = build_nested_tree(store, {"data.jsonl": ("manifest", m_feat_hash)})
    c_feat = Commit(tree_hash=tree_feat, parent_hashes=[h_base], author="test", message="feat refresh", timestamp=2000)
    h_feat = store.write("commits", serialize_commit(c_feat))

    await client.post("/api/v1/repos/resolve-repo/refs/heads/main", json={"old": None, "new": h_main})
    await client.post("/api/v1/repos/resolve-repo/refs/heads/feature", json={"old": None, "new": h_feat})

    pr_resp = await client.post(
        "/api/v1/repos/resolve-repo/pulls",
        json={"title": "Conflict PR", "source_branch": "feature", "target_branch": "main", "author": "tester"},
    )
    assert pr_resp.status_code == 201
    pr_data = pr_resp.json()
    assert pr_data["is_mergeable"] is False

    return store, h_base, h_main, h_feat, pr_data


class TestConflictResolution:
    async def test_resolve_choosing_theirs(self, client, tmp_path):
        store, h_base, h_main, h_feat, pr_data = await _setup_conflict_repo(client, tmp_path)
        pr_id = pr_data["pull_request_id"]
        merge_resp = await client.post(
            f"/api/v1/repos/resolve-repo/pulls/{pr_id}/merge",
            json={"message": "merge", "author": "merger"},
        )
        assert merge_resp.status_code == 409

        resp = await client.post(
            f"/api/v1/repos/resolve-repo/pulls/{pr_id}/resolve",
            json={
                "resolutions": [{"file_path": "data.jsonl", "row_hash": "c" * 64, "choice": "theirs"}],
                "message": "Resolve: pick feature version",
                "author": "resolver",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "merged"
        assert data["merge_commit"] is not None
        assert len(data["merge_commit"]) == 64

        ref_resp = await client.get("/api/v1/repos/resolve-repo/refs/heads/main")
        assert ref_resp.json()["target_hash"] == data["merge_commit"]

    async def test_resolve_choosing_ours(self, client, tmp_path):
        store, h_base, h_main, h_feat, pr_data = await _setup_conflict_repo(client, tmp_path)
        pr_id = pr_data["pull_request_id"]
        resp = await client.post(
            f"/api/v1/repos/resolve-repo/pulls/{pr_id}/resolve",
            json={
                "resolutions": [{"file_path": "data.jsonl", "row_hash": "b" * 64, "choice": "ours"}],
                "message": "Resolve: pick main version",
                "author": "resolver",
            },
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "merged"

    async def test_resolve_non_conflicting_pr_fails(self, client, tmp_path):
        resp = await client.post("/api/v1/repos", json={"name": "no-conflict"})
        data_dir = tmp_path / "data"
        store = ObjectStore(data_dir / "repos" / "no-conflict" / "objects")

        row_a = ManifestEntry(row_hash="a" * 64, query_fingerprint=None)
        m = Manifest(entries=[row_a])
        m_hash = store.write("manifests", serialize_manifest(m))
        tree_hash = build_nested_tree(store, {"data.jsonl": ("manifest", m_hash)})
        c = Commit(tree_hash=tree_hash, parent_hashes=[], author="test", message="init", timestamp=1000)
        h = store.write("commits", serialize_commit(c))

        await client.post("/api/v1/repos/no-conflict/refs/heads/main", json={"old": None, "new": h})
        await client.post("/api/v1/repos/no-conflict/refs/heads/feat", json={"old": None, "new": h})

        pr_resp = await client.post(
            "/api/v1/repos/no-conflict/pulls",
            json={"title": "Clean PR", "source_branch": "feat", "target_branch": "main", "author": "tester"},
        )
        pr_id = pr_resp.json()["pull_request_id"]

        resp = await client.post(
            f"/api/v1/repos/no-conflict/pulls/{pr_id}/resolve",
            json={"resolutions": [], "message": "resolve nothing", "author": "r"},
        )
        assert resp.status_code == 400

    async def test_resolve_already_merged_pr_fails(self, client, tmp_path):
        store, h_base, h_main, h_feat, pr_data = await _setup_conflict_repo(client, tmp_path)
        pr_id = pr_data["pull_request_id"]
        await client.post(
            f"/api/v1/repos/resolve-repo/pulls/{pr_id}/resolve",
            json={
                "resolutions": [{"file_path": "data.jsonl", "row_hash": "c" * 64, "choice": "theirs"}],
                "message": "resolve",
                "author": "r",
            },
        )
        resp = await client.post(
            f"/api/v1/repos/resolve-repo/pulls/{pr_id}/resolve",
            json={"resolutions": [], "message": "re-resolve", "author": "r"},
        )
        assert resp.status_code == 400
