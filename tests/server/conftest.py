import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from dit.server.database import create_db_engine, create_session_factory
from dit.server.models import Base


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
        table.schema = "datahub"
    await eng.dispose()


@pytest_asyncio.fixture
async def session(engine: AsyncEngine):
    factory = create_session_factory(engine)
    async with factory() as s:
        yield s
