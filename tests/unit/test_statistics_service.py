from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from visiox_api.services.statistics import (
    _database_month_label,
    collect_scoped_statistics,
)
from visiox_db.base import Base
from visiox_db.models.datasets import Dataset
from visiox_db.models.edge_compute import ComputeNode
from visiox_db.models.identity import (
    PRINCIPAL_GROUP,
    PRINCIPAL_ORGANIZATION,
    PRINCIPAL_USER,
    ROLE_ADMIN,
    Organization,
    ResourceGrant,
    User,
    UserGroup,
    UserGroupMembership,
)
from visiox_db.models.model_space import (
    DeploymentService,
    TrainingJob,
    TrainingPipeline,
)


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db_session:
        yield db_session
    engine.dispose()


def test_admin_statistics_include_organization_totals_statuses_and_months(
    session: Session,
) -> None:
    organization = _organization(session, "org-1")
    admin = _user(session, organization.id, "admin", role=ROLE_ADMIN)
    member = _user(session, organization.id, "member")
    group = UserGroup(id="group-1", organization_id=organization.id, name="Operators")
    session.add(group)
    pipeline = _pipeline(
        organization.id,
        admin.id,
        "admin-pipeline",
        "configured",
        datetime(2026, 3, 12, tzinfo=timezone.utc),
    )
    dataset = _dataset(
        organization.id,
        member.id,
        "admin-dataset",
        "validated",
        datetime(2026, 5, 3, tzinfo=timezone.utc),
    )
    session.add_all([pipeline, dataset])
    session.flush()
    session.add_all(
        [
            TrainingJob(
                pipeline_id=pipeline.id,
                organization_id=organization.id,
                owner_user_id=admin.id,
                status="running",
                created_at=datetime(2026, 5, 9, tzinfo=timezone.utc),
            ),
            DeploymentService(
                name="admin-service",
                pipeline_id=pipeline.id,
                organization_id=organization.id,
                owner_user_id=admin.id,
                model_name="model",
                model_weight="best.pt",
                environment="gpu",
                endpoint="pending",
                status="healthy",
                created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
            ),
            ComputeNode(
                name="admin-node",
                organization_id=organization.id,
                owner_user_id=admin.id,
                platform_kind="linux",
                architecture="x86_64",
                agent_version="1.0",
                status="online",
                created_at=datetime(2026, 5, 11, tzinfo=timezone.utc),
            ),
        ]
    )
    session.commit()

    result = collect_scoped_statistics(
        session,
        admin,
        months=4,
        now=datetime(2026, 5, 20, tzinfo=timezone.utc),
    )

    assert result["totals"] == {
        "pipelines": 1,
        "datasets": 1,
        "training_jobs": 1,
        "services": 1,
        "nodes": 1,
        "users": 2,
        "groups": 1,
    }
    assert result["status_buckets"]["pipelines"] == [
        {"label": "configured", "value": 1}
    ]
    assert result["status_buckets"]["datasets"] == [{"label": "validated", "value": 1}]
    assert result["status_buckets"]["training_jobs"] == [{"label": "running", "value": 1}]
    assert result["status_buckets"]["services"] == [{"label": "healthy", "value": 1}]
    assert result["status_buckets"]["nodes"] == [{"label": "online", "value": 1}]
    assert result["creation_trends"]["pipelines"] == {
        "labels": ["2026-02", "2026-03", "2026-04", "2026-05"],
        "values": [0, 1, 0, 0],
    }
    assert result["creation_trends"]["datasets"] == {
        "labels": ["2026-02", "2026-03", "2026-04", "2026-05"],
        "values": [0, 0, 0, 1],
    }
    assert result["generated_at"].tzinfo is not None
    assert result["generated_at"].utcoffset().total_seconds() == 0


def test_member_statistics_combine_owned_and_granted_resources_without_duplicates(
    session: Session,
) -> None:
    organization = _organization(session, "org-1")
    owner = _user(session, organization.id, "owner")
    member = _user(session, organization.id, "member")
    group = UserGroup(id="group-1", organization_id=organization.id, name="Reviewers")
    session.add_all([group, UserGroupMembership(group_id=group.id, user_id=member.id)])

    owned = _dataset(organization.id, member.id, "owned", "created")
    direct = _dataset(organization.id, owner.id, "direct", "validated")
    grouped = _dataset(organization.id, owner.id, "grouped", "validated")
    public = _dataset(organization.id, owner.id, "public", "created")
    multi_granted = _dataset(organization.id, owner.id, "multi-granted", "validated")
    hidden = _dataset(organization.id, owner.id, "hidden", "created")
    session.add_all([owned, direct, grouped, public, multi_granted, hidden])
    session.flush()
    session.add_all(
        [
            _grant(organization.id, "dataset", direct.id, PRINCIPAL_USER, member.id),
            _grant(organization.id, "dataset", grouped.id, PRINCIPAL_GROUP, group.id),
            _grant(
                organization.id,
                "dataset",
                public.id,
                PRINCIPAL_ORGANIZATION,
                organization.id,
            ),
            _grant(
                organization.id,
                "dataset",
                multi_granted.id,
                PRINCIPAL_USER,
                member.id,
            ),
            _grant(
                organization.id,
                "dataset",
                multi_granted.id,
                PRINCIPAL_GROUP,
                group.id,
            ),
            _grant(
                organization.id,
                "dataset",
                multi_granted.id,
                PRINCIPAL_ORGANIZATION,
                organization.id,
            ),
        ]
    )
    session.commit()

    result = collect_scoped_statistics(
        session,
        member,
        now=datetime(2026, 5, 20, tzinfo=timezone.utc),
    )

    assert result["totals"]["datasets"] == 5
    assert result["status_buckets"]["datasets"] == [
        {"label": "created", "value": 2},
        {"label": "validated", "value": 3},
    ]
    assert result["totals"]["users"] == 1
    assert result["totals"]["groups"] == 1
    assert _serialized_resource_identifiers(result) == set()


def test_empty_system_returns_zero_aggregate_series_with_no_resource_details(
    session: Session,
) -> None:
    organization = _organization(session, "org-1")
    admin = _user(session, organization.id, "admin", role=ROLE_ADMIN)

    result = collect_scoped_statistics(
        session,
        admin,
        months=3,
        now=datetime(2026, 5, 20, tzinfo=timezone.utc),
    )

    assert result["totals"] == {
        "pipelines": 0,
        "datasets": 0,
        "training_jobs": 0,
        "services": 0,
        "nodes": 0,
        "users": 1,
        "groups": 0,
    }
    assert result["status_buckets"] == {
        "pipelines": [],
        "datasets": [],
        "training_jobs": [],
        "services": [],
        "nodes": [],
    }
    assert all(
        trend == {"labels": ["2026-03", "2026-04", "2026-05"], "values": [0, 0, 0]}
        for trend in result["creation_trends"].values()
    )
    assert _serialized_resource_identifiers(result) == set()


def test_postgresql_month_bucket_converts_timestamps_to_utc_before_truncation() -> None:
    session = _PostgresqlCompilationSession()

    month = _database_month_label(session, Dataset.created_at)
    sql = str(
        select(month).compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "date_trunc('month', timezone('UTC', datasets.created_at))" in sql


def _organization(session: Session, organization_id: str) -> Organization:
    organization = Organization(id=organization_id, name=organization_id, slug=organization_id)
    session.add(organization)
    session.flush()
    return organization


def _user(session: Session, organization_id: str, user_id: str, *, role: str = "member") -> User:
    user = User(
        id=user_id,
        organization_id=organization_id,
        username=user_id,
        display_name=user_id,
        email=f"{user_id}@example.test",
        password_hash="hash",
        role=role,
        must_change_password=False,
    )
    session.add(user)
    session.flush()
    return user


def _dataset(
    organization_id: str,
    owner_user_id: str,
    name: str,
    status: str,
    created_at: datetime | None = None,
) -> Dataset:
    values = {
        "name": name,
        "organization_id": organization_id,
        "owner_user_id": owner_user_id,
        "task": "detect",
        "status": status,
    }
    if created_at is not None:
        values["created_at"] = created_at
    return Dataset(**values)


def _pipeline(
    organization_id: str,
    owner_user_id: str,
    name: str,
    status: str,
    created_at: datetime,
) -> TrainingPipeline:
    return TrainingPipeline(
        name=name,
        organization_id=organization_id,
        owner_user_id=owner_user_id,
        task="detect",
        scale="n",
        status=status,
        created_at=created_at,
    )


def _grant(
    organization_id: str,
    resource_type: str,
    resource_id: str,
    principal_type: str,
    principal_id: str,
) -> ResourceGrant:
    return ResourceGrant(
        organization_id=organization_id,
        resource_type=resource_type,
        resource_id=resource_id,
        principal_type=principal_type,
        principal_id=principal_id,
        permissions=["view"],
    )


def _serialized_resource_identifiers(result: dict[str, object]) -> set[str]:
    payload = repr(result)
    forbidden = {
        "owned",
        "direct",
        "grouped",
        "public",
        "multi-granted",
        "hidden",
    }
    return {value for value in forbidden if value in payload}


class _PostgresqlCompilationSession:
    def get_bind(self) -> object:
        return _PostgresqlBind()


class _PostgresqlBind:
    dialect = postgresql.dialect()
