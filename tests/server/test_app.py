import pytest
from httpx import AsyncClient, ASGITransport

from dit.server.app import create_app
from dit.server.config import ServerSettings
from dit.server.database import create_db_engine, create_session_factory
from dit.server.models import Base
from dit.server.auth import get_session


@pytest.fixture
def test_settings(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return ServerSettings(
        database_url="sqlite+aiosqlite:///:memory:",
        data_dir=str(data_dir),
    )


@pytest.fixture
def test_app(test_settings):
    return create_app(settings=test_settings)


async def test_health(test_app):
    # Override the lifespan manually for testing
    engine = await create_db_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        for table in Base.metadata.tables.values():
            table.schema = None
        await conn.run_sync(Base.metadata.create_all)

    session_factory = create_session_factory(engine)

    async def override_get_session():
        async with session_factory() as session:
            yield session

    test_app.dependency_overrides[get_session] = override_get_session

    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "checks" in data

    for table in Base.metadata.tables.values():
        table.schema = "datahub"
    await engine.dispose()
