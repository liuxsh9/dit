import time

from dit.core.store import ObjectStore
from dit.core.objects import (
    Commit, Manifest, ManifestEntry,
    serialize_commit, serialize_manifest,
)
from dit.core.tree_builder import build_nested_tree


async def _setup_pr_repo(client, tmp_path):
    """Create a repo with main and feature branches for PR testing."""
    resp = await client.post("/api/v1/repos", json={"name": "pr-repo"})
    assert resp.status_code == 201

    data_dir = tmp_path / "data"
    store = ObjectStore(data_dir / "repos" / "pr-repo" / "objects")

    row_a = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
    row_b = ManifestEntry(row_hash="b" * 64, query_fingerprint="q2")
    row_c = ManifestEntry(row_hash="c" * 64, query_fingerprint="q3")

    # Base commit (row_a only)
    m_base = Manifest(entries=[row_a])
    m_base_hash = store.write("manifests", serialize_manifest(m_base))
    tree_base = build_nested_tree(store, {"data.jsonl": ("manifest", m_base_hash)})
    c_base = Commit(
        tree_hash=tree_base, parent_hashes=[], author="test", message="base",
        timestamp=int(time.time()),
    )
    h_base = store.write("commits", serialize_commit(c_base))

    # Main commit (row_a + row_b)
    m_main = Manifest(entries=[row_a, row_b])
    m_main_hash = store.write("manifests", serialize_manifest(m_main))
    tree_main = build_nested_tree(store, {"data.jsonl": ("manifest", m_main_hash)})
    c_main = Commit(
        tree_hash=tree_main, parent_hashes=[h_base], author="test", message="main",
        timestamp=int(time.time()),
    )
    h_main = store.write("commits", serialize_commit(c_main))

    # Feature commit (row_a + row_c)
    m_feat = Manifest(entries=[row_a, row_c])
    m_feat_hash = store.write("manifests", serialize_manifest(m_feat))
    tree_feat = build_nested_tree(store, {"data.jsonl": ("manifest", m_feat_hash)})
    c_feat = Commit(
        tree_hash=tree_feat, parent_hashes=[h_base], author="test", message="feat",
        timestamp=int(time.time()),
    )
    h_feat = store.write("commits", serialize_commit(c_feat))

    # Create refs
    await client.post(
        "/api/v1/repos/pr-repo/refs/heads/main",
        json={"old": None, "new": h_main},
    )
    await client.post(
        "/api/v1/repos/pr-repo/refs/heads/feature",
        json={"old": None, "new": h_feat},
    )
    return store, h_base, h_main, h_feat


class TestCreatePR:
    async def test_create_pr_success(self, client, tmp_path):
        store, h_base, h_main, h_feat = await _setup_pr_repo(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/pr-repo/pulls",
            json={
                "title": "Add new training data",
                "source_branch": "feature",
                "target_branch": "main",
                "author": "zhangsan",
            },
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["title"] == "Add new training data"
        assert data["status"] == "open"
        assert data["source_ref"] == "heads/feature"
        assert data["target_ref"] == "heads/main"
        assert data["source_commit"] == h_feat
        assert data["target_commit"] == h_main
        assert data["pull_request_id"] == 1
        assert "stats_added" in data
        assert "stats_removed" in data
        assert "is_mergeable" in data

    async def test_create_pr_defaults_author_to_token_label(self, client, tmp_path):
        await _setup_pr_repo(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/pr-repo/pulls",
            json={
                "title": "Token-authored PR",
                "source_branch": "feature",
                "target_branch": "main",
            },
        )

        assert resp.status_code == 201, resp.text
        assert resp.json()["author"] == "test-admin"

    async def test_create_pr_replaces_unknown_author_with_token_label(self, client, tmp_path):
        await _setup_pr_repo(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/pr-repo/pulls",
            json={
                "title": "Token-authored PR",
                "source_branch": "feature",
                "target_branch": "main",
                "author": "unknown",
            },
        )

        assert resp.status_code == 201, resp.text
        assert resp.json()["author"] == "test-admin"

    async def test_create_pr_branch_not_found(self, client, tmp_path):
        await _setup_pr_repo(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/pr-repo/pulls",
            json={
                "title": "Bad PR",
                "source_branch": "nonexistent",
                "target_branch": "main",
                "author": "tester",
            },
        )
        assert resp.status_code == 404

    async def test_create_pr_same_branch(self, client, tmp_path):
        await _setup_pr_repo(client, tmp_path)
        resp = await client.post(
            "/api/v1/repos/pr-repo/pulls",
            json={
                "title": "Self PR",
                "source_branch": "main",
                "target_branch": "main",
                "author": "tester",
            },
        )
        assert resp.status_code == 400

    async def test_create_pr_repo_not_found(self, client):
        resp = await client.post(
            "/api/v1/repos/no-such-repo/pulls",
            json={
                "title": "Bad",
                "source_branch": "feat",
                "target_branch": "main",
                "author": "tester",
            },
        )
        assert resp.status_code == 404


class TestListPRs:
    async def test_list_prs_empty(self, client, tmp_path):
        await _setup_pr_repo(client, tmp_path)
        resp = await client.get("/api/v1/repos/pr-repo/pulls")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_list_prs_with_filter(self, client, tmp_path):
        await _setup_pr_repo(client, tmp_path)
        await client.post(
            "/api/v1/repos/pr-repo/pulls",
            json={
                "title": "Open PR",
                "source_branch": "feature",
                "target_branch": "main",
                "author": "tester",
            },
        )
        resp = await client.get("/api/v1/repos/pr-repo/pulls?status=open")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["status"] == "open"

        resp2 = await client.get("/api/v1/repos/pr-repo/pulls?status=closed")
        assert resp2.status_code == 200
        assert resp2.json() == []


class TestGetPR:
    async def test_get_pr_detail(self, client, tmp_path):
        await _setup_pr_repo(client, tmp_path)
        create_resp = await client.post(
            "/api/v1/repos/pr-repo/pulls",
            json={
                "title": "Detail PR",
                "source_branch": "feature",
                "target_branch": "main",
                "author": "tester",
            },
        )
        pr_id = create_resp.json()["pull_request_id"]
        resp = await client.get(f"/api/v1/repos/pr-repo/pulls/{pr_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["title"] == "Detail PR"
        assert data["pull_request_id"] == pr_id

    async def test_get_pr_not_found(self, client, tmp_path):
        await _setup_pr_repo(client, tmp_path)
        resp = await client.get("/api/v1/repos/pr-repo/pulls/999")
        assert resp.status_code == 404


class TestUpdatePR:
    async def test_close_pr(self, client, tmp_path):
        await _setup_pr_repo(client, tmp_path)
        create_resp = await client.post(
            "/api/v1/repos/pr-repo/pulls",
            json={
                "title": "To Close",
                "source_branch": "feature",
                "target_branch": "main",
                "author": "tester",
            },
        )
        pr_id = create_resp.json()["pull_request_id"]
        resp = await client.patch(
            f"/api/v1/repos/pr-repo/pulls/{pr_id}",
            json={"status": "closed"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "closed"

    async def test_reopen_pr(self, client, tmp_path):
        await _setup_pr_repo(client, tmp_path)
        create_resp = await client.post(
            "/api/v1/repos/pr-repo/pulls",
            json={
                "title": "To Reopen",
                "source_branch": "feature",
                "target_branch": "main",
                "author": "tester",
            },
        )
        pr_id = create_resp.json()["pull_request_id"]
        await client.patch(
            f"/api/v1/repos/pr-repo/pulls/{pr_id}",
            json={"status": "closed"},
        )
        resp = await client.patch(
            f"/api/v1/repos/pr-repo/pulls/{pr_id}",
            json={"status": "open"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "open"

    async def test_update_merged_pr_fails(self, client, tmp_path):
        """Cannot close/reopen a merged PR."""
        await _setup_pr_repo(client, tmp_path)
        create_resp = await client.post(
            "/api/v1/repos/pr-repo/pulls",
            json={
                "title": "Merged PR",
                "source_branch": "feature",
                "target_branch": "main",
                "author": "tester",
            },
        )
        pr_id = create_resp.json()["pull_request_id"]
        merge_resp = await client.post(
            f"/api/v1/repos/pr-repo/pulls/{pr_id}/merge",
            json={"message": "merge it", "author": "tester"},
        )
        assert merge_resp.status_code == 200

        resp = await client.patch(
            f"/api/v1/repos/pr-repo/pulls/{pr_id}",
            json={"status": "closed"},
        )
        assert resp.status_code == 400

    async def test_update_title(self, client, tmp_path):
        await _setup_pr_repo(client, tmp_path)
        create_resp = await client.post(
            "/api/v1/repos/pr-repo/pulls",
            json={
                "title": "Old Title",
                "source_branch": "feature",
                "target_branch": "main",
                "author": "tester",
            },
        )
        pr_id = create_resp.json()["pull_request_id"]
        resp = await client.patch(
            f"/api/v1/repos/pr-repo/pulls/{pr_id}",
            json={"title": "New Title"},
        )
        assert resp.status_code == 200
        assert resp.json()["title"] == "New Title"
