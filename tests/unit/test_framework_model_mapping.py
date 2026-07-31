import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest
from sqlalchemy import inspect

import visiox_db.models.model_space as model_space
from visiox_db.models import (
    BaseModel,
    TrainedModel,
    TrainingJobAttempt,
    TrainingPipeline,
)
from visiox_training.errors import UnknownAdapterError
from visiox_training.registry import AdapterRegistry, LegacyEngineMapping


MIGRATION_PATH = Path(
    "infra/migrations/versions/20260731_0001_multi_framework_training.py"
)
EXPECTED_LEGACY_MAPPINGS = {
    "yolo26": {
        "task_kind": "object_detection",
        "framework": "ultralytics",
        "adapter_key": "ultralytics.object_detection.v1",
        "adapter_version": "1.0.0",
    },
    "llamafactory": {
        "task_kind": "llm_sft",
        "framework": "llamafactory",
        "adapter_key": "llamafactory.llm_sft.v1",
        "adapter_version": "1.0.0",
    },
}


def _load_migration_module():
    spec = importlib.util.spec_from_file_location(
        "multi_framework_training_migration", MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("engine", "expected"),
    [
        (
            "yolo26",
            LegacyEngineMapping(
                task_type="object_detection",
                framework="ultralytics",
                adapter_key="ultralytics.object_detection.v1",
            ),
        ),
        (
            "llamafactory",
            LegacyEngineMapping(
                task_type="llm_sft",
                framework="llamafactory",
                adapter_key="llamafactory.llm_sft.v1",
            ),
        ),
    ],
)
def test_model_migration_reuses_exact_legacy_engine_mapping(engine, expected) -> None:
    assert AdapterRegistry.legacy_engine_mapping(engine) == expected


def test_model_migration_rejects_unknown_legacy_engine() -> None:
    with pytest.raises(UnknownAdapterError, match="Unknown legacy engine 'unknown'"):
        AdapterRegistry.legacy_engine_mapping("unknown")


def test_migration_snapshot_matches_authoritative_legacy_mapping() -> None:
    migration = _load_migration_module()
    assert migration.LEGACY_ENGINE_MAPPINGS == EXPECTED_LEGACY_MAPPINGS

    for engine, expected in EXPECTED_LEGACY_MAPPINGS.items():
        canonical = AdapterRegistry.legacy_engine_mapping(engine)
        assert {
            "task_kind": canonical.task_type,
            "framework": canonical.framework,
            "adapter_key": canonical.adapter_key,
        } == {key: expected[key] for key in ("task_kind", "framework", "adapter_key")}


def test_db_compatibility_snapshot_matches_authoritative_legacy_mapping() -> None:
    assert (
        model_space.LEGACY_PIPELINE_COMPATIBILITY_MAPPINGS == EXPECTED_LEGACY_MAPPINGS
    )

    for engine, expected in EXPECTED_LEGACY_MAPPINGS.items():
        canonical = AdapterRegistry.legacy_engine_mapping(engine)
        assert {
            "task_kind": canonical.task_type,
            "framework": canonical.framework,
            "adapter_key": canonical.adapter_key,
        } == {key: expected[key] for key in ("task_kind", "framework", "adapter_key")}


def test_db_models_import_without_training_package() -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        (
            str(Path("packages/visiox-common/src").resolve()),
            str(Path("packages/visiox-db/src").resolve()),
        )
    )
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import builtins; real_import = builtins.__import__; "
                "builtins.__import__ = lambda name, *args, **kwargs: "
                "(_ for _ in ()).throw(ImportError(name)) "
                "if name.startswith('visiox_training') else "
                "real_import(name, *args, **kwargs); import visiox_db.models"
            ),
        ],
        cwd=Path.cwd(),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_pipeline_defaults_execute_without_training_package() -> None:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        (
            str(Path("packages/visiox-common/src").resolve()),
            str(Path("packages/visiox-db/src").resolve()),
        )
    )
    script = """
import builtins
import json

real_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name.startswith("visiox_training"):
        raise ImportError(name)
    return real_import(name, *args, **kwargs)

builtins.__import__ = guarded_import

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from visiox_db.base import Base
from visiox_db.models import TrainedModel, TrainingPipeline

engine = create_engine("sqlite:///:memory:")
Base.metadata.create_all(engine)
with Session(engine) as session:
    session.add_all(
        (
            TrainingPipeline(
                name="llama",
                engine="llamafactory",
                task="sft",
                scale="7b",
                params_template={"model_family": "Qwen2.5", "rank": 8},
            ),
            TrainingPipeline(
                name="yolo",
                engine="yolo26",
                task="detect",
                scale="n",
                params_template={"epochs": 12},
            ),
        )
    )
    session.commit()
    rows = session.scalars(select(TrainingPipeline).order_by(TrainingPipeline.name)).all()
    artifact = TrainedModel(
        pipeline_id=rows[0].id,
        name="llama-artifact",
        version="1",
        task="sft",
        artifact_uri="minio://models/adapter.safetensors",
        metrics={},
    )
    session.add(artifact)
    session.commit()
    print(
        json.dumps(
            {
                "artifact_role": artifact.artifact_role,
                "pipelines": [
                    {
                        "name": row.name,
                        "task_kind": row.task_kind,
                        "framework": row.framework,
                        "adapter_key": row.adapter_key,
                        "adapter_version": row.adapter_version,
                        "model_family": row.model_family,
                        "recipe": row.recipe,
                    }
                    for row in rows
                ],
            },
            sort_keys=True,
        )
    )
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path.cwd(),
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (
        '{"artifact_role": "unassigned", "pipelines": '
        '[{"adapter_key": "llamafactory.llm_sft.v1", '
        '"adapter_version": "1.0.0", "framework": "llamafactory", '
        '"model_family": "Qwen2.5", "name": "llama", '
        '"recipe": {"model_family": "Qwen2.5", "rank": 8}, '
        '"task_kind": "llm_sft"}, '
        '{"adapter_key": "ultralytics.object_detection.v1", '
        '"adapter_version": "1.0.0", "framework": "ultralytics", '
        '"model_family": "yolo26", "name": "yolo", '
        '"recipe": {"epochs": 12}, "task_kind": "object_detection"}]}'
    )


def test_llamafactory_style_trained_model_does_not_fabricate_identity() -> None:
    identity_columns = (
        "framework",
        "adapter_key",
        "model_family",
        "model_format",
    )
    columns = inspect(TrainedModel).columns
    for column_name in identity_columns:
        assert columns[column_name].nullable is True
        assert columns[column_name].default is None

    model = TrainedModel(
        pipeline_id="pipeline-llama",
        training_job_id="job-llama",
        name="support-assistant",
        version="1",
        task="sft",
        artifact_uri="minio://models/job-llama/adapter_model.safetensors",
        metrics={"loss": 0.42},
        status="ready",
    )

    assert {column: getattr(model, column) for column in identity_columns} == {
        column: None for column in identity_columns
    }
    artifact_role = inspect(TrainedModel).columns["artifact_role"]
    assert artifact_role.nullable is False
    assert artifact_role.default is not None
    assert artifact_role.default.arg == "unassigned"


def test_framework_model_tables_expose_persisted_identity_columns() -> None:
    assert {
        "framework",
        "model_family",
        "variant",
        "artifact_format",
        "artifact_metadata",
    } <= set(inspect(BaseModel).columns.keys())
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
    } <= set(inspect(TrainingPipeline).columns.keys())
    assert {
        "ix_training_pipelines_cloned_from_pipeline_id",
        "ix_training_pipelines_first_submitted_job_id",
    } <= {index.name for index in TrainingPipeline.__table__.indexes}
    assert TrainingJobAttempt.__table__.name == "training_job_attempts"
    assert {
        "ix_training_job_attempts_started_at",
        "ix_training_job_attempts_status",
        "ix_training_job_attempts_training_job_id",
    } <= {index.name for index in TrainingJobAttempt.__table__.indexes}
    attempt_job_fk = next(
        foreign_key
        for foreign_key in TrainingJobAttempt.__table__.foreign_keys
        if foreign_key.parent.name == "training_job_id"
    )
    assert attempt_job_fk.ondelete == "CASCADE"
    assert {
        "framework",
        "adapter_key",
        "model_family",
        "model_format",
        "artifact_role",
        "checksum",
        "size_bytes",
        "evaluation_report_uri",
        "deployment_compatibility",
        "artifact_manifest",
        "display_name",
        "training_job_attempt_id",
    } <= set(inspect(TrainedModel).columns.keys())
    assert "ix_trained_models_training_job_id" in {
        index.name for index in TrainedModel.__table__.indexes
    }
