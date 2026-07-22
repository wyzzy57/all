from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


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
