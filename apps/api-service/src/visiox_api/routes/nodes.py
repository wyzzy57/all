from collections.abc import Generator
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.orm import Session

from visiox_api.services.node_registry import refresh_stale_nodes
from visiox_api.services.management_proxy import require_management_proxy
from visiox_common.settings import Settings, get_settings
from visiox_db.models import ComputeNode, ResourcePool
from visiox_db.session import get_session


router = APIRouter(tags=["nodes"], dependencies=[Depends(require_management_proxy)])


class NodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    resource_pool_id: str | None
    status: str
    architecture: str
    platform_kind: str
    capabilities: dict[str, Any]
    resources: dict[str, Any]
    fingerprint: dict[str, Any]
    agent_version: str
    certificate_expires_at: datetime | None
    last_seen_at: datetime | None
    created_at: datetime
    updated_at: datetime


class NodeListResponse(BaseModel):
    items: list[NodeResponse]
    total: int


class ResourcePoolResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    kind: str
    selector: dict[str, Any]
    compatibility_policy: dict[str, Any]
    enabled: bool


class ResourcePoolListResponse(BaseModel):
    items: list[ResourcePoolResponse]
    total: int


def get_node_session() -> Generator[Session]:
    yield from get_session()


def _refresh_stale(session: Session, settings: Settings) -> None:
    refresh_stale_nodes(session, datetime.now(UTC), settings.agent_offline_after_seconds)


@router.get("/nodes", response_model=NodeListResponse)
def list_nodes(
    session: Session = Depends(get_node_session),
    settings: Settings = Depends(get_settings),
) -> NodeListResponse:
    _refresh_stale(session, settings)
    nodes = list(session.scalars(select(ComputeNode).order_by(ComputeNode.created_at, ComputeNode.id)))
    return NodeListResponse(items=[NodeResponse.model_validate(node) for node in nodes], total=len(nodes))


@router.get("/nodes/{node_id}", response_model=NodeResponse)
def get_node(
    node_id: str,
    session: Session = Depends(get_node_session),
    settings: Settings = Depends(get_settings),
) -> NodeResponse:
    _refresh_stale(session, settings)
    node = session.get(ComputeNode, node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    return NodeResponse.model_validate(node)


@router.get("/resource-pools", response_model=ResourcePoolListResponse)
def list_resource_pools(session: Session = Depends(get_node_session)) -> ResourcePoolListResponse:
    pools = list(session.scalars(select(ResourcePool).order_by(ResourcePool.created_at, ResourcePool.id)))
    return ResourcePoolListResponse(
        items=[ResourcePoolResponse.model_validate(pool) for pool in pools],
        total=len(pools),
    )


@router.post("/nodes/{node_id}/drain", response_model=NodeResponse)
def drain_node(node_id: str, session: Session = Depends(get_node_session)) -> NodeResponse:
    node = session.get(ComputeNode, node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")
    node.status = "draining"
    session.commit()
    return NodeResponse.model_validate(node)
