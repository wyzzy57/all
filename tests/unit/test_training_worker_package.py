from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from visiox_training_worker.fixed_entrypoint import build_worker_command


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


def test_fixed_entrypoint_preserves_ultralytics_torchrun_semantics() -> None:
    command = build_worker_command(
        {
            "schema_version": "1.0",
            "adapter_key": "ultralytics.object_detection.v1",
            "adapter_version": "1.0.0",
            "argv": ["/usr/local/bin/visiox-train"],
            "env": {
                "VISIOX_TRAINING_ARGUMENTS_JSON": '["model=/workspace/model/base.pt","epochs=2"]',
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
        "epochs=2",
    )
