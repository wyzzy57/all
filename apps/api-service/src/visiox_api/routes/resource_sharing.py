from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from visiox_api.dependencies.auth import get_current_user
from visiox_api.dependencies.database import get_db_session
from visiox_api.services.audit import record_audit
from visiox_api.services.authorization import resolve_authorization_target
from visiox_db.models import (
    ComputeNode,
    Dataset,
    DeploymentService,
    ResourcePool,
    TrainedModel,
    TrainingJob,
    TrainingPipeline,
)
from visiox_db.models.identity import (
    AUDIT_RESULT_SUCCESS,
    PRINCIPAL_GROUP,
    PRINCIPAL_ORGANIZATION,
    PRINCIPAL_USER,
    ROLE_ADMIN,
    STATUS_ACTIVE,
    STATUS_DELETED,
    ResourceGrant,
    Organization,
    User,
    UserGroup,
)


router = APIRouter(prefix="/resources", tags=["resource-sharing"])

Permission = Literal["view", "use", "edit", "delete", "manage", "invoke"]
PrincipalType = Literal["user", "group", "organization"]

_RESOURCE_MODELS = {
    "dataset": Dataset,
    "pipeline": TrainingPipeline,
    "training_job": TrainingJob,
    "trained_model": TrainedModel,
    "service": DeploymentService,
    "resource_pool": ResourcePool,
    "node": ComputeNode,
}
_PERMISSIONS = {
    "dataset": {"view", "use", "edit", "delete", "manage"},
    "pipeline": {"view", "use", "edit", "delete", "manage"},
    "training_job": {"view", "edit", "delete", "manage"},
    "trained_model": {"view", "use", "edit", "delete", "manage"},
    "service": {"view", "use", "edit", "delete", "manage", "invoke"},
    "resource_pool": {"view", "use", "edit", "delete", "manage"},
    "node": {"view", "use", "edit", "delete", "manage"},
}
_NO_ORGANIZATION_SHARING = {"node", "resource_pool"}


class SharingGrantInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    principal_type: PrincipalType
    principal_id: str = Field(min_length=1, max_length=36)
    permissions: list[Permission] = Field(min_length=1)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def validate_expiry(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        if normalized <= datetime.now(UTC):
            raise ValueError("expires_at must be in the future")
        return normalized


class SharingReplaceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grants: list[SharingGrantInput]


class SharingGrantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    principal_type: str
    principal_id: str
    permissions: list[str]
    expires_at: datetime | None


class SharingResponse(BaseModel):
    resource_type: str
    resource_id: str
    visibility: str
    grants: list[SharingGrantResponse]


class SharingUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    display_name: str


class SharingGroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str


class SharingPrincipalListResponse(BaseModel):
    organization: SharingGroupResponse
    users: list[SharingUserResponse]
    groups: list[SharingGroupResponse]


def _load_managed_resource(
    session: Session,
    actor: User,
    resource_type: str,
    resource_id: str,
):
    if resource_type not in _RESOURCE_MODELS:
        raise HTTPException(status_code=404, detail="Resource type not found")
    target = resolve_authorization_target(session, resource_type, resource_id)
    if target is None or target.organization_id != actor.organization_id:
        raise HTTPException(status_code=404, detail="Resource not found")
    if actor.role != ROLE_ADMIN and target.owner_user_id != actor.id:
        raise HTTPException(status_code=403, detail="Only the owner or an administrator can manage sharing")
    resource = session.get(_RESOURCE_MODELS[resource_type], resource_id)
    if resource is None:
        raise HTTPException(status_code=404, detail="Resource not found")
    return resource


def _validate_principal(
    session: Session,
    actor: User,
    resource_type: str,
    grant: SharingGrantInput,
) -> None:
    if grant.principal_type == PRINCIPAL_ORGANIZATION:
        if resource_type in _NO_ORGANIZATION_SHARING:
            raise HTTPException(status_code=422, detail="Nodes and resource pools cannot be published to the organization")
        valid = grant.principal_id == actor.organization_id
    elif grant.principal_type == PRINCIPAL_USER:
        principal = session.get(User, grant.principal_id)
        valid = (
            principal is not None
            and principal.organization_id == actor.organization_id
            and principal.status != STATUS_DELETED
        )
    elif grant.principal_type == PRINCIPAL_GROUP:
        principal = session.get(UserGroup, grant.principal_id)
        valid = principal is not None and principal.organization_id == actor.organization_id
    else:
        valid = False
    if not valid:
        raise HTTPException(status_code=422, detail="Principal does not belong to the organization")

    unsupported = set(grant.permissions) - _PERMISSIONS[resource_type]
    if unsupported:
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported permissions for {resource_type}: {', '.join(sorted(unsupported))}",
        )


def _sharing_response(session: Session, resource_type: str, resource) -> SharingResponse:
    grants = list(
        session.scalars(
            select(ResourceGrant)
            .where(
                ResourceGrant.resource_type == resource_type,
                ResourceGrant.resource_id == resource.id,
            )
            .order_by(ResourceGrant.created_at, ResourceGrant.id)
        )
    )
    return SharingResponse(
        resource_type=resource_type,
        resource_id=resource.id,
        visibility=resource.visibility,
        grants=grants,
    )


@router.get("/sharing-principals", response_model=SharingPrincipalListResponse)
def list_sharing_principals(
    session: Session = Depends(get_db_session),
    actor: User = Depends(get_current_user),
) -> SharingPrincipalListResponse:
    organization = session.get(Organization, actor.organization_id)
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    users = list(
        session.scalars(
            select(User)
            .where(
                User.organization_id == actor.organization_id,
                User.status == STATUS_ACTIVE,
            )
            .order_by(User.display_name, User.username, User.id)
        )
    )
    groups = list(
        session.scalars(
            select(UserGroup)
            .where(
                UserGroup.organization_id == actor.organization_id,
                UserGroup.status == STATUS_ACTIVE,
            )
            .order_by(UserGroup.name, UserGroup.id)
        )
    )
    return SharingPrincipalListResponse(
        organization=SharingGroupResponse(id=organization.id, name=organization.name),
        users=users,
        groups=groups,
    )


@router.get("/{resource_type}/{resource_id}/sharing", response_model=SharingResponse)
def get_resource_sharing(
    resource_type: str,
    resource_id: str,
    session: Session = Depends(get_db_session),
    actor: User = Depends(get_current_user),
) -> SharingResponse:
    resource = _load_managed_resource(session, actor, resource_type, resource_id)
    return _sharing_response(session, resource_type, resource)


@router.put("/{resource_type}/{resource_id}/sharing", response_model=SharingResponse)
def replace_resource_sharing(
    resource_type: str,
    resource_id: str,
    payload: SharingReplaceRequest,
    request: Request,
    session: Session = Depends(get_db_session),
    actor: User = Depends(get_current_user),
) -> SharingResponse:
    resource = _load_managed_resource(session, actor, resource_type, resource_id)
    identities: set[tuple[str, str]] = set()
    for grant in payload.grants:
        _validate_principal(session, actor, resource_type, grant)
        identity = (grant.principal_type, grant.principal_id)
        if identity in identities:
            raise HTTPException(status_code=422, detail="Each principal may appear only once")
        identities.add(identity)

    session.execute(
        delete(ResourceGrant).where(
            ResourceGrant.organization_id == actor.organization_id,
            ResourceGrant.resource_type == resource_type,
            ResourceGrant.resource_id == resource_id,
        )
    )
    for item in payload.grants:
        session.add(
            ResourceGrant(
                organization_id=actor.organization_id,
                resource_type=resource_type,
                resource_id=resource_id,
                principal_type=item.principal_type,
                principal_id=item.principal_id,
                permissions=list(dict.fromkeys(item.permissions)),
                expires_at=item.expires_at,
                created_by=actor.id,
            )
        )

    if any(item.principal_type == PRINCIPAL_ORGANIZATION for item in payload.grants):
        resource.visibility = "organization"
    elif payload.grants:
        resource.visibility = "shared"
    else:
        resource.visibility = "private"
    if isinstance(resource, TrainingPipeline):
        resource.is_public = resource.visibility == "organization"
        resource.public_scope = {"visibility": resource.visibility}

    record_audit(
        session,
        actor,
        "resource.sharing.replace",
        resource_type,
        resource_id,
        AUDIT_RESULT_SUCCESS,
        request.headers.get("x-request-id"),
        {"visibility": resource.visibility, "grant_count": len(payload.grants)},
    )
    session.commit()
    return _sharing_response(session, resource_type, resource)
