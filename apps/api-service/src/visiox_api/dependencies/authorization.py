from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from visiox_api.services.authorization import (
    AuthorizationDeniedError,
    AuthorizationTarget,
    assert_permission,
    resolve_authorization_target,
)
from visiox_db.models.identity import User
from visiox_db.models.identity import ROLE_ADMIN


def require_resource_permission(
    session: Session,
    actor: User,
    resource_type: str,
    resource_id: str,
    permission: str,
) -> AuthorizationTarget:
    target = resolve_authorization_target(session, resource_type, resource_id)
    unowned_admin_compatibility = (
        target is not None
        and target.organization_id is None
        and actor.role == ROLE_ADMIN
    )
    if target is None or (
        target.organization_id != actor.organization_id
        and not unowned_admin_compatibility
    ):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resource not found",
        )
    try:
        assert_permission(
            session,
            actor,
            target.resource_type,
            target.resource_id,
            target.owner_user_id or "",
            permission,
        )
    except AuthorizationDeniedError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied",
        ) from exc
    return target
