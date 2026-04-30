from dit.server.models import Token


async def _create_repo(client, name: str = "rr-test-repo") -> str:
    resp = await client.post("/api/v1/repos", json={"name": name})
    assert resp.status_code == 201
    return name


class TestCreateReviewerRule:
    async def test_create_reviewer_rule(self, client):
        repo = await _create_repo(client, "create-rr-repo")
        resp = await client.post(
            f"/api/v1/repos/{repo}/reviewer-rules",
            json={"pattern": "feature-impl/**"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["pattern"] == "feature-impl/**"
        assert data["reviewer_token_id"] is None
        assert data["id"] is not None

    async def test_create_reviewer_rule_with_token(self, client, session):
        repo = await _create_repo(client, "create-rr-token-repo")

        token = Token(
            token_hash="b" * 64,
            label="rr-test-token",
            permissions="read",
            role="reader",
        )
        session.add(token)
        await session.commit()
        await session.refresh(token)

        resp = await client.post(
            f"/api/v1/repos/{repo}/reviewer-rules",
            json={"pattern": "src/**", "reviewer_token_id": token.id},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["pattern"] == "src/**"
        assert data["reviewer_token_id"] == token.id


class TestListReviewerRules:
    async def test_list_reviewer_rules(self, client):
        repo = await _create_repo(client, "list-rr-repo")
        await client.post(f"/api/v1/repos/{repo}/reviewer-rules", json={"pattern": "feature-impl/**"})
        await client.post(f"/api/v1/repos/{repo}/reviewer-rules", json={"pattern": "docs/**"})

        resp = await client.get(f"/api/v1/repos/{repo}/reviewer-rules")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        patterns = {r["pattern"] for r in data}
        assert patterns == {"feature-impl/**", "docs/**"}


class TestDeleteReviewerRule:
    async def test_delete_reviewer_rule(self, client):
        repo = await _create_repo(client, "delete-rr-repo")
        create_resp = await client.post(
            f"/api/v1/repos/{repo}/reviewer-rules",
            json={"pattern": "feature-impl/**"},
        )
        rule_id = create_resp.json()["id"]

        resp = await client.delete(f"/api/v1/repos/{repo}/reviewer-rules/{rule_id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == rule_id

        list_resp = await client.get(f"/api/v1/repos/{repo}/reviewer-rules")
        assert list_resp.json() == []

    async def test_delete_nonexistent_rule(self, client):
        repo = await _create_repo(client, "del-missing-rr-repo")
        resp = await client.delete(f"/api/v1/repos/{repo}/reviewer-rules/99999")
        assert resp.status_code == 404


class TestMatchReviewerRules:
    async def test_match_reviewer_rules_for_files(self, client):
        repo = await _create_repo(client, "match-rr-repo")
        await client.post(
            f"/api/v1/repos/{repo}/reviewer-rules",
            json={"pattern": "feature-impl/**"},
        )
        await client.post(
            f"/api/v1/repos/{repo}/reviewer-rules",
            json={"pattern": "bug-fix/**"},
        )

        resp = await client.post(
            f"/api/v1/repos/{repo}/reviewer-rules/match",
            json={"file_paths": ["feature-impl/core.py", "tests/test_core.py"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["pattern"] == "feature-impl/**"

    async def test_repo_not_found(self, client):
        resp = await client.get("/api/v1/repos/nonexistent-repo/reviewer-rules")
        assert resp.status_code == 404
