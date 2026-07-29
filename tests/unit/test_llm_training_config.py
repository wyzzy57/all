from __future__ import annotations

import json

from visiox_llm_training_worker.config import apply_managed_config, load_run_identity


def test_run_identity_produces_deterministic_attempt_name_and_tags(tmp_path) -> None:
    config_path = tmp_path / "train.yaml"
    config_path.write_text("stage: sft\n", encoding="utf-8")
    (tmp_path / "visiox-run.json").write_text(
        json.dumps(
            {
                "training_job_id": "job-42",
                "distributed_run_id": "run-9",
                "attempt": 2,
                "organization_id": "org-1",
                "owner_user_id": "user-1",
                "pipeline_id": "pipeline-1",
                "node_ids": ["node-a", "node-b"],
                "model_revision": "abc123",
                "dataset_version_id": "version-3",
                "dataset_checksum": "d" * 64,
                "training_image_digest": "registry/llm@sha256:" + "e" * 64,
            }
        ),
        encoding="utf-8",
    )

    identity = load_run_identity(config_path)

    assert identity.run_name == "visiox-job-42-attempt-2"
    assert identity.tags()["visiox.node_ids"] == "node-a,node-b"
    assert identity.tags()["visiox.dataset_version_id"] == "version-3"


def test_managed_config_overrides_user_managed_paths_and_identity(tmp_path) -> None:
    config_path = tmp_path / "train.yaml"
    config_path.write_text("stage: sft\n", encoding="utf-8")
    (tmp_path / "visiox-run.json").write_text(
        json.dumps(
            {
                "training_job_id": "job-safe",
                "distributed_run_id": "run-safe",
                "attempt": 1,
                "pipeline_id": "pipeline-safe",
                "node_ids": ["node-a"],
                "model_revision": "revision",
                "dataset_checksum": "d" * 64,
                "training_image_digest": "registry/llm@sha256:" + "e" * 64,
            }
        ),
        encoding="utf-8",
    )
    identity = load_run_identity(config_path)

    config = apply_managed_config(
        {
            "output_dir": "/tmp/escape",
            "logging_dir": "/tmp/escape",
            "report_to": "none",
            "run_name": "user-controlled",
        },
        identity,
    )

    assert config["output_dir"] == "/workspace/output/job-job-safe"
    assert config["logging_dir"] == config["output_dir"]
    assert config["report_to"] == "tensorboard"
    assert config["run_name"] == "visiox-job-safe-attempt-1"
