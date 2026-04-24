import pytest


async def _create_repo(client, name: str = "review-repo") -> str:
    resp = await client.post("/api/v1/repos", json={"name": name})
    assert resp.status_code == 201
    return name


class TestSubmitReview:
    async def test_submit_approval(self, client):
        repo = await _create_repo(client, "submit-approval-repo")
        resp = await client.post(
            f"/api/v1/repos/{repo}/pulls/1/reviews",
            json={"status": "approved"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "approved"
        assert data["pull_request_id"] == 1
        assert data["token_id"] is not None
        assert data["created_at"] is not None

    async def test_submit_changes_requested(self, client):
        repo = await _create_repo(client, "submit-changes-repo")
        resp = await client.post(
            f"/api/v1/repos/{repo}/pulls/2/reviews",
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

    async def test_review_permission(self, client):
        # admin token (level 50) passes reviewer requirement (level 20)
        repo = await _create_repo(client, "perm-review-repo")
        resp = await client.post(
            f"/api/v1/repos/{repo}/pulls/5/reviews",
            json={"status": "approved"},
        )
        assert resp.status_code == 201


class TestListReviews:
    async def test_list_reviews(self, client):
        repo = await _create_repo(client, "list-reviews-repo")
        await client.post(f"/api/v1/repos/{repo}/pulls/10/reviews", json={"status": "approved"})
        resp = await client.get(f"/api/v1/repos/{repo}/pulls/10/reviews")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["status"] == "approved"
        assert data[0]["pull_request_id"] == 10


class TestUpsertReview:
    async def test_upsert_review(self, client):
        # Second review from same token replaces first
        repo = await _create_repo(client, "upsert-review-repo")
        resp1 = await client.post(
            f"/api/v1/repos/{repo}/pulls/7/reviews",
            json={"status": "approved"},
        )
        assert resp1.status_code == 201
        approval_id = resp1.json()["id"]

        resp2 = await client.post(
            f"/api/v1/repos/{repo}/pulls/7/reviews",
            json={"status": "changes_requested"},
        )
        assert resp2.status_code == 201
        assert resp2.json()["id"] == approval_id
        assert resp2.json()["status"] == "changes_requested"

        list_resp = await client.get(f"/api/v1/repos/{repo}/pulls/7/reviews")
        assert len(list_resp.json()) == 1
        assert list_resp.json()[0]["status"] == "changes_requested"


class TestRepoNotFound:
    async def test_repo_not_found(self, client):
        resp = await client.post(
            "/api/v1/repos/nonexistent-repo/pulls/1/reviews",
            json={"status": "approved"},
        )
        assert resp.status_code == 404
