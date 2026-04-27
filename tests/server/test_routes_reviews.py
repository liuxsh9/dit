import pytest

from dit.core.objects import Commit, Manifest, ManifestEntry, serialize_commit, serialize_manifest
from dit.core.store import ObjectStore
from dit.core.tree_builder import build_nested_tree


async def _create_repo(client, name: str = "review-repo") -> str:
    resp = await client.post("/api/v1/repos", json={"name": name})
    assert resp.status_code == 201
    return name


async def _create_review_pr(client, tmp_path, repo: str) -> int:
    await _create_repo(client, repo)
    store = ObjectStore(tmp_path / "data" / "repos" / repo / "objects")

    row = ManifestEntry(row_hash="a" * 64, query_fingerprint="q1")
    manifest_hash = store.write("manifests", serialize_manifest(Manifest(entries=[row])))
    tree_hash = build_nested_tree(store, {"data.jsonl": ("manifest", manifest_hash)})
    commit_hash = store.write(
        "commits",
        serialize_commit(
            Commit(
                tree_hash=tree_hash,
                parent_hashes=[],
                author="tester",
                message="init",
                timestamp=1000,
            )
        ),
    )

    await client.post(f"/api/v1/repos/{repo}/refs/heads/main", json={"old": None, "new": commit_hash})
    await client.post(f"/api/v1/repos/{repo}/refs/heads/feature", json={"old": None, "new": commit_hash})
    pr_resp = await client.post(
        f"/api/v1/repos/{repo}/pulls",
        json={"title": "Review PR", "source_branch": "feature", "target_branch": "main", "author": "tester"},
    )
    assert pr_resp.status_code == 201
    return pr_resp.json()["pull_request_id"]


class TestSubmitReview:
    async def test_submit_approval(self, client, tmp_path):
        repo = "submit-approval-repo"
        pr_id = await _create_review_pr(client, tmp_path, repo)
        resp = await client.post(
            f"/api/v1/repos/{repo}/pulls/{pr_id}/reviews",
            json={"status": "approved"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "approved"
        assert data["pull_request_id"] == pr_id
        assert data["token_id"] is not None
        assert data["created_at"] is not None

    async def test_submit_changes_requested(self, client, tmp_path):
        repo = "submit-changes-repo"
        pr_id = await _create_review_pr(client, tmp_path, repo)
        resp = await client.post(
            f"/api/v1/repos/{repo}/pulls/{pr_id}/reviews",
            json={"status": "changes_requested"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "changes_requested"

    async def test_invalid_status_rejected(self, client):
        repo = await _create_repo(client, "invalid-status-repo")
        resp = await client.post(
            f"/api/v1/repos/{repo}/pulls/1/reviews",
            json={"status": "rejected"},
        )
        assert resp.status_code == 422

    async def test_review_permission(self, client, tmp_path):
        # admin token (level 50) passes reviewer requirement (level 20)
        repo = "perm-review-repo"
        pr_id = await _create_review_pr(client, tmp_path, repo)
        resp = await client.post(
            f"/api/v1/repos/{repo}/pulls/{pr_id}/reviews",
            json={"status": "approved"},
        )
        assert resp.status_code == 201


class TestListReviews:
    async def test_list_reviews(self, client, tmp_path):
        repo = "list-reviews-repo"
        pr_id = await _create_review_pr(client, tmp_path, repo)
        await client.post(f"/api/v1/repos/{repo}/pulls/{pr_id}/reviews", json={"status": "approved"})
        resp = await client.get(f"/api/v1/repos/{repo}/pulls/{pr_id}/reviews")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["status"] == "approved"
        assert data[0]["pull_request_id"] == pr_id

    async def test_reviews_are_scoped_by_repo(self, client, tmp_path):
        repo_a = "review-scope-a"
        repo_b = "review-scope-b"
        pr_a = await _create_review_pr(client, tmp_path, repo_a)
        pr_b = await _create_review_pr(client, tmp_path, repo_b)

        await client.post(f"/api/v1/repos/{repo_a}/pulls/{pr_a}/reviews", json={"status": "approved"})

        resp_a = await client.get(f"/api/v1/repos/{repo_a}/pulls/{pr_a}/reviews")
        resp_b = await client.get(f"/api/v1/repos/{repo_b}/pulls/{pr_b}/reviews")

        assert resp_a.status_code == 200
        assert len(resp_a.json()) == 1
        assert resp_b.status_code == 200
        assert resp_b.json() == []


class TestUpsertReview:
    async def test_upsert_review(self, client, tmp_path):
        # Second review from same token replaces first
        repo = "upsert-review-repo"
        pr_id = await _create_review_pr(client, tmp_path, repo)
        resp1 = await client.post(
            f"/api/v1/repos/{repo}/pulls/{pr_id}/reviews",
            json={"status": "approved"},
        )
        assert resp1.status_code == 201
        approval_id = resp1.json()["id"]

        resp2 = await client.post(
            f"/api/v1/repos/{repo}/pulls/{pr_id}/reviews",
            json={"status": "changes_requested"},
        )
        assert resp2.status_code == 201
        assert resp2.json()["id"] == approval_id
        assert resp2.json()["status"] == "changes_requested"

        list_resp = await client.get(f"/api/v1/repos/{repo}/pulls/{pr_id}/reviews")
        assert len(list_resp.json()) == 1
        assert list_resp.json()[0]["status"] == "changes_requested"


class TestRepoNotFound:
    async def test_repo_not_found(self, client):
        resp = await client.post(
            "/api/v1/repos/nonexistent-repo/pulls/1/reviews",
            json={"status": "approved"},
        )
        assert resp.status_code == 404
