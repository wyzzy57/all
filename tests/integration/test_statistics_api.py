from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select

from visiox_db.models import (
    ComputeNode,
    Dataset,
    DeploymentService,
    TrainingJob,
    TrainingPipeline,
)
from visiox_db.models.identity import (
    PRINCIPAL_GROUP,
    PRINCIPAL_ORGANIZATION,
    PRINCIPAL_USER,
    ResourceGrant,
    UserGroup,
    UserGroupMembership,
)
from visiox_api.routes import statistics as statistics_routes
from visiox_api.routes.statistics import StatisticsCache


def test_statistics_endpoints_require_authentication_and_member_cannot_use_admin_routes(
    authenticated_client,
) -> None:
    client, _, _, identity = authenticated_client

    for path in ("/statistics/workbench", "/statistics/resources"):
        response = client.get(path)
        assert response.status_code == 401

    for path in ("/admin/statistics/overview", "/admin/statistics/resources"):
        response = client.get(path, headers=identity["member_headers"])
        assert response.status_code == 403


def test_member_statistics_include_only_owned_direct_group_and_organization_granted_resources(
    authenticated_client,
) -> None:
    client, session_factory, _, identity = authenticated_client
    with session_factory() as session:
        group = UserGroup(
            organization_id=identity["organization_id"], name="Analytics"
        )
        owned_dataset = Dataset(
            name="owned-statistics-dataset",
            task="detect",
            organization_id=identity["organization_id"],
            owner_user_id=identity["member_id"],
            status="validated",
        )
        direct_pipeline = TrainingPipeline(
            name="direct-statistics-pipeline",
            task="detect",
            scale="n",
            organization_id=identity["organization_id"],
            owner_user_id=identity["admin_id"],
            status="configured",
        )
        group_dataset = Dataset(
            name="group-statistics-dataset",
            task="detect",
            organization_id=identity["organization_id"],
            owner_user_id=identity["admin_id"],
            status="created",
        )
        public_service = DeploymentService(
            name="organization-statistics-service",
            pipeline_id=direct_pipeline.id,
            organization_id=identity["organization_id"],
            owner_user_id=identity["admin_id"],
            model_name="model",
            model_weight="best.pt",
            environment="cpu",
            endpoint="pending",
            status="running",
            calls=7,
        )
        hidden_dataset = Dataset(
            name="private-statistics-dataset",
            task="detect",
            organization_id=identity["organization_id"],
            owner_user_id=identity["admin_id"],
            status="created",
        )
        session.add_all([group, owned_dataset, direct_pipeline, group_dataset, hidden_dataset])
        session.flush()
        public_service.pipeline_id = direct_pipeline.id
        session.add(public_service)
        session.flush()
        session.add_all(
            [
                UserGroupMembership(group_id=group.id, user_id=identity["member_id"]),
                ResourceGrant(
                    organization_id=identity["organization_id"],
                    resource_type="pipeline",
                    resource_id=direct_pipeline.id,
                    principal_type=PRINCIPAL_USER,
                    principal_id=identity["member_id"],
                    permissions=["view"],
                    created_by=identity["admin_id"],
                ),
                ResourceGrant(
                    organization_id=identity["organization_id"],
                    resource_type="dataset",
                    resource_id=group_dataset.id,
                    principal_type=PRINCIPAL_GROUP,
                    principal_id=group.id,
                    permissions=["view"],
                    created_by=identity["admin_id"],
                ),
                ResourceGrant(
                    organization_id=identity["organization_id"],
                    resource_type="service",
                    resource_id=public_service.id,
                    principal_type=PRINCIPAL_ORGANIZATION,
                    principal_id=identity["organization_id"],
                    permissions=["view"],
                    created_by=identity["admin_id"],
                ),
            ]
        )
        session.commit()

    response = client.get("/statistics/workbench", headers=identity["member_headers"])

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["totals"] == {
        "pipelines": 1,
        "datasets": 2,
        "training_jobs": 0,
        "services": 1,
        "nodes": 0,
        "users": 1,
        "groups": 1,
    }
    assert payload["generated_at"]
    assert "private-statistics-dataset" not in response.text
    assert "owned-statistics-dataset" not in response.text


def test_resource_statistics_are_scoped_and_cache_invalidates_when_a_grant_changes(
    authenticated_client,
) -> None:
    client, session_factory, _, identity = authenticated_client
    with session_factory() as session:
        shared_node = ComputeNode(
            name="visible-statistics-node",
            organization_id=identity["organization_id"],
            owner_user_id=identity["admin_id"],
            architecture="x86_64",
            platform_kind="linux",
            agent_version="1.0",
            status="online",
            inventory_refreshed_at=datetime.now(UTC),
            resources={"cpu_utilization_percent": 0.0},
            fingerprint={},
        )
        hidden_node = ComputeNode(
            name="hidden-statistics-node",
            organization_id=identity["organization_id"],
            owner_user_id=identity["admin_id"],
            architecture="x86_64",
            platform_kind="linux",
            agent_version="1.0",
            status="online",
            inventory_refreshed_at=datetime.now(UTC),
            resources={"cpu_utilization_percent": 99.0},
            fingerprint={},
        )
        session.add_all([shared_node, hidden_node])
        session.flush()
        session.add(
            ResourceGrant(
                organization_id=identity["organization_id"],
                resource_type="node",
                resource_id=shared_node.id,
                principal_type=PRINCIPAL_USER,
                principal_id=identity["member_id"],
                permissions=["view"],
                created_by=identity["admin_id"],
            )
        )
        session.commit()

    first = client.get("/statistics/resources", headers=identity["member_headers"])
    assert first.status_code == 200, first.text
    first_payload = first.json()
    assert first_payload["nodes"]["freshness"]["fresh"] == 1
    assert first_payload["nodes"]["resource_usage"]["cpu_utilization_percent"]["value"] == 0.0
    assert first_payload["generated_at"]
    assert "hidden-statistics-node" not in first.text
    assert "visible-statistics-node" not in first.text

    # A newly granted resource must not receive another actor's cached payload.
    with session_factory() as session:
        hidden_node = session.scalar(
            select(ComputeNode).where(ComputeNode.name == "hidden-statistics-node")
        )
        assert hidden_node is not None
        session.add(
            ResourceGrant(
                organization_id=identity["organization_id"],
                resource_type="node",
                resource_id=hidden_node.id,
                principal_type=PRINCIPAL_USER,
                principal_id=identity["member_id"],
                permissions=["view"],
                created_by=identity["admin_id"],
            )
        )
        session.commit()

    second = client.get("/statistics/resources", headers=identity["member_headers"])
    assert second.status_code == 200, second.text
    second_payload = second.json()
    assert second_payload["nodes"]["freshness"]["fresh"] == 2
    assert second_payload["nodes"]["resource_usage"]["cpu_utilization_percent"]["value"] == 49.5

    with session_factory() as session:
        grant = session.scalar(
            select(ResourceGrant).where(
                ResourceGrant.resource_type == "node",
                ResourceGrant.resource_id == hidden_node.id,
                ResourceGrant.principal_id == identity["member_id"],
            )
        )
        assert grant is not None
        session.delete(grant)
        session.commit()

    revoked = client.get("/statistics/resources", headers=identity["member_headers"])
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["nodes"]["freshness"]["fresh"] == 1


def test_statistics_cache_is_copy_safe_actor_isolated_and_bounded(monkeypatch) -> None:
    clock = [datetime(2026, 7, 28, tzinfo=UTC)]
    cache = StatisticsCache(now=lambda: clock[0], ttl=timedelta(seconds=5), max_entries=2)
    monkeypatch.setattr(statistics_routes, "_authorization_revision", lambda session, actor: "r1")
    first_actor = SimpleNamespace(organization_id="org", id="user-1", role="member")
    second_actor = SimpleNamespace(organization_id="org", id="user-2", role="member")

    first = cache.get_or_create(object(), first_actor, "overview", lambda: {"values": [1]})
    first["values"].append(99)
    cached = cache.get_or_create(object(), first_actor, "overview", lambda: {"values": [2]})
    second = cache.get_or_create(object(), second_actor, "overview", lambda: {"values": [3]})

    assert cached == {"values": [1]}
    assert second == {"values": [3]}
    assert len(cache._entries) == 2

    cache.get_or_create(object(), first_actor, "resources", lambda: {"values": [4]})
    assert len(cache._entries) == 2

    clock[0] += timedelta(seconds=6)
    cache.get_or_create(object(), first_actor, "fresh", lambda: {"values": [5]})
    assert len(cache._entries) == 1


def test_administrator_statistics_cover_only_their_organization_without_resource_details(
    authenticated_client,
) -> None:
    client, session_factory, _, identity = authenticated_client
    with session_factory() as session:
        pipeline = TrainingPipeline(
            name="administrator-statistics-pipeline",
            task="detect",
            scale="n",
            organization_id=identity["organization_id"],
            owner_user_id=identity["admin_id"],
            status="configured",
        )
        outsider_dataset = Dataset(
            name="outsider-statistics-dataset",
            task="detect",
            organization_id=identity["other_organization_id"],
            owner_user_id=identity["outsider_id"],
            status="validated",
        )
        session.add_all([pipeline, outsider_dataset])
        session.commit()

    overview = client.get(
        "/admin/statistics/overview", headers=identity["admin_headers"]
    )
    resources = client.get(
        "/admin/statistics/resources", headers=identity["admin_headers"]
    )

    assert overview.status_code == 200, overview.text
    assert resources.status_code == 200, resources.text
    assert overview.json()["totals"]["pipelines"] == 1
    assert overview.json()["totals"]["datasets"] == 0
    assert overview.json()["generated_at"]
    assert resources.json()["generated_at"]
    assert "administrator-statistics-pipeline" not in overview.text
    assert "outsider-statistics-dataset" not in overview.text


def test_admin_overview_includes_only_recent_organization_failures_with_safe_fields(
    authenticated_client,
) -> None:
    client, session_factory, _, identity = authenticated_client
    base_time = datetime(2026, 7, 29, 9, 0, tzinfo=UTC)
    with session_factory() as session:
        failed_pipeline = TrainingPipeline(
            name="recent-failed-pipeline",
            task="detect",
            scale="n",
            organization_id=identity["organization_id"],
            owner_user_id=identity["admin_id"],
            status="failed",
        )
        healthy_pipeline = TrainingPipeline(
            name="healthy-pipeline",
            task="detect",
            scale="n",
            organization_id=identity["organization_id"],
            owner_user_id=identity["admin_id"],
            status="success",
        )
        outsider_pipeline = TrainingPipeline(
            name="outsider-failed-pipeline",
            task="detect",
            scale="n",
            organization_id=identity["other_organization_id"],
            owner_user_id=identity["outsider_id"],
            status="failed",
        )
        session.add_all([failed_pipeline, healthy_pipeline, outsider_pipeline])
        session.flush()
        failed_job = TrainingJob(
            pipeline_id=healthy_pipeline.id,
            organization_id=identity["organization_id"],
            owner_user_id=identity["admin_id"],
            status="failed",
            metrics={"private_error": "must not be returned"},
        )
        cross_organization_job = TrainingJob(
            pipeline_id=outsider_pipeline.id,
            organization_id=identity["organization_id"],
            owner_user_id=identity["admin_id"],
            status="failed",
        )
        failed_service = DeploymentService(
            name="recent-failed-service",
            pipeline_id=healthy_pipeline.id,
            organization_id=identity["organization_id"],
            owner_user_id=identity["admin_id"],
            model_name="model",
            model_weight="best.pt",
            environment="cpu",
            endpoint="http://secret.internal",
            status="failed",
            config={"token": "must not be returned"},
        )
        failed_node = ComputeNode(
            name="recent-failed-node",
            organization_id=identity["organization_id"],
            owner_user_id=identity["admin_id"],
            architecture="x86_64",
            platform_kind="linux",
            agent_version="1.0",
            status="offline",
            fingerprint={"serial": "must not be returned"},
        )
        session.add_all([failed_job, cross_organization_job, failed_service, failed_node])
        session.flush()
        failed_pipeline.updated_at = base_time
        failed_job.updated_at = base_time + timedelta(minutes=1)
        failed_service.updated_at = base_time + timedelta(minutes=2)
        failed_node.updated_at = base_time + timedelta(minutes=3)
        cross_organization_job.updated_at = base_time + timedelta(minutes=4)
        session.commit()

    response = client.get(
        "/admin/statistics/overview", headers=identity["admin_headers"]
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    failures = payload["recent_failures"]
    assert [failure["resource_type"] for failure in failures] == [
        "training_job",
        "node",
        "service",
        "training_job",
        "pipeline",
    ]
    assert failures[0]["name"].startswith("Training job ")
    assert failures[3]["name"] == "healthy-pipeline"
    assert all(
        set(failure) == {"resource_type", "resource_id", "name", "status", "updated_at"}
        for failure in failures
    )
    assert "outsider-failed-pipeline" not in response.text
    assert "private_error" not in response.text
    assert "secret.internal" not in response.text
    assert "must not be returned" not in response.text
