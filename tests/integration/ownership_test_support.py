from sqlalchemy import event
from sqlalchemy.orm import Session, sessionmaker

from visiox_db.models import (
    ComputeNode,
    Dataset,
    DeploymentService,
    ResourcePool,
    TrainedModel,
    TrainingJob,
    TrainingPipeline,
)


PRIMARY_RESOURCE_TYPES = (
    Dataset,
    TrainingPipeline,
    TrainingJob,
    TrainedModel,
    DeploymentService,
    ResourcePool,
    ComputeNode,
)


def install_legacy_ownership(
    factory: sessionmaker[Session],
    *,
    organization_id: str = "legacy-org",
    owner_user_id: str = "legacy-admin",
) -> None:
    @event.listens_for(factory, "before_flush")
    def assign_legacy_ownership(session: Session, _flush_context, _instances) -> None:
        for resource in session.new:
            if not isinstance(resource, PRIMARY_RESOURCE_TYPES):
                continue
            if resource.organization_id is None:
                resource.organization_id = organization_id
            if resource.owner_user_id is None:
                resource.owner_user_id = owner_user_id
