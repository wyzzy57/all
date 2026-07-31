import inspect
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from visiox_common.settings import get_settings
from visiox_api.routes.auth import router as auth_router
from visiox_api.routes.account import router as account_router
from visiox_api.routes.admin_authorization import router as admin_authorization_router
from visiox_api.routes.admin_groups import router as admin_groups_router
from visiox_api.routes.admin_users import router as admin_users_router
from visiox_api.routes.agent_enrollment import router as agent_enrollment_router
from visiox_api.routes.base_models import router as base_models_router
from visiox_api.routes.dataset_samples import router as dataset_samples_router
from visiox_api.routes.datasets import router as datasets_router
from visiox_api.routes.edge_ssh import router as edge_ssh_router
from visiox_api.routes.framework_capabilities import (
    router as framework_capabilities_router,
)
from visiox_api.routes.label_projects import router as label_projects_router
from visiox_api.routes.label_webhooks import router as label_webhooks_router
from visiox_api.routes.llm import router as llm_router
from visiox_api.routes.llm_datasets import router as llm_datasets_router
from visiox_api.routes.log_streams import router as log_streams_router
from visiox_api.routes.nodes import router as nodes_router
from visiox_api.routes.pipeline_evaluation import router as pipeline_evaluation_router
from visiox_api.routes.pipeline_inference import router as pipeline_inference_router
from visiox_api.routes.pipelines import router as pipelines_router
from visiox_api.routes.resource_sharing import router as resource_sharing_router
from visiox_api.routes.services import router as services_router
from visiox_api.routes.statistics import StatisticsCache, router as statistics_router
from visiox_api.routes.tasks import router as tasks_router
from visiox_api.routes.trained_models import router as trained_models_router
from visiox_api.routes.training_observability import (
    router as training_observability_router,
)
from visiox_api.routes.training_jobs import router as training_jobs_router
from visiox_api.services.training_observability import TrainingObservabilityService
from visiox_api.services.agent_identity import ensure_agent_ca
from visiox_api.services.bootstrap_admin import (
    BootstrapAdminIdentityConflictError,
    bootstrap_default_admin,
)
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
    if not settings.is_local_environment:
        settings.read_management_proxy_auth_token()
    if settings.agent_gateway_enabled:
        ensure_agent_ca(settings)
    if (
        settings.is_local_environment
        and not settings.bootstrap_admin_password_file.is_file()
    ):
        logger.warning(
            "Bootstrap admin username=%s admin_created=false",
            settings.bootstrap_admin_username,
        )
    else:
        try:
            session_factory = create_session_factory()
            with session_factory() as session:
                bootstrap_result = bootstrap_default_admin(session, settings)
        except BootstrapAdminIdentityConflictError:
            raise
        except ValueError:
            if not settings.is_local_environment:
                raise
            logger.warning(
                "Bootstrap admin username=%s admin_created=false",
                settings.bootstrap_admin_username,
            )
        else:
            logger.info(
                "Bootstrap admin username=%s admin_created=%s",
                settings.bootstrap_admin_username,
                bootstrap_result.admin_created,
            )
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
    app.state.training_observability_service = TrainingObservabilityService(
        get_settings()
    )
    app.state.statistics_cache = StatisticsCache()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=True,
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
                "postgres": "configured",
                "redis": "configured",
                "minio": "configured",
                "registry": "configured",
                "label_studio": "configured",
            },
        }

    app.include_router(auth_router)
    app.include_router(account_router)
    app.include_router(admin_users_router)
    app.include_router(admin_groups_router)
    app.include_router(admin_authorization_router)
    app.include_router(tasks_router)
    app.include_router(agent_enrollment_router)
    app.include_router(agent_gateway_router)
    app.include_router(nodes_router)
    app.include_router(base_models_router)
    app.include_router(llm_datasets_router)
    app.include_router(datasets_router)
    app.include_router(edge_ssh_router)
    app.include_router(framework_capabilities_router)
    app.include_router(dataset_samples_router)
    app.include_router(label_projects_router)
    app.include_router(label_webhooks_router)
    app.include_router(llm_router)
    app.include_router(log_streams_router)
    app.include_router(pipelines_router)
    app.include_router(resource_sharing_router)
    app.include_router(pipeline_evaluation_router)
    app.include_router(pipeline_inference_router)
    app.include_router(services_router)
    app.include_router(training_jobs_router)
    app.include_router(training_observability_router)
    app.include_router(statistics_router)
    app.include_router(trained_models_router)
    app.include_router(task_progress_router)

    return app


app = create_app()
