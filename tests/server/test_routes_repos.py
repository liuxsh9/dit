import pytest


class TestReposRoutes:
    async def test_create_repo(self, client):
        resp = await client.post("/api/v1/repos", json={"name": "my-repo"})
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "my-repo"
        assert "id" in data

    async def test_create_duplicate_repo(self, client):
        await client.post("/api/v1/repos", json={"name": "dup-repo"})
        resp = await client.post("/api/v1/repos", json={"name": "dup-repo"})
        assert resp.status_code == 409

    async def test_list_repos(self, client):
        await client.post("/api/v1/repos", json={"name": "repo-a"})
        await client.post("/api/v1/repos", json={"name": "repo-b"})
        resp = await client.get("/api/v1/repos")
        assert resp.status_code == 200
        names = [r["name"] for r in resp.json()]
        assert "repo-a" in names
        assert "repo-b" in names

    async def test_list_repos_empty(self, client):
        resp = await client.get("/api/v1/repos")
        assert resp.status_code == 200
        assert resp.json() == []
