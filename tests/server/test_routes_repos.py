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

    async def test_create_repo_path_traversal_rejected(self, client):
        resp = await client.post("/api/v1/repos", json={"name": "../etc/passwd"})
        assert resp.status_code == 400

    async def test_create_repo_slash_rejected(self, client):
        resp = await client.post("/api/v1/repos", json={"name": "foo/bar"})
        assert resp.status_code == 400

    async def test_create_repo_empty_name_rejected(self, client):
        resp = await client.post("/api/v1/repos", json={"name": ""})
        assert resp.status_code == 400

    async def test_create_repo_too_long_rejected(self, client):
        resp = await client.post("/api/v1/repos", json={"name": "a" * 129})
        assert resp.status_code == 400

    async def test_create_repo_valid_names_accepted(self, client):
        for name in ["my-repo", "repo_v2", "Repo.2024", "a"]:
            resp = await client.post("/api/v1/repos", json={"name": name})
            assert resp.status_code == 201, f"Expected 201 for name={name!r}, got {resp.status_code}"
