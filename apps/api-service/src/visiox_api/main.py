import inspect
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from visiox_common.settings import get_settings
from visiox_api.routes.agent_enrollment import router as agent_enrollment_router
from visiox_api.routes.base_models import router as base_models_router
from visiox_api.routes.dataset_samples import router as dataset_samples_router
from visiox_api.routes.datasets import router as datasets_router
from visiox_api.routes.label_projects import router as label_projects_router
from visiox_api.routes.nodes import router as nodes_router
from visiox_api.routes.pipeline_evaluation import router as pipeline_evaluation_router
from visiox_api.routes.pipeline_inference import router as pipeline_inference_router
from visiox_api.routes.pipelines import router as pipelines_router
from visiox_api.routes.services import router as services_router
from visiox_api.routes.tasks import router as tasks_router
from visiox_api.routes.trained_models import router as trained_models_router
from visiox_api.routes.training_observability import router as training_observability_router
from visiox_api.routes.training_jobs import router as training_jobs_router
from visiox_api.services.training_observability import TrainingObservabilityService
from visiox_api.seed_base_models import seed_yolo26_base_models
from visiox_api.ws.agents import router as agent_gateway_router
from visiox_api.ws.tasks import router as task_progress_router
from visiox_db.session import create_session_factory
from visiox_storage.client import MinioObjectStorageClient


logger = logging.getLogger(__name__)


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
    if settings.seed_base_models_on_startup:
        try:
            session_factory = create_session_factory()
            with session_factory() as session:
                seed_yolo26_base_models(session, storage=app.state.object_storage)
        except Exception:
            logger.exception("Failed to seed YOLO26 base models")
    try:
        yield
    finally:
        close_result = app.state.redis.aclose()
        if inspect.isawaitable(close_result):
            await close_result


def create_app() -> FastAPI:
    app = FastAPI(title="Visiox API", version="0.1.0", lifespan=lifespan)
    app.state.training_observability_service = TrainingObservabilityService(get_settings())
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

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
    app.include_router(agent_enrollment_router)
    app.include_router(agent_gateway_router)
    app.include_router(nodes_router)
    app.include_router(base_models_router)
    app.include_router(datasets_router)
    app.include_router(dataset_samples_router)
    app.include_router(label_projects_router)
    app.include_router(pipelines_router)
    app.include_router(pipeline_evaluation_router)
    app.include_router(pipeline_inference_router)
    app.include_router(services_router)
    app.include_router(training_jobs_router)
    app.include_router(training_observability_router)
    app.include_router(trained_models_router)
    app.include_router(task_progress_router)

    return app


app = create_app()
