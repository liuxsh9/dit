import hashlib
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient, ASGITransport

from dit.server.app import create_app
from dit.server.auth import _synthetic_admin_token, get_session
from dit.server.config import ServerSettings
from dit.server.database import create_db_engine, create_session_factory
from dit.server.models import Base, Token


SERVICE_TOKEN = "internal-service-secret-xyz"


@pytest.fixture
async def service_token_engine():
    eng = await create_db_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        for table in Base.metadata.tables.values():
            table.schema = None
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    for table in Base.metadata.tables.values():
        table.schema = "dit"
    await eng.dispose()


@pytest.fixture
async def service_client(service_token_engine, tmp_path):
    settings = ServerSettings(
        database_url="sqlite+aiosqlite:///:memory:",
        data_dir=str(tmp_path / "data"),
        service_token=SERVICE_TOKEN,
    )
    app = create_app(settings=settings)
    factory = create_session_factory(service_token_engine)

    async def override_get_session():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session

    async with factory() as s:
        t = Token(
            token_hash=hashlib.sha256(b"regular-token").hexdigest(),
            label="regular",
            permissions="admin",
        )
        s.add(t)
        await s.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestServiceTokenAuth:
    def test_synthetic_admin_token_has_admin_role(self):
        token = _synthetic_admin_token()
        assert token.label == "service-token"
        assert token.permissions == "admin"
        assert token.role == "owner"
        assert token.repo_scope is None
        assert token.expires_at is None
        assert isinstance(token.created_at, datetime)
        assert token.created_at.tzinfo == timezone.utc

    async def test_service_token_grants_admin_access(self, service_client):
        resp = await service_client.get(
            "/api/v1/repos",
            headers={"X-Service-Token": SERVICE_TOKEN},
        )
        assert resp.status_code == 200

    async def test_service_token_can_bootstrap_first_admin_token(self, service_client):
        resp = await service_client.post(
            "/api/v1/admin/tokens",
            json={"label": "bootstrap-admin", "permissions": "admin"},
            headers={"X-Service-Token": SERVICE_TOKEN},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["label"] == "bootstrap-admin"
        assert data["permissions"] == "admin"
        assert data["token"].startswith("dit_")

    async def test_service_token_wrong_secret_rejected(self, service_client):
        resp = await service_client.get(
            "/api/v1/repos",
            headers={"X-Service-Token": "wrong-secret"},
        )
        assert resp.status_code == 401

    async def test_service_token_no_header_falls_through_to_bearer(self, service_client):
        resp = await service_client.get(
            "/api/v1/repos",
            headers={"Authorization": "Bearer regular-token"},
        )
        assert resp.status_code == 200

    async def test_service_token_no_auth_rejected(self, service_client):
        resp = await service_client.get("/api/v1/repos")
        assert resp.status_code == 401

    async def test_service_token_not_configured_ignores_header(self, tmp_path):
        from dit.server.database import create_db_engine, create_session_factory
        eng = await create_db_engine("sqlite+aiosqlite:///:memory:")
        async with eng.begin() as conn:
            for table in Base.metadata.tables.values():
                table.schema = None
            await conn.run_sync(Base.metadata.create_all)
        settings = ServerSettings(
            database_url="sqlite+aiosqlite:///:memory:",
            data_dir=str(tmp_path / "data2"),
            service_token="",
        )
        app = create_app(settings=settings)
        factory = create_session_factory(eng)

        async def override():
            async with factory() as s:
                yield s

        app.dependency_overrides[get_session] = override
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.get(
                "/api/v1/repos",
                headers={"X-Service-Token": "any-value"},
            )
            assert resp.status_code == 401
        for table in Base.metadata.tables.values():
            table.schema = "dit"
        await eng.dispose()
