from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


async def create_db_engine(url: str) -> AsyncEngine:
    if _is_sqlite(url):
        return create_async_engine(
            url,
            echo=False,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
    return create_async_engine(
        url,
        echo=False,
        pool_size=20,
        max_overflow=10,
        pool_timeout=30,
        pool_recycle=3600,
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
