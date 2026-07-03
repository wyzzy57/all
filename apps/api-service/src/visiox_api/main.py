import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from visiox_common.settings import get_settings
from visiox_api.routes.tasks import router as tasks_router
from visiox_api.ws.tasks import router as task_progress_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from redis import asyncio as redis

    app.state.redis = redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        yield
    finally:
        close_result = app.state.redis.aclose()
        if inspect.isawaitable(close_result):
            await close_result


def create_app() -> FastAPI:
    app = FastAPI(title="Visiox API", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict[str, object]:
        settings = get_settings()
        return {
            "service": "api-service",
            "status": "ok",
            "environment": settings.environment,
            "dependencies": {
                "postgres": settings.postgres_dsn,
                "redis": settings.redis_url,
                "minio": settings.minio_endpoint,
                "registry": settings.registry_url,
                "label_studio": settings.label_studio_url,
            },
        }

    app.include_router(tasks_router)
    app.include_router(task_progress_router)

    return app


app = create_app()
