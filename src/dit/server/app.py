from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

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

    application = FastAPI(title="Dit", version="0.1.0", lifespan=lifespan)
    application.state.settings = settings
    application.state.data_dir = Path(settings.data_dir)

    @application.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    from dit.server.middleware.metrics import MetricsMiddleware
    application.add_middleware(MetricsMiddleware)

    from dit.server.middleware.logging import LoggingMiddleware
    application.add_middleware(LoggingMiddleware)

    if settings.rate_limit:
        from dit.server.middleware.rate_limit import create_limiter
        from slowapi import _rate_limit_exceeded_handler
        from slowapi.errors import RateLimitExceeded
        from slowapi.middleware import SlowAPIMiddleware

        limiter = create_limiter(settings.rate_limit)
        application.state.limiter = limiter
        application.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
        application.add_middleware(SlowAPIMiddleware)

    @application.get("/health")
    async def health():
        import time as _time
        from sqlalchemy import text
        from starlette.responses import JSONResponse

        checks = {}
        overall = "healthy"

        try:
            factory = application.dependency_overrides.get(get_session)
            if factory:
                async for session in factory():
                    start = _time.monotonic()
                    await session.execute(text("SELECT 1"))
                    latency = (_time.monotonic() - start) * 1000
                    checks["database"] = {"status": "healthy", "latency_ms": round(latency, 2)}
                    break
            else:
                checks["database"] = {"status": "healthy", "latency_ms": 0}
        except Exception as exc:
            checks["database"] = {"status": "unhealthy", "error": str(exc)}
            overall = "unhealthy"

        data_dir = getattr(application.state, "data_dir", None)
        if data_dir and data_dir.is_dir():
            checks["data_dir"] = {"status": "healthy"}
        else:
            checks["data_dir"] = {"status": "unhealthy", "error": "data directory not found"}
            overall = "unhealthy"

        status_code = 200 if overall == "healthy" else 503
        return JSONResponse(
            content={"status": overall, "checks": checks},
            status_code=status_code,
        )

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

    from dit.server.routes.blame_api import router as blame_router
    application.include_router(blame_router)

    from dit.server.routes.gc_api import router as gc_router
    application.include_router(gc_router)

    from dit.server.routes.dedup_api import router as dedup_router
    application.include_router(dedup_router)

    from dit.server.routes.fsck_api import router as fsck_router
    application.include_router(fsck_router)

    return application


app = create_app()
