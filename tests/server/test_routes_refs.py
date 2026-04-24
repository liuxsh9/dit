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

    async def test_cas_atomic_concurrent(self, client):
        import asyncio
        await self._create_repo(client)
        await client.post(
            "/api/v1/repos/test-repo/refs/heads/main",
            json={"old": None, "new": "a" * 64},
        )
        results = await asyncio.gather(
            client.post(
                "/api/v1/repos/test-repo/refs/heads/main",
                json={"old": "a" * 64, "new": "b" * 64},
            ),
            client.post(
                "/api/v1/repos/test-repo/refs/heads/main",
                json={"old": "a" * 64, "new": "c" * 64},
            ),
            return_exceptions=False,
        )
        statuses = sorted([r.status_code for r in results])
        assert statuses == [200, 409], f"Expected one 200 and one 409, got {statuses}"


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


class TestBranchProtectionEnforcement:
    async def test_push_to_protected_branch_requires_pr(self, client):
        """Direct push to a branch with require_pr=True returns 403."""
        await client.post("/api/v1/repos", json={"name": "prot-pr-repo"})
        await client.post(
            "/api/v1/repos/prot-pr-repo/branch-protection",
            json={"branch_pattern": "main", "require_pr": True},
        )
        # Create initial ref (allowed)
        await client.post(
            "/api/v1/repos/prot-pr-repo/refs/heads/main",
            json={"old": None, "new": "a" * 64},
        )
        # Direct CAS update → 403
        resp = await client.post(
            "/api/v1/repos/prot-pr-repo/refs/heads/main",
            json={"old": "a" * 64, "new": "b" * 64},
        )
        assert resp.status_code == 403
        assert "pull request" in resp.json()["detail"].lower()

    async def test_push_to_unprotected_branch_allowed(self, client):
        await client.post("/api/v1/repos", json={"name": "prot-unprotected-repo"})
        await client.post(
            "/api/v1/repos/prot-unprotected-repo/branch-protection",
            json={"branch_pattern": "main", "require_pr": True},
        )
        await client.post(
            "/api/v1/repos/prot-unprotected-repo/refs/heads/feature",
            json={"old": None, "new": "a" * 64},
        )
        resp = await client.post(
            "/api/v1/repos/prot-unprotected-repo/refs/heads/feature",
            json={"old": "a" * 64, "new": "b" * 64},
        )
        assert resp.status_code == 200

    async def test_push_to_wildcard_pattern_blocked(self, client):
        await client.post("/api/v1/repos", json={"name": "prot-wildcard-repo"})
        await client.post(
            "/api/v1/repos/prot-wildcard-repo/branch-protection",
            json={"branch_pattern": "release/*", "require_pr": True},
        )
        await client.post(
            "/api/v1/repos/prot-wildcard-repo/refs/heads/release/v1",
            json={"old": None, "new": "a" * 64},
        )
        resp = await client.post(
            "/api/v1/repos/prot-wildcard-repo/refs/heads/release/v1",
            json={"old": "a" * 64, "new": "b" * 64},
        )
        assert resp.status_code == 403

    async def test_initial_branch_creation_not_blocked(self, client):
        await client.post("/api/v1/repos", json={"name": "prot-create-repo"})
        await client.post(
            "/api/v1/repos/prot-create-repo/branch-protection",
            json={"branch_pattern": "main", "require_pr": True},
        )
        resp = await client.post(
            "/api/v1/repos/prot-create-repo/refs/heads/main",
            json={"old": None, "new": "a" * 64},
        )
        assert resp.status_code == 200
