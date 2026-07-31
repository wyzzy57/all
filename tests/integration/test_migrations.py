import ast
import json
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.dialects import postgresql


EXPECTED_TABLES = {
    "tasks",
    "model_sources",
    "base_models",
    "trained_models",
    "training_pipelines",
    "training_jobs",
    "pipeline_evaluations",
    "datasets",
    "dataset_samples",
    "annotations",
    "label_projects",
}

EXPECTED_TABLES |= {
    "resource_pools",
    "compute_nodes",
    "agent_enrollment_tokens",
    "node_commands",
    "node_events",
    "edge_ssh_credentials",
    "remote_executions",
    "deployment_instances",
    "distributed_training_runs",
    "training_job_attempts",
}

EXPECTED_TABLES |= {
    "organizations",
    "users",
    "user_groups",
    "user_group_memberships",
    "user_sessions",
    "resource_grants",
    "resource_allocation_policies",
    "audit_logs",
    "log_streams",
    "log_chunks",
}

REMOVED_TABLES = {
    "devices",
    "cameras",
    "edge_apps",
    "edge_app_versions",
    "deployments",
}


def _alembic_config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _default_alembic_config() -> Config:
    return Config("alembic.ini")


def test_alembic_upgrade_creates_database_core_tables(tmp_path):
    database_path = tmp_path / "visiox.db"
    database_url = f"sqlite:///{database_path}"

    command.upgrade(_alembic_config(database_url), "head")

    engine = create_engine(database_url)
    table_names = set(inspect(engine).get_table_names())

    assert EXPECTED_TABLES.issubset(table_names)
    assert REMOVED_TABLES.isdisjoint(table_names)
    assert {"roles", "permissions"}.isdisjoint(table_names)
    allocation_constraints = {
        constraint["name"]: tuple(constraint["column_names"])
        for constraint in inspect(engine).get_unique_constraints(
            "resource_allocation_policies"
        )
    }
    assert allocation_constraints["uq_resource_allocation_policy_identity"] == (
        "organization_id",
        "principal_type",
        "principal_id",
        "resource_pool_id",
    )


def test_alembic_uses_environment_database_url_over_ini_default(tmp_path, monkeypatch):
    database_path = tmp_path / "visiox-env.db"
    database_url = f"sqlite:///{database_path}"
    monkeypatch.setenv("VISIOX_POSTGRES_DSN", database_url)

    command.upgrade(_default_alembic_config(), "head")

    engine = create_engine(database_url)
    table_names = set(inspect(engine).get_table_names())

    assert EXPECTED_TABLES.issubset(table_names)
    assert REMOVED_TABLES.isdisjoint(table_names)


def test_unified_node_inventory_migration_backfills_existing_nodes(tmp_path):
    database_path = tmp_path / "unified-node-inventory.db"
    database_url = f"sqlite:///{database_path}"
    config = _alembic_config(database_url)

    command.upgrade(config, "20260727_0001")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO resource_pools "
                "(id, name, kind, selector, compatibility_policy, enabled) VALUES "
                "('pool-1', 'legacy-pool', 'x86', '{}', '{}', 1)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO compute_nodes "
                "(id, name, resource_pool_id, status, architecture, platform_kind, "
                "capabilities, resources, fingerprint, agent_version) VALUES "
                "('node-ssh', 'ssh-node', 'pool-1', 'online', 'x86_64', 'server', "
                "'{}', '{}', '{}', 'ssh-bootstrap'), "
                "('node-agent', 'agent-node', 'pool-1', 'online', 'x86_64', 'server', "
                "'{}', '{}', '{}', '0.1.0')"
            )
        )

    command.upgrade(config, "20260727_0002")
    inspector = inspect(create_engine(database_url))
    node_columns = {
        column["name"]: column for column in inspector.get_columns("compute_nodes")
    }
    assert {
        "enabled",
        "labels",
        "connection_method",
        "inventory_refreshed_at",
        "resource_revision",
    }.issubset(node_columns)
    assert node_columns["enabled"]["nullable"] is False
    assert node_columns["labels"]["nullable"] is False
    assert node_columns["connection_method"]["nullable"] is False
    assert node_columns["resource_revision"]["nullable"] is False
    assert {
        "ix_compute_nodes_enabled",
        "ix_compute_nodes_inventory_refreshed_at",
    }.issubset({index["name"] for index in inspector.get_indexes("compute_nodes")})

    with engine.connect() as connection:
        rows = {
            row.id: row
            for row in connection.execute(
                text(
                    "SELECT id, enabled, labels, connection_method, "
                    "inventory_refreshed_at, resource_revision FROM compute_nodes"
                )
            )
        }
    assert rows["node-ssh"].enabled == 1
    assert json.loads(rows["node-ssh"].labels) == {}
    assert rows["node-ssh"].connection_method == "ssh"
    assert rows["node-agent"].connection_method == "agent"
    assert rows["node-agent"].inventory_refreshed_at is None
    assert rows["node-agent"].resource_revision == 0


def test_training_pipeline_engine_migration_preserves_existing_rows(tmp_path):
    database_path = tmp_path / "pipeline-engine.db"
    database_url = f"sqlite:///{database_path}"
    config = _alembic_config(database_url)

    command.upgrade(config, "20260720_0001")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO training_pipelines "
                "(id, name, task, scale, params_template, default_environment, status, is_public, public_scope, is_favorite, created_at, updated_at) "
                "VALUES ('pipeline-1', 'existing', 'detect', 'n', '{}', '{}', 'draft', 0, '{}', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

    command.upgrade(config, "20260724_0001")
    inspector = inspect(create_engine(database_url))
    assert "engine" in {
        column["name"] for column in inspector.get_columns("training_pipelines")
    }
    with create_engine(database_url).connect() as connection:
        assert (
            connection.execute(
                text("SELECT engine FROM training_pipelines WHERE id = 'pipeline-1'")
            ).scalar_one()
            == "yolo26"
        )


def test_agent_identity_recovery_columns_upgrade_downgrade_and_upgrade(tmp_path):
    database_path = tmp_path / "agent-identity-recovery.db"
    database_url = f"sqlite:///{database_path}"
    config = _alembic_config(database_url)

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    inspector = inspect(engine)
    token_columns = {
        column["name"] for column in inspector.get_columns("agent_enrollment_tokens")
    }
    node_columns = {column["name"] for column in inspector.get_columns("compute_nodes")}
    assert {
        "enrollment_request_id",
        "enrollment_csr_fingerprint",
        "enrollment_certificate_pem",
        "enrollment_ca_certificate_pem",
        "enrollment_gateway_url",
        "enrollment_heartbeat_interval_seconds",
    }.issubset(token_columns)
    assert {
        "pending_certificate_serial",
        "pending_certificate_fingerprint",
        "pending_certificate_expires_at",
        "pending_certificate_pem",
        "pending_renewal_request_id",
        "pending_renewal_csr_fingerprint",
    }.issubset(node_columns)
    assert "enrollment_request_id" in {
        column
        for constraint in inspector.get_unique_constraints("agent_enrollment_tokens")
        for column in constraint["column_names"]
    }
    node_unique_columns = {
        column
        for constraint in inspector.get_unique_constraints("compute_nodes")
        for column in constraint["column_names"]
    }
    assert {
        "pending_certificate_serial",
        "pending_certificate_fingerprint",
        "pending_renewal_request_id",
    }.issubset(node_unique_columns)
    assert "ix_compute_nodes_pending_certificate_expires_at" in {
        index["name"] for index in inspector.get_indexes("compute_nodes")
    }

    command.downgrade(config, "20260715_0001")
    inspector = inspect(create_engine(database_url))
    assert "enrollment_request_id" not in {
        column["name"] for column in inspector.get_columns("agent_enrollment_tokens")
    }
    assert "pending_certificate_pem" not in {
        column["name"] for column in inspector.get_columns("compute_nodes")
    }

    command.upgrade(config, "head")
    inspector = inspect(create_engine(database_url))
    assert "enrollment_request_id" in {
        column["name"] for column in inspector.get_columns("agent_enrollment_tokens")
    }


def test_ssh_edge_runtime_upgrade_from_current_head_and_downgrade_back(tmp_path):
    database_path = tmp_path / "ssh-edge-runtime.db"
    database_url = f"sqlite:///{database_path}"
    config = _alembic_config(database_url)

    command.upgrade(config, "20260717_0001")
    inspector = inspect(create_engine(database_url))
    assert {
        "edge_ssh_credentials",
        "remote_executions",
        "deployment_instances",
        "distributed_training_runs",
    }.isdisjoint(inspector.get_table_names())

    command.upgrade(config, "20260720_0001")
    inspector = inspect(create_engine(database_url))
    assert {
        "edge_ssh_credentials",
        "remote_executions",
        "deployment_instances",
        "distributed_training_runs",
    }.issubset(inspector.get_table_names())
    credential_unique_columns = {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("edge_ssh_credentials")
    }
    assert ("node_id",) in credential_unique_columns
    assert ("ssh_host", "ssh_port") in credential_unique_columns
    assert "idempotency_key" in {
        column
        for constraint in inspector.get_unique_constraints("remote_executions")
        for column in constraint["column_names"]
    }
    assert {
        foreign_key["referred_table"]
        for foreign_key in inspector.get_foreign_keys("remote_executions")
    } >= {
        "compute_nodes",
        "deployment_services",
    }
    assert {
        foreign_key["referred_table"]
        for foreign_key in inspector.get_foreign_keys("deployment_instances")
    } >= {
        "compute_nodes",
        "deployment_services",
    }
    assert {
        foreign_key["referred_table"]
        for foreign_key in inspector.get_foreign_keys("distributed_training_runs")
    } >= {
        "resource_pools",
        "training_jobs",
    }
    assert {
        "ix_remote_executions_node_id",
        "ix_remote_executions_status",
        "ix_deployment_instances_deployment_service_id",
        "ix_deployment_instances_node_id",
        "ix_distributed_training_runs_training_job_id",
        "ix_distributed_training_runs_resource_pool_id",
    }.issubset(
        {
            index["name"]
            for table in (
                "remote_executions",
                "deployment_instances",
                "distributed_training_runs",
            )
            for index in inspector.get_indexes(table)
        }
    )

    command.downgrade(config, "20260717_0001")
    inspector = inspect(create_engine(database_url))
    assert {
        "edge_ssh_credentials",
        "remote_executions",
        "deployment_instances",
        "distributed_training_runs",
    }.isdisjoint(inspector.get_table_names())


def test_ssh_edge_migration_explicit_identifiers_fit_postgresql():
    migration_path = Path("infra/migrations/versions/20260720_0001_ssh_edge_runtime.py")
    tree = ast.parse(migration_path.read_text(encoding="utf-8"))
    identifiers: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr in {
            "create_index",
            "drop_index",
        }:
            if node.args and isinstance(node.args[0], ast.Constant):
                identifiers.append(node.args[0].value)
        for keyword in node.keywords:
            if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                identifiers.append(keyword.value.value)

    dialect = postgresql.dialect()
    preparer = dialect.identifier_preparer
    assert identifiers
    for identifier in identifiers:
        dialect.validate_identifier(identifier)
        assert preparer.quote_identifier(identifier)


def test_training_jobs_does_not_reference_trained_models(tmp_path):
    database_path = tmp_path / "visiox.db"
    database_url = f"sqlite:///{database_path}"

    command.upgrade(_alembic_config(database_url), "head")

    engine = create_engine(database_url)
    foreign_keys = inspect(engine).get_foreign_keys("training_jobs")

    assert "trained_models" not in {
        foreign_key["referred_table"] for foreign_key in foreign_keys
    }


def test_resource_ownership_nullable_migration_adds_compatible_columns(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'resource-ownership.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "20260727_0002")
    command.upgrade(config, "20260727_0003")

    inspector = inspect(create_engine(database_url))
    for table in (
        "datasets",
        "training_pipelines",
        "training_jobs",
        "trained_models",
        "deployment_services",
        "resource_pools",
        "compute_nodes",
    ):
        columns = {column["name"]: column for column in inspector.get_columns(table)}
        assert columns["organization_id"]["nullable"] is True
        assert columns["owner_user_id"]["nullable"] is True
        assert columns["visibility"]["nullable"] is False
        assert {"organization_id", "owner_user_id", "visibility"} <= {
            column
            for index in inspector.get_indexes(table)
            for column in index["column_names"]
        }


def test_resource_ownership_constraints_require_backfill_and_reject_nulls(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'resource-ownership-constraints.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "20260727_0003")
    engine = create_engine(database_url)

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organizations (id, name, slug, status) "
                "VALUES ('org-1', 'Default', 'default', 'active')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, organization_id, username, display_name, email, password_hash, "
                "role, status, must_change_password) VALUES "
                "('user-1', 'org-1', 'admin', 'Admin', 'admin@example.test', "
                "'hash', 'admin', 'active', false)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO datasets "
                "(id, name, task, status, class_schema, sample_count, annotation_count, "
                "created_at, updated_at) VALUES "
                "('dataset-1', 'legacy', 'detect', 'created', '{}', 0, 0, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

    try:
        command.upgrade(config, "20260727_0004")
    except RuntimeError as error:
        assert "datasets=1" in str(error)
    else:
        raise AssertionError("constraint migration accepted an unowned dataset")

    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE datasets SET organization_id='org-1', owner_user_id='user-1' "
                "WHERE id='dataset-1'"
            )
        )
    command.upgrade(config, "20260727_0004")

    inspector = inspect(engine)
    for table in (
        "datasets",
        "training_pipelines",
        "training_jobs",
        "trained_models",
        "deployment_services",
        "resource_pools",
        "compute_nodes",
    ):
        columns = {column["name"]: column for column in inspector.get_columns(table)}
        assert columns["organization_id"]["nullable"] is False
        assert columns["owner_user_id"]["nullable"] is False

    with engine.begin() as connection:
        try:
            connection.execute(
                text(
                    "INSERT INTO datasets "
                    "(id, name, task, status, class_schema, sample_count, annotation_count, "
                    "created_at, updated_at) VALUES "
                    "('dataset-2', 'invalid', 'detect', 'created', '{}', 0, 0, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
        except Exception as error:
            assert "NOT NULL" in str(error).upper()
        else:
            raise AssertionError("database accepted a dataset without ownership")


def test_durable_logs_and_service_revision_migration_backfills_existing_services(
    tmp_path,
):
    database_url = f"sqlite:///{tmp_path / 'durable-logs.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "20260727_0004")
    engine = create_engine(database_url)

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organizations (id, name, slug, status) "
                "VALUES ('org-logs', 'Logs', 'logs', 'active')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, organization_id, username, display_name, email, password_hash, "
                "role, status, must_change_password) VALUES "
                "('user-logs', 'org-logs', 'admin', 'Admin', 'logs@example.test', "
                "'hash', 'admin', 'active', false)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO training_pipelines "
                "(id, name, organization_id, owner_user_id, visibility, engine, task, "
                "scale, params_template, default_environment, status, is_public, "
                "public_scope, is_favorite, created_at, updated_at) VALUES "
                "('pipeline-logs', 'pipeline-logs', 'org-logs', 'user-logs', 'private', "
                "'yolo26', 'detect', 'n', '{}', '{}', 'ready', false, '{}', false, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        for service_id, status in (
            ("service-running", "running"),
            ("service-stopped", "stopped"),
            ("service-failed", "failed"),
        ):
            connection.execute(
                text(
                    "INSERT INTO deployment_services "
                    "(id, name, organization_id, owner_user_id, visibility, pipeline_id, "
                    "model_name, model_weight, environment, instance_count, instance_name, "
                    "resource_summary, status, endpoint, calls, config, created_at, updated_at) "
                    "VALUES (:id, :id, 'org-logs', 'user-logs', 'private', 'pipeline-logs', "
                    "'yolo26n.pt', 'best.pt', 'cpu', 1, 'default', '', :status, '', 0, '{}', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"id": service_id, "status": status},
            )
        connection.execute(
            text(
                "INSERT INTO resource_pools "
                "(id, name, organization_id, owner_user_id, visibility, kind, selector, "
                "compatibility_policy, enabled, created_at, updated_at) VALUES "
                "('pool-logs', 'pool-logs', 'org-logs', 'user-logs', 'private', 'x86', "
                "'{}', '{}', true, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO compute_nodes "
                "(id, name, organization_id, owner_user_id, visibility, resource_pool_id, "
                "status, architecture, platform_kind, capabilities, resources, fingerprint, "
                "agent_version, enabled, labels, connection_method, resource_revision, "
                "created_at, updated_at) VALUES "
                "('node-logs', 'node-logs', 'org-logs', 'user-logs', 'private', 'pool-logs', "
                "'online', 'x86_64', 'server', '{}', '{}', '{}', 'test', true, '{}', 'ssh', 0, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        for service_id, instance_status in (
            ("service-running", "running"),
            ("service-stopped", "stopped"),
            ("service-failed", "failed"),
        ):
            connection.execute(
                text(
                    "INSERT INTO deployment_instances "
                    "(id, deployment_service_id, node_id, instance_name, engine, status, "
                    "rollback_metadata, created_at, updated_at) VALUES "
                    "(:id, :service_id, 'node-logs', 'default', 'engine', :status, '{}', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {
                    "id": f"instance-{service_id}",
                    "service_id": service_id,
                    "status": instance_status,
                },
            )

    command.upgrade(config, "20260727_0005")

    inspector = inspect(engine)
    assert {"log_streams", "log_chunks"}.issubset(inspector.get_table_names())
    service_columns = {
        column["name"]: column
        for column in inspector.get_columns("deployment_services")
    }
    instance_columns = {
        column["name"]: column
        for column in inspector.get_columns("deployment_instances")
    }
    assert service_columns["desired_state"]["nullable"] is False
    assert "active_revision" in service_columns
    assert instance_columns["deployment_revision"]["nullable"] is False
    assert "ix_log_streams_resource" in {
        index["name"] for index in inspector.get_indexes("log_streams")
    }
    assert "ix_log_chunks_stream_sequence" in {
        index["name"] for index in inspector.get_indexes("log_chunks")
    }
    assert ("stream_id", "sequence") in {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("log_chunks")
    }

    with engine.connect() as connection:
        desired_states = dict(
            connection.execute(
                text("SELECT id, desired_state FROM deployment_services")
            ).all()
        )
        revisions = dict(
            connection.execute(
                text(
                    "SELECT deployment_service_id, deployment_revision FROM deployment_instances"
                )
            ).all()
        )
        active_revisions = dict(
            connection.execute(
                text("SELECT id, active_revision FROM deployment_services")
            ).all()
        )
    assert desired_states == {
        "service-running": "running",
        "service-stopped": "stopped",
        "service-failed": "running",
    }
    assert set(revisions.values()) == {1}
    assert set(active_revisions.values()) == {1}


def test_dataset_version_migration_backfills_validated_assets_and_event_constraints(
    tmp_path,
):
    database_url = f"sqlite:///{tmp_path / 'dataset-versions.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "20260727_0005")
    engine = create_engine(database_url)

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organizations (id, name, slug, status) "
                "VALUES ('org-datasets', 'Datasets', 'datasets', 'active')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, organization_id, username, display_name, email, password_hash, "
                "role, status, must_change_password) VALUES "
                "('user-datasets', 'org-datasets', 'admin', 'Admin', "
                "'datasets@example.test', 'hash', 'admin', 'active', false)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO datasets "
                "(id, name, organization_id, owner_user_id, visibility, task, status, format, "
                "class_schema, schema_config, manifest_checksum, sample_count, annotation_count, "
                "storage_uri, created_at, updated_at) VALUES "
                "('dataset-published', 'published', 'org-datasets', 'user-datasets', 'private', "
                "'llm', 'validated', 'openai_messages', '{}', '{}', "
                "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 8, 6, "
                "'minio://datasets/dataset-published/train.jsonl', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP), "
                "('dataset-working', 'working', 'org-datasets', 'user-datasets', 'private', "
                "'llm', 'created', 'openai_messages', '{}', '{}', NULL, 8, 0, NULL, "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO label_projects "
                "(id, dataset_id, provider, external_project_id, sync_status, created_at, updated_at) "
                "VALUES ('label-project-1', 'dataset-working', 'label_studio', '42', 'pending', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

    command.upgrade(config, "20260727_0006")

    inspector = inspect(engine)
    assert {"dataset_versions", "label_sync_events"}.issubset(
        inspector.get_table_names()
    )
    dataset_columns = {
        column["name"]: column for column in inspector.get_columns("datasets")
    }
    assert dataset_columns["asset_role"]["nullable"] is False
    assert ("dataset_id", "version") in {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("dataset_versions")
    }
    assert ("provider", "event_key") in {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("label_sync_events")
    }

    with engine.connect() as connection:
        roles = dict(
            connection.execute(text("SELECT id, asset_role FROM datasets")).all()
        )
        version = connection.execute(
            text(
                "SELECT dataset_id, version, manifest_checksum, object_uri, total_count, valid_count "
                "FROM dataset_versions"
            )
        ).one()
    assert roles == {"dataset-published": "published", "dataset-working": "working"}
    assert tuple(version) == (
        "dataset-published",
        1,
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "minio://datasets/dataset-published/train.jsonl",
        8,
        6,
    )


def test_multi_framework_training_migration_preserves_and_backfills_legacy_rows(
    tmp_path,
):
    database_url = f"sqlite:///{tmp_path / 'multi-framework-training.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "20260727_0006")
    engine = create_engine(database_url)

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organizations (id, name, slug, status) VALUES "
                "('org-training', 'Training', 'training', 'active')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, organization_id, username, display_name, email, password_hash, "
                "role, status, must_change_password) VALUES "
                "('user-training', 'org-training', 'trainer', 'Trainer', "
                "'trainer@example.test', 'hash', 'admin', 'active', false)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO base_models "
                "(id, family, task, scale, filename, source_path, local_uri, checksum, "
                "size_bytes, status, created_at, updated_at) VALUES "
                "('base-yolo', 'yolo26', 'detect', 'n', 'yolo26n.pt', "
                "'models/yolo26n.pt', 'minio://models/base/yolo26n.pt', NULL, NULL, "
                "'ready', '2026-07-01 01:00:00', '2026-07-01 01:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO training_pipelines "
                "(id, name, organization_id, owner_user_id, visibility, engine, task, scale, "
                "base_model_id, dataset_id, params_template, default_environment, status, is_public, "
                "public_scope, is_favorite, created_at, updated_at) VALUES "
                "('pipeline-yolo', 'legacy-yolo', 'org-training', 'user-training', "
                "'private', 'yolo26', 'detect', 'n', 'base-yolo', 'dataset-current-yolo', "
                '\'{"epochs": 12, "imgsz": 640}\', \'{"device": "0"}\', '
                "'ready', false, '{}', true, '2026-07-01 02:00:00', "
                "'2026-07-01 02:00:00'), "
                "('pipeline-llama', 'legacy-llama', 'org-training', 'user-training', "
                "'private', 'llamafactory', 'sft', '7b', NULL, 'dataset-current-llama', "
                '\'{"model_family": "Qwen2.5", "finetuning_type": "lora"}\', '
                "'{\"device\": \"0,1\"}', 'ready', false, '{}', false, "
                "'2026-07-02 02:00:00', '2026-07-02 02:00:00'), "
                "('pipeline-draft', 'legacy-draft', 'org-training', 'user-training', "
                "'private', 'yolo26', 'detect', 's', NULL, NULL, '{\"epochs\": 3}', '{}', "
                "'draft', false, '{}', false, '2026-07-03 02:00:00', "
                "'2026-07-03 02:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO tasks "
                "(id, task_type, status, progress, resource_type, resource_id, payload, "
                "retryable, created_at, updated_at) VALUES "
                "('task-yolo', 'TRAIN_MODEL', 'COMPLETED', 100, 'training_job', "
                "'job-yolo', '{\"base_model_id\": \"base-submitted-yolo\"}', false, "
                "'2026-07-04 02:00:00', '2026-07-04 04:00:00'), "
                "('task-llama', 'EDGE_TRAIN', 'COMPLETED', 100, 'training_job', "
                '\'job-llama\', \'{"dataset_id": "dataset-submitted-llama", '
                '"model_reference": {"model_id": "Qwen/Qwen2.5-7B"}}\', false, '
                "'2026-07-05 02:00:00', '2026-07-05 05:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO training_jobs "
                "(id, pipeline_id, organization_id, owner_user_id, visibility, task_id, status, params, "
                "metrics, log_uri, started_at, finished_at, created_at, updated_at) VALUES "
                "('job-yolo', 'pipeline-yolo', 'org-training', 'user-training', 'private', 'task-yolo', "
                "'completed', '{\"batch\": 8}', '{\"map50\": 0.81}', "
                "'minio://logs/job-yolo.log', '2026-07-04 03:00:00', "
                "'2026-07-04 04:00:00', '2026-07-04 02:00:00', "
                "'2026-07-04 04:00:00'), "
                "('job-llama', 'pipeline-llama', 'org-training', 'user-training', 'private', 'task-llama', "
                "'completed', '{\"learning_rate\": 0.0001}', '{\"loss\": 0.42}', "
                "'minio://logs/job-llama.log', '2026-07-05 03:00:00', "
                "'2026-07-05 05:00:00', '2026-07-05 02:00:00', "
                "'2026-07-05 05:00:00')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO trained_models "
                "(id, pipeline_id, organization_id, owner_user_id, visibility, training_job_id, "
                "name, version, task, artifact_uri, metrics, status, created_at, updated_at) VALUES "
                "('model-yolo', 'pipeline-yolo', 'org-training', 'user-training', 'private', "
                "'job-yolo', 'pepper-detector', '1', 'detect', "
                "'minio://models/job-yolo/best.pt', '{\"map50\": 0.81}', 'ready', "
                "'2026-07-04 04:00:00', '2026-07-04 04:00:00'), "
                "('model-llama', 'pipeline-llama', 'org-training', 'user-training', 'private', "
                "'job-llama', 'support-assistant', '1', 'sft', "
                "'minio://models/job-llama/adapter_model.safetensors', "
                "'{\"loss\": 0.42}', 'ready', '2026-07-05 05:00:00', "
                "'2026-07-05 05:00:00')"
            )
        )

    legacy_rows = {}
    with engine.connect() as connection:
        for table in (
            "base_models",
            "tasks",
            "training_pipelines",
            "training_jobs",
            "trained_models",
        ):
            legacy_rows[table] = (
                connection.execute(text(f"SELECT * FROM {table} ORDER BY id"))
                .mappings()
                .all()
            )

    command.upgrade(config, "20260731_0001")

    inspector = inspect(engine)
    assert "training_job_attempts" in inspector.get_table_names()
    pipeline_columns = {
        column["name"]: column for column in inspector.get_columns("training_pipelines")
    }
    trained_model_columns = {
        column["name"]: column for column in inspector.get_columns("trained_models")
    }
    assert {
        "task_kind",
        "framework",
        "adapter_key",
        "adapter_version",
        "model_family",
        "recipe",
        "framework_locked_at",
        "first_submitted_job_id",
        "cloned_from_pipeline_id",
    } <= pipeline_columns.keys()
    assert all(
        not pipeline_columns[column]["nullable"]
        for column in (
            "task_kind",
            "framework",
            "adapter_key",
            "adapter_version",
            "model_family",
            "recipe",
        )
    )
    assert all(
        trained_model_columns[column]["nullable"]
        for column in (
            "framework",
            "adapter_key",
            "model_family",
            "model_format",
        )
    )
    assert trained_model_columns["artifact_role"]["nullable"] is False
    assert {
        "ix_training_pipelines_cloned_from_pipeline_id",
        "ix_training_pipelines_first_submitted_job_id",
    } <= {index["name"] for index in inspector.get_indexes("training_pipelines")}

    with engine.connect() as connection:
        pipelines = {
            row["id"]: row
            for row in connection.execute(
                text(
                    "SELECT id, engine, task, scale, task_kind, framework, adapter_key, "
                    "adapter_version, model_family, recipe, framework_locked_at, "
                    "first_submitted_job_id, cloned_from_pipeline_id FROM training_pipelines"
                )
            ).mappings()
        }
        jobs = {
            row["id"]: row
            for row in connection.execute(
                text(
                    "SELECT id, params, metrics, resolved_snapshot, launch_spec_checksum "
                    "FROM training_jobs"
                )
            ).mappings()
        }
        models = {
            row["id"]: row
            for row in connection.execute(
                text(
                    "SELECT id, name, artifact_uri, metrics, status, framework, adapter_key, "
                    "model_family, model_format, artifact_role, checksum, size_bytes, "
                    "evaluation_report_uri, deployment_compatibility, artifact_manifest, "
                    "display_name, training_job_attempt_id FROM trained_models"
                )
            ).mappings()
        }
        base_model = (
            connection.execute(
                text(
                    "SELECT family, task, scale, filename, checksum, framework, model_family, "
                    "variant, artifact_format, artifact_metadata FROM base_models "
                    "WHERE id='base-yolo'"
                )
            )
            .mappings()
            .one()
        )
        attempt_count = connection.execute(
            text("SELECT COUNT(*) FROM training_job_attempts")
        ).scalar_one()

    assert pipelines["pipeline-yolo"] == {
        "id": "pipeline-yolo",
        "engine": "yolo26",
        "task": "detect",
        "scale": "n",
        "task_kind": "object_detection",
        "framework": "ultralytics",
        "adapter_key": "ultralytics.object_detection.v1",
        "adapter_version": "1.0.0",
        "model_family": "yolo26",
        "recipe": '{"epochs": 12, "imgsz": 640}',
        "framework_locked_at": "2026-07-04 02:00:00",
        "first_submitted_job_id": "job-yolo",
        "cloned_from_pipeline_id": None,
    }
    assert pipelines["pipeline-llama"]["task_kind"] == "llm_sft"
    assert pipelines["pipeline-llama"]["framework"] == "llamafactory"
    assert pipelines["pipeline-llama"]["adapter_key"] == "llamafactory.llm_sft.v1"
    assert pipelines["pipeline-llama"]["adapter_version"] == "1.0.0"
    assert pipelines["pipeline-llama"]["model_family"] == "Qwen2.5"
    assert pipelines["pipeline-draft"]["model_family"] == "yolo26"
    assert pipelines["pipeline-draft"]["framework_locked_at"] is None
    assert pipelines["pipeline-draft"]["first_submitted_job_id"] is None
    assert json.loads(jobs["job-yolo"]["params"]) == {"batch": 8}
    assert json.loads(jobs["job-yolo"]["metrics"]) == {"map50": 0.81}
    assert jobs["job-yolo"]["launch_spec_checksum"] is None
    assert json.loads(jobs["job-yolo"]["resolved_snapshot"]) == {
        "adapter_key": "ultralytics.object_detection.v1",
        "adapter_version": "1.0.0",
        "base_model_id": "base-submitted-yolo",
        "framework": "ultralytics",
        "legacy_engine": "yolo26",
        "legacy_scale": "n",
        "legacy_task": "detect",
        "model_family": "yolo26",
        "params": {"batch": 8},
        "pipeline_id": "pipeline-yolo",
        "task_kind": "object_detection",
    }
    llama_snapshot = json.loads(jobs["job-llama"]["resolved_snapshot"])
    assert llama_snapshot["dataset_id"] == "dataset-submitted-llama"
    assert "base_model_id" not in llama_snapshot
    assert "dataset_id" not in json.loads(jobs["job-yolo"]["resolved_snapshot"])
    assert models["model-yolo"]["name"] == "pepper-detector"
    assert models["model-yolo"]["artifact_uri"] == "minio://models/job-yolo/best.pt"
    assert json.loads(models["model-yolo"]["metrics"]) == {"map50": 0.81}
    assert models["model-yolo"]["status"] == "ready"
    assert models["model-yolo"]["model_format"] == "pt"
    assert models["model-llama"]["model_format"] == "safetensors"
    for model in models.values():
        assert model["artifact_role"] == "legacy_primary"
        assert model["checksum"] is None
        assert model["size_bytes"] is None
        assert model["evaluation_report_uri"] is None
        assert json.loads(model["deployment_compatibility"]) == {}
        assert json.loads(model["artifact_manifest"]) == {}
        assert model["display_name"] == model["name"]
        assert model["training_job_attempt_id"] is None
    assert base_model == {
        "family": "yolo26",
        "task": "detect",
        "scale": "n",
        "filename": "yolo26n.pt",
        "checksum": None,
        "framework": "ultralytics",
        "model_family": "yolo26",
        "variant": "n",
        "artifact_format": "pt",
        "artifact_metadata": "{}",
    }
    assert attempt_count == 0
    assert len(pipelines) == 3
    assert len(jobs) == 2
    assert len(models) == 2

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO trained_models "
                "(id, pipeline_id, organization_id, owner_user_id, visibility, training_job_id, "
                "name, version, task, artifact_uri, metrics, status, artifact_role, "
                "deployment_compatibility, artifact_manifest, display_name, created_at, updated_at) "
                "VALUES ('model-yolo-unassigned', 'pipeline-yolo', 'org-training', "
                "'user-training', 'private', 'job-yolo', 'pending-role', '1', 'detect', "
                "'minio://models/job-yolo/pending', '{}', 'ready', 'unassigned', '{}', '{}', "
                "'pending-role', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        try:
            connection.execute(
                text(
                    "INSERT INTO trained_models "
                    "(id, pipeline_id, organization_id, owner_user_id, visibility, training_job_id, "
                    "name, version, task, artifact_uri, metrics, status, artifact_role, "
                    "deployment_compatibility, artifact_manifest, display_name, created_at, updated_at) "
                    "VALUES ('model-yolo-unassigned-duplicate', 'pipeline-yolo', 'org-training', "
                    "'user-training', 'private', 'job-yolo', 'duplicate-role', '1', 'detect', "
                    "'minio://models/job-yolo/pending-duplicate', '{}', 'ready', 'unassigned', "
                    "'{}', '{}', 'duplicate-role', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
        except Exception as error:
            assert "UNIQUE" in str(error).upper()
        else:
            raise AssertionError(
                "database accepted a duplicate unassigned artifact role"
            )
        connection.execute(
            text(
                "INSERT INTO trained_models "
                "(id, pipeline_id, organization_id, owner_user_id, visibility, training_job_id, "
                "name, version, task, artifact_uri, metrics, status, framework, adapter_key, "
                "model_family, model_format, artifact_role, deployment_compatibility, "
                "artifact_manifest, display_name, created_at, updated_at) VALUES "
                "('model-yolo-report', 'pipeline-yolo', 'org-training', 'user-training', "
                "'private', 'job-yolo', 'pepper-report', '1', 'detect', "
                "'minio://models/job-yolo/report.json', '{}', 'ready', 'ultralytics', "
                "'ultralytics.object_detection.v1', 'yolo26', 'json', "
                "'evaluation_report', '{}', '{}', 'pepper-report', CURRENT_TIMESTAMP, "
                "CURRENT_TIMESTAMP)"
            )
        )
        try:
            connection.execute(
                text(
                    "INSERT INTO trained_models "
                    "(id, pipeline_id, organization_id, owner_user_id, visibility, training_job_id, "
                    "name, version, task, artifact_uri, metrics, status, framework, adapter_key, "
                    "model_family, model_format, artifact_role, deployment_compatibility, "
                    "artifact_manifest, display_name, created_at, updated_at) VALUES "
                    "('model-yolo-duplicate', 'pipeline-yolo', 'org-training', 'user-training', "
                    "'private', 'job-yolo', 'duplicate', '1', 'detect', "
                    "'minio://models/job-yolo/duplicate.pt', '{}', 'ready', 'ultralytics', "
                    "'ultralytics.object_detection.v1', 'yolo26', 'pt', 'legacy_primary', "
                    "'{}', '{}', 'duplicate', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
        except Exception as error:
            assert "UNIQUE" in str(error).upper()
        else:
            raise AssertionError("database accepted a duplicate legacy artifact role")
        connection.execute(
            text(
                "DELETE FROM trained_models WHERE id IN "
                "('model-yolo-report', 'model-yolo-unassigned')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO training_job_attempts "
                "(id, training_job_id, attempt_number, status, launch_spec, metrics, "
                "artifact_manifest, container_ids, created_at, updated_at) VALUES "
                "('attempt-yolo-1', 'job-yolo', 1, 'completed', '{}', '{}', '{}', "
                "'[]', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        for model_id, role in (
            ("attempt-checkpoint", "checkpoint"),
            ("attempt-report", "evaluation_report"),
        ):
            connection.execute(
                text(
                    "INSERT INTO trained_models "
                    "(id, pipeline_id, organization_id, owner_user_id, visibility, "
                    "training_job_id, training_job_attempt_id, name, version, task, "
                    "artifact_uri, metrics, status, framework, adapter_key, model_family, "
                    "model_format, artifact_role, deployment_compatibility, "
                    "artifact_manifest, display_name, created_at, updated_at) VALUES "
                    "(:id, 'pipeline-yolo', 'org-training', 'user-training', 'private', "
                    "'job-yolo', 'attempt-yolo-1', :id, '1', 'detect', "
                    "'minio://models/job-yolo/attempt-artifact', '{}', 'ready', "
                    "'ultralytics', 'ultralytics.object_detection.v1', 'yolo26', "
                    "'unknown', :role, '{}', '{}', :id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"id": model_id, "role": role},
            )
        try:
            connection.execute(
                text(
                    "INSERT INTO trained_models "
                    "(id, pipeline_id, organization_id, owner_user_id, visibility, "
                    "training_job_id, training_job_attempt_id, name, version, task, "
                    "artifact_uri, metrics, status, framework, adapter_key, model_family, "
                    "model_format, artifact_role, deployment_compatibility, "
                    "artifact_manifest, display_name, created_at, updated_at) VALUES "
                    "('attempt-checkpoint-duplicate', 'pipeline-yolo', 'org-training', "
                    "'user-training', 'private', 'job-yolo', 'attempt-yolo-1', "
                    "'duplicate', '1', 'detect', 'minio://models/job-yolo/duplicate', "
                    "'{}', 'ready', 'ultralytics', 'ultralytics.object_detection.v1', "
                    "'yolo26', 'unknown', 'checkpoint', '{}', '{}', 'duplicate', "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                )
            )
        except Exception as error:
            assert "UNIQUE" in str(error).upper()
        else:
            raise AssertionError("database accepted a duplicate attempt artifact role")
        connection.execute(
            text(
                "DELETE FROM trained_models WHERE training_job_attempt_id='attempt-yolo-1'"
            )
        )
        connection.execute(
            text("DELETE FROM training_job_attempts WHERE id='attempt-yolo-1'")
        )

    command.downgrade(config, "20260727_0006")

    inspector = inspect(engine)
    assert "training_job_attempts" not in inspector.get_table_names()
    assert "framework" not in {
        column["name"] for column in inspector.get_columns("base_models")
    }
    assert "task_kind" not in {
        column["name"] for column in inspector.get_columns("training_pipelines")
    }
    assert "resolved_snapshot" not in {
        column["name"] for column in inspector.get_columns("training_jobs")
    }
    assert "artifact_role" not in {
        column["name"] for column in inspector.get_columns("trained_models")
    }
    assert {
        index["name"]: tuple(index["column_names"])
        for index in inspector.get_indexes("trained_models")
        if index["unique"]
    }["uq_trained_models_training_job"] == ("training_job_id",)
    assert {
        tuple(constraint["column_names"])
        for constraint in inspector.get_unique_constraints("base_models")
    } >= {("family", "task", "scale"), ("filename",)}

    with engine.connect() as connection:
        for table, rows in legacy_rows.items():
            restored = (
                connection.execute(
                    text(
                        f"SELECT * FROM {table} WHERE id != 'model-yolo-report' ORDER BY id"
                    )
                )
                .mappings()
                .all()
            )
            assert [dict(row) for row in restored] == [dict(row) for row in rows]


def test_multi_framework_downgrade_preflight_is_atomic(tmp_path):
    database_url = f"sqlite:///{tmp_path / 'multi-framework-downgrade.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "20260731_0001")
    engine = create_engine(database_url)

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO organizations (id, name, slug, status) VALUES "
                "('org-downgrade', 'Downgrade', 'downgrade', 'active')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO users "
                "(id, organization_id, username, display_name, email, password_hash, role, "
                "status, must_change_password) VALUES ('user-downgrade', 'org-downgrade', "
                "'owner', 'Owner', 'owner@example.test', 'hash', 'admin', 'active', false)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO training_pipelines "
                "(id, name, organization_id, owner_user_id, visibility, engine, task, scale, "
                "task_kind, framework, adapter_key, adapter_version, model_family, recipe, "
                "params_template, default_environment, status, is_public, public_scope, "
                "is_favorite, created_at, updated_at) VALUES ('pipeline-downgrade', "
                "'pipeline-downgrade', 'org-downgrade', 'user-downgrade', 'private', "
                "'yolo26', 'detect', 'n', 'object_detection', 'ultralytics', "
                "'ultralytics.object_detection.v1', '1.0.0', 'yolo26', '{}', '{}', '{}', "
                "'ready', false, '{}', false, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO training_jobs "
                "(id, pipeline_id, organization_id, owner_user_id, visibility, status, params, "
                "metrics, resolved_snapshot, created_at, updated_at) VALUES ('job-downgrade', "
                "'pipeline-downgrade', 'org-downgrade', 'user-downgrade', 'private', "
                "'completed', '{}', '{}', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        for model_id, role in (
            ("model-downgrade-primary", "legacy_primary"),
            ("model-downgrade-report", "evaluation_report"),
        ):
            connection.execute(
                text(
                    "INSERT INTO trained_models "
                    "(id, pipeline_id, organization_id, owner_user_id, visibility, "
                    "training_job_id, name, version, task, artifact_uri, metrics, status, "
                    "artifact_role, deployment_compatibility, artifact_manifest, display_name, "
                    "created_at, updated_at) VALUES (:id, 'pipeline-downgrade', "
                    "'org-downgrade', 'user-downgrade', 'private', 'job-downgrade', :id, "
                    "'1', 'detect', :uri, '{}', 'ready', :role, '{}', '{}', :id, "
                    "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {
                    "id": model_id,
                    "role": role,
                    "uri": f"minio://models/{model_id}",
                },
            )

    def schema_snapshot():
        inspector = inspect(engine)
        with engine.connect() as connection:
            return {
                "tables": set(inspector.get_table_names()),
                "trained_columns": tuple(
                    column["name"] for column in inspector.get_columns("trained_models")
                ),
                "trained_indexes": tuple(
                    sorted(
                        index["name"]
                        for index in inspector.get_indexes("trained_models")
                    )
                ),
                "attempt_count": connection.execute(
                    text("SELECT COUNT(*) FROM training_job_attempts")
                ).scalar_one(),
                "model_count": connection.execute(
                    text("SELECT COUNT(*) FROM trained_models")
                ).scalar_one(),
                "revision": connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one(),
            }

    before = schema_snapshot()
    try:
        command.downgrade(config, "20260727_0006")
    except RuntimeError as error:
        assert "multiple trained models" in str(error)
        assert "reconcile artifacts" in str(error)
    else:
        raise AssertionError("downgrade accepted multiple models for one training job")
    assert schema_snapshot() == before

    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM trained_models WHERE id='model-downgrade-report'")
        )
        connection.execute(
            text(
                "INSERT INTO training_job_attempts "
                "(id, training_job_id, attempt_number, status, launch_spec, metrics, "
                "artifact_manifest, container_ids, created_at, updated_at) VALUES "
                "('attempt-downgrade', 'job-downgrade', 1, 'completed', '{}', '{}', '{}', "
                "'[]', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )

    before = schema_snapshot()
    try:
        command.downgrade(config, "20260727_0006")
    except RuntimeError as error:
        assert "training job attempts" in str(error)
        assert "remove attempts" in str(error)
    else:
        raise AssertionError("downgrade accepted persisted training attempts")
    assert schema_snapshot() == before

    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM training_job_attempts WHERE id='attempt-downgrade'")
        )
    command.downgrade(config, "20260727_0006")
    inspector = inspect(engine)
    assert "training_job_attempts" not in inspector.get_table_names()
    assert "artifact_role" not in {
        column["name"] for column in inspector.get_columns("trained_models")
    }


def test_multi_framework_downgrade_rejects_base_model_tuple_collisions_atomically(
    tmp_path,
):
    database_url = f"sqlite:///{tmp_path / 'base-model-downgrade.db'}"
    config = _alembic_config(database_url)
    command.upgrade(config, "20260731_0001")
    engine = create_engine(database_url)

    with engine.begin() as connection:
        for model_id, framework, filename, artifact_format in (
            ("base-ultralytics", "ultralytics", "shared.pt", "pt"),
            ("base-paddlex", "paddlex", "shared.pdparams", "pdparams"),
        ):
            connection.execute(
                text(
                    "INSERT INTO base_models "
                    "(id, family, task, scale, filename, source_path, status, framework, "
                    "model_family, variant, artifact_format, artifact_metadata, created_at, "
                    "updated_at) VALUES (:id, 'shared-family', 'detect', 'n', :filename, "
                    ":filename, 'ready', :framework, 'shared-family', 'n', "
                    ":artifact_format, '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {
                    "id": model_id,
                    "framework": framework,
                    "filename": filename,
                    "artifact_format": artifact_format,
                },
            )

    def schema_and_data_snapshot():
        inspector = inspect(engine)
        with engine.connect() as connection:
            return {
                "tables": tuple(sorted(inspector.get_table_names())),
                "trained_columns": tuple(
                    column["name"] for column in inspector.get_columns("trained_models")
                ),
                "base_columns": tuple(
                    column["name"] for column in inspector.get_columns("base_models")
                ),
                "base_unique_constraints": tuple(
                    sorted(
                        (
                            constraint["name"],
                            tuple(constraint["column_names"]),
                        )
                        for constraint in inspector.get_unique_constraints(
                            "base_models"
                        )
                    )
                ),
                "base_rows": tuple(
                    connection.execute(
                        text(
                            "SELECT id, family, task, scale, filename, framework "
                            "FROM base_models ORDER BY id"
                        )
                    ).all()
                ),
                "revision": connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one(),
            }

    before = schema_and_data_snapshot()
    try:
        command.downgrade(config, "20260727_0006")
    except RuntimeError as error:
        assert "legacy base model tuple collisions" in str(error)
        assert "reconcile base models" in str(error)
    else:
        raise AssertionError(
            "downgrade accepted colliding legacy base model identities"
        )
    assert schema_and_data_snapshot() == before


def test_multi_framework_training_migration_identifiers_fit_postgresql():
    migration_path = Path(
        "infra/migrations/versions/20260731_0001_multi_framework_training.py"
    )
    tree = ast.parse(migration_path.read_text(encoding="utf-8"))
    identifiers: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute) and node.func.attr in {
            "create_index",
            "drop_index",
            "create_foreign_key",
            "drop_constraint",
            "create_unique_constraint",
        }:
            if node.args and isinstance(node.args[0], ast.Constant):
                identifiers.append(node.args[0].value)
        for keyword in node.keywords:
            if keyword.arg == "name" and isinstance(keyword.value, ast.Constant):
                identifiers.append(keyword.value.value)

    dialect = postgresql.dialect()
    assert identifiers
    for identifier in identifiers:
        dialect.validate_identifier(identifier)


def test_multi_framework_training_migration_uses_bounded_backfill_reads():
    migration_path = Path(
        "infra/migrations/versions/20260731_0001_multi_framework_training.py"
    )
    tree = ast.parse(migration_path.read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]

    assert not any(
        isinstance(call.func, ast.Attribute) and call.func.attr == "all"
        for call in calls
    )
    assert any(
        isinstance(call.func, ast.Attribute) and call.func.attr == "fetchmany"
        for call in calls
    )


def test_session_module_import_does_not_read_settings(monkeypatch):
    import visiox_common.settings as settings

    sys.modules.pop("visiox_db.session", None)

    def fail_get_settings():
        raise AssertionError("settings should not be read at import time")

    monkeypatch.setattr(settings, "get_settings", fail_get_settings)

    __import__("visiox_db.session")


def test_yolo26_base_model_seed_has_all_tasks_and_scales():
    seed_path = Path("infra/seed/yolo26_base_models.json")

    records = json.loads(seed_path.read_text(encoding="utf-8"))

    assert len(records) == 30
    assert {record["task"] for record in records} == {
        "detect",
        "segment",
        "semantic",
        "pose",
        "obb",
        "classify",
    }
    assert {record["scale"] for record in records} == {"n", "s", "m", "l", "x"}
    assert len({record["id"] for record in records}) == 30
    assert len({record["filename"] for record in records}) == 30
    assert {record["status"] for record in records} == {"ready"}
    assert all(
        record["local_uri"].startswith("minio://models/base/") for record in records
    )
