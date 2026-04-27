import hashlib

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from dit.server.models import Token


async def test_create_token(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/admin/tokens",
        json={"label": "ci-bot", "permissions": "push"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["label"] == "ci-bot"
    assert data["permissions"] == "push"
    assert data["token"].startswith("dit_")
    assert "id" in data


async def test_create_token_with_scope(client: AsyncClient) -> None:
    repo_resp = await client.post("/api/v1/repos", json={"name": "scoped-repo"})
    repo_id = repo_resp.json()["id"]
    response = await client.post(
        "/api/v1/admin/tokens",
        json={"label": "scoped-bot", "permissions": "read", "repo_scope": repo_id},
    )
    assert response.status_code == 201
    assert response.json()["permissions"] == "read"


async def test_create_reviewer_role_token(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/admin/tokens",
        json={"label": "reviewer-bot", "permissions": "read", "role": "reviewer"},
    )
    assert response.status_code == 201
    data = response.json()
    assert data["permissions"] == "read"
    assert data["role"] == "reviewer"


async def test_create_token_rejects_unknown_role(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/admin/tokens",
        json={"label": "bad-role", "permissions": "read", "role": "reviewr"},
    )
    assert response.status_code == 422


async def test_revoke_token(client: AsyncClient) -> None:
    create_resp = await client.post(
        "/api/v1/admin/tokens",
        json={"label": "to-revoke", "permissions": "push"},
    )
    token_id = create_resp.json()["id"]
    response = await client.delete(f"/api/v1/admin/tokens/{token_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == token_id
    assert data["deleted"] is True


async def test_revoke_missing_token_returns_404(client: AsyncClient) -> None:
    response = await client.delete("/api/v1/admin/tokens/99999")
    assert response.status_code == 404


async def test_token_raw_value_is_unique(client: AsyncClient) -> None:
    r1 = await client.post("/api/v1/admin/tokens", json={"label": "t1", "permissions": "push"})
    r2 = await client.post("/api/v1/admin/tokens", json={"label": "t2", "permissions": "push"})
    assert r1.json()["token"] != r2.json()["token"]
