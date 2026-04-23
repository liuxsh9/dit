import pytest


class TestRefsRoutes:
    async def _create_repo(self, client, name="test-repo"):
        resp = await client.post("/api/v1/repos", json={"name": name})
        assert resp.status_code == 201
        return resp.json()

    async def test_create_ref(self, client):
        await self._create_repo(client)
        resp = await client.post(
            "/api/v1/repos/test-repo/refs/heads/main",
            json={"old": None, "new": "a" * 64},
        )
        assert resp.status_code == 200
        assert resp.json()["target_hash"] == "a" * 64

    async def test_get_ref(self, client):
        await self._create_repo(client)
        await client.post(
            "/api/v1/repos/test-repo/refs/heads/main",
            json={"old": None, "new": "a" * 64},
        )
        resp = await client.get("/api/v1/repos/test-repo/refs/heads/main")
        assert resp.status_code == 200
        assert resp.json()["target_hash"] == "a" * 64

    async def test_cas_update_success(self, client):
        await self._create_repo(client)
        await client.post(
            "/api/v1/repos/test-repo/refs/heads/main",
            json={"old": None, "new": "a" * 64},
        )
        resp = await client.post(
            "/api/v1/repos/test-repo/refs/heads/main",
            json={"old": "a" * 64, "new": "b" * 64},
        )
        assert resp.status_code == 200
        assert resp.json()["target_hash"] == "b" * 64

    async def test_cas_update_conflict(self, client):
        await self._create_repo(client)
        await client.post(
            "/api/v1/repos/test-repo/refs/heads/main",
            json={"old": None, "new": "a" * 64},
        )
        resp = await client.post(
            "/api/v1/repos/test-repo/refs/heads/main",
            json={"old": "c" * 64, "new": "d" * 64},
        )
        assert resp.status_code == 409

    async def test_list_refs(self, client):
        await self._create_repo(client)
        await client.post(
            "/api/v1/repos/test-repo/refs/heads/main",
            json={"old": None, "new": "a" * 64},
        )
        await client.post(
            "/api/v1/repos/test-repo/refs/heads/dev",
            json={"old": None, "new": "b" * 64},
        )
        resp = await client.get("/api/v1/repos/test-repo/refs")
        assert resp.status_code == 200
        names = [r["name"] for r in resp.json()]
        assert "heads/dev" in names
        assert "heads/main" in names

    async def test_ref_not_found(self, client):
        await self._create_repo(client)
        resp = await client.get("/api/v1/repos/test-repo/refs/heads/nope")
        assert resp.status_code == 404

    async def test_repo_not_found(self, client):
        resp = await client.get("/api/v1/repos/no-repo/refs")
        assert resp.status_code == 404


class TestTagRoutes:
    async def _create_repo(self, client, name="test-repo"):
        resp = await client.post("/api/v1/repos", json={"name": name})
        assert resp.status_code == 201

    async def test_create_tag(self, client):
        await self._create_repo(client)
        resp = await client.post(
            "/api/v1/repos/test-repo/refs/tags/v1.0",
            json={"old": None, "new": "a" * 64},
        )
        assert resp.status_code == 200
        assert resp.json()["target_hash"] == "a" * 64

    async def test_get_tag(self, client):
        await self._create_repo(client)
        await client.post(
            "/api/v1/repos/test-repo/refs/tags/v1.0",
            json={"old": None, "new": "a" * 64},
        )
        resp = await client.get("/api/v1/repos/test-repo/refs/tags/v1.0")
        assert resp.status_code == 200
        assert resp.json()["target_hash"] == "a" * 64

    async def test_delete_tag(self, client):
        await self._create_repo(client)
        await client.post(
            "/api/v1/repos/test-repo/refs/tags/v1.0",
            json={"old": None, "new": "a" * 64},
        )
        resp = await client.delete("/api/v1/repos/test-repo/refs/tags/v1.0")
        assert resp.status_code == 200
        get_resp = await client.get("/api/v1/repos/test-repo/refs/tags/v1.0")
        assert get_resp.status_code == 404

    async def test_delete_nonexistent_tag(self, client):
        await self._create_repo(client)
        resp = await client.delete("/api/v1/repos/test-repo/refs/tags/nope")
        assert resp.status_code == 404
