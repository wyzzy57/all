from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from visiox_api.dependencies.auth import require_admin
from visiox_api.dependencies.database import get_db_session
from visiox_api.schemas.identity import (
    GroupCreateRequest,
    GroupListResponse,
    GroupMembersRequest,
    GroupResponse,
    GroupUpdateRequest,
)
from visiox_api.services.audit import record_audit
from visiox_db.models.identity import (
    AUDIT_RESULT_SUCCESS,
    PRINCIPAL_GROUP,
    STATUS_ACTIVE,
    STATUS_DELETED,
    ResourceAllocationPolicy,
    ResourceGrant,
    User,
    UserGroup,
    UserGroupMembership,
)


router = APIRouter(
    prefix="/admin/groups",
    tags=["admin-groups"],
    dependencies=[Depends(require_admin)],
)


def _group(session: Session, actor: User, group_id: str) -> UserGroup:
    group = session.scalar(
        select(UserGroup).where(
            UserGroup.id == group_id,
            UserGroup.organization_id == actor.organization_id,
        )
    )
    if group is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Group not found"
        )
    return group


def _response(session: Session, group: UserGroup) -> GroupResponse:
    member_ids = list(
        session.scalars(
            select(UserGroupMembership.user_id)
            .where(UserGroupMembership.group_id == group.id)
            .order_by(UserGroupMembership.user_id)
        )
    )
    return GroupResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        status=group.status,
        member_ids=member_ids,
        member_count=len(member_ids),
    )


def _commit_or_conflict(session: Session) -> None:
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Group name already exists"
        ) from None


@router.get("", response_model=GroupListResponse)
def list_groups(
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    session: Session = Depends(get_db_session),
    actor: User = Depends(require_admin),
) -> GroupListResponse:
    predicate = UserGroup.organization_id == actor.organization_id
    total = (
        session.scalar(select(func.count()).select_from(UserGroup).where(predicate))
        or 0
    )
    groups = list(
        session.scalars(
            select(UserGroup)
            .where(predicate)
            .order_by(UserGroup.name, UserGroup.id)
            .offset(offset)
            .limit(limit)
        )
    )
    return GroupListResponse(
        items=[_response(session, group) for group in groups],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post("", response_model=GroupResponse, status_code=status.HTTP_201_CREATED)
def create_group(
    payload: GroupCreateRequest,
    request: Request,
    session: Session = Depends(get_db_session),
    actor: User = Depends(require_admin),
) -> GroupResponse:
    group = UserGroup(
        organization_id=actor.organization_id,
        name=payload.name,
        description=payload.description,
        status=STATUS_ACTIVE,
    )
    try:
        session.add(group)
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Group name already exists"
        ) from None
    record_audit(
        session,
        actor,
        "admin.group.create",
        "group",
        group.id,
        AUDIT_RESULT_SUCCESS,
        request.headers.get("x-request-id"),
        {"name": group.name},
    )
    _commit_or_conflict(session)
    return _response(session, group)


@router.patch("/{group_id}", response_model=GroupResponse)
def update_group(
    group_id: str,
    payload: GroupUpdateRequest,
    request: Request,
    session: Session = Depends(get_db_session),
    actor: User = Depends(require_admin),
) -> GroupResponse:
    group = _group(session, actor, group_id)
    changes = payload.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(group, field, value)
    record_audit(
        session,
        actor,
        "admin.group.update",
        "group",
        group.id,
        AUDIT_RESULT_SUCCESS,
        request.headers.get("x-request-id"),
        {"fields": sorted(changes)},
    )
    _commit_or_conflict(session)
    return _response(session, group)


@router.put("/{group_id}/members", response_model=GroupResponse)
def replace_group_members(
    group_id: str,
    payload: GroupMembersRequest,
    request: Request,
    session: Session = Depends(get_db_session),
    actor: User = Depends(require_admin),
) -> GroupResponse:
    group = _group(session, actor, group_id)
    user_ids = set(payload.user_ids)
    users = (
        list(session.scalars(select(User).where(User.id.in_(user_ids))))
        if user_ids
        else []
    )
    valid_ids = {
        user.id
        for user in users
        if user.organization_id == actor.organization_id
        and user.status != STATUS_DELETED
    }
    if valid_ids != user_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Every member must be an existing non-deleted user in the organization",
        )
    session.execute(
        delete(UserGroupMembership).where(UserGroupMembership.group_id == group.id)
    )
    session.add_all(
        UserGroupMembership(group_id=group.id, user_id=user_id)
        for user_id in sorted(user_ids)
    )
    record_audit(
        session,
        actor,
        "admin.group.members.replace",
        "group",
        group.id,
        AUDIT_RESULT_SUCCESS,
        request.headers.get("x-request-id"),
        {"member_count": len(user_ids)},
    )
    session.commit()
    return _response(session, group)


@router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_group(
    group_id: str,
    request: Request,
    session: Session = Depends(get_db_session),
    actor: User = Depends(require_admin),
) -> None:
    group = _group(session, actor, group_id)
    session.execute(
        delete(UserGroupMembership).where(UserGroupMembership.group_id == group.id)
    )
    session.execute(
        delete(ResourceGrant).where(
            ResourceGrant.organization_id == actor.organization_id,
            ResourceGrant.principal_type == PRINCIPAL_GROUP,
            ResourceGrant.principal_id == group.id,
        )
    )
    session.execute(
        delete(ResourceAllocationPolicy).where(
            ResourceAllocationPolicy.organization_id == actor.organization_id,
            ResourceAllocationPolicy.principal_type == PRINCIPAL_GROUP,
            ResourceAllocationPolicy.principal_id == group.id,
        )
    )
    session.delete(group)
    record_audit(
        session,
        actor,
        "admin.group.delete",
        "group",
        group.id,
        AUDIT_RESULT_SUCCESS,
        request.headers.get("x-request-id"),
        {},
    )
    session.commit()
