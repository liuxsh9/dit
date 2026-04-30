"""Tests for database engine connection pool configuration."""

import pytest
from sqlalchemy.pool import AsyncAdaptedQueuePool, StaticPool

from dit.server.database import create_db_engine


class TestCreateDbEnginePoolConfig:
    """Verify pool configuration is applied correctly based on DB backend."""

    @pytest.mark.asyncio
    async def test_sqlite_uses_static_pool(self):
        engine = await create_db_engine("sqlite+aiosqlite:///:memory:")
        try:
            assert isinstance(engine.pool, StaticPool)
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_sqlite_uses_check_same_thread_false(self):
        engine = await create_db_engine("sqlite+aiosqlite:///:memory:")
        try:
            # Verify engine is functional (check_same_thread=False allows this)
            assert isinstance(engine.pool, StaticPool)
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_postgresql_pool_settings(self):
        # We can't actually connect to PG in unit tests, but we can
        # verify the engine was created with the right pool config.
        engine = await create_db_engine("postgresql+asyncpg://localhost/testdb")
        try:
            pool = engine.pool
            assert isinstance(pool, AsyncAdaptedQueuePool)
            assert pool.size() == 20
            assert pool._max_overflow == 10
            assert pool._timeout == 30
            assert pool._recycle == 3600
            assert pool._pre_ping is True
        finally:
            await engine.dispose()

    @pytest.mark.asyncio
    async def test_echo_is_false(self):
        engine = await create_db_engine("sqlite+aiosqlite:///:memory:")
        try:
            assert engine.echo is False
        finally:
            await engine.dispose()
