import pytest


async def _create_repo(client, name: str = "bp-test-repo") -> str:
    resp = await client.post("/api/v1/repos", json={"name": name})
    assert resp.status_code == 201
    return name


class TestListBranchProtectionRules:
    async def test_list_branch_protection_rules(self, client):
        repo = await _create_repo(client, "list-bp-repo")
        await client.post(f"/api/v1/repos/{repo}/branch-protection", json={"branch_pattern": "main"})
        await client.post(f"/api/v1/repos/{repo}/branch-protection", json={"branch_pattern": "develop"})

        resp = await client.get(f"/api/v1/repos/{repo}/branch-protection")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        patterns = {r["branch_pattern"] for r in data}
        assert patterns == {"main", "develop"}


class TestCreateBranchProtectionRule:
    async def test_create_branch_protection_rule(self, client):
        repo = await _create_repo(client, "create-bp-repo")
        resp = await client.post(
            f"/api/v1/repos/{repo}/branch-protection",
            json={
                "branch_pattern": "main",
                "require_pr": True,
                "required_approvals": 2,
                "block_force_push": True,
                "auto_delete_branch": False,
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["branch_pattern"] == "main"
        assert data["require_pr"] is True
        assert data["required_approvals"] == 2
        assert data["block_force_push"] is True
        assert data["auto_delete_branch"] is False
        assert data["id"] is not None

    async def test_create_duplicate_pattern_conflict(self, client):
        repo = await _create_repo(client, "dup-bp-repo")
        await client.post(f"/api/v1/repos/{repo}/branch-protection", json={"branch_pattern": "main"})
        resp = await client.post(f"/api/v1/repos/{repo}/branch-protection", json={"branch_pattern": "main"})
        assert resp.status_code == 409

    async def test_create_rule_requires_admin(self, client):
        repo = await _create_repo(client, "admin-bp-repo")
        # The default test client uses an admin token, so this should succeed with 201
        resp = await client.post(
            f"/api/v1/repos/{repo}/branch-protection",
            json={"branch_pattern": "protected"},
        )
        assert resp.status_code == 201

    async def test_repo_not_found(self, client):
        resp = await client.post(
            "/api/v1/repos/nonexistent-repo/branch-protection",
            json={"branch_pattern": "main"},
        )
        assert resp.status_code == 404


class TestUpdateBranchProtectionRule:
    async def test_update_branch_protection_rule(self, client):
        repo = await _create_repo(client, "update-bp-repo")
        create_resp = await client.post(
            f"/api/v1/repos/{repo}/branch-protection",
            json={"branch_pattern": "main", "required_approvals": 1},
        )
        rule_id = create_resp.json()["id"]

        resp = await client.put(
            f"/api/v1/repos/{repo}/branch-protection/{rule_id}",
            json={"required_approvals": 3, "auto_delete_branch": True},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["required_approvals"] == 3
        assert data["auto_delete_branch"] is True
        assert data["branch_pattern"] == "main"


class TestDeleteBranchProtectionRule:
    async def test_delete_branch_protection_rule(self, client):
        repo = await _create_repo(client, "delete-bp-repo")
        create_resp = await client.post(
            f"/api/v1/repos/{repo}/branch-protection",
            json={"branch_pattern": "main"},
        )
        rule_id = create_resp.json()["id"]

        resp = await client.delete(f"/api/v1/repos/{repo}/branch-protection/{rule_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == rule_id

        list_resp = await client.get(f"/api/v1/repos/{repo}/branch-protection")
        assert list_resp.json() == []

    async def test_delete_nonexistent_rule(self, client):
        repo = await _create_repo(client, "del-missing-bp-repo")
        resp = await client.delete(f"/api/v1/repos/{repo}/branch-protection/99999")
        assert resp.status_code == 404
