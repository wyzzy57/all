from collections.abc import Generator
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from visiox_db.models import DeploymentService, TrainedModel, TrainingPipeline
from visiox_db.session import get_session
from visiox_api.routes.pipeline_inference import (
    PipelinePredictor,
    PipelinePredictResponse,
    get_pipeline_inference_storage,
    get_pipeline_predictor,
    predict_pipeline_image,
)
from visiox_storage.client import ObjectStorageClient


router = APIRouter(prefix="/services", tags=["services"])


class ServiceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    pipeline_id: str
    trained_model_id: str | None = None
    model_name: str = Field(min_length=1, max_length=160)
    model_weight: str = Field(min_length=1, max_length=160)
    environment: str = Field(min_length=1, max_length=120)
    instance_name: str = Field(min_length=1, max_length=160, pattern=r"^[\w\u4e00-\u9fff-]+$")
    resource_summary: str = Field(default="", max_length=255)
    config: dict[str, Any] = Field(default_factory=dict)


class ServiceUpdateRequest(BaseModel):
    status: Literal["running", "stopped"]


class ServiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    pipeline_id: str
    trained_model_id: str | None
    model_name: str
    model_weight: str
    environment: str
    instance_count: int
    instance_name: str
    resource_summary: str
    status: str
    endpoint: str
    calls: int
    config: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class ServiceListResponse(BaseModel):
    items: list[ServiceResponse]
    total: int
    limit: int
    offset: int


def get_service_session() -> Generator[Session]:
    yield from get_session()


@router.post("", response_model=ServiceResponse, status_code=status.HTTP_201_CREATED)
def create_service(request: ServiceCreateRequest, session: Session = Depends(get_service_session)) -> DeploymentService:
    pipeline = session.get(TrainingPipeline, request.pipeline_id)
    if pipeline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pipeline not found")
    if request.trained_model_id:
        trained_model = session.get(TrainedModel, request.trained_model_id)
        if trained_model is None or trained_model.pipeline_id != pipeline.id or trained_model.status != "ready":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Trained model is not ready for this pipeline")

    service = DeploymentService(
        name=request.name.strip(),
        pipeline_id=pipeline.id,
        trained_model_id=request.trained_model_id,
        model_name=request.model_name,
        model_weight=request.model_weight,
        environment=request.environment,
        instance_count=1,
        instance_name=request.instance_name.strip(),
        resource_summary=request.resource_summary,
        status="running",
        endpoint="pending",
        config=request.config,
    )
    session.add(service)
    try:
        session.flush()
        service.endpoint = f"/services/{service.id}/predict/image"
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Service name already exists") from exc
    session.refresh(service)
    return service


@router.get("", response_model=ServiceListResponse)
def list_services(
    status_filter: str | None = Query(default=None, alias="status"),
    pipeline_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    session: Session = Depends(get_service_session),
) -> ServiceListResponse:
    filters = []
    if status_filter:
        filters.append(DeploymentService.status == status_filter)
    if pipeline_id:
        filters.append(DeploymentService.pipeline_id == pipeline_id)
    count_query = select(func.count()).select_from(DeploymentService)
    list_query = select(DeploymentService).order_by(DeploymentService.created_at.desc(), DeploymentService.id.desc())
    if filters:
        count_query = count_query.where(*filters)
        list_query = list_query.where(*filters)
    total = session.scalar(count_query) or 0
    items = session.scalars(list_query.limit(limit).offset(offset)).all()
    return ServiceListResponse(items=list(items), total=total, limit=limit, offset=offset)


@router.get("/{service_id}", response_model=ServiceResponse)
def get_service(service_id: str, session: Session = Depends(get_service_session)) -> DeploymentService:
    service = session.get(DeploymentService, service_id)
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    return service


@router.patch("/{service_id}", response_model=ServiceResponse)
def update_service(
    service_id: str,
    request: ServiceUpdateRequest,
    session: Session = Depends(get_service_session),
) -> DeploymentService:
    service = session.get(DeploymentService, service_id)
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    service.status = request.status
    session.add(service)
    session.commit()
    session.refresh(service)
    return service


@router.post("/{service_id}/predict/image", response_model=PipelinePredictResponse)
async def predict_service_image(
    service_id: str,
    file: UploadFile = File(...),
    session: Session = Depends(get_service_session),
    storage: ObjectStorageClient = Depends(get_pipeline_inference_storage),
    predictor: PipelinePredictor = Depends(get_pipeline_predictor),
) -> PipelinePredictResponse:
    service = session.get(DeploymentService, service_id)
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    if service.status != "running":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Service is not running")
    result = await predict_pipeline_image(
        pipeline_id=service.pipeline_id,
        file=file,
        model_weight=service.model_weight,
        environment=service.environment,
        session=session,
        storage=storage,
        predictor=predictor,
    )
    service.calls += 1
    session.add(service)
    session.commit()
    return result


@router.delete("/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_service(service_id: str, session: Session = Depends(get_service_session)) -> Response:
    service = session.get(DeploymentService, service_id)
    if service is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Service not found")
    session.delete(service)
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
