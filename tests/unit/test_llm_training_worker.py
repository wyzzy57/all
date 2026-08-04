from __future__ import annotations

import json
from pathlib import Path

import visiox_llm_training_worker.entrypoint as worker
from visiox_llm_training_worker.fixed_entrypoint import build_worker_command


def test_edge_image_installs_tensorboard_for_training_observability() -> None:
    dockerfile = Path("workers/llm-training-worker/Dockerfile.edge").read_text(
        encoding="utf-8"
    )

    assert '"tensorboard>=2.18,<3"' in dockerfile


def test_edge_image_exposes_fixed_training_entrypoint() -> None:
    dockerfile = Path("workers/llm-training-worker/Dockerfile.edge").read_text(
        encoding="utf-8"
    )

    assert "/usr/local/bin/visiox-train" in dockerfile
    assert "visiox_llm_training_worker.fixed_entrypoint" in dockerfile


def test_fixed_entrypoint_preserves_llamafactory_worker_call() -> None:
    command, environment = build_worker_command(
        {
            "schema_version": "1.0",
            "adapter_key": "llamafactory.llm_sft.v1",
            "adapter_version": "1.0.0",
            "argv": ["/usr/local/bin/visiox-train"],
            "env": {
                "VISIOX_LLM_CONFIG_PATH": "/workspace/dataset/train.yaml",
                "HF_HOME": "/workspace/model-cache/huggingface",
                "MODELSCOPE_CACHE": "/workspace/model-cache/modelscope",
                "USE_MODELSCOPE_HUB": "1",
            },
            "working_directory": "workspace",
        },
        base_environment={},
    )

    assert command[-3:] == (
        "-m",
        "visiox_llm_training_worker.entrypoint",
        "/workspace/dataset/train.yaml",
    )
    assert environment["HF_HOME"] == "/workspace/model-cache/huggingface"
    assert environment["MODELSCOPE_CACHE"] == "/workspace/model-cache/modelscope"
    assert environment["USE_MODELSCOPE_HUB"] == "1"


def test_latest_trainer_log_skips_malformed_tail(tmp_path: Path) -> None:
    log = tmp_path / "trainer_log.jsonl"
    log.write_text(
        '{"loss": 1.25, "current_steps": 2, "total_steps": 4}\nnot-json\n',
        encoding="utf-8",
    )

    assert worker._latest_trainer_log(tmp_path)["loss"] == 1.25


def test_latest_trainer_log_merges_recent_scalar_events(tmp_path: Path) -> None:
    (tmp_path / "trainer_log.jsonl").write_text(
        "\n".join(
            (
                '{"current_steps": 6, "total_steps": 6, "loss": 1.7, "lr": 0.00001}',
                '{"current_steps": 6, "total_steps": 6, "eval_loss": 3.8}',
                '{"current_steps": 6, "total_steps": 6, "epoch": 1.0, "percentage": 100}',
            )
        ),
        encoding="utf-8",
    )

    latest = worker._latest_trainer_log(tmp_path)

    assert latest["loss"] == 1.7
    assert latest["eval_loss"] == 3.8
    assert latest["epoch"] == 1.0


def test_progress_snapshot_contains_llm_metrics(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "trainer_log.jsonl").write_text(
        json.dumps(
            {
                "loss": 0.75,
                "lr": 0.0001,
                "epoch": 1.0,
                "current_steps": 5,
                "total_steps": 10,
                "percentage": 50,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        worker, "_resource_snapshot", lambda: {"system.cpu_percent": 12.0}
    )

    worker._write_progress(
        tmp_path,
        {"num_train_epochs": 2},
        state="running",
    )

    payload = json.loads(
        (tmp_path / "visiox-progress.json").read_text(encoding="utf-8")
    )
    assert payload["engine"] == "llamafactory"
    assert payload["progress"]["percent"] == 50
    assert payload["latest_metrics"]["loss"] == 0.75
    assert payload["latest_metrics"]["learning_rate"] == 0.0001
    assert payload["resources"] == [{"system.cpu_percent": 12.0}]


def test_artifact_manifest_hashes_adapter_outputs(tmp_path: Path) -> None:
    (tmp_path / "adapter_model.safetensors").write_bytes(b"adapter")
    (tmp_path / "adapter_config.json").write_text("{}", encoding="utf-8")

    worker._write_artifact_manifest(
        tmp_path,
        task_id="job-1",
        adapter_key="llamafactory.llm_sft.v1",
        adapter_version="1.0.0",
    )

    manifest = json.loads(
        (tmp_path / "artifact-manifest.json").read_text(encoding="utf-8")
    )
    assert {entry["path"] for entry in manifest["artifacts"]} == {
        "adapter_config.json",
        "adapter_model.safetensors",
    }
    assert manifest["task_id"] == "job-1"
    assert manifest["adapter_key"] == "llamafactory.llm_sft.v1"
    assert all(len(entry["checksum_sha256"]) == 64 for entry in manifest["artifacts"])
    assert all(entry["artifact_type"] for entry in manifest["artifacts"])
    assert len(manifest["checksum_sha256"]) == 64
