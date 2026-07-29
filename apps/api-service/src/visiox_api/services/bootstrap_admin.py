from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from visiox_api.services.security import hash_password
from visiox_common.settings import Settings
from visiox_db.models.identity import (
    ROLE_ADMIN,
    STATUS_ACTIVE,
    Organization,
    User,
)


@dataclass(frozen=True)
class BootstrapAdminResult:
    organization_created: bool
    admin_created: bool


class BootstrapAdminIdentityConflictError(ValueError):
    pass


def bootstrap_default_admin(
    session: Session,
    settings: Settings,
) -> BootstrapAdminResult:
    for attempt in range(2):
        try:
            organization = session.scalar(
                select(Organization).where(Organization.slug == "default")
            )
            organization_created = organization is None
            if organization is None:
                organization = Organization(
                    name="Default",
                    slug="default",
                    status=STATUS_ACTIVE,
                )
                session.add(organization)
                session.flush()

            admin = session.scalar(
                select(User).where(
                    User.organization_id == organization.id,
                    User.username == settings.bootstrap_admin_username,
                    User.email == settings.bootstrap_admin_email,
                )
            )
            admin_created = admin is None
            if admin is None:
                identity_collision = session.scalar(
                    select(User.id).where(
                        User.organization_id == organization.id,
                        or_(
                            User.username == settings.bootstrap_admin_username,
                            User.email == settings.bootstrap_admin_email,
                        ),
                    )
                )
                if identity_collision is not None:
                    raise BootstrapAdminIdentityConflictError(
                        "bootstrap admin username or email conflicts with an existing user"
                    )
                password = settings.read_bootstrap_admin_password()
                session.add(
                    User(
                        organization_id=organization.id,
                        username=settings.bootstrap_admin_username,
                        display_name=settings.bootstrap_admin_username,
                        email=settings.bootstrap_admin_email,
                        password_hash=hash_password(password),
                        role=ROLE_ADMIN,
                        status=STATUS_ACTIVE,
                        must_change_password=True,
                    )
                )

            session.commit()
            return BootstrapAdminResult(
                organization_created=organization_created,
                admin_created=admin_created,
            )
        except IntegrityError:
            session.rollback()
            if attempt == 1:
                raise
        except Exception:
            session.rollback()
            raise

    raise RuntimeError("bootstrap admin retry loop exhausted")
