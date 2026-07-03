from fastapi import FastAPI

from visiox_common.settings import get_settings


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

    return app


app = create_app()
