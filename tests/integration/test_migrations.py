import json
import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


EXPECTED_TABLES = {
    "tasks",
    "model_sources",
    "base_models",
    "trained_models",
    "training_pipelines",
    "training_jobs",
    "datasets",
    "dataset_samples",
    "annotations",
    "label_projects",
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
    assert {"users", "roles", "permissions"}.isdisjoint(table_names)


def test_alembic_uses_environment_database_url_over_ini_default(tmp_path, monkeypatch):
    database_path = tmp_path / "visiox-env.db"
    database_url = f"sqlite:///{database_path}"
    monkeypatch.setenv("VISIOX_POSTGRES_DSN", database_url)

    command.upgrade(_default_alembic_config(), "head")

    engine = create_engine(database_url)
    table_names = set(inspect(engine).get_table_names())

    assert EXPECTED_TABLES.issubset(table_names)


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
