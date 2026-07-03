import json
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


def test_alembic_upgrade_creates_database_core_tables(tmp_path):
    database_path = tmp_path / "visiox.db"
    database_url = f"sqlite:///{database_path}"

    command.upgrade(_alembic_config(database_url), "head")

    engine = create_engine(database_url)
    table_names = set(inspect(engine).get_table_names())

    assert EXPECTED_TABLES.issubset(table_names)
    assert {"users", "roles", "permissions"}.isdisjoint(table_names)


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
