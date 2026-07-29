from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import and_, exists, false, or_, select, true
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.orm import Session

from visiox_db.models.identity import (
    PERMISSION_DELETE,
    PERMISSION_EDIT,
    PERMISSION_INVOKE,
    PERMISSION_MANAGE,
    PERMISSION_USE,
    PERMISSION_VIEW,
    PRINCIPAL_GROUP,
    PRINCIPAL_ORGANIZATION,
    PRINCIPAL_USER,
    ROLE_ADMIN,
    ResourceGrant,
    User,
    UserGroup,
    UserGroupMembership,
)
from visiox_db.models.datasets import Annotation, Dataset, DatasetSample, LabelProject
from visiox_db.models.edge_compute import ComputeNode, ResourcePool
from visiox_db.models.model_space import (
    DeploymentInstance,
    DeploymentService,
    PipelineEvaluation,
    TrainedModel,
    TrainingJob,
    TrainingPipeline,
)


ALL_PERMISSIONS = {
    PERMISSION_VIEW,
    PERMISSION_USE,
    PERMISSION_EDIT,
    PERMISSION_DELETE,
    PERMISSION_MANAGE,
    PERMISSION_INVOKE,
}


class AuthorizationDeniedError(Exception):
    code = "permission_denied"

    def __init__(
        self,
        *,
        resource_type: str,
        resource_id: str,
        permission: str,
    ) -> None:
        super().__init__("Permission denied")
        self.resource_type = resource_type
        self.resource_id = resource_id
        self.permission = permission


@dataclass(frozen=True)
class AuthorizationTarget:
    resource_type: str
    resource_id: str
    organization_id: str | None
    owner_user_id: str | None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def resolve_permissions(
    session: Session,
    actor: User,
    resource_type: str,
    resource_id: str,
    owner_user_id: str,
) -> set[str]:
    if actor.role == ROLE_ADMIN or actor.id == owner_user_id:
        return set(ALL_PERMISSIONS)

    group_ids = set(
        session.scalars(
            select(UserGroupMembership.group_id)
            .join(UserGroup, UserGroup.id == UserGroupMembership.group_id)
            .where(
                UserGroupMembership.user_id == actor.id,
                UserGroup.organization_id == actor.organization_id,
            )
        )
    )
    principals = [
        and_(
            ResourceGrant.principal_type == PRINCIPAL_USER,
            ResourceGrant.principal_id == actor.id,
        ),
        and_(
            ResourceGrant.principal_type == PRINCIPAL_ORGANIZATION,
            ResourceGrant.principal_id == actor.organization_id,
        ),
    ]
    if group_ids:
        principals.append(
            and_(
                ResourceGrant.principal_type == PRINCIPAL_GROUP,
                ResourceGrant.principal_id.in_(group_ids),
            )
        )

    grants = session.scalars(
        select(ResourceGrant).where(
            ResourceGrant.organization_id == actor.organization_id,
            ResourceGrant.resource_type == resource_type,
            ResourceGrant.resource_id == resource_id,
            or_(*principals),
        )
    )
    now = datetime.now(UTC)
    permissions: set[str] = set()
    for grant in grants:
        if grant.expires_at is not None and _as_utc(grant.expires_at) <= now:
            continue
        permissions.update(grant.permissions)

    return permissions & ALL_PERMISSIONS


def assert_permission(
    session: Session,
    actor: User,
    resource_type: str,
    resource_id: str,
    owner_user_id: str,
    permission: str,
) -> None:
    permissions = resolve_permissions(
        session,
        actor,
        resource_type,
        resource_id,
        owner_user_id,
    )
    if permission not in permissions:
        raise AuthorizationDeniedError(
            resource_type=resource_type,
            resource_id=resource_id,
            permission=permission,
        )


def authorized_resource_predicate(
    session: Session,
    actor: User,
    model: type,
    resource_type: str,
    permission: str = PERMISSION_VIEW,
) -> ColumnElement[bool]:
    if actor.role == ROLE_ADMIN:
        return or_(
            model.organization_id == actor.organization_id,
            model.organization_id.is_(None),
        )
    if permission not in ALL_PERMISSIONS:
        return false()

    group_ids = select(UserGroupMembership.group_id).join(
        UserGroup,
        UserGroup.id == UserGroupMembership.group_id,
    ).where(
        UserGroupMembership.user_id == actor.id,
        UserGroup.organization_id == actor.organization_id,
    )
    now = datetime.now(UTC)
    grant_exists = exists(
        select(ResourceGrant.id).where(
            ResourceGrant.organization_id == actor.organization_id,
            ResourceGrant.resource_type == resource_type,
            ResourceGrant.resource_id == model.id,
            ResourceGrant.permissions.contains(permission),
            or_(ResourceGrant.expires_at.is_(None), ResourceGrant.expires_at > now),
            or_(
                and_(
                    ResourceGrant.principal_type == PRINCIPAL_USER,
                    ResourceGrant.principal_id == actor.id,
                ),
                and_(
                    ResourceGrant.principal_type == PRINCIPAL_ORGANIZATION,
                    ResourceGrant.principal_id == actor.organization_id,
                ),
                and_(
                    ResourceGrant.principal_type == PRINCIPAL_GROUP,
                    ResourceGrant.principal_id.in_(group_ids),
                ),
            ),
        )
    )
    return and_(
        model.organization_id == actor.organization_id,
        or_(model.owner_user_id == actor.id, grant_exists),
    )


def resolve_authorization_target(
    session: Session,
    resource_type: str,
    resource_id: str,
) -> AuthorizationTarget | None:
    primary_models = {
        "dataset": Dataset,
        "pipeline": TrainingPipeline,
        "training_job": TrainingJob,
        "training_artifact": TrainingJob,
        "trained_model": TrainedModel,
        "service": DeploymentService,
        "resource_pool": ResourcePool,
        "node": ComputeNode,
    }
    model = primary_models.get(resource_type)
    if model is not None:
        resource = session.get(model, resource_id)
        if resource is None:
            return None
        effective_type = "training_job" if resource_type == "training_artifact" else resource_type
        return _target(effective_type, resource)

    if resource_type == "dataset_sample":
        sample = session.get(DatasetSample, resource_id)
        return _parent_target(session, Dataset, sample.dataset_id, "dataset") if sample else None
    if resource_type == "annotation":
        annotation = session.get(Annotation, resource_id)
        if annotation is None:
            return None
        sample = session.get(DatasetSample, annotation.dataset_sample_id)
        return _parent_target(session, Dataset, sample.dataset_id, "dataset") if sample else None
    if resource_type == "label_project":
        project = session.get(LabelProject, resource_id)
        return _parent_target(session, Dataset, project.dataset_id, "dataset") if project else None
    if resource_type == "pipeline_evaluation":
        evaluation = session.get(PipelineEvaluation, resource_id)
        return _parent_target(session, TrainingPipeline, evaluation.pipeline_id, "pipeline") if evaluation else None
    if resource_type == "deployment_instance":
        instance = session.get(DeploymentInstance, resource_id)
        return _parent_target(session, DeploymentService, instance.deployment_service_id, "service") if instance else None
    return None


def _parent_target(
    session: Session,
    model: type,
    resource_id: str,
    resource_type: str,
) -> AuthorizationTarget | None:
    resource = session.get(model, resource_id)
    return _target(resource_type, resource) if resource is not None else None


def _target(resource_type: str, resource: object) -> AuthorizationTarget:
    return AuthorizationTarget(
        resource_type=resource_type,
        resource_id=getattr(resource, "id"),
        organization_id=getattr(resource, "organization_id"),
        owner_user_id=getattr(resource, "owner_user_id"),
    )
