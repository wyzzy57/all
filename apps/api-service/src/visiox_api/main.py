import inspect
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from visiox_common.settings import get_settings
from visiox_api.routes.base_models import router as base_models_router
from visiox_api.routes.dataset_samples import router as dataset_samples_router
from visiox_api.routes.datasets import router as datasets_router
from visiox_api.routes.label_projects import router as label_projects_router
from visiox_api.routes.tasks import router as tasks_router
from visiox_api.ws.tasks import router as task_progress_router
from visiox_storage.client import MinioObjectStorageClient


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    from redis import asyncio as redis

    settings = get_settings()
    app.state.redis = redis.from_url(settings.redis_url, decode_responses=True)
    app.state.object_storage = MinioObjectStorageClient(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
    )
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
    app.include_router(base_models_router)
    app.include_router(datasets_router)
    app.include_router(dataset_samples_router)
    app.include_router(label_projects_router)
    app.include_router(task_progress_router)

    return app


app = create_app()
