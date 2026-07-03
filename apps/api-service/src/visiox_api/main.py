from fastapi import FastAPI

from visiox_common.settings import get_settings
from visiox_api.routes.tasks import router as tasks_router
from visiox_api.ws.tasks import router as task_progress_router


def create_app() -> FastAPI:
    app = FastAPI(title="Visiox API", version="0.1.0")

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
