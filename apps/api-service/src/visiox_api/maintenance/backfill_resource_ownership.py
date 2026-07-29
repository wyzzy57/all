from dataclasses import dataclass

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from visiox_common.settings import get_settings
from visiox_db.models import (
    ComputeNode,
    Dataset,
    DeploymentService,
    ResourceGrant,
    ResourcePool,
    TrainedModel,
    TrainingJob,
    TrainingPipeline,
    User,
)
from visiox_db.models.identity import Organization
from visiox_db.session import create_session_factory


RESOURCE_MODELS = (
    Dataset,
    TrainingPipeline,
    TrainingJob,
    TrainedModel,
    DeploymentService,
    ResourcePool,
    ComputeNode,
)


@dataclass(frozen=True, slots=True)
class BackfillResult:
    before: int
    after: int
    updated: int


def backfill_resource_ownership(
    session: Session,
    organization: Organization,
    owner: User,
) -> BackfillResult:
    before = _unowned_count(session)
    updated = 0
    for model in RESOURCE_MODELS:
        unowned = or_(model.organization_id.is_(None), model.owner_user_id.is_(None))
        public_pipeline_ids: list[str] = []
        if model is TrainingPipeline:
            public_pipeline_ids = list(
                session.scalars(
                    select(TrainingPipeline.id).where(unowned, TrainingPipeline.is_public.is_(True))
                )
            )
        result = session.execute(
            update(model)
            .where(unowned)
            .values(organization_id=organization.id, owner_user_id=owner.id),
            execution_options={"synchronize_session": "fetch"},
        )
        updated += result.rowcount or 0
        if public_pipeline_ids:
            session.execute(
                update(TrainingPipeline)
                .where(TrainingPipeline.id.in_(public_pipeline_ids))
                .values(visibility="organization"),
                execution_options={"synchronize_session": "fetch"},
            )
            for pipeline_id in public_pipeline_ids:
                _ensure_organization_view_grant(session, pipeline_id, organization, owner)
    session.commit()
    session.expire_all()
    after = _unowned_count(session)
    return BackfillResult(before=before, after=after, updated=updated)


def _ensure_organization_view_grant(
    session: Session,
    pipeline_id: str,
    organization: Organization,
    owner: User,
) -> None:
    grant = session.scalar(
        select(ResourceGrant).where(
            ResourceGrant.organization_id == organization.id,
            ResourceGrant.resource_type == "pipeline",
            ResourceGrant.resource_id == pipeline_id,
            ResourceGrant.principal_type == "organization",
            ResourceGrant.principal_id == organization.id,
        )
    )
    if grant is None:
        session.add(
            ResourceGrant(
                organization_id=organization.id,
                resource_type="pipeline",
                resource_id=pipeline_id,
                principal_type="organization",
                principal_id=organization.id,
                permissions=["view"],
                created_by=owner.id,
            )
        )
    elif "view" not in grant.permissions:
        grant.permissions = [*grant.permissions, "view"]


def _unowned_count(session: Session) -> int:
    return sum(
        session.scalar(
            select(func.count()).select_from(model).where(
                (model.organization_id.is_(None)) | (model.owner_user_id.is_(None))
            )
        )
        or 0
        for model in RESOURCE_MODELS
    )


def main() -> int:
    settings = get_settings()
    factory = create_session_factory()
    with factory() as session:
        organization = session.scalar(select(Organization).where(Organization.slug == "default"))
        owner = session.scalar(
            select(User).where(User.username == settings.bootstrap_admin_username)
        )
        if organization is None or owner is None:
            if _unowned_count(session) == 0:
                print("resource ownership backfill: no resources require ownership")
                return 0
            print("resource ownership backfill failed: bootstrap identity is unavailable")
            return 1
        result = backfill_resource_ownership(session, organization, owner)
        print(
            f"resource ownership backfill: before={result.before} "
            f"updated={result.updated} after={result.after}"
        )
        return 0 if result.after == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
