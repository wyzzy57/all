from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from visiox_api.services.authorization import (
    authorized_resource_predicate,
    resolve_authorization_target,
)
from visiox_db.base import Base
from visiox_db.models import (
    Annotation,
    Dataset,
    DatasetSample,
    DeploymentInstance,
    DeploymentService,
    LabelProject,
    PipelineEvaluation,
    ResourceGrant,
    TrainingPipeline,
)
from visiox_db.models.edge_compute import ComputeNode
from visiox_db.models.identity import (
    Organization,
    User,
    UserGroup,
    UserGroupMembership,
)


def test_authorized_query_returns_owned_and_granted_resources_without_duplicates() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        org = Organization(name="Default", slug="default")
        session.add(org); session.flush()
        owner = _user(org.id, "owner")
        member = _user(org.id, "member")
        admin = _user(org.id, "admin", role="admin")
        session.add_all([owner, member, admin]); session.flush()
        owned = Dataset(name="owned", task="detect", organization_id=org.id, owner_user_id=member.id)
        granted = Dataset(name="granted", task="detect", organization_id=org.id, owner_user_id=owner.id)
        expired = Dataset(name="expired", task="detect", organization_id=org.id, owner_user_id=owner.id)
        session.add_all([owned, granted, expired]); session.flush()
        session.add_all([
            ResourceGrant(
                organization_id=org.id, resource_type="dataset", resource_id=granted.id,
                principal_type="user", principal_id=member.id, permissions=["view"],
            ),
            ResourceGrant(
                organization_id=org.id, resource_type="dataset", resource_id=expired.id,
                principal_type="user", principal_id=member.id, permissions=["view"],
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
            ),
        ])
        session.commit()

        visible = session.scalars(
            select(Dataset).where(
                authorized_resource_predicate(session, member, Dataset, "dataset")
            ).order_by(Dataset.name)
        ).all()
        admin_visible = session.scalars(
            select(Dataset).where(
                authorized_resource_predicate(session, admin, Dataset, "dataset")
            )
        ).all()

        assert [dataset.name for dataset in visible] == ["granted", "owned"]
        assert len({dataset.id for dataset in visible}) == len(visible)
        assert len(admin_visible) == 3


def test_authorized_query_accepts_group_and_organization_grants() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        org = Organization(name="Default", slug="default")
        other_org = Organization(name="Other", slug="other")
        session.add_all([org, other_org]); session.flush()
        owner = _user(org.id, "owner")
        member = _user(org.id, "member")
        outsider = _user(other_org.id, "outsider")
        group = UserGroup(organization_id=org.id, name="Vision")
        session.add_all([owner, member, outsider, group]); session.flush()
        session.add(UserGroupMembership(group_id=group.id, user_id=member.id))
        grouped = Dataset(name="grouped", task="detect", organization_id=org.id, owner_user_id=owner.id)
        organization = Dataset(name="organization", task="detect", organization_id=org.id, owner_user_id=owner.id)
        cross_org = Dataset(name="cross-org", task="detect", organization_id=other_org.id, owner_user_id=outsider.id)
        session.add_all([grouped, organization, cross_org]); session.flush()
        session.add_all([
            ResourceGrant(
                organization_id=org.id, resource_type="dataset", resource_id=grouped.id,
                principal_type="group", principal_id=group.id, permissions=["view"],
            ),
            ResourceGrant(
                organization_id=org.id, resource_type="dataset", resource_id=organization.id,
                principal_type="organization", principal_id=org.id, permissions=["view", "use"],
            ),
            ResourceGrant(
                organization_id=other_org.id, resource_type="dataset", resource_id=cross_org.id,
                principal_type="user", principal_id=member.id, permissions=["view"],
            ),
        ])
        session.commit()

        visible = session.scalars(
            select(Dataset).where(
                authorized_resource_predicate(session, member, Dataset, "dataset")
            ).order_by(Dataset.name)
        ).all()
        usable = session.scalars(
            select(Dataset).where(
                authorized_resource_predicate(session, member, Dataset, "dataset", "use")
            )
        ).all()

        assert [dataset.name for dataset in visible] == ["grouped", "organization"]
        assert [dataset.name for dataset in usable] == ["organization"]


def test_admin_query_is_limited_to_their_organization() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        organization = Organization(name="Default", slug="default")
        other_organization = Organization(name="Other", slug="other")
        session.add_all([organization, other_organization]); session.flush()
        admin = _user(organization.id, "admin", role="admin")
        other_owner = _user(other_organization.id, "other-owner")
        session.add_all([admin, other_owner]); session.flush()
        local = Dataset(
            name="local",
            task="detect",
            organization_id=organization.id,
            owner_user_id=admin.id,
        )
        foreign = Dataset(
            name="foreign",
            task="detect",
            organization_id=other_organization.id,
            owner_user_id=other_owner.id,
        )
        session.add_all([local, foreign]); session.commit()

        visible = session.scalars(
            select(Dataset).where(
                authorized_resource_predicate(session, admin, Dataset, "dataset")
            )
        ).all()

        assert [dataset.id for dataset in visible] == [local.id]


def test_child_resources_resolve_to_their_authorization_parent() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        org = Organization(name="Default", slug="default")
        session.add(org); session.flush()
        owner = _user(org.id, "owner")
        session.add(owner); session.flush()
        dataset = Dataset(name="dataset", task="detect", organization_id=org.id, owner_user_id=owner.id)
        pipeline = TrainingPipeline(
            name="pipeline", task="detect", scale="n", organization_id=org.id,
            owner_user_id=owner.id,
        )
        session.add_all([dataset, pipeline]); session.flush()
        sample = DatasetSample(dataset_id=dataset.id, file_uri="sample.jpg")
        label_project = LabelProject(dataset_id=dataset.id, provider="label_studio")
        evaluation = PipelineEvaluation(
            pipeline_id=pipeline.id, dataset_id=dataset.id, evaluation_set="val",
            model_weight="best.pt", environment="cpu",
        )
        service = DeploymentService(
            name="service", pipeline_id=pipeline.id, model_name="model",
            model_weight="best.pt", environment="cpu", endpoint="pending",
            organization_id=org.id, owner_user_id=owner.id,
        )
        node = ComputeNode(
            name="node", platform_kind="linux", architecture="x86_64",
            agent_version="manual-ssh",
        )
        session.add_all([sample, label_project, evaluation, service, node]); session.flush()
        annotation = Annotation(dataset_sample_id=sample.id, source="label_studio")
        instance = DeploymentInstance(
            deployment_service_id=service.id, node_id=node.id,
            instance_name="default", engine="docker",
        )
        session.add_all([annotation, instance]); session.commit()

        cases = [
            ("dataset_sample", sample.id, "dataset", dataset.id),
            ("annotation", annotation.id, "dataset", dataset.id),
            ("label_project", label_project.id, "dataset", dataset.id),
            ("pipeline_evaluation", evaluation.id, "pipeline", pipeline.id),
            ("deployment_instance", instance.id, "service", service.id),
        ]
        for child_type, child_id, parent_type, parent_id in cases:
            target = resolve_authorization_target(session, child_type, child_id)
            assert target is not None
            assert (target.resource_type, target.resource_id) == (parent_type, parent_id)
            assert target.owner_user_id == owner.id


def _user(organization_id: str, username: str, *, role: str = "member") -> User:
    return User(
        organization_id=organization_id,
        username=username,
        display_name=username,
        email=f"{username}@example.test",
        password_hash="hash",
        role=role,
        status="active",
        must_change_password=False,
    )
