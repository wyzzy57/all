import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine

import visiox_edge_executor_worker.distributed_execution as distributed_execution
from visiox_api.services.resource_scheduler import freeze_distributed_allocation
from visiox_db.base import Base
from visiox_db.models import (
    DistributedTrainingRun,
    RemoteExecution,
    Task,
    TrainingJob,
    TrainingJobAttempt,
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
    _require_remote_adapter,
    _llamafactory_dataset_info,
    _launch_request,
    _resolved_runtime_inputs,
    _resume_staging_request,
    _set_container_dataset_root,
    _staging_request,
    _artifact_uri,
    _validate_remote_artifact_manifest,
    build_distributed_handlers,
)
from visiox_edge_executor_worker.scripts import load_packaged_script
from visiox_training.contracts import ArtifactEntry, ArtifactManifest, LaunchSpec
from visiox_training.runtime import write_artifact_manifest


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
        _runtime_inputs("paddlex"),
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


def _runtime_inputs(
    framework: str = "ultralytics",
    artifact_roles: tuple[str, ...] = ("dataset", "model", "checkpoint"),
) -> dict[str, object]:
    return {
        "parameters": {"epochs": 1},
        "model": {
            "source": framework,
            "id": "model-1",
            "family": "model-family",
            "runtime_id": "runtime-model",
            "checksum": "a" * 64,
            "revision": None,
        },
        "dataset": {
            "id": "dataset-1",
            "version_id": "dataset-version-1",
            "version": 1,
            "format": "yolo" if framework != "llamafactory" else "alpaca",
            "uri": "minio://datasets/dataset-1/version-1",
            "manifest_checksum": "b" * 64,
        },
        "artifacts": [
            {
                "role": role,
                "path": {
                    "dataset": "/workspace/dataset",
                    "model": "/workspace/model/base.pt",
                    "checkpoint": "/workspace/checkpoint/last.pt",
                }[role],
            }
            for role in artifact_roles
        ],
    }


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
    assert "dst=/workspace/input/launch-spec.json,readonly" in script
    assert "dst=/workspace/input,readonly" not in script
    assert "dst=/workspace/output" in script
    assert '"USER": "visiox-edge"' in script
    assert '"LOGNAME": "visiox-edge"' in script
    assert '"TORCHINDUCTOR_CACHE_DIR": "/tmp/torchinductor"' in script


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


def test_remote_staging_verifies_fixed_entrypoint_exists_in_image() -> None:
    script = load_packaged_script("stage_training.sh").decode("utf-8")

    assert '"--entrypoint", "/usr/bin/test"' in script
    assert '"-x", FIXED_ENTRYPOINT' in script


def test_remote_staging_resume_reuses_workspace_without_docker(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = load_packaged_script("stage_training.sh").decode("utf-8")
    embedded = script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    namespace: dict[str, object] = {"__name__": "visiox_script_test"}
    exec(compile(embedded, "stage_training.sh", "exec"), namespace)

    launch_spec = LaunchSpec(
        adapter_key="paddlex.object_detection.v1",
        adapter_version="1.0.0",
        argv=("/usr/local/bin/visiox-train",),
        working_directory="workspace",
        env={
            "VISIOX_TRAINING_JOB_ID": "job-1",
            "VISIOX_TRAINING_RUN_ID": "run-1",
            "VISIOX_TRAINING_ATTEMPT": "2",
            "VISIOX_FRAMEWORK": "paddlex",
            "VISIOX_NODE_ID": "node-1",
            "VISIOX_NODE_RANK": "0",
            "VISIOX_NNODES": "1",
            "VISIOX_NPROC_PER_NODE": "1",
            "VISIOX_GPU_UUIDS_JSON": '["GPU-one"]',
            "VISIOX_MASTER_ADDR": "10.0.0.1",
            "VISIOX_MASTER_PORT": "29500",
            "VISIOX_OUTPUT_DIR": "/workspace/output",
            "VISIOX_DATASET_DIR": "/workspace/dataset",
            "VISIOX_RUNTIME_INPUTS_JSON": json.dumps(_runtime_inputs("paddlex")),
        },
    ).model_dump(mode="json")
    assert namespace["validate_launch_spec"](launch_spec) == launch_spec  # type: ignore[operator]
    encoded = json.dumps(
        launch_spec,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    root = tmp_path / ".local" / "share" / "visiox" / "training" / "run-1" / "2"
    input_dir = root / "input"
    output_dir = root / "output"
    input_dir.mkdir(parents=True)
    output_dir.mkdir()
    (input_dir / "launch-spec.json").write_bytes(encoded)
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "action": "resume",
                "run_id": "run-1",
                "attempt": 2,
                "training_job_id": "job-1",
                "adapter_key": "paddlex.object_detection.v1",
                "adapter_version": "1.0.0",
            }
        ),
        encoding="utf-8",
    )

    path_type = type(Path())

    class WorkspacePath(path_type):
        @classmethod
        def home(cls):
            return cls(tmp_path)

    namespace["Path"] = WorkspacePath
    monkeypatch.setattr(
        namespace["subprocess"],
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("resume must not invoke Docker")
        ),
    )
    monkeypatch.setattr(sys, "argv", ["stage_training.py", str(request_path)])

    assert namespace["main"]() == 0  # type: ignore[operator]
    response = json.loads(capsys.readouterr().out)
    assert response["root"] == str(root)
    assert response["paths"] == {
        "output": str(output_dir),
        "launch_spec": str(input_dir / "launch-spec.json"),
        "artifacts": {},
    }


def test_remote_staging_rejects_downloaded_artifact_checksum_mismatch(
    tmp_path: Path,
) -> None:
    script = load_packaged_script("stage_training.sh").decode("utf-8")
    embedded = script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    namespace: dict[str, object] = {"__name__": "visiox_script_test"}
    exec(compile(embedded, "stage_training.sh", "exec"), namespace)

    class Response:
        sent = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _size: int) -> bytes:
            if self.sent:
                return b""
            self.sent = True
            return b"wrong payload"

    namespace["urlopen"] = lambda *_args, **_kwargs: Response()

    with pytest.raises(ValueError, match="checksum mismatch"):
        namespace["download"](  # type: ignore[operator]
            "https://storage.invalid/model.pt",
            tmp_path / "model.pt",
            "0" * 64,
        )


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


def test_completed_job_convergence_derives_pipeline_from_active_job() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    finished_at = datetime.now(UTC)
    with session_factory() as session:
        pipeline = TrainingPipeline(
            id="pipeline-converge", name="pipeline-converge", task="detect", scale="n"
        )
        task = Task(id="task-converge", task_type="EDGE_TRAIN", status="RUNNING")
        completed = TrainingJob(
            id="job-completed",
            pipeline_id=pipeline.id,
            task_id=task.id,
            trained_model_id="legacy-model",
            status="running",
            metrics={
                "weights": {
                    "best.pt": "minio://models/best.pt",
                    "last.pt": "minio://models/last.pt",
                }
            },
            finished_at=finished_at,
        )
        active = TrainingJob(
            id="job-active", pipeline_id=pipeline.id, status="running"
        )
        active_attempt = TrainingJobAttempt(
            training_job_id=active.id,
            attempt_number=1,
            status="training",
            launch_spec={},
            launch_spec_checksum="a" * 64,
        )
        run = DistributedTrainingRun(
            id="run-converge",
            training_job_id=completed.id,
            resource_pool_id="pool-1",
            node_ids=["node-1"],
            ranks=[],
            master_addr="10.0.0.1",
            master_port=29500,
        )
        execution = RemoteExecution(
            id="execution-converge",
            node_id="node-1",
            task_id=task.id,
            training_job_id=completed.id,
            resource_type="distributed_training_run",
            resource_id=run.id,
            operation="train",
            status="running",
            idempotency_key="converge-active",
        )
        session.add_all(
            [pipeline, task, completed, active, active_attempt, run, execution]
        )
        session.commit()

    handler = DistributedTrainingHandler(session_factory, object(), object())  # type: ignore[arg-type]

    assert handler._converge_existing_success("execution-converge") is True
    with session_factory() as session:
        assert session.get(TrainingPipeline, "pipeline-converge").status == "running"  # type: ignore[union-attr]


def test_edge_extracts_framework_neutral_inputs_from_immutable_snapshot() -> None:
    snapshot = {
        **_runtime_inputs(),
        "framework": "ultralytics",
        "adapter_key": "ultralytics.object_detection.v1",
        "adapter_version": "1.0.0",
    }
    inputs = _resolved_runtime_inputs(  # type: ignore[arg-type]
        SimpleNamespace(resolved_snapshot=snapshot),
        LaunchSpec(
            adapter_key="ultralytics.object_detection.v1",
            adapter_version="1.0.0",
            argv=("/usr/local/bin/visiox-train",),
        ),
        [{"role": "dataset"}, {"role": "model"}],
    )

    assert inputs["parameters"] == {"epochs": 1}
    assert inputs["model"]["runtime_id"] == "runtime-model"
    assert inputs["dataset"]["format"] == "yolo"
    assert inputs["artifacts"] == [
        {"role": "dataset", "path": "/workspace/dataset"},
        {"role": "model", "path": "/workspace/model/base.pt"},
    ]


def test_remote_adapter_identity_is_validated_without_execution_allowlist() -> None:
    pipeline = SimpleNamespace(
        framework="paddlex",
        adapter_key="paddlex.object_detection.v1",
        adapter_version="1.0.0",
        engine="paddlex",
    )
    launch_spec = LaunchSpec(
        adapter_key="paddlex.object_detection.v1",
        adapter_version="1.0.0",
        argv=("/usr/local/bin/visiox-train",),
    )

    adapter = _require_remote_adapter(pipeline, launch_spec)  # type: ignore[arg-type]

    assert adapter.framework == "paddlex"
    assert adapter.adapter_key == "paddlex.object_detection.v1"


def test_edge_prepares_paddlex_coco_dataset_from_immutable_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uploaded: dict[str, bytes] = {}

    class Storage:
        def put_file(self, bucket, object_name, path, content_type=None):
            assert bucket == "training"
            assert content_type == "application/gzip"
            uploaded[object_name] = path.read_bytes()
            return f"minio://{bucket}/{object_name}"

        def presigned_get_url(self, uri, *, expires):
            assert expires == distributed_execution._PRESIGNED_URL_TTL
            return f"https://storage.invalid/{uri.removeprefix('minio://')}"

    def export_dataset(
        _session,
        _storage,
        dataset_id,
        dataset_version_id,
        output_dir,
        *,
        runtime_model_id,
    ):
        assert dataset_id == "dataset-paddlex"
        assert dataset_version_id == "version-paddlex"
        assert runtime_model_id == "PP-YOLOE_plus-S"
        (output_dir / "annotations").mkdir(parents=True)
        (output_dir / "annotations" / "instance_train.json").write_text(
            "{}", encoding="utf-8"
        )
        return SimpleNamespace(manifest_checksum="b" * 64)

    monkeypatch.setattr(
        distributed_execution,
        "export_paddlex_detection_dataset",
        export_dataset,
        raising=False,
    )
    handler = DistributedTrainingHandler(
        create_session_factory(create_engine("sqlite://")),
        object(),
        Storage(),  # type: ignore[arg-type]
    )
    artifacts = handler._prepare_artifacts(
        SimpleNamespace(id="run-paddlex", attempt=2, checkpoint_uri=None),
        SimpleNamespace(
            resolved_snapshot={
                "model": {"runtime_id": "PP-YOLOE_plus-S"},
                "dataset": {
                    "version_id": "version-paddlex",
                    "format": "coco",
                    "manifest_checksum": "b" * 64,
                },
            }
        ),
        SimpleNamespace(),
        SimpleNamespace(
            framework="paddlex",
            adapter_key="paddlex.object_detection.v1",
            adapter_version="1.0.0",
        ),
        None,
        SimpleNamespace(id="dataset-paddlex"),
    )

    object_name = "distributed/run-paddlex/2/paddlex-dataset.tar.gz"
    assert artifacts == [
        {
            "role": "dataset",
            "download_url": f"https://storage.invalid/training/{object_name}",
            "checksum_sha256": hashlib.sha256(uploaded[object_name]).hexdigest(),
            "target_path": "dataset.tar.gz",
            "unpack_to": "dataset",
        }
    ]


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
        _runtime_inputs("paddlex", ("dataset",)),
        artifacts,
        mlflow_tracking_uri="http://platform.test:5001",
    )

    assert request["schema_version"] == "1.0"
    assert request["runtime_image_digest"].endswith("@sha256:" + "a" * 64)
    assert request["artifacts"] == artifacts
    spec = LaunchSpec.model_validate(request["launch_spec"])
    assert spec.adapter_key == "paddlex.object_detection.v1"
    assert spec.argv == ("/usr/local/bin/visiox-train",)
    assert spec.env["VISIOX_FRAMEWORK"] == "paddlex"
    assert spec.env["VISIOX_NODE_ID"] == "node-1"
    assert spec.env["VISIOX_GPU_UUIDS_JSON"] == '["GPU-one"]'
    assert spec.env["VISIOX_MASTER_ADDR"] == "10.10.40.10"
    assert spec.env["VISIOX_MASTER_PORT"] == "29500"
    assert spec.env["VISIOX_OUTPUT_DIR"] == "/workspace/output"
    assert spec.env["MLFLOW_TRACKING_URI"] == "http://platform.test:5001"
    assert spec.env["MLFLOW_HTTP_REQUEST_TIMEOUT"] == "3"
    assert spec.env["MLFLOW_HTTP_REQUEST_MAX_RETRIES"] == "0"
    runtime_inputs = json.loads(spec.env["VISIOX_RUNTIME_INPUTS_JSON"])
    assert runtime_inputs["parameters"] == {"epochs": 1}
    assert runtime_inputs["artifacts"] == [
        {"path": "/workspace/dataset", "role": "dataset"}
    ]
    assert "VISIOX_TRAINING_ARGUMENTS_JSON" not in spec.env
    assert "VISIOX_FRAMEWORK_PARAMETERS_JSON" not in spec.env
    assert len(request["launch_spec_checksum"]) == 64
    assert "action" not in request
    assert "engine" not in request


def test_resume_staging_request_contains_only_persisted_workspace_identity() -> None:
    request = _resume_staging_request(  # type: ignore[arg-type]
        SimpleNamespace(id="run-1", attempt=2),
        SimpleNamespace(id="job-1"),
        SimpleNamespace(
            adapter_key="paddlex.object_detection.v1",
            adapter_version="1.0.0",
        ),
    )

    assert request == {
        "schema_version": "1.0",
        "action": "resume",
        "run_id": "run-1",
        "attempt": 2,
        "training_job_id": "job-1",
        "adapter_key": "paddlex.object_detection.v1",
        "adapter_version": "1.0.0",
    }


def test_active_distributed_job_reuses_persisted_container_without_relaunching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = LaunchSpec(
        adapter_key="paddlex.object_detection.v1",
        adapter_version="1.0.0",
        argv=("/usr/local/bin/visiox-train",),
    )
    run = SimpleNamespace(
        id="run-resume",
        attempt=1,
        node_ids=["node-1"],
        ranks=[
            {
                "node_id": "node-1",
                "node_rank": 0,
                "lan_address": "10.0.0.1",
                "gpu_uuids": ["GPU-one"],
            }
        ],
        container_ids=["a" * 64],
    )
    job = SimpleNamespace(id="job-resume")
    attempt = SimpleNamespace(
        launch_spec=spec.model_dump(mode="json"),
        launch_spec_checksum=spec.canonical_checksum_sha256(),
    )
    pipeline = SimpleNamespace(
        framework="paddlex",
        adapter_key="paddlex.object_detection.v1",
        adapter_version="1.0.0",
        engine="paddlex",
    )
    execution = SimpleNamespace(id="execution-resume")
    handler = DistributedTrainingHandler(lambda: None, object(), object())  # type: ignore[arg-type]
    handler._converge_existing_success = lambda _execution_id: False  # type: ignore[method-assign]
    handler._load = lambda _execution: (  # type: ignore[method-assign]
        run,
        job,
        attempt,
        SimpleNamespace(),
        pipeline,
        None,
        SimpleNamespace(),
    )
    handler._ensure_log_stream = lambda *_args: None  # type: ignore[method-assign]
    handler._transition = lambda *_args: None  # type: ignore[method-assign]
    handler._prepare_artifacts = lambda *_args: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AssertionError("active recovery must not prepare artifacts")
    )
    handler._container_ids = lambda _run_id: ("a" * 64,)  # type: ignore[method-assign]
    handler._mark_training = lambda _execution_id: None  # type: ignore[method-assign]
    handler._wait_for_completion = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
    handler._collect_rank_zero = lambda *_args, **_kwargs: SimpleNamespace(  # type: ignore[method-assign]
        artifacts={}
    )
    handler._cache_final_observability = lambda *_args: None  # type: ignore[method-assign]
    handler._mark_succeeded = lambda *_args: None  # type: ignore[method-assign]
    handler._close_log_stream = lambda *_args: None  # type: ignore[method-assign]
    handler._mark_failed = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    resume_requests: list[dict[str, object]] = []
    handler._stage._load_target = lambda _node_id: object()  # type: ignore[method-assign]
    handler._stage._run_script = lambda _target, request: (  # type: ignore[method-assign]
        resume_requests.append(request)
        or {
            "root": "/home/edge/.local/share/visiox/training/run-resume/1",
            "paths": {
                "output": "/home/edge/.local/share/visiox/training/run-resume/1/output",
                "launch_spec": "/home/edge/.local/share/visiox/training/run-resume/1/input/launch-spec.json",
                "artifacts": {},
            },
        }
    )
    handler._launch._run_script = lambda *_args, **_kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
        AssertionError("active recovery must not launch another container")
    )

    result = handler.execute(execution)  # type: ignore[arg-type]

    assert result.status == "succeeded"
    assert resume_requests == [
        {
            "schema_version": "1.0",
            "action": "resume",
            "run_id": "run-resume",
            "attempt": 1,
            "training_job_id": "job-resume",
            "adapter_key": "paddlex.object_detection.v1",
            "adapter_version": "1.0.0",
        }
    ]


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
        _runtime_inputs(artifact_roles=()),
        [],
    )
    stage.paths = {
        "output": "/home/edge/run/output",
        "launch_spec": "/home/edge/run/input/launch-spec.json",
        "artifacts": {},
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
        "adapter_version",
        "gpu_uuids",
        "node_rank",
        "launch_spec_checksum",
        "paths",
        "mounts",
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


def test_managed_dataset_mount_is_writable_but_model_inputs_remain_read_only() -> None:
    mounts = distributed_execution._artifact_mounts(
        {
            "dataset": "/home/edge/run/input/dataset",
            "model": "/home/edge/run/input/model.pt",
            "checkpoint": "/home/edge/run/input/last.pt",
        }
    )

    assert mounts == [
        {
            "source": "/home/edge/run/input/dataset",
            "target": "/workspace/dataset",
            "read_only": False,
        },
        {
            "source": "/home/edge/run/input/model.pt",
            "target": "/workspace/model/base.pt",
            "read_only": True,
        },
        {
            "source": "/home/edge/run/input/last.pt",
            "target": "/workspace/checkpoint/last.pt",
            "read_only": True,
        },
    ]


def test_remote_rank_accepts_only_the_managed_dataset_as_writable(
    tmp_path: Path,
) -> None:
    script = load_packaged_script("launch_rank.sh").decode("utf-8")
    embedded = script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    namespace: dict[str, object] = {"__name__": "visiox_script_test"}
    exec(compile(embedded, "launch_rank.sh", "exec"), namespace)
    validate = namespace["validate"]
    staged_request = _generic_staging_request()
    root = tmp_path / "run"
    input_path = root / "input"
    output_path = root / "output"
    dataset_path = input_path / "dataset"
    model_path = input_path / "model.pt"
    input_path.mkdir(parents=True)
    output_path.mkdir()
    dataset_path.mkdir()
    model_path.write_bytes(b"model")
    launch_spec_path = input_path / "launch-spec.json"
    launch_spec_path.write_text("{}", encoding="utf-8")
    request = _launch_request(  # type: ignore[arg-type]
        SimpleNamespace(
            id="run-1",
            attempt=1,
            training_image_digest=staged_request["runtime_image_digest"],
        ),
        SimpleNamespace(node_rank=0, gpu_uuids=("GPU-one",)),
        SimpleNamespace(
            paths={
                "output": str(output_path),
                "launch_spec": str(launch_spec_path),
                "artifacts": {
                    "dataset": str(dataset_path),
                    "model": str(model_path),
                },
            }
        ),
        staged_request,
    )

    assert validate(request) == request  # type: ignore[operator]
    request["mounts"][1]["read_only"] = False
    with pytest.raises(ValueError, match="distributed rank request"):
        validate(request)  # type: ignore[operator]


def test_remote_rank_script_rejects_control_characters() -> None:
    script = load_packaged_script("stage_training.sh").decode("utf-8")
    embedded = script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    namespace: dict[str, object] = {"__name__": "visiox_script_test"}
    exec(compile(embedded, "stage_training.sh", "exec"), namespace)
    namespace["Path"] = PurePosixPath
    validate = namespace["validate"]
    request = _generic_staging_request()
    request["launch_spec"]["env"]["VISIOX_RUNTIME_INPUTS_JSON"] = json.dumps(
        {
            **_runtime_inputs("paddlex"),
            "parameters": {"custom": "epochs=2\nwhoami"},
            "artifacts": [
                {"role": "dataset", "path": "/workspace/dataset"},
                {"role": "model", "path": "/workspace/model/base.pt"},
                {"role": "checkpoint", "path": "/workspace/checkpoint/last.pt"},
            ],
        }
    )
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
                "output": str(output_path),
                "launch_spec": str(launch_spec_path),
                "artifacts": {},
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


def test_remote_rank_collect_streams_last_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "output"
    checkpoint = output / "runs" / "job-1" / "weights" / "last.pt"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(b"checkpoint")
    script = load_packaged_script("launch_rank.sh").decode("utf-8")
    embedded = script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    namespace: dict[str, object] = {"__name__": "visiox_script_test"}
    exec(compile(embedded, "launch_rank.sh", "exec"), namespace)
    uploaded: list[bytes] = []
    bodies: list[object] = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def urlopen(request, timeout):
        assert timeout == 600
        bodies.append(request.data)
        uploaded.append(request.data.read())
        return Response()

    namespace["urlopen"] = urlopen
    manifest = write_artifact_manifest(
        output,
        task_id="job-1",
        adapter_key="ultralytics.object_detection.v1",
        adapter_version="1.0.0",
    ).model_dump(mode="json")
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda _path: (_ for _ in ()).throw(AssertionError("whole-file read")),
    )
    relative = "runs/job-1/weights/last.pt"
    result = namespace["collect_artifacts"](  # type: ignore[operator]
        str(output),
        {relative: "https://minio.internal/checkpoint?signature=short-lived"},
        manifest,
    )

    assert uploaded == [b"checkpoint"]
    assert all(hasattr(body, "read") for body in bodies)
    assert result[relative]["size_bytes"] == 10


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
        assert hasattr(request.data, "read")
        uploaded.append(request.data.read())
        return Response()

    namespace["urlopen"] = urlopen
    manifest = write_artifact_manifest(
        output,
        task_id="job-1",
        adapter_key="ultralytics.object_detection.v1",
        adapter_version="1.0.0",
    ).model_dump(mode="json")
    uploads = {
        f"runs/job-1/{name}": f"https://minio.internal/{name}" for name in expected
    }
    result = namespace["collect_artifacts"](  # type: ignore[operator]
        str(output),
        uploads,
        manifest,
    )

    assert uploaded == list(expected.values())
    assert set(result) == set(uploads)


def test_remote_rank_large_artifact_never_uses_whole_file_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "output"
    artifact = output / "weights" / "large.pt"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"x" * (3 * 1024 * 1024 + 17))
    script = load_packaged_script("launch_rank.sh").decode("utf-8")
    embedded = script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    namespace: dict[str, object] = {"__name__": "visiox_script_test"}
    exec(compile(embedded, "launch_rank.sh", "exec"), namespace)
    manifest = write_artifact_manifest(
        output,
        task_id="job-1",
        adapter_key="ultralytics.object_detection.v1",
        adapter_version="1.0.0",
    ).model_dump(mode="json")
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda _path: (_ for _ in ()).throw(AssertionError("whole-file read")),
    )
    chunks: list[int] = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def urlopen(request, timeout):
        assert timeout == 600
        while chunk := request.data.read(1024 * 1024):
            chunks.append(len(chunk))
        return Response()

    namespace["urlopen"] = urlopen
    result = namespace["collect_artifacts"](  # type: ignore[operator]
        str(output),
        {"weights/large.pt": "https://minio.internal/large.pt"},
        manifest,
    )

    assert len(chunks) == 4
    assert max(chunks) <= 1024 * 1024
    assert result["weights/large.pt"]["size_bytes"] == artifact.stat().st_size


def test_llm_launch_spec_carries_unified_runtime_inputs_without_edge_translation() -> (
    None
):
    run = SimpleNamespace(
        id="run-llm",
        attempt=1,
        training_image_digest="registry.local/visiox/llm@sha256:" + "a" * 64,
        master_addr="10.10.40.10",
        master_port=29500,
    )
    rank = SimpleNamespace(node_id="node-1", gpu_uuids=("GPU-one",), node_rank=0)
    request = _staging_request(  # type: ignore[arg-type]
        run,
        SimpleNamespace(id="job-llm"),
        rank,
        (rank,),  # type: ignore[arg-type]
        "llamafactory",
        "llamafactory.llm_sft.v1",
        "1.0.0",
        {
            **_runtime_inputs("llamafactory", ()),
            "parameters": {"num_train_epochs": 2},
            "model": {
                **_runtime_inputs("llamafactory", ())["model"],
                "source": "modelscope",
                "id": "Qwen/Qwen3-0.6B",
                "runtime_id": "Qwen/Qwen3-0.6B",
                "revision": "c" * 40,
            },
        },
        [],
    )

    spec = request["launch_spec"]
    parsed = LaunchSpec.model_validate(spec)
    assert parsed.adapter_key == "llamafactory.llm_sft.v1"
    assert parsed.argv == ("/usr/local/bin/visiox-train",)
    assert parsed.env["VISIOX_FRAMEWORK"] == "llamafactory"
    runtime_inputs = json.loads(parsed.env["VISIOX_RUNTIME_INPUTS_JSON"])
    assert runtime_inputs["model"]["source"] == "modelscope"
    assert runtime_inputs["parameters"] == {"num_train_epochs": 2}
    assert "USE_MODELSCOPE_HUB" not in parsed.env
    assert parsed.env["HF_HOME"] == "/workspace/model-cache/huggingface"
    assert parsed.env["MODELSCOPE_CACHE"] == "/workspace/model-cache/modelscope"


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
    assert "dst=/workspace/input/launch-spec.json,readonly" in script
    assert "dst=/workspace/input,readonly" not in script
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
        _runtime_inputs("paddlex", ("dataset",)),
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
        (
            "unknown request schema",
            lambda request: request.__setitem__("schema_version", "2.0"),
            False,
        ),
        (
            "mutable image",
            lambda request: request.__setitem__(
                "runtime_image_digest", "registry.local/visiox/paddlex:latest"
            ),
            False,
        ),
        (
            "unsafe artifact path",
            lambda request: request["artifacts"][0].__setitem__(
                "target_path", "../dataset.tar.gz"
            ),
            False,
        ),
        (
            "host artifact path",
            lambda request: request["artifacts"][0].__setitem__(
                "target_path", "C:/dataset.tar.gz"
            ),
            False,
        ),
        (
            "unknown launch schema",
            lambda request: request["launch_spec"].__setitem__("schema_version", "2.0"),
            True,
        ),
        (
            "arbitrary entrypoint",
            lambda request: request["launch_spec"].__setitem__("argv", ["/bin/sh"]),
            True,
        ),
        (
            "newline framework parameter",
            lambda request: request["launch_spec"]["env"].__setitem__(
                "VISIOX_RUNTIME_INPUTS_JSON",
                json.dumps(
                    {
                        **_runtime_inputs("paddlex"),
                        "parameters": {"custom": "epochs=1\nwhoami"},
                        "artifacts": [
                            {"role": "dataset", "path": "/workspace/dataset"},
                            {"role": "model", "path": "/workspace/model/base.pt"},
                            {
                                "role": "checkpoint",
                                "path": "/workspace/checkpoint/last.pt",
                            },
                        ],
                    }
                ),
            ),
            True,
        ),
        (
            "host path framework parameter",
            lambda request: request["launch_spec"]["env"].__setitem__(
                "VISIOX_RUNTIME_INPUTS_JSON",
                json.dumps(
                    {
                        **_runtime_inputs("paddlex"),
                        "parameters": {"resume": "C:/host/checkpoint.pt"},
                        "artifacts": [
                            {"role": "dataset", "path": "/workspace/dataset"},
                            {"role": "model", "path": "/workspace/model/base.pt"},
                            {
                                "role": "checkpoint",
                                "path": "/workspace/checkpoint/last.pt",
                            },
                        ],
                    }
                ),
            ),
            True,
        ),
        (
            "host runtime path",
            lambda request: request["launch_spec"].__setitem__(
                "working_directory", "C:/workspace"
            ),
            True,
        ),
        (
            "checksum mismatch",
            lambda request: request.__setitem__("launch_spec_checksum", "0" * 64),
            False,
        ),
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
    unsigned = ArtifactManifest(
        task_id="job-1",
        adapter_key="ultralytics.object_detection.v1",
        adapter_version="1.0.0",
        artifacts=(
            ArtifactEntry(
                path="weights/best.bin",
                size_bytes=len(b"trusted model"),
                checksum_sha256=hashlib.sha256(b"trusted model").hexdigest(),
                artifact_type="model_weight",
            ),
        ),
    )
    manifest = unsigned.model_copy(
        update={"checksum_sha256": unsigned.canonical_checksum_sha256()}
    ).model_dump(mode="json")

    assert (
        namespace["validate_artifact_manifest"](  # type: ignore[operator]
            manifest, output
        )
        == manifest
    )

    for invalid_manifest in (
        {**manifest, "schema_version": "2.0"},
        {
            **manifest,
            "artifacts": [{**manifest["artifacts"][0], "path": "../best.bin"}],
        },
        {**manifest, "artifacts": [{**manifest["artifacts"][0], "size_bytes": 1}]},
        {
            **manifest,
            "artifacts": [{**manifest["artifacts"][0], "checksum_sha256": "0" * 64}],
        },
        {
            **manifest,
            "artifacts": manifest["artifacts"] * (namespace["MAX_ARTIFACT_COUNT"] + 1),
        },
    ):
        with pytest.raises(ValueError):
            namespace["validate_artifact_manifest"](invalid_manifest, output)  # type: ignore[operator]


def test_control_plane_rejects_artifact_manifest_identity_and_checksum_mismatch() -> (
    None
):
    unsigned = ArtifactManifest(
        task_id="job-1",
        adapter_key="ultralytics.object_detection.v1",
        adapter_version="1.0.0",
        artifacts=(),
    )
    manifest = unsigned.model_copy(
        update={"checksum_sha256": unsigned.canonical_checksum_sha256()}
    )
    spec = LaunchSpec(
        adapter_key="ultralytics.object_detection.v1",
        adapter_version="1.0.0",
        argv=("/usr/local/bin/visiox-train",),
    )

    assert (
        _validate_remote_artifact_manifest(
            manifest.model_dump(mode="json"), task_id="job-1", launch_spec=spec
        )
        == manifest
    )
    with pytest.raises(ValueError, match="identity"):
        _validate_remote_artifact_manifest(
            manifest.model_copy(update={"task_id": "other"}).model_dump(mode="json"),
            task_id="job-1",
            launch_spec=spec,
        )
    with pytest.raises(ValueError, match="checksum"):
        _validate_remote_artifact_manifest(
            manifest.model_copy(update={"checksum_sha256": "0" * 64}).model_dump(
                mode="json"
            ),
            task_id="job-1",
            launch_spec=spec,
        )


def test_control_plane_reads_roles_from_train_result_sidecar() -> None:
    payload = json.dumps(
        {
            "schema_version": "1.0",
            "framework": "ultralytics",
            "status": "success",
            "exit_code": 0,
            "artifacts": [
                {
                    "role": "best_weights",
                    "path": "runs/job/weights/champion.ckpt",
                }
            ],
        }
    ).encode()

    class Storage:
        def get_file(self, bucket, object_name, destination):
            assert (bucket, object_name) == (
                "training",
                "jobs/job-1/attempt-2/artifacts/train_result.json",
            )
            destination.write_bytes(payload)

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    handler = DistributedTrainingHandler(
        create_session_factory(engine), object(), Storage()  # type: ignore[arg-type]
    )
    manifest = ArtifactManifest(
        task_id="job-1",
        adapter_key="ultralytics.object_detection.v1",
        adapter_version="1.0.0",
        artifacts=(
            ArtifactEntry(
                path="runs/job/weights/champion.ckpt",
                size_bytes=4,
                checksum_sha256="a" * 64,
                artifact_type="model_weight",
            ),
            ArtifactEntry(
                path="train_result.json",
                size_bytes=len(payload),
                checksum_sha256=hashlib.sha256(payload).hexdigest(),
                artifact_type="metrics",
            ),
        ),
    )

    roles = handler._load_artifact_roles(
        "job-1",
        SimpleNamespace(
            framework="ultralytics",
            adapter_key="ultralytics.object_detection.v1",
        ),
        manifest,
        {
            "runs/job/weights/champion.ckpt": "minio://models/champion.ckpt",
            "train_result.json": (
                "minio://training/jobs/job-1/attempt-2/artifacts/train_result.json"
            ),
        },
    )

    assert roles == {"runs/job/weights/champion.ckpt": "best_weights"}


def test_control_plane_reads_real_paddlex_sidecar_contract() -> None:
    sidecar_artifacts = [
        {"role": "config", "path": "config.yaml"},
        {"role": "train_log", "path": "train.log"},
        {"role": "best_dynamic_weights", "path": "best_model/best_model.pdparams"},
        {
            "role": "best_static_inference",
            "path": "best_model/inference/inference.json",
        },
        {
            "role": "best_static_inference",
            "path": "best_model/inference/inference.pdiparams",
        },
        {"role": "last_weights", "path": "last_model/model.pdparams"},
        {"role": "evaluation_report", "path": "evaluation_metrics.json"},
        {"role": "visualization", "path": "results.png"},
        {"role": "visualdl", "path": "visualdl/events.vdlrecords.1"},
    ]
    payload = json.dumps(
        {
            "schema_version": "1.0",
            "framework": "paddlex",
            "status": "completed",
            "exit_code": 0,
            "artifacts": sidecar_artifacts,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()

    class Storage:
        def get_file(self, _bucket, _object_name, destination):
            destination.write_bytes(payload)

    artifact_types = {
        "config.yaml": "training_output",
        "train.log": "training_output",
        "best_model/best_model.pdparams": "model_weight",
        "best_model/inference/inference.json": "metrics",
        "best_model/inference/inference.pdiparams": "training_output",
        "last_model/model.pdparams": "model_weight",
        "evaluation_metrics.json": "metrics",
        "results.png": "visualization",
        "visualdl/events.vdlrecords.1": "training_output",
    }
    entries = [
        ArtifactEntry(
            path=path,
            size_bytes=4,
            checksum_sha256="a" * 64,
            artifact_type=artifact_type,
        )
        for path, artifact_type in artifact_types.items()
    ]
    entries.append(
        ArtifactEntry(
            path="train_result.json",
            size_bytes=len(payload),
            checksum_sha256=hashlib.sha256(payload).hexdigest(),
            artifact_type="metrics",
        )
    )
    manifest = ArtifactManifest(
        task_id="job-paddlex",
        adapter_key="paddlex.object_detection.v1",
        adapter_version="1.0.0",
        artifacts=tuple(entries),
    )
    handler = DistributedTrainingHandler(
        create_session_factory(create_engine("sqlite://")), object(), Storage()  # type: ignore[arg-type]
    )
    artifact_uris = {
        entry.path: f"minio://training/{entry.path}" for entry in manifest.artifacts
    }

    roles = handler._load_artifact_roles(
        "job-paddlex",
        SimpleNamespace(
            framework="paddlex", adapter_key="paddlex.object_detection.v1"
        ),
        manifest,
        artifact_uris,
    )

    assert roles == {
        "best_model/best_model.pdparams": "best_dynamic_weights",
        "best_model/inference/inference.json": "best_static_inference",
        "best_model/inference/inference.pdiparams": "best_static_inference",
        "last_model/model.pdparams": "last_weights",
    }


@pytest.mark.parametrize(
    ("role", "artifact_type"),
    [
        ("invented_report", "metrics"),
        ("best_dynamic_weights", "visualization"),
    ],
)
def test_control_plane_rejects_invalid_paddlex_sidecar_roles(
    role: str, artifact_type: str
) -> None:
    payload = json.dumps(
        {
            "schema_version": "1.0",
            "framework": "paddlex",
            "status": "success",
            "exit_code": 0,
            "artifacts": [{"role": role, "path": "reported-artifact"}],
        }
    ).encode()

    class Storage:
        def get_file(self, _bucket, _object_name, destination):
            destination.write_bytes(payload)

    manifest = ArtifactManifest(
        task_id="job-paddlex",
        adapter_key="paddlex.object_detection.v1",
        adapter_version="1.0.0",
        artifacts=(
            ArtifactEntry(
                path="reported-artifact",
                size_bytes=4,
                checksum_sha256="a" * 64,
                artifact_type=artifact_type,
            ),
            ArtifactEntry(
                path="train_result.json",
                size_bytes=len(payload),
                checksum_sha256=hashlib.sha256(payload).hexdigest(),
                artifact_type="metrics",
            ),
        ),
    )
    handler = DistributedTrainingHandler(
        create_session_factory(create_engine("sqlite://")), object(), Storage()  # type: ignore[arg-type]
    )

    with pytest.raises(distributed_execution.ArtifactCollectionError):
        handler._load_artifact_roles(
            "job-paddlex",
            SimpleNamespace(
                framework="paddlex", adapter_key="paddlex.object_detection.v1"
            ),
            manifest,
            {
                entry.path: f"minio://training/{entry.path}"
                for entry in manifest.artifacts
            },
        )


def test_control_plane_ignores_nested_train_result_sidecar() -> None:
    class Storage:
        def get_file(self, *_args):
            raise AssertionError("nested sidecar must not be trusted")

    handler = DistributedTrainingHandler(
        create_session_factory(create_engine("sqlite://")), object(), Storage()  # type: ignore[arg-type]
    )
    manifest = ArtifactManifest(
        task_id="job-1",
        adapter_key="ultralytics.object_detection.v1",
        adapter_version="1.0.0",
        artifacts=(
            ArtifactEntry(
                path="forged/train_result.json",
                size_bytes=2,
                checksum_sha256=hashlib.sha256(b"{}").hexdigest(),
                artifact_type="metrics",
            ),
        ),
    )

    assert handler._load_artifact_roles(
        "job-1",
        SimpleNamespace(
            framework="ultralytics",
            adapter_key="ultralytics.object_detection.v1",
        ),
        manifest,
        {
            "forged/train_result.json": (
                "minio://training/jobs/job-1/attempt-1/artifacts/forged/train_result.json"
            )
        },
    ) == {}


@pytest.mark.parametrize(
    ("payload_update", "role", "weight_type"),
    [
        ({"status": "failed"}, "best_weights", "model_weight"),
        ({"exit_code": 1}, "best_weights", "model_weight"),
        ({"schema_version": "2.0"}, "best_weights", "model_weight"),
        ({"framework": "paddlex"}, "best_weights", "model_weight"),
        ({}, "best_weights", "visualization"),
        ({}, "adapter_weights", "model_weight"),
    ],
)
def test_control_plane_rejects_untrusted_train_result_roles(
    payload_update: dict[str, object], role: str, weight_type: str
) -> None:
    payload_data = {
        "schema_version": "1.0",
        "framework": "ultralytics",
        "status": "success",
        "exit_code": 0,
        "artifacts": [{"role": role, "path": "output/model.bin"}],
        **payload_update,
    }
    payload = json.dumps(payload_data).encode()

    class Storage:
        def get_file(self, _bucket, _object_name, destination):
            destination.write_bytes(payload)

    handler = DistributedTrainingHandler(
        create_session_factory(create_engine("sqlite://")), object(), Storage()  # type: ignore[arg-type]
    )
    manifest = ArtifactManifest(
        task_id="job-1",
        adapter_key="ultralytics.object_detection.v1",
        adapter_version="1.0.0",
        artifacts=(
            ArtifactEntry(
                path="output/model.bin",
                size_bytes=4,
                checksum_sha256="a" * 64,
                artifact_type=weight_type,
            ),
            ArtifactEntry(
                path="train_result.json",
                size_bytes=len(payload),
                checksum_sha256=hashlib.sha256(payload).hexdigest(),
                artifact_type="metrics",
            ),
        ),
    )

    with pytest.raises(distributed_execution.ArtifactCollectionError):
        handler._load_artifact_roles(
            "job-1",
            SimpleNamespace(
                framework="ultralytics",
                adapter_key="ultralytics.object_detection.v1",
            ),
            manifest,
            {
                "output/model.bin": "minio://models/output/model.bin",
                "train_result.json": "minio://training/train_result.json",
            },
        )


@pytest.mark.parametrize(("size_delta", "checksum"), [(1, None), (0, "0" * 64)])
def test_control_plane_verifies_downloaded_train_result(
    size_delta: int, checksum: str | None
) -> None:
    payload = json.dumps(
        {
            "schema_version": "1.0",
            "framework": "ultralytics",
            "status": "success",
            "exit_code": 0,
            "artifacts": [],
        }
    ).encode()

    class Storage:
        def get_file(self, _bucket, _object_name, destination):
            destination.write_bytes(payload)

    handler = DistributedTrainingHandler(
        create_session_factory(create_engine("sqlite://")), object(), Storage()  # type: ignore[arg-type]
    )
    manifest = ArtifactManifest(
        task_id="job-1",
        adapter_key="ultralytics.object_detection.v1",
        adapter_version="1.0.0",
        artifacts=(
            ArtifactEntry(
                path="train_result.json",
                size_bytes=len(payload) + size_delta,
                checksum_sha256=checksum or hashlib.sha256(payload).hexdigest(),
                artifact_type="metrics",
            ),
        ),
    )

    with pytest.raises(distributed_execution.ArtifactCollectionError, match="integrity"):
        handler._load_artifact_roles(
            "job-1",
            SimpleNamespace(
                framework="ultralytics",
                adapter_key="ultralytics.object_detection.v1",
            ),
            manifest,
            {"train_result.json": "minio://training/train_result.json"},
        )


def test_artifact_uri_preserves_framework_native_path_per_attempt() -> None:
    path = "output/best_model/inference/model.json"

    assert _artifact_uri("job-1", 2, path) == (
        "minio://models/trained/job-1/attempt-2/"
        "output/best_model/inference/model.json"
    )


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (
            "runs/job/results.png",
            "minio://training/jobs/job-1/attempt-2/visualizations/results.png",
        ),
        (
            "train_result.json",
            "minio://training/jobs/job-1/attempt-2/artifacts/train_result.json",
        ),
        (
            "observability/visiox-progress.json",
            "minio://training/jobs/job-1/attempt-2/artifacts/observability/visiox-progress.json",
        ),
    ],
)
def test_non_model_artifact_uris_are_attempt_scoped(path: str, expected: str) -> None:
    assert _artifact_uri("job-1", 2, path) == expected


def test_final_observability_cache_reads_attempt_scoped_artifact_uris(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    downloads: list[tuple[str, str, str]] = []

    class Storage:
        def get_file(self, bucket, object_name, destination):
            downloads.append(
                (bucket, object_name, destination.relative_to(tmp_path).as_posix())
            )
            destination.write_bytes(b"cached")

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    handler = DistributedTrainingHandler(
        create_session_factory(engine), object(), Storage()  # type: ignore[arg-type]
    )
    monkeypatch.setattr(
        distributed_execution,
        "get_settings",
        lambda: SimpleNamespace(training_runs_root=tmp_path),
    )
    collected = SimpleNamespace(
        artifacts={
            "observability/visiox-progress.json": object(),
        }
    )

    handler._cache_final_observability("job-1", 2, collected)  # type: ignore[arg-type]

    assert set(downloads) == {
        (
            "training",
            "jobs/job-1/attempt-2/artifacts/observability/visiox-progress.json",
            "runs/job-job-1/attempt-2/visiox-progress.json",
        ),
    }


def test_periodic_observability_sync_uploads_to_attempt_scoped_uri(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    uploads: list[str] = []
    downloads: list[str] = []

    class Storage:
        def presigned_put_url(self, uri, *, expires):
            del expires
            uploads.append(uri)
            return "https://storage.invalid/upload"

        def get_file(self, bucket, object_name, destination):
            assert bucket == "training"
            assert object_name == (
                "jobs/job-sync/attempt-2/artifacts/visiox-progress.json"
            )
            downloads.append(destination.relative_to(tmp_path).as_posix())
            destination.write_text('{"progress":{"percent":50}}', encoding="utf-8")

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        pipeline = TrainingPipeline(
            id="pipeline-sync", name="pipeline-sync", task="detect", scale="n"
        )
        task = Task(id="task-sync", task_type="EDGE_TRAIN", status="RUNNING")
        job = TrainingJob(
            id="job-sync", pipeline_id=pipeline.id, task_id=task.id, status="training"
        )
        run = DistributedTrainingRun(
            id="run-sync",
            training_job_id=job.id,
            resource_pool_id="pool-1",
            node_ids=["node-1"],
            ranks=[],
            master_addr="10.0.0.1",
            master_port=29500,
            attempt=2,
        )
        execution = RemoteExecution(
            id="execution-sync",
            node_id="node-1",
            task_id=task.id,
            training_job_id=job.id,
            resource_type="distributed_training_run",
            resource_id=run.id,
            operation="train",
            status="running",
            idempotency_key="sync-test",
        )
        session.add_all([pipeline, task, job, run, execution])
        session.commit()

    handler = DistributedTrainingHandler(
        session_factory, object(), Storage()  # type: ignore[arg-type]
    )
    manifest = ArtifactManifest(
        task_id="job-sync",
        adapter_key="ultralytics.object_detection.v1",
        adapter_version="1.0.0",
        artifacts=(
            ArtifactEntry(
                path="visiox-progress.json",
                size_bytes=1,
                checksum_sha256="a" * 64,
                artifact_type="metrics",
            ),
        ),
    )
    handler._remote_artifact_manifest = lambda *_args, **_kwargs: manifest  # type: ignore[method-assign]
    handler._launch._load_target = lambda _node_id: object()  # type: ignore[method-assign]
    handler._launch._run_script = lambda _target, _payload: {  # type: ignore[method-assign]
        "artifacts": {
            "visiox-progress.json": {"checksum": "a" * 64, "size_bytes": 1}
        }
    }
    handler._persist_observability_snapshot = lambda *_args: None  # type: ignore[method-assign]
    monkeypatch.setattr(
        distributed_execution,
        "get_settings",
        lambda: SimpleNamespace(training_runs_root=tmp_path),
    )

    handler._sync_observability(
        "execution-sync",
        SimpleNamespace(node_id="node-1"),  # type: ignore[arg-type]
        SimpleNamespace(paths={"output": "/workspace/output"}),  # type: ignore[arg-type]
        task_id="job-sync",
        launch_spec=LaunchSpec(
            adapter_key="ultralytics.object_detection.v1",
            adapter_version="1.0.0",
            argv=("/usr/local/bin/visiox-train",),
        ),
    )

    assert uploads == [
        "minio://training/jobs/job-sync/attempt-2/artifacts/visiox-progress.json"
    ]
    assert downloads == ["runs/job-job-sync/attempt-2/visiox-progress.json"]


def test_post_training_phases_are_persisted_on_active_attempt_and_pipeline() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        pipeline = TrainingPipeline(
            id="pipeline-phases", name="pipeline-phases", task="detect", scale="n"
        )
        task = Task(id="task-phases", task_type="EDGE_TRAIN", status="RUNNING")
        job = TrainingJob(
            id="job-phases",
            pipeline_id=pipeline.id,
            task_id=task.id,
            status="training",
        )
        attempt = TrainingJobAttempt(
            id="attempt-phases",
            training_job_id=job.id,
            attempt_number=1,
            status="training",
            launch_spec={},
            launch_spec_checksum="a" * 64,
        )
        run = DistributedTrainingRun(
            id="run-phases",
            training_job_id=job.id,
            resource_pool_id="pool-1",
            node_ids=["node-1"],
            ranks=[],
            master_addr="10.0.0.1",
            master_port=29500,
            attempt=1,
            status="training",
        )
        execution = RemoteExecution(
            id="execution-phases",
            node_id="node-1",
            task_id=task.id,
            training_job_id=job.id,
            resource_type="distributed_training_run",
            resource_id=run.id,
            operation="train",
            status="running",
            idempotency_key="phase-test",
        )
        session.add_all([pipeline, task, job, attempt, run, execution])
        session.commit()

    handler = DistributedTrainingHandler(session_factory, object(), object())  # type: ignore[arg-type]
    handler._transition("execution-phases", "evaluating", 90)
    handler._transition("execution-phases", "artifact_collecting", 95)

    with session_factory() as session:
        assert session.get(TrainingJob, "job-phases").status == "artifact_collecting"  # type: ignore[union-attr]
        assert session.get(TrainingJobAttempt, "attempt-phases").status == "artifact_collecting"  # type: ignore[union-attr]
        assert session.get(TrainingPipeline, "pipeline-phases").status == "artifact_collecting"  # type: ignore[union-attr]


def test_artifact_collection_failure_uses_durable_error_code() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        pipeline = TrainingPipeline(
            id="pipeline-failure", name="pipeline-failure", task="detect", scale="n"
        )
        task = Task(id="task-failure", task_type="EDGE_TRAIN", status="RUNNING")
        job = TrainingJob(
            id="job-failure",
            pipeline_id=pipeline.id,
            task_id=task.id,
            status="artifact_collecting",
        )
        attempt = TrainingJobAttempt(
            id="attempt-failure",
            training_job_id=job.id,
            attempt_number=1,
            status="artifact_collecting",
            launch_spec={},
            launch_spec_checksum="a" * 64,
        )
        run = DistributedTrainingRun(
            id="run-failure",
            training_job_id=job.id,
            resource_pool_id="pool-1",
            node_ids=["node-1"],
            ranks=[],
            master_addr="10.0.0.1",
            master_port=29500,
            attempt=1,
            status="artifact_collecting",
        )
        execution = RemoteExecution(
            id="execution-failure",
            node_id="node-1",
            task_id=task.id,
            training_job_id=job.id,
            resource_type="distributed_training_run",
            resource_id=run.id,
            operation="train",
            status="running",
            idempotency_key="failure-test",
        )
        session.add_all([pipeline, task, job, attempt, run, execution])
        session.commit()

    handler = DistributedTrainingHandler(session_factory, object(), object())  # type: ignore[arg-type]
    handler._mark_failed(
        "execution-failure",
        error_code="ARTIFACT_COLLECTION_FAILED",
        error_message="Required best artifact was not collected",
    )

    with session_factory() as session:
        execution = session.get(RemoteExecution, "execution-failure")
        task = session.get(Task, "task-failure")
        attempt = session.get(TrainingJobAttempt, "attempt-failure")
        assert execution.error_code == "ARTIFACT_COLLECTION_FAILED"  # type: ignore[union-attr]
        assert task.error_code == "ARTIFACT_COLLECTION_FAILED"  # type: ignore[union-attr]
        assert attempt.status == "failed"  # type: ignore[union-attr]


def test_late_failure_does_not_downgrade_completed_artifact_ingestion() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    with session_factory() as session:
        pipeline = TrainingPipeline(
            id="pipeline-race", name="pipeline-race", task="detect", scale="n", status="success"
        )
        task = Task(id="task-race", task_type="EDGE_TRAIN", status="SUCCESS")
        job = TrainingJob(
            id="job-race", pipeline_id=pipeline.id, task_id=task.id, status="success"
        )
        attempt = TrainingJobAttempt(
            id="attempt-race",
            training_job_id=job.id,
            attempt_number=1,
            status="succeeded",
            launch_spec={},
            launch_spec_checksum="a" * 64,
        )
        run = DistributedTrainingRun(
            id="run-race",
            training_job_id=job.id,
            resource_pool_id="pool-1",
            node_ids=["node-1"],
            ranks=[],
            master_addr="10.0.0.1",
            master_port=29500,
            attempt=1,
            status="succeeded",
        )
        execution = RemoteExecution(
            id="execution-race",
            node_id="node-1",
            task_id=task.id,
            training_job_id=job.id,
            resource_type="distributed_training_run",
            resource_id=run.id,
            operation="train",
            status="running",
            idempotency_key="race-test",
        )
        session.add_all([pipeline, task, job, attempt, run, execution])
        session.commit()

    handler = DistributedTrainingHandler(session_factory, object(), object())  # type: ignore[arg-type]
    handler._mark_failed("execution-race", error_message="late unique conflict")

    with session_factory() as session:
        assert session.get(TrainingJob, "job-race").status == "success"  # type: ignore[union-attr]
        assert session.get(TrainingJobAttempt, "attempt-race").status == "succeeded"  # type: ignore[union-attr]
        assert session.get(TrainingPipeline, "pipeline-race").status == "success"  # type: ignore[union-attr]
        assert session.get(Task, "task-race").status == "SUCCESS"  # type: ignore[union-attr]


def test_stop_distributed_training_cancels_active_attempt_and_derives_pipeline() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    spec = LaunchSpec(
        adapter_key="ultralytics.object_detection.v1",
        adapter_version="1.0.0",
        argv=("/usr/local/bin/visiox-train",),
    )
    with session_factory() as session:
        pipeline = TrainingPipeline(
            id="pipeline-stop", name="pipeline-stop", task="detect", scale="n"
        )
        task = Task(id="task-stop", task_type="EDGE_STOP_TRAINING", status="RUNNING")
        job = TrainingJob(
            id="job-stop", pipeline_id=pipeline.id, task_id=task.id, status="stopping"
        )
        attempt = TrainingJobAttempt(
            id="attempt-stop",
            training_job_id=job.id,
            attempt_number=1,
            status="stopping",
            launch_spec=spec.model_dump(mode="json"),
            launch_spec_checksum=spec.canonical_checksum_sha256(),
        )
        run = DistributedTrainingRun(
            id="run-stop",
            training_job_id=job.id,
            resource_pool_id="pool-1",
            node_ids=["node-1"],
            ranks=[
                {
                    "node_id": "node-1",
                    "node_rank": 0,
                    "lan_address": "10.0.0.1",
                    "gpu_uuids": ["GPU-1"],
                }
            ],
            master_addr="10.0.0.1",
            master_port=29500,
            attempt=1,
            status="stopping",
        )
        execution = RemoteExecution(
            id="execution-stop",
            node_id="node-1",
            task_id=task.id,
            training_job_id=job.id,
            resource_type="distributed_training_run",
            resource_id=run.id,
            operation="stop_training",
            status="running",
            idempotency_key="stop-test",
        )
        session.add_all([pipeline, task, job, attempt, run, execution])
        session.commit()

    handler = StopDistributedTrainingHandler(session_factory, object(), object())  # type: ignore[arg-type]
    handler._stop._load_target = lambda _node_id: object()  # type: ignore[method-assign]
    handler._stop._run_script = lambda _target, _payload: {  # type: ignore[method-assign]
        "stopped_container_ids": []
    }
    handler._try_collect_checkpoint = lambda *_args, **_kwargs: None  # type: ignore[method-assign]

    with session_factory() as session:
        execution = session.get(RemoteExecution, "execution-stop")
        assert execution is not None
        result = handler.execute(execution)

    assert result.status == "succeeded"
    with session_factory() as session:
        assert session.get(TrainingJobAttempt, "attempt-stop").status == "canceled"  # type: ignore[union-attr]
        assert session.get(TrainingPipeline, "pipeline-stop").status == "canceled"  # type: ignore[union-attr]
