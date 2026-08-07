from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from threading import Event
from types import SimpleNamespace

import pytest


def _worker_module():
    return importlib.import_module("visiox_paddlex_training_worker.entrypoint")


def _artifacts_module():
    return importlib.import_module("visiox_paddlex_training_worker.artifacts")


def _paddlex_main_module():
    return importlib.import_module("visiox_paddlex_training_worker.paddlex_main")


def _paddledetection_installer_module():
    path = (
        Path(__file__).parents[2]
        / "workers"
        / "paddlex-training-worker"
        / "install_paddledetection.py"
    )
    spec = importlib.util.spec_from_file_location(
        "visiox_install_paddledetection",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _launch_spec() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "adapter_key": "paddlex.object_detection.v1",
        "adapter_version": "1.0.0",
        "argv": ["/usr/local/bin/visiox-train"],
        "env": {
            "VISIOX_RUNTIME_INPUTS_JSON": json.dumps(
                {
                    "parameters": {
                        "epochs": 2,
                        "batch_size": 4,
                        "learning_rate": 0.001,
                        "image_size": 640,
                        "workers": 2,
                        "amp": True,
                        "devices": [0],
                        "resume": False,
                    },
                    "model": {
                        "source": "paddlex",
                        "id": "RT-DETR-L",
                        "runtime_id": "RT-DETR-L",
                        "revision": "paddlex-model-zoo/3.0.3/RT-DETR-L",
                    },
                    "dataset": {
                        "id": "dataset-1",
                        "version_id": "version-1",
                        "format": "coco",
                    },
                    "artifacts": [
                        {"role": "dataset", "path": "/workspace/dataset"}
                    ],
                }
            ),
            "VISIOX_TRAINING_JOB_ID": "job-1",
            "VISIOX_OUTPUT_DIR": "/workspace/output",
        },
        "working_directory": "workspace",
    }


def test_worker_package_import_does_not_import_paddlex() -> None:
    source_root = (
        Path(__file__).parents[2] / "workers" / "paddlex-training-worker" / "src"
    )
    training_root = Path(__file__).parents[2] / "packages" / "visiox-training" / "src"
    script = """
import builtins
real_import = builtins.__import__

def guarded_import(name, *args, **kwargs):
    if name == 'paddlex' or name.startswith('paddlex.'):
        raise AssertionError('PaddleX was imported in the worker process')
    return real_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
import visiox_paddlex_training_worker.entrypoint
"""
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join((str(source_root), str(training_root)))

    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr


def test_installer_locates_marker_in_paddlex_repository_cache(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    installer = _paddledetection_installer_module()
    paddlex_root = tmp_path / "paddlex"
    paddlex_root.mkdir()
    requested_packages: list[str] = []

    def fake_find_spec(package: str) -> SimpleNamespace:
        requested_packages.append(package)
        return SimpleNamespace(submodule_search_locations=[str(paddlex_root)])

    monkeypatch.setattr(installer, "find_spec", fake_find_spec)

    assert installer._paddlex_repository_root() == (
        paddlex_root / "repo_manager" / "repos" / "PaddleDetection"
    )
    assert requested_packages == ["paddlex"]


def test_edge_image_uses_pinned_official_runtime_and_fixed_entrypoint() -> None:
    dockerfile = Path("workers/paddlex-training-worker/Dockerfile.edge").read_text(
        encoding="utf-8"
    )
    requirements = Path(
        "workers/paddlex-training-worker/requirements.lock"
    ).read_text(encoding="utf-8")
    shared_requirements_path = Path(
        "packages/visiox-paddlex/requirements.runtime.lock"
    )
    assert shared_requirements_path.is_file()
    shared_requirements = shared_requirements_path.read_text(encoding="utf-8")
    installer_path = Path(
        "workers/paddlex-training-worker/install_paddledetection.py"
    )
    assert installer_path.is_file()
    installer = installer_path.read_text(encoding="utf-8")

    cuda_base = (
        "nvidia/cuda:11.8.0-base-ubuntu22.04"
        "@sha256:79e5b2cf878ee9006f5b3738caeea34fdc7708a32db53fe3e80db0b48bd286a0"
    )
    paddle_wheel = (
        "https://paddle-whl.bj.bcebos.com/stable/cu118/paddlepaddle-gpu/"
        "paddlepaddle_gpu-3.0.0-cp310-cp310-linux_x86_64.whl"
    )
    paddlex_config_base = (
        "https://raw.githubusercontent.com/PaddlePaddle/PaddleX/v3.0.3/"
        "paddlex/repo_apis/PaddleDetection_api/configs"
    )
    assert f"ARG CUDA_BASE_IMAGE={cuda_base}" in dockerfile
    assert "FROM ${CUDA_BASE_IMAGE}" in dockerfile
    assert "cudnn8-runtime" not in dockerfile
    assert f"ARG PADDLE_WHEEL_URL={paddle_wheel}" in dockerfile
    assert "ARG UBUNTU_MIRROR=https://mirrors.aliyun.com/ubuntu" in dockerfile
    assert "Acquire::Retries=3" in dockerfile
    assert re.search(
        r"^ARG PADDLE_WHEEL_SHA256=[a-f0-9]{64}$",
        dockerfile,
        re.MULTILINE,
    )
    assert "sha256sum -c -" in dockerfile
    assert "--output /tmp/paddlepaddle_gpu-3.0.0-cp310-cp310-linux_x86_64.whl" in dockerfile
    assert "pip install --no-cache-dir /tmp/paddlepaddle_gpu-3.0.0-cp310-cp310-linux_x86_64.whl" in dockerfile
    assert (
        "from importlib.metadata import version; "
        "assert version('paddlepaddle-gpu') == '3.0.0'"
    ) in dockerfile
    assert 'import paddle; assert paddle.__version__' not in dockerfile
    assert "python3.10 -m venv" in dockerfile
    assert "paddlepaddle/paddle" not in dockerfile
    assert "tensorrt" not in dockerfile.casefold()
    assert "paddlex[cv]==3.0.3" in shared_requirements
    assert "paddlex[cv]" not in requirements
    assert "pip install --no-cache-dir -r /app/requirements.lock" in dockerfile
    shared_copy = (
        "COPY packages/visiox-paddlex/requirements.runtime.lock "
        "/tmp/paddlex-runtime-requirements.lock"
    )
    shared_install = (
        "pip install --no-cache-dir -r /tmp/paddlex-runtime-requirements.lock"
    )
    assert shared_copy in dockerfile
    assert shared_install in dockerfile
    assert dockerfile.index(shared_copy) < dockerfile.index("WORKDIR /app")
    assert (
        'mkdir -p "$VIRTUAL_ENV/lib/python3.10/site-packages/'
        'paddlex/repo_manager/repos"'
    ) in dockerfile
    assert "COPY workers/paddlex-training-worker/install_paddledetection.py" in dockerfile
    assert "python /tmp/install_paddledetection.py" in dockerfile
    assert "paddlex --install PaddleDetection" not in dockerfile
    assert f"ARG PADDLEX_CONFIG_BASE_URL={paddlex_config_base}" in dockerfile
    assert (
        "ARG PPYOLOE_CONFIG_SHA256="
        "20ec1fc27f96943026ebf7306ce850ab784be0dd7975c2fc03341f1a776bd0f8"
    ) in dockerfile
    assert (
        "ARG RTDETR_CONFIG_SHA256="
        "fa18adf6bc279ac628e6775ac33274913a6ebd3a74df78231620cc859bdf179d"
    ) in dockerfile
    assert '"$PADDLEX_CONFIG_BASE_URL/PP-YOLOE_plus-S.yaml"' in dockerfile
    assert '"$PADDLEX_CONFIG_BASE_URL/RT-DETR-L.yaml"' in dockerfile
    assert "Config('PP-YOLOE_plus-S')" in dockerfile
    assert "Config('RT-DETR-L')" in dockerfile
    assert "repo.install_external_deps = _defer_driver_bound_custom_ops" in installer
    assert 'repo_name != "PaddleDetection"' in installer
    assert 'distribution("paddledet")' in installer
    assert 'find_spec("ppdet")' in installer
    assert "paddlex_main.py" in dockerfile
    assert "/usr/local/bin/visiox-train" in dockerfile
    assert "visiox_paddlex_training_worker.entrypoint" in dockerfile


def test_python_310_training_runtime_does_not_import_typing_self() -> None:
    runtime_package = Path("packages/visiox-training/src/visiox_training")
    incompatible = [
        path.as_posix()
        for path in runtime_package.rglob("*.py")
        if re.search(
            r"^from typing import .*\bSelf\b",
            path.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    ]

    assert incompatible == []


def test_python_310_training_runtime_does_not_import_datetime_utc() -> None:
    runtime_packages = (
        Path("packages/visiox-training/src/visiox_training"),
        Path(
            "workers/paddlex-training-worker/src/"
            "visiox_paddlex_training_worker"
        ),
    )
    incompatible = [
        path.as_posix()
        for package in runtime_packages
        for path in package.rglob("*.py")
        if re.search(
            r"^from datetime import .*\bUTC\b",
            path.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
    ]

    assert incompatible == []


def test_paddlex_child_configures_workers_resize_and_visualdl() -> None:
    module = _paddlex_main_module()

    class FakeConfig(dict):
        def __init__(self) -> None:
            super().__init__(
                worker_num=0,
                eval_size=[640, 640],
                TrainReader={
                    "batch_transforms": [
                        {"BatchRandomResize": {"target_size": [480, 512, 544]}}
                    ]
                },
                EvalReader={
                    "sample_transforms": [
                        {"Resize": {"target_size": [640, 640]}}
                    ]
                },
                TestReader={
                    "inputs_def": {"image_shape": [3, 640, 640]},
                    "sample_transforms": [
                        {"Resize": {"target_size": [640, 640]}}
                    ]
                },
            )
            self.updated_workers: list[int] = []

        def update_num_workers(self, workers: int) -> None:
            self.updated_workers.append(workers)
            self["worker_num"] = workers

    config = FakeConfig()

    module.apply_runtime_config(
        config,
        image_size=768,
        workers=5,
        visualdl_dir="/workspace/output/visualdl",
    )

    assert config.updated_workers == [5]
    assert config["eval_size"] == [768, 768]
    assert config["TrainReader"]["batch_transforms"][0]["BatchRandomResize"][
        "target_size"
    ] == [768]
    assert config["EvalReader"]["sample_transforms"][0]["Resize"][
        "target_size"
    ] == [768, 768]
    assert config["TestReader"]["sample_transforms"][0]["Resize"][
        "target_size"
    ] == [768, 768]
    assert config["TestReader"]["inputs_def"]["image_shape"] == [3, 768, 768]
    assert config["use_vdl"] is True
    assert config["vdl_log_dir"] == "/workspace/output/visualdl"


def test_resource_sample_preserves_each_gpu(monkeypatch) -> None:
    resources = importlib.import_module("visiox_paddlex_training_worker.resources")

    monkeypatch.setattr(
        resources.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            [],
            0,
            stdout=(
                "0, GPU-a, 50, 1024, 8192, 60, 100\n"
                "1, GPU-b, 75, 2048, 8192, 65, 120\n"
            ),
        ),
    )

    sample = resources.collect_resource_sample(step=7)

    assert sample["step"] == 7
    assert [gpu["uuid"] for gpu in sample["gpus"]] == ["GPU-a", "GPU-b"]
    assert sample["gpus"][1]["utilization_percent"] == 75.0


@pytest.mark.parametrize(
    ("returncode", "stop_requested", "expected_state"),
    [(0, False, "completed"), (7, False, "failed"), (-15, True, "canceled")],
)
def test_worker_always_writes_durable_outputs_and_manifest(
    tmp_path: Path,
    monkeypatch,
    returncode: int,
    stop_requested: bool,
    expected_state: str,
) -> None:
    worker = _worker_module()
    popen_calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    class FakeProcess:
        def __init__(self, command, stdout, stderr) -> None:
            self.returncode: int | None = None
            self._stdout = stdout
            self._stderr = stderr
            self.terminated = False
            if "Global.mode=export" in command:
                bundle = tmp_path / "best_model" / "inference"
                bundle.mkdir(parents=True, exist_ok=True)
                (bundle / "inference.json").write_text("{}", encoding="utf-8")
                (bundle / "inference.pdiparams").write_bytes(b"weights")
            else:
                best = tmp_path / "best_model"
                best.mkdir(parents=True, exist_ok=True)
                (best / "best_model.pdparams").write_bytes(b"dynamic")
                stdout.write(
                    "Epoch: [1] [1/2] learning_rate: 0.001 loss: 2.5\n"
                )
            stderr.write("durable warning\n")
            stdout.flush()
            stderr.flush()

        def poll(self):
            if stop_requested and not self.terminated:
                return None
            self.returncode = returncode
            return returncode

        def terminate(self) -> None:
            self.terminated = True

    def fake_popen(command, **kwargs):
        popen_calls.append((tuple(command), kwargs))
        return FakeProcess(command, kwargs["stdout"], kwargs["stderr"])

    class FakeSampler:
        latest = {"step": 0, "gpus": [{"index": 0, "uuid": "GPU-a"}]}
        step = 0

        def __init__(self, output_dir) -> None:
            self.path = output_dir / "resource_metrics.jsonl"

        def start(self) -> None:
            self.path.write_text(json.dumps(self.latest) + "\n", encoding="utf-8")

        def stop(self) -> None:
            pass

    monkeypatch.setattr(worker, "ResourceSampler", FakeSampler)
    stop_event = Event()
    if stop_requested:
        stop_event.set()

    exit_code = worker.run_training(
        _launch_spec(),
        output_dir=tmp_path,
        popen_factory=fake_popen,
        stop_event=stop_event,
        sleep=lambda _seconds: None,
        mlflow_module=None,
        writer_factory=lambda _: None,
    )

    assert exit_code == returncode
    assert len(popen_calls) == (2 if returncode == 0 and not stop_requested else 1)
    assert popen_calls[0][0][:4] == (
        "python",
        "/opt/paddlex-runtime/paddlex_main.py",
        "-c",
        "paddlex/configs/modules/object_detection/RT-DETR-L.yaml",
    )
    assert popen_calls[0][1].get("shell") is not True
    assert popen_calls[0][1]["env"]["VISIOX_PADDLEX_IMAGE_SIZE"] == "640"
    assert popen_calls[0][1]["env"]["VISIOX_PADDLEX_WORKERS"] == "2"
    assert popen_calls[0][1]["env"]["VISIOX_PADDLEX_VDL_DIR"] == str(
        tmp_path / "visualdl"
    )
    assert (tmp_path / "stdout.log").read_text(encoding="utf-8").endswith(
        "loss: 2.5\n"
    )
    expected_warning_count = 2 if returncode == 0 and not stop_requested else 1
    assert (tmp_path / "stderr.log").read_text(encoding="utf-8") == (
        "durable warning\n" * expected_warning_count
    )
    progress = json.loads(
        (tmp_path / "visiox-progress.json").read_text(encoding="utf-8")
    )
    assert progress["state"] == expected_state
    assert progress["latest_metrics"]["loss"] == 2.5
    assert progress["resources"][0]["gpus"][0]["uuid"] == "GPU-a"
    result = json.loads((tmp_path / "train_result.json").read_text(encoding="utf-8"))
    assert result["status"] == expected_state
    manifest = json.loads(
        (tmp_path / "artifact-manifest.json").read_text(encoding="utf-8")
    )
    paths = {item["path"] for item in manifest["artifacts"]}
    assert {
        "resource_metrics.jsonl",
        "stderr.log",
        "stdout.log",
        "train_result.json",
        "visiox-metrics.jsonl",
        "visiox-progress.json",
    } <= paths


def test_export_failure_marks_the_training_run_failed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worker = _worker_module()
    calls = 0

    class FakeProcess:
        def __init__(self, command) -> None:
            nonlocal calls
            calls += 1
            self.returncode = 0 if "Global.mode=train" in command else 9

        def poll(self):
            return self.returncode

    def fake_popen(command, **_kwargs):
        if "Global.mode=train" in command:
            best = tmp_path / "best_model"
            best.mkdir(parents=True, exist_ok=True)
            (best / "best_model.pdparams").write_bytes(b"dynamic")
        return FakeProcess(command)

    class FakeSampler:
        latest = {}
        step = 0

        def __init__(self, output_dir) -> None:
            self.path = output_dir / "resource_metrics.jsonl"

        def start(self) -> None:
            self.path.touch()

        def stop(self) -> None:
            pass

    monkeypatch.setattr(worker, "ResourceSampler", FakeSampler)

    exit_code = worker.run_training(
        _launch_spec(),
        output_dir=tmp_path,
        popen_factory=fake_popen,
        sleep=lambda _seconds: None,
        mlflow_module=None,
        writer_factory=lambda _: None,
    )

    assert calls == 2
    assert exit_code == 9
    result = json.loads((tmp_path / "train_result.json").read_text(encoding="utf-8"))
    assert result["status"] == "failed"


def test_cleanup_failure_does_not_skip_result_or_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worker = _worker_module()

    class FailedProcess:
        def poll(self):
            return 7

    class BrokenSampler:
        latest = {}
        step = 0

        def __init__(self, output_dir) -> None:
            self.path = output_dir / "resource_metrics.jsonl"

        def start(self) -> None:
            self.path.touch()

        def stop(self) -> None:
            raise RuntimeError("sampler did not stop")

    monkeypatch.setattr(worker, "ResourceSampler", BrokenSampler)

    exit_code = worker.run_training(
        _launch_spec(),
        output_dir=tmp_path,
        popen_factory=lambda *_args, **_kwargs: FailedProcess(),
        sleep=lambda _seconds: None,
        mlflow_module=None,
        writer_factory=lambda _: None,
    )

    assert exit_code == 7
    assert (tmp_path / "train_result.json").is_file()
    assert (tmp_path / "artifact-manifest.json").is_file()
    assert "sampler did not stop" in (tmp_path / "stderr.log").read_text(
        encoding="utf-8"
    )


def test_artifact_collection_catalogs_prescribed_outputs(tmp_path: Path) -> None:
    files = {
        "train.log": b"train",
        "config.yaml": b"Global: {}",
        "best_model/model.pdparams": b"dynamic",
        "best_model/inference.pdmodel": b"static-graph",
        "best_model/inference.json": b"pir-static-graph",
        "best_model/inference.pdiparams": b"static-params",
        "last_model/model.pdparams": b"last",
        "checkpoint/epoch_2.pdparams": b"checkpoint",
        "eval/evaluation.json": b"{}",
        "visualizations/sample.jpg": b"jpg",
        "visualdl/vdlrecords.1.log": b"visualdl",
    }
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)

    result_path = _artifacts_module().write_train_result(
        tmp_path,
        status="completed",
        exit_code=0,
    )

    result = json.loads(result_path.read_text(encoding="utf-8"))
    roles = {item["role"] for item in result["artifacts"]}
    assert roles == {
        "config",
        "evaluation_report",
        "best_dynamic_weights",
        "best_static_inference",
        "last_weights",
        "checkpoint_weights",
        "train_log",
        "visualization",
        "visualdl",
    }


@pytest.mark.parametrize(
    ("status", "create_real_last", "expected_last_paths"),
    [
        ("failed", False, set()),
        ("completed", True, {"model_final.pdparams"}),
    ],
)
def test_artifact_collection_excludes_resume_staging_weights(
    tmp_path: Path,
    status: str,
    create_real_last: bool,
    expected_last_paths: set[str],
) -> None:
    resume_dir = tmp_path / ".resume"
    resume_dir.mkdir()
    (resume_dir / "last.pdparams").write_bytes(b"input-checkpoint")
    (resume_dir / "last.pdopt").write_bytes(b"input-optimizer")
    if create_real_last:
        (tmp_path / "model_final.pdparams").write_bytes(b"trained-output")

    result_path = _artifacts_module().write_train_result(
        tmp_path,
        status=status,
        exit_code=0 if status == "completed" else 1,
    )

    result = json.loads(result_path.read_text(encoding="utf-8"))
    assert {
        item["path"]
        for item in result["artifacts"]
        if item["role"] == "last_weights"
    } == expected_last_paths
    assert all(
        not item["path"].startswith(".resume/") for item in result["artifacts"]
    )


def test_bootstrap_failure_writes_durable_failure_outputs(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worker = _worker_module()
    monkeypatch.setattr(worker, "OUTPUT_DIR", tmp_path)

    try:
        raise RuntimeError("invalid launch spec")
    except RuntimeError:
        worker._write_bootstrap_failure("job-bootstrap")

    assert "invalid launch spec" in (tmp_path / "stderr.log").read_text(
        encoding="utf-8"
    )
    progress = json.loads(
        (tmp_path / "visiox-progress.json").read_text(encoding="utf-8")
    )
    assert progress["state"] == "failed"
    result = json.loads((tmp_path / "train_result.json").read_text(encoding="utf-8"))
    assert result["status"] == "failed"
    manifest = json.loads(
        (tmp_path / "artifact-manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["task_id"] == "job-bootstrap"
    assert (tmp_path / "visiox-metrics.jsonl").read_bytes() == b""
    assert (tmp_path / "resource_metrics.jsonl").read_bytes() == b""


def test_resume_checkpoint_is_copied_to_writable_paddle_prefix(tmp_path: Path) -> None:
    worker = _worker_module()
    checkpoint_dir = tmp_path / "checkpoint"
    checkpoint_dir.mkdir()
    (checkpoint_dir / "last.pt").write_bytes(b"params")
    (checkpoint_dir / "last.pdopt").write_bytes(b"optimizer")
    (checkpoint_dir / "last.pdema").write_bytes(b"ema")
    output_dir = tmp_path / "output"

    prefix = worker._prepare_resume_checkpoint(
        output_dir,
        checkpoint_dir=checkpoint_dir,
    )

    assert prefix == output_dir / ".resume" / "last"
    assert (prefix.with_suffix(".pdparams")).read_bytes() == b"params"
    assert (prefix.with_suffix(".pdopt")).read_bytes() == b"optimizer"
    assert (prefix.with_suffix(".pdema")).read_bytes() == b"ema"


def test_termination_escalates_once_and_does_not_poll_forever(
    tmp_path: Path,
    monkeypatch,
) -> None:
    worker = _worker_module()
    signals: list[bool] = []

    class StuckProcess:
        pid = None

        def poll(self):
            return None

        def wait(self, timeout):
            raise subprocess.TimeoutExpired("paddlex", timeout)

        def terminate(self):
            signals.append(False)

        def kill(self):
            signals.append(True)

    class FakeSampler:
        latest = {}
        step = 0

        def __init__(self, output_dir) -> None:
            self.path = output_dir / "resource_metrics.jsonl"

        def start(self) -> None:
            self.path.touch()

        def stop(self) -> None:
            pass

    clock = iter((0.0, 11.0, 11.0, 17.0, 17.0))
    monkeypatch.setattr(worker, "ResourceSampler", FakeSampler)
    stop_event = Event()
    stop_event.set()

    exit_code = worker.run_training(
        _launch_spec(),
        output_dir=tmp_path,
        popen_factory=lambda *_args, **_kwargs: StuckProcess(),
        stop_event=stop_event,
        sleep=lambda _seconds: None,
        monotonic=lambda: next(clock),
        mlflow_module=None,
        writer_factory=lambda _: None,
    )

    assert exit_code == -9
    assert signals.count(False) <= 2
    assert signals.count(True) <= 2


def test_resource_sampler_skips_write_when_stopped_during_collection(
    tmp_path: Path,
) -> None:
    resources = importlib.import_module("visiox_paddlex_training_worker.resources")
    sampler = None

    def sample_factory(*, step: int):
        assert sampler is not None
        sampler._stop.set()
        return {"step": step, "gpus": []}

    sampler = resources.ResourceSampler(
        tmp_path,
        interval_seconds=0,
        sample_factory=sample_factory,
    )

    sampler._run()

    assert not sampler.path.exists()
