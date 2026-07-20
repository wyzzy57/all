import ast
import json
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
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
    assert {"users", "roles", "permissions"}.isdisjoint(table_names)


def test_alembic_uses_environment_database_url_over_ini_default(tmp_path, monkeypatch):
    database_path = tmp_path / "visiox-env.db"
    database_url = f"sqlite:///{database_path}"
    monkeypatch.setenv("VISIOX_POSTGRES_DSN", database_url)

    command.upgrade(_default_alembic_config(), "head")

    engine = create_engine(database_url)
    table_names = set(inspect(engine).get_table_names())

    assert EXPECTED_TABLES.issubset(table_names)
    assert REMOVED_TABLES.isdisjoint(table_names)


def test_agent_identity_recovery_columns_upgrade_downgrade_and_upgrade(tmp_path):
    database_path = tmp_path / "agent-identity-recovery.db"
    database_url = f"sqlite:///{database_path}"
    config = _alembic_config(database_url)

    command.upgrade(config, "head")
    engine = create_engine(database_url)
    inspector = inspect(engine)
    token_columns = {column["name"] for column in inspector.get_columns("agent_enrollment_tokens")}
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
        column for constraint in inspector.get_unique_constraints("agent_enrollment_tokens") for column in constraint["column_names"]
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
        column for constraint in inspector.get_unique_constraints("remote_executions") for column in constraint["column_names"]
    }
    assert {foreign_key["referred_table"] for foreign_key in inspector.get_foreign_keys("remote_executions")} >= {
        "compute_nodes",
        "deployment_services",
    }
    assert {foreign_key["referred_table"] for foreign_key in inspector.get_foreign_keys("deployment_instances")} >= {
        "compute_nodes",
        "deployment_services",
    }
    assert {foreign_key["referred_table"] for foreign_key in inspector.get_foreign_keys("distributed_training_runs")} >= {
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
    }.issubset({index["name"] for table in (
        "remote_executions",
        "deployment_instances",
        "distributed_training_runs",
    ) for index in inspector.get_indexes(table)})

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
        if isinstance(node.func, ast.Attribute) and node.func.attr in {"create_index", "drop_index"}:
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

    assert "trained_models" not in {foreign_key["referred_table"] for foreign_key in foreign_keys}


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
    assert all(record["local_uri"].startswith("minio://models/base/") for record in records)
