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
    application.state.data_dir = Path(settings.data_dir)

    @application.get("/health")
    async def health():
        return {"status": "ok"}

    from dit.server.routes.repos import router as repos_router
    application.include_router(repos_router)

    from dit.server.routes.refs import router as refs_router
    application.include_router(refs_router)

    from dit.server.routes.objects import router as objects_router
    application.include_router(objects_router)

    from dit.server.routes.tokens import router as tokens_router
    application.include_router(tokens_router)

    from dit.server.routes.webhooks import router as webhooks_router
    application.include_router(webhooks_router)

    from dit.server.routes.merge import router as merge_router
    application.include_router(merge_router)

    from dit.server.routes.tree import router as tree_router
    application.include_router(tree_router)

    from dit.server.routes.manifest_api import router as manifest_router
    application.include_router(manifest_router)

    from dit.server.routes.log import router as log_router
    application.include_router(log_router)

    from dit.server.routes.diff_api import router as diff_api_router
    application.include_router(diff_api_router)

    from dit.server.routes.pulls import router as pulls_router
    application.include_router(pulls_router)

    from dit.server.routes.pr_comments import router as pr_comments_router
    application.include_router(pr_comments_router)

    from dit.server.routes.branch_protection import router as branch_protection_router
    application.include_router(branch_protection_router)

    from dit.server.routes.reviews import router as reviews_router
    application.include_router(reviews_router)

    from dit.server.routes.reviewer_rules import router as reviewer_rules_router
    application.include_router(reviewer_rules_router)

    from dit.server.routes.meta_api import router as meta_router
    application.include_router(meta_router)

    from dit.server.routes.export_api import router as export_router
    application.include_router(export_router)

    from dit.server.routes.stats_api import router as stats_router
    application.include_router(stats_router)

    from dit.server.routes.search_api import router as search_router
    application.include_router(search_router)

    from dit.server.routes.validate_api import router as validate_router
    application.include_router(validate_router)

    return application


app = create_app()
