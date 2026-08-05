from __future__ import annotations

import os
from pathlib import Path
import json
import subprocess
import sys

import pytest

import visiox_training.runtime as runtime
import visiox_training_worker.fixed_entrypoint as fixed_entrypoint
from visiox_training.runtime import ArtifactManifestRefresher, run_worker_command
from visiox_training_worker.fixed_entrypoint import build_worker_command


def _runtime_inputs() -> str:
    return (
        '{"artifacts":[{"path":"/workspace/dataset","role":"dataset"},'
        '{"path":"/workspace/model/base.pt","role":"model"}],'
        '"dataset":{"format":"yolo","id":"dataset-1","manifest_checksum":"'
        + "b"
        * 64
        + '","uri":"minio://datasets/1","version":1,"version_id":"version-1"},'
        '"model":{"checksum":"'
        + "a"
        * 64
        + '","family":"yolo26","id":"model-1","revision":null,'
        '"runtime_id":"yolo26n.pt","source":"base_model"},'
        '"parameters":{"device":"0,1","epochs":2,"workers":4}}'
    )


def test_train_entrypoint_import_does_not_load_control_plane_dependencies() -> None:
    source_root = Path(__file__).parents[2] / "workers" / "training-worker" / "src"
    script = """
import builtins
real_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name.startswith('sqlalchemy'):
        raise AssertionError('control-plane dependency was imported')
    return real_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
import visiox_training_worker.train_entrypoint
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(source_root)

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_training_worker_public_exports_remain_available() -> None:
    from visiox_training_worker import CommandResult, TrainingWorkerRunners

    assert CommandResult.__name__ == "CommandResult"
    assert TrainingWorkerRunners.__name__ == "TrainingWorkerRunners"


def test_edge_image_exposes_fixed_training_entrypoint() -> None:
    dockerfile = Path("workers/training-worker/Dockerfile.edge").read_text(
        encoding="utf-8"
    )

    assert "/usr/local/bin/visiox-train" in dockerfile
    assert "visiox_training_worker.fixed_entrypoint" in dockerfile
    assert (
        "COPY packages/visiox-yolo26/src/visiox_yolo26 "
        "/usr/local/lib/python3.12/site-packages/visiox_yolo26"
    ) in dockerfile


def test_fixed_entrypoint_preserves_ultralytics_torchrun_semantics() -> None:
    command = build_worker_command(
        {
            "schema_version": "1.0",
            "adapter_key": "ultralytics.object_detection.v1",
            "adapter_version": "1.0.0",
            "argv": ["/usr/local/bin/visiox-train"],
            "env": {
                "VISIOX_RUNTIME_INPUTS_JSON": _runtime_inputs(),
                "VISIOX_TRAINING_JOB_ID": "job-1",
                "VISIOX_NNODES": "2",
                "VISIOX_NPROC_PER_NODE": "2",
                "VISIOX_NODE_RANK": "1",
                "VISIOX_MASTER_ADDR": "10.10.40.11",
                "VISIOX_MASTER_PORT": "29500",
            },
            "working_directory": "workspace",
        }
    )

    assert command == (
        "torchrun",
        "--nnodes=2",
        "--nproc-per-node=2",
        "--node-rank=1",
        "--master-addr=10.10.40.11",
        "--master-port=29500",
        "-m",
        "visiox_training_worker.train_entrypoint",
        "model=/workspace/model/base.pt",
        "data=/workspace/dataset/data.yaml",
        "project=/workspace/output/runs",
        "name=job-job-1",
        "exist_ok=True",
        "epochs=2",
        "workers=4",
    )


def test_fixed_entrypoint_writes_ultralytics_artifact_roles(tmp_path: Path) -> None:
    output = tmp_path / "output"
    weights = output / "runs" / "job-1" / "weights"
    weights.mkdir(parents=True)
    (weights / "best.pt").write_bytes(b"best")
    (weights / "last.pt").write_bytes(b"last")

    result_path = fixed_entrypoint.write_train_result(
        output, status="success", exit_code=0
    )

    assert json.loads(result_path.read_text(encoding="utf-8")) == {
        "schema_version": "1.0",
        "framework": "ultralytics",
        "status": "success",
        "exit_code": 0,
        "artifacts": [
            {"role": "best_weights", "path": "runs/job-1/weights/best.pt"},
            {"role": "last_weights", "path": "runs/job-1/weights/last.pt"},
        ],
    }


def test_manifest_refresher_tracks_replaced_and_deleted_files(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    weight = output / "best.pt"
    weight.write_bytes(b"first")
    refresher = ArtifactManifestRefresher(
        output,
        task_id="job-1",
        adapter_key="ultralytics.object_detection.v1",
        adapter_version="1.0.0",
        minimum_interval_seconds=0,
    )

    first = refresher.refresh(force=True, strict=True)
    replacement = output / "best.pt.replacement"
    replacement.write_bytes(b"second")
    replacement.replace(weight)
    second = refresher.refresh(force=True, strict=True)
    weight.unlink()
    third = refresher.refresh(force=True, strict=True)

    assert first is not None and second is not None and third is not None
    assert first.artifacts[0].checksum_sha256 != second.artifacts[0].checksum_sha256
    assert third.artifacts == ()


def test_periodic_manifest_refresh_isolates_file_replacement_but_final_is_strict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    weight = output / "best.pt"
    weight.write_bytes(b"first")
    refresher = ArtifactManifestRefresher(
        output,
        task_id="job-1",
        adapter_key="ultralytics.object_detection.v1",
        adapter_version="1.0.0",
        minimum_interval_seconds=0,
    )
    refresher.refresh(force=True, strict=True)
    weight.write_bytes(b"changed-before-refresh")
    original = runtime._stream_file_digest

    def replace_during_hash(path: Path) -> str:
        digest = original(path)
        path.write_bytes(b"changed-during-hash")
        return digest

    monkeypatch.setattr(runtime, "_stream_file_digest", replace_during_hash)

    assert refresher.refresh(force=True, strict=False) is None
    with pytest.raises(RuntimeError, match="changed while hashing"):
        refresher.refresh(force=True, strict=True)


def test_manifest_refresher_throttles_and_reuses_unchanged_checksums(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "output"
    output.mkdir()
    (output / "large.pt").write_bytes(b"x" * (2 * 1024 * 1024))
    calls = 0
    original = runtime._stream_file_digest

    def count_digest(path: Path) -> str:
        nonlocal calls
        calls += 1
        return original(path)

    monkeypatch.setattr(runtime, "_stream_file_digest", count_digest)
    refresher = ArtifactManifestRefresher(
        output,
        task_id="job-1",
        adapter_key="ultralytics.object_detection.v1",
        adapter_version="1.0.0",
        minimum_interval_seconds=60,
    )

    first = refresher.refresh(force=True, strict=False)
    throttled = refresher.refresh(strict=False)
    unchanged = refresher.refresh(force=True, strict=False)

    assert first is not None
    assert throttled is first
    assert unchanged is not None
    assert calls == 1


def test_worker_poll_callback_exception_is_logged_without_killing_training(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Process:
        calls = 0

        def wait(self, timeout):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired("train", timeout)
            return 0

        def poll(self):
            return None

        def send_signal(self, _signum):
            return None

    monkeypatch.setattr(
        runtime.subprocess, "Popen", lambda *_args, **_kwargs: Process()
    )
    monkeypatch.setattr(runtime.signal, "signal", lambda *_args: runtime.signal.SIG_DFL)

    def fail_refresh() -> None:
        raise RuntimeError("transient manifest race")

    assert (
        run_worker_command(
            ("train",),
            environment={},
            on_poll=fail_refresh,
            poll_interval_seconds=0.01,
        )
        == 0
    )
    assert "periodic artifact manifest refresh failed" in caplog.text
