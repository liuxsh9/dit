from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from dit.server.config import ServerSettings
from dit.server.database import create_db_engine, create_session_factory
from dit.server.auth import get_session


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = app.state.settings
    engine = await create_db_engine(settings.database_url)
    session_factory = create_session_factory(engine)

    async def override_get_session():
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = override_get_session
    app.state.engine = engine
    app.state.data_dir = Path(settings.data_dir)
    yield
    await engine.dispose()


def create_app(settings: ServerSettings | None = None) -> FastAPI:
    if settings is None:
        settings = ServerSettings()

    application = FastAPI(title="DataHub", version="0.1.0", lifespan=lifespan)
    application.state.settings = settings

    @application.get("/health")
    async def health():
        return {"status": "ok"}

    from dit.server.routes.repos import router as repos_router
    application.include_router(repos_router)

    return application


app = create_app()
