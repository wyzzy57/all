import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

from visiox_api.services.resource_scheduler import freeze_distributed_allocation
from visiox_db.base import Base
from visiox_db.models import (
    DistributedTrainingRun,
    RemoteExecution,
    Task,
    TrainingJob,
    TrainingPipeline,
)
from visiox_db.session import create_session_factory
from visiox_edge_executor_worker.distributed import (
    DistributedNode,
    IncompatibleResourcePoolError,
    build_distributed_plan,
    torchrun_argv,
)
from visiox_edge_executor_worker.distributed_execution import (
    DistributedTrainingHandler,
    StopDistributedTrainingHandler,
    _llamafactory_dataset_info,
    _launch_request,
    _set_container_dataset_root,
    _staging_request,
    build_distributed_handlers,
    _training_arguments,
)
from visiox_edge_executor_worker.scripts import load_packaged_script


def _canonical_checksum(payload: dict[str, object]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _generic_staging_request() -> dict[str, object]:
    rank = SimpleNamespace(node_id="node-1", gpu_uuids=("GPU-one",), node_rank=0)
    return _staging_request(  # type: ignore[arg-type]
        SimpleNamespace(
            id="run-1",
            attempt=1,
            training_image_digest="registry.local/visiox/paddlex@sha256:" + "a" * 64,
            master_addr="10.10.40.10",
            master_port=29500,
        ),
        SimpleNamespace(id="job-1"),
        rank,
        (rank,),
        "paddlex",
        "paddlex.object_detection.v1",
        "1.0.0",
        ["epochs=1"],
        {},
        [
            {
                "role": "dataset",
                "download_url": "https://storage.invalid/dataset.tar.gz",
                "checksum_sha256": "b" * 64,
                "target_path": "archives/dataset.tar.gz",
                "unpack_to": "dataset",
            },
            {
                "role": "model",
                "download_url": "https://storage.invalid/model.bin",
                "checksum_sha256": "c" * 64,
                "target_path": "artifacts/model.bin",
                "unpack_to": None,
            },
            {
                "role": "checkpoint",
                "download_url": "https://storage.invalid/checkpoint.bin",
                "checksum_sha256": "d" * 64,
                "target_path": "artifacts/checkpoint.bin",
                "unpack_to": None,
            },
        ],
    )


def _node(
    node_id: str,
    *,
    pool_id: str = "pool-x86",
    platform: str = "x86_nvidia",
    architecture: str = "x86_64",
    compatibility_key: str = "x86_nvidia:x86_64:12:10:8.9",
    address: str | None = None,
    gpus: tuple[str, ...] = ("GPU-0", "GPU-1"),
    status: str = "online",
    draining: bool = False,
) -> DistributedNode:
    return DistributedNode(
        node_id=node_id,
        resource_pool_id=pool_id,
        platform_kind=platform,
        architecture=architecture,
        compatibility_key=compatibility_key,
        lan_address=address or f"10.10.40.{10 + int(node_id[-1])}",
        gpu_uuids=gpus,
        status=status,
        draining=draining,
    )


def test_scheduler_assigns_stable_node_and_gpu_ranks() -> None:
    plan = build_distributed_plan(
        [_node("node-2"), _node("node-1")],
        requested_gpus=4,
        master_port=29600,
    )

    assert plan.resource_pool_id == "pool-x86"
    assert plan.master_addr == "10.10.40.11"
    assert plan.master_port == 29600
    assert plan.world_size == 4
    assert [node.node_id for node in plan.nodes] == ["node-1", "node-2"]
    assert [node.node_rank for node in plan.nodes] == [0, 1]
    assert [node.gpu_uuids for node in plan.nodes] == [
        ("GPU-0", "GPU-1"),
        ("GPU-0", "GPU-1"),
    ]


def test_distributed_allocation_snapshot_is_stable_json_data() -> None:
    plan = build_distributed_plan(
        [_node("node-2"), _node("node-1")],
        requested_gpus=4,
        master_port=29600,
    )

    request, allocation = freeze_distributed_allocation(plan)

    assert request == {
        "kind": "distributed",
        "resource_pool_id": "pool-x86",
        "gpu_count": 4,
    }
    assert allocation["node_ids"] == ["node-1", "node-2"]
    assert allocation["ranks"][0]["gpu_uuids"] == ["GPU-0", "GPU-1"]
    assert allocation["world_size"] == 4
    assert allocation["master_port"] == 29600


@pytest.mark.parametrize(
    "nodes",
    [
        [_node("node-1"), _node("node-2", platform="jetson")],
        [_node("node-1"), _node("node-2", pool_id="pool-other")],
        [_node("node-1"), _node("node-2", compatibility_key="other")],
    ],
)
def test_scheduler_rejects_mixed_or_incompatible_nodes(
    nodes: list[DistributedNode],
) -> None:
    with pytest.raises(IncompatibleResourcePoolError):
        build_distributed_plan(nodes, requested_gpus=4)


def test_scheduler_rejects_offline_draining_and_insufficient_resources() -> None:
    with pytest.raises(IncompatibleResourcePoolError, match="online"):
        build_distributed_plan([_node("node-1", status="offline")], requested_gpus=1)
    with pytest.raises(IncompatibleResourcePoolError, match="draining"):
        build_distributed_plan([_node("node-1", draining=True)], requested_gpus=1)
    with pytest.raises(IncompatibleResourcePoolError, match="GPU"):
        build_distributed_plan([_node("node-1", gpus=("GPU-0",))], requested_gpus=2)


def test_torchrun_argv_uses_explicit_static_ranks_without_shell_text() -> None:
    plan = build_distributed_plan([_node("node-1"), _node("node-2")], requested_gpus=4)
    argv = torchrun_argv(
        plan,
        plan.nodes[1],
        training_arguments=("model=/workspace/model/base.pt", "epochs=2"),
    )

    assert argv[:7] == (
        "torchrun",
        "--nnodes=2",
        "--nproc-per-node=2",
        "--node-rank=1",
        "--master-addr=10.10.40.11",
        "--master-port=29500",
        "-m",
    )
    assert argv[7:] == (
        "visiox_training_worker.train_entrypoint",
        "model=/workspace/model/base.pt",
        "epochs=2",
    )


def test_torchrun_argv_rejects_control_characters() -> None:
    plan = build_distributed_plan([_node("node-1")], requested_gpus=1)
    with pytest.raises(ValueError, match="argument"):
        torchrun_argv(plan, plan.nodes[0], training_arguments=("epochs=2\nwhoami",))


def test_remote_training_scripts_are_json_driven_and_do_not_eval_request_values() -> (
    None
):
    remote = Path(__file__).parents[2] / "workers" / "edge-executor-worker" / "remote"
    for name in ("stage_training.sh", "launch_rank.sh", "stop_training.sh"):
        text = (remote / name).read_text(encoding="utf-8")
        assert "json.load" in text
        assert "eval " not in text
        assert "docker" in text


def test_remote_rank_container_uses_edge_user_for_writable_output_mount() -> None:
    script = load_packaged_script("launch_rank.sh").decode("utf-8")

    assert '"--user", f"{os.getuid()}:{os.getgid()}"' in script
    assert "dst=/workspace/input,readonly" in script
    assert "dst=/workspace/output" in script
    assert '"USER=visiox-edge"' in script
    assert '"LOGNAME=visiox-edge"' in script
    assert '"TORCHINDUCTOR_CACHE_DIR=/tmp/torchinductor"' in script


def test_remote_staging_failure_reports_safe_stage_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = load_packaged_script("stage_training.sh").decode("utf-8")
    embedded = script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    namespace: dict[str, object] = {"__name__": "visiox_script_test"}
    exec(compile(embedded, "stage_training.sh", "exec"), namespace)
    request_path = tmp_path / "request.json"
    request = _generic_staging_request()
    request["artifacts"][0]["download_url"] = (
        "https://minio.invalid/dataset?X-Amz-Signature=secret"
    )
    request_path.write_text(json.dumps(request), encoding="utf-8")

    def fail_pull(*_args, **_kwargs):
        raise RuntimeError("X-Amz-Signature=must-not-appear")

    monkeypatch.setattr(namespace["subprocess"], "run", fail_pull)  # type: ignore[arg-type]
    monkeypatch.setattr(sys, "argv", ["stage_training.py", str(request_path)])

    assert namespace["main"]() == 1  # type: ignore[operator]
    stderr = capsys.readouterr().err
    assert (
        stderr
        == "training artifact staging failed at stage=runtime-image-pull (RuntimeError)\n"
    )
    assert "must-not-appear" not in stderr


def test_remote_staging_uses_ssh_user_owned_workspace() -> None:
    script = load_packaged_script("stage_training.sh").decode("utf-8")

    assert 'Path.home() / ".local" / "share" / "visiox" / "training"' in script
    assert 'Path("/var/lib/visiox/training")' not in script


def test_production_handler_factory_registers_train_resume_and_stop() -> None:
    handlers = build_distributed_handlers(object(), object(), object())  # type: ignore[arg-type]

    assert set(handlers) == {"train", "resume_training", "stop_training"}
    assert isinstance(handlers["train"], DistributedTrainingHandler)
    assert isinstance(handlers["resume_training"], DistributedTrainingHandler)
    assert isinstance(handlers["stop_training"], StopDistributedTrainingHandler)


def test_completed_distributed_job_converges_without_relaunching() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    finished_at = datetime.now(UTC)
    with session_factory() as session:
        pipeline = TrainingPipeline(
            id="pipeline-1",
            name="pipeline-1",
            task="detect",
            scale="n",
            status="running",
        )
        task = Task(
            id="task-1",
            task_type="EDGE_TRAIN",
            status="RUNNING",
            progress=65,
            stage="training",
            error_code="EDGE_TRAIN_FAILED",
            error_message="Distributed edge training failed",
        )
        job = TrainingJob(
            id="job-1",
            pipeline_id=pipeline.id,
            task_id=task.id,
            trained_model_id="model-1",
            status="running",
            metrics={
                "weights": {
                    "best.pt": "minio://models/trained/job-1/best.pt",
                    "last.pt": "minio://models/trained/job-1/last.pt",
                }
            },
            finished_at=finished_at,
        )
        run = DistributedTrainingRun(
            id="run-1",
            training_job_id=job.id,
            resource_pool_id="pool-1",
            node_ids=["node-1"],
            ranks=[],
            master_addr="10.0.0.1",
            master_port=29500,
            world_size=1,
            status="training",
        )
        execution = RemoteExecution(
            id="execution-1",
            node_id="node-1",
            task_id=task.id,
            training_job_id=job.id,
            resource_type="distributed_training_run",
            resource_id=run.id,
            operation="train",
            status="running",
            idempotency_key="train-run-1-attempt-1",
        )
        session.add_all([pipeline, task, job, run, execution])
        session.commit()

    handler = DistributedTrainingHandler(session_factory, object(), object())  # type: ignore[arg-type]

    assert handler._converge_existing_success("execution-1") is True

    with session_factory() as session:
        assert session.get(TrainingJob, "job-1").status == "success"  # type: ignore[union-attr]
        assert session.get(DistributedTrainingRun, "run-1").status == "succeeded"  # type: ignore[union-attr]
        current_task = session.get(Task, "task-1")
        assert current_task is not None
        assert current_task.status == "SUCCESS"
        assert current_task.progress == 100
        assert current_task.error_code is None
        assert session.get(TrainingPipeline, "pipeline-1").status == "success"  # type: ignore[union-attr]


def test_distributed_training_arguments_remove_global_device_selection() -> None:
    arguments = _training_arguments(  # type: ignore[arg-type]
        SimpleNamespace(id="job-1", params={"epochs": 2, "device": "0,1"}),
        SimpleNamespace(payload={"environment": {"workers": 4}}),
        "yolo26",
    )

    assert "model=/workspace/model/base.pt" in arguments
    assert "data=/workspace/dataset/data.yaml" in arguments
    assert "epochs=2" in arguments
    assert "workers=4" in arguments
    assert not any(argument.startswith("device=") for argument in arguments)


def test_staging_request_matches_remote_script_contract() -> None:
    artifacts = [
        {
            "role": "dataset",
            "download_url": "https://storage.invalid/dataset.tar.gz",
            "checksum_sha256": "b" * 64,
            "target_path": "archives/dataset.tar.gz",
            "unpack_to": "dataset",
        }
    ]
    rank = SimpleNamespace(node_id="node-1", gpu_uuids=("GPU-one",), node_rank=0)
    request = _staging_request(  # type: ignore[arg-type]
        SimpleNamespace(
            id="run-1",
            attempt=1,
            training_image_digest="registry.local/visiox/train@sha256:" + "a" * 64,
            master_addr="10.10.40.10",
            master_port=29500,
        ),
        SimpleNamespace(id="job-1"),
        rank,
        (rank,),
        "paddlex",
        "paddlex.object_detection.v1",
        "1.0.0",
        ["epochs=1"],
        {},
        artifacts,
    )

    assert request["schema_version"] == "1.0"
    assert request["runtime_image_digest"].endswith("@sha256:" + "a" * 64)
    assert request["artifacts"] == artifacts
    assert request["launch_spec"]["framework"] == "paddlex"
    assert request["launch_spec"]["entrypoint"] == "/usr/local/bin/visiox-train"
    assert request["launch_spec"]["distributed"]["gpu_uuids"] == ["GPU-one"]
    assert request["launch_spec"]["distributed"]["rendezvous"] == {
        "master_addr": "10.10.40.10",
        "master_port": 29500,
    }
    assert request["launch_spec"]["paths"]["output"] == "/workspace/output"
    assert len(request["launch_spec_checksum"]) == 64
    assert "action" not in request
    assert "engine" not in request


def test_launch_request_matches_remote_script_contract() -> None:
    run = SimpleNamespace(
        id="run-1",
        attempt=1,
        training_image_digest="registry.local/visiox/train@sha256:" + "a" * 64,
        master_addr="10.10.40.10",
        master_port=29500,
    )
    rank = SimpleNamespace(node_id="node-1", gpu_uuids=("GPU-one",), node_rank=0)
    stage = SimpleNamespace(
        paths={
            "model": "/home/edge/model.pt",
            "dataset": "/home/edge/dataset",
            "output": "/home/edge/output",
        }
    )

    staged_request = _staging_request(  # type: ignore[arg-type]
        run,
        SimpleNamespace(id="job-1"),
        rank,
        (rank,),
        "ultralytics",
        "ultralytics.object_detection.v1",
        "1.0.0",
        ["epochs=1"],
        {},
        [],
    )
    stage.paths = {
        "input": "/home/edge/run/input",
        "output": "/home/edge/run/output",
        "launch_spec": "/home/edge/run/input/launch-spec.json",
    }
    request = _launch_request(run, rank, stage, staged_request)  # type: ignore[arg-type]

    assert request["action"] == "launch"
    assert set(request) == {
        "schema_version",
        "action",
        "run_id",
        "attempt",
        "runtime_image_digest",
        "framework",
        "adapter_key",
        "gpu_uuids",
        "node_rank",
        "launch_spec_checksum",
        "paths",
    }


def test_exported_dataset_uses_training_container_mount_root(tmp_path: Path) -> None:
    data_yaml = tmp_path / "data.yaml"
    data_yaml.write_text(
        "path: .\ntrain: images/train\nval: images/val\nnames:\n  0: pepper\n",
        encoding="utf-8",
    )

    _set_container_dataset_root(tmp_path)

    assert data_yaml.read_text(encoding="utf-8").startswith(
        "path: /workspace/dataset\ntrain: images/train\n"
    )


def test_remote_rank_script_rejects_control_characters() -> None:
    script = load_packaged_script("stage_training.sh").decode("utf-8")
    embedded = script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    namespace: dict[str, object] = {"__name__": "visiox_script_test"}
    exec(compile(embedded, "stage_training.sh", "exec"), namespace)
    namespace["Path"] = PurePosixPath
    validate = namespace["validate"]
    rank = SimpleNamespace(node_id="node-1", gpu_uuids=("GPU-one",), node_rank=0)
    request = _generic_staging_request()
    request["launch_spec"]["parameters"]["arguments"] = ["epochs=2\nwhoami"]
    request["launch_spec_checksum"] = _canonical_checksum(request["launch_spec"])

    with pytest.raises(ValueError, match="request"):
        validate(request)  # type: ignore[operator]


def test_remote_rank_failure_reports_safe_stage_only(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = load_packaged_script("launch_rank.sh").decode("utf-8")
    embedded = script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    namespace: dict[str, object] = {"__name__": "visiox_script_test"}
    exec(compile(embedded, "launch_rank.sh", "exec"), namespace)
    namespace["os"] = SimpleNamespace(getuid=lambda: 1000, getgid=lambda: 1000)
    staged_request = _generic_staging_request()
    root = tmp_path / "run"
    input_path = root / "input"
    output_path = root / "output"
    input_path.mkdir(parents=True)
    output_path.mkdir()
    launch_spec_path = input_path / "launch-spec.json"
    launch_spec_path.write_text(
        json.dumps(
            staged_request["launch_spec"],
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    request = _launch_request(  # type: ignore[arg-type]
        SimpleNamespace(
            id="run-1",
            attempt=1,
            training_image_digest=staged_request["runtime_image_digest"],
        ),
        SimpleNamespace(node_rank=0, gpu_uuids=("GPU-one",)),
        SimpleNamespace(
            paths={
                "input": str(input_path),
                "output": str(output_path),
                "launch_spec": str(launch_spec_path),
            }
        ),
        staged_request,
    )
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    calls = 0

    def fail_launch(*_args, **kwargs):
        nonlocal calls
        calls += 1
        if kwargs.get("check"):
            raise RuntimeError("token=must-not-appear")
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(namespace["subprocess"], "run", fail_launch)  # type: ignore[arg-type]
    monkeypatch.setattr(sys, "argv", ["launch_rank.py", str(request_path)])

    assert namespace["main"]() == 1  # type: ignore[operator]
    stderr = capsys.readouterr().err
    assert calls == 2
    assert (
        stderr
        == "distributed rank operation failed at stage=container-launch (RuntimeError)\n"
    )
    assert "must-not-appear" not in stderr


def test_remote_rank_collect_uploads_last_checkpoint(tmp_path: Path) -> None:
    output = tmp_path / "output"
    checkpoint = output / "runs" / "job-1" / "weights" / "last.pt"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"checkpoint")
    script = load_packaged_script("launch_rank.sh").decode("utf-8")
    embedded = script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    namespace: dict[str, object] = {"__name__": "visiox_script_test"}
    exec(compile(embedded, "launch_rank.sh", "exec"), namespace)
    uploaded: list[bytes] = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def urlopen(request, timeout):
        assert timeout == 600
        uploaded.append(request.data)
        return Response()

    namespace["urlopen"] = urlopen
    result = namespace["collect_artifacts"](  # type: ignore[operator]
        str(output),
        {"last.pt": "https://minio.internal/checkpoint?signature=short-lived"},
    )

    assert uploaded == [b"checkpoint"]
    assert result["last.pt"]["size_bytes"] == 10


def test_remote_rank_collect_uploads_ultralytics_visualizations(tmp_path: Path) -> None:
    output = tmp_path / "output"
    run_dir = output / "runs" / "job-1"
    run_dir.mkdir(parents=True)
    expected = {
        "confusion_matrix.png": b"confusion",
        "train_batch0.jpg": b"train-batch",
        "val_batch0_pred.jpg": b"validation-batch",
        "BoxPR_curve.png": b"pr-curve",
    }
    for name, payload in expected.items():
        (run_dir / name).write_bytes(payload)
    script = load_packaged_script("launch_rank.sh").decode("utf-8")
    embedded = script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    namespace: dict[str, object] = {"__name__": "visiox_script_test"}
    exec(compile(embedded, "launch_rank.sh", "exec"), namespace)
    uploaded: list[bytes] = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def urlopen(request, timeout):
        assert timeout == 600
        uploaded.append(request.data)
        return Response()

    namespace["urlopen"] = urlopen
    result = namespace["collect_artifacts"](  # type: ignore[operator]
        str(output),
        {name: f"https://minio.internal/{name}" for name in expected},
    )

    assert uploaded == list(expected.values())
    assert set(result) == set(expected)


def test_llm_launch_spec_preserves_model_source_without_shell_branch() -> None:
    run = SimpleNamespace(
        id="run-llm",
        attempt=1,
        training_image_digest="registry.local/visiox/llm@sha256:" + "a" * 64,
        master_addr="10.10.40.10",
        master_port=29500,
    )
    rank = SimpleNamespace(node_id="node-1", gpu_uuids=("GPU-one",), node_rank=0)
    stage = SimpleNamespace(
        paths={
            "dataset": "/home/edge/llm-dataset",
            "output": "/home/edge/llm-output",
        }
    )

    request = _staging_request(  # type: ignore[arg-type]
        run,
        SimpleNamespace(id="job-llm"),
        rank,
        (rank,),  # type: ignore[arg-type]
        "llamafactory",
        "llamafactory.llm_sft.v1",
        "1.0.0",
        [],
        {"model_source": "modelscope"},
        [],
    )

    spec = request["launch_spec"]
    assert spec["framework"] == "llamafactory"
    assert spec["parameters"] == {
        "arguments": [],
        "model_source": "modelscope",
    }
    assert spec["entrypoint"] == "/usr/local/bin/visiox-train"


def test_alpaca_dataset_info_only_maps_canonical_required_columns() -> None:
    dataset = SimpleNamespace(format="alpaca")

    info = _llamafactory_dataset_info(dataset)  # type: ignore[arg-type]

    assert info == {
        "file_name": "train.jsonl",
        "columns": {
            "prompt": "instruction",
            "query": "input",
            "response": "output",
        },
    }


def test_remote_rank_script_uses_only_fixed_image_entrypoint() -> None:
    script = load_packaged_script("launch_rank.sh").decode("utf-8")

    assert "ENGINES" not in script
    assert "visiox_training_worker.train_entrypoint" not in script
    assert "visiox_llm_training_worker.entrypoint" not in script
    assert '"/usr/local/bin/visiox-train"' in script
    assert "dst=/workspace/input,readonly" in script
    assert "dst=/workspace/input/launch-spec.json,readonly" not in script
    for label in (
        "training-job-id",
        "training-attempt",
        "training-node-rank",
        "training-framework",
        "training-adapter-key",
        "launch-spec-checksum",
    ):
        assert label in script


def test_remote_staging_accepts_generic_framework_without_engine_allowlist() -> None:
    script = load_packaged_script("stage_training.sh").decode("utf-8")
    embedded = script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    namespace: dict[str, object] = {"__name__": "visiox_script_test"}
    exec(compile(embedded, "stage_training.sh", "exec"), namespace)
    validate = namespace["validate"]

    rank = SimpleNamespace(node_id="node-1", gpu_uuids=("GPU-one",), node_rank=0)
    request = _staging_request(  # type: ignore[arg-type]
        SimpleNamespace(
            id="run-paddlex",
            attempt=1,
            training_image_digest="registry.local/visiox/paddlex@sha256:" + "a" * 64,
            master_addr="10.10.40.10",
            master_port=29500,
        ),
        SimpleNamespace(id="job-paddlex"),
        rank,
        (rank,),
        "paddlex",
        "paddlex.object_detection.v1",
        "1.0.0",
        ["epochs=1"],
        {},
        [
            {
                "role": "dataset",
                "download_url": "https://minio.internal/dataset?signature=short-lived",
                "checksum_sha256": "b" * 64,
                "target_path": "archives/dataset.tar.gz",
                "unpack_to": "dataset",
            }
        ],
    )

    assert validate(request) == request  # type: ignore[operator]
    assert "ENGINES" not in script


@pytest.mark.parametrize(
    ("case", "mutate", "resign"),
    [
        ("unknown request schema", lambda request: request.__setitem__("schema_version", "2.0"), False),
        ("mutable image", lambda request: request.__setitem__("runtime_image_digest", "registry.local/visiox/paddlex:latest"), False),
        ("unsafe artifact path", lambda request: request["artifacts"][0].__setitem__("target_path", "../dataset.tar.gz"), False),
        ("host artifact path", lambda request: request["artifacts"][0].__setitem__("target_path", "C:/dataset.tar.gz"), False),
        ("unknown launch schema", lambda request: request["launch_spec"].__setitem__("schema_version", "2.0"), True),
        ("arbitrary entrypoint", lambda request: request["launch_spec"].__setitem__("entrypoint", "/bin/sh"), True),
        ("newline argument", lambda request: request["launch_spec"]["parameters"]["arguments"].__setitem__(0, "epochs=1\nwhoami"), True),
        ("host runtime path", lambda request: request["launch_spec"]["paths"].__setitem__("dataset", "/home/user/dataset"), True),
        ("checksum mismatch", lambda request: request.__setitem__("launch_spec_checksum", "0" * 64), False),
    ],
)
def test_remote_staging_rejects_untrusted_launch_contracts(
    case: str,
    mutate,
    resign: bool,
) -> None:
    del case
    script = load_packaged_script("stage_training.sh").decode("utf-8")
    embedded = script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    namespace: dict[str, object] = {"__name__": "visiox_script_test"}
    exec(compile(embedded, "stage_training.sh", "exec"), namespace)
    request = copy.deepcopy(_generic_staging_request())
    mutate(request)
    if resign:
        request["launch_spec_checksum"] = _canonical_checksum(request["launch_spec"])

    with pytest.raises(ValueError, match="staging request"):
        namespace["validate"](request)  # type: ignore[operator]


def test_artifact_manifest_is_bounded_and_checksum_verified(tmp_path: Path) -> None:
    output = tmp_path / "output"
    output.mkdir()
    artifact = output / "weights" / "best.bin"
    artifact.parent.mkdir()
    artifact.write_bytes(b"trusted model")
    script = load_packaged_script("launch_rank.sh").decode("utf-8")
    embedded = script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    namespace: dict[str, object] = {"__name__": "visiox_script_test"}
    exec(compile(embedded, "launch_rank.sh", "exec"), namespace)
    manifest = {
        "schema_version": "1.0",
        "artifacts": [
            {
                "path": "weights/best.bin",
                "size_bytes": len(b"trusted model"),
                "checksum_sha256": hashlib.sha256(b"trusted model").hexdigest(),
            }
        ],
    }

    assert namespace["validate_artifact_manifest"](  # type: ignore[operator]
        manifest, output
    ) == manifest

    for invalid_manifest in (
        {**manifest, "schema_version": "2.0"},
        {**manifest, "artifacts": [{**manifest["artifacts"][0], "path": "../best.bin"}]},
        {**manifest, "artifacts": [{**manifest["artifacts"][0], "size_bytes": 1}]},
        {**manifest, "artifacts": [{**manifest["artifacts"][0], "checksum_sha256": "0" * 64}]},
        {**manifest, "artifacts": manifest["artifacts"] * (namespace["MAX_ARTIFACT_COUNT"] + 1)},
    ):
        with pytest.raises(ValueError):
            namespace["validate_artifact_manifest"](invalid_manifest, output)  # type: ignore[operator]
