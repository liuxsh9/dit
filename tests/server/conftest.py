import hashlib

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from dit.server.app import create_app
from dit.server.auth import get_session
from dit.server.config import ServerSettings
from dit.server.database import create_db_engine, create_session_factory
from dit.server.models import Base, Token


@pytest_asyncio.fixture
async def engine():
    eng = await create_db_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        for table in Base.metadata.tables.values():
            table.schema = None
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    # Reset schemas for other test sessions
    for table in Base.metadata.tables.values():
        table.schema = "dit"
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine: AsyncEngine):
    factory = create_session_factory(engine)
    async with factory() as s:
        yield s


ADMIN_TOKEN_RAW = "test-admin-token"


@pytest_asyncio.fixture
async def client(engine, tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    settings = ServerSettings(
        database_url="sqlite+aiosqlite:///:memory:",
        data_dir=str(data_dir),
    )
    app = create_app(settings=settings)
    factory = create_session_factory(engine)

    async def override_get_session():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session

    # Create admin token
    async with factory() as s:
        token_hash = hashlib.sha256(ADMIN_TOKEN_RAW.encode()).hexdigest()
        t = Token(token_hash=token_hash, label="test-admin", permissions="admin", role="owner")
        s.add(t)
        await s.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"Authorization": f"Bearer {ADMIN_TOKEN_RAW}"},
    ) as c:
        yield c
