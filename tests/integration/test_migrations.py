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
        "datasets", "training_pipelines", "training_jobs", "trained_models",
        "deployment_services", "resource_pools", "compute_nodes",
    ):
        columns = {column["name"]: column for column in inspector.get_columns(table)}
        assert columns["organization_id"]["nullable"] is True
        assert columns["owner_user_id"]["nullable"] is True
        assert columns["visibility"]["nullable"] is False
        assert {"organization_id", "owner_user_id", "visibility"} <= {
            column for index in inspector.get_indexes(table) for column in index["column_names"]
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
        "datasets", "training_pipelines", "training_jobs", "trained_models",
        "deployment_services", "resource_pools", "compute_nodes",
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


def test_durable_logs_and_service_revision_migration_backfills_existing_services(tmp_path):
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
                text("SELECT deployment_service_id, deployment_revision FROM deployment_instances")
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


def test_dataset_version_migration_backfills_validated_assets_and_event_constraints(tmp_path):
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
    assert {"dataset_versions", "label_sync_events"}.issubset(inspector.get_table_names())
    dataset_columns = {column["name"]: column for column in inspector.get_columns("datasets")}
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
        roles = dict(connection.execute(text("SELECT id, asset_role FROM datasets")).all())
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
