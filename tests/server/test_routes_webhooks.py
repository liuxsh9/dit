import pytest


class TestWebhookRoutes:
    async def _create_repo(self, client, name="test-repo"):
        resp = await client.post("/api/v1/repos", json={"name": name})
        assert resp.status_code == 201

    async def test_create_webhook(self, client):
        await self._create_repo(client)
        resp = await client.post(
            "/api/v1/repos/test-repo/webhooks",
            json={
                "url": "https://example.com/hook",
                "secret": "mysecret",
                "events": "ref_update,branch_create",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["url"] == "https://example.com/hook"
        assert data["events"] == "ref_update,branch_create"
        assert data["active"] is True

    async def test_list_webhooks(self, client):
        await self._create_repo(client)
        await client.post(
            "/api/v1/repos/test-repo/webhooks",
            json={"url": "https://a.com/hook", "secret": "", "events": "ref_update"},
        )
        await client.post(
            "/api/v1/repos/test-repo/webhooks",
            json={"url": "https://b.com/hook", "secret": "", "events": "branch_create"},
        )
        resp = await client.get("/api/v1/repos/test-repo/webhooks")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    async def test_delete_webhook(self, client):
        await self._create_repo(client)
        create_resp = await client.post(
            "/api/v1/repos/test-repo/webhooks",
            json={"url": "https://a.com/hook", "secret": "", "events": "ref_update"},
        )
        wh_id = create_resp.json()["id"]
        resp = await client.delete(f"/api/v1/repos/test-repo/webhooks/{wh_id}")
        assert resp.status_code == 200
        list_resp = await client.get("/api/v1/repos/test-repo/webhooks")
        assert len(list_resp.json()) == 0

    async def test_delete_nonexistent_webhook(self, client):
        await self._create_repo(client)
        resp = await client.delete("/api/v1/repos/test-repo/webhooks/999")
        assert resp.status_code == 404

    async def test_webhook_repo_not_found(self, client):
        resp = await client.get("/api/v1/repos/nope/webhooks")
        assert resp.status_code == 404


class TestWebhookDeprecationHeaders:
    async def _create_repo(self, client, name="depr-repo"):
        await client.post("/api/v1/repos", json={"name": name})

    async def test_create_webhook_has_deprecation_header(self, client):
        await self._create_repo(client, "depr-create-repo")
        resp = await client.post(
            "/api/v1/repos/depr-create-repo/webhooks",
            json={"url": "https://example.com/hook", "secret": "", "events": "ref_update"},
        )
        assert resp.status_code == 201
        assert "Deprecation" in resp.headers
        assert "Sunset" in resp.headers

    async def test_list_webhooks_has_deprecation_header(self, client):
        await self._create_repo(client, "depr-list-repo")
        resp = await client.get("/api/v1/repos/depr-list-repo/webhooks")
        assert resp.status_code == 200
        assert "Deprecation" in resp.headers

    async def test_delete_webhook_has_deprecation_header(self, client):
        await self._create_repo(client, "depr-del-repo")
        create_resp = await client.post(
            "/api/v1/repos/depr-del-repo/webhooks",
            json={"url": "https://example.com/hook", "secret": "", "events": "ref_update"},
        )
        wh_id = create_resp.json()["id"]
        resp = await client.delete(f"/api/v1/repos/depr-del-repo/webhooks/{wh_id}")
        assert resp.status_code == 200
        assert "Deprecation" in resp.headers

    async def test_webhooks_still_functional(self, client):
        await self._create_repo(client, "depr-func-repo")
        create_resp = await client.post(
            "/api/v1/repos/depr-func-repo/webhooks",
            json={"url": "https://example.com/hook", "secret": "s", "events": "ref_update"},
        )
        assert create_resp.status_code == 201
        assert create_resp.json()["url"] == "https://example.com/hook"
        list_resp = await client.get("/api/v1/repos/depr-func-repo/webhooks")
        assert len(list_resp.json()) == 1
