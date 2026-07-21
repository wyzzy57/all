from pathlib import Path
from types import SimpleNamespace

import pytest

from visiox_edge_executor_worker.distributed import (
    DistributedNode,
    IncompatibleResourcePoolError,
    build_distributed_plan,
    torchrun_argv,
)
from visiox_edge_executor_worker.distributed_execution import (
    DistributedTrainingHandler,
    StopDistributedTrainingHandler,
    build_distributed_handlers,
    _training_arguments,
)
from visiox_edge_executor_worker.scripts import load_packaged_script


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


@pytest.mark.parametrize(
    "nodes",
    [
        [_node("node-1"), _node("node-2", platform="jetson")],
        [_node("node-1"), _node("node-2", pool_id="pool-other")],
        [_node("node-1"), _node("node-2", compatibility_key="other")],
    ],
)
def test_scheduler_rejects_mixed_or_incompatible_nodes(nodes: list[DistributedNode]) -> None:
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


def test_remote_training_scripts_are_json_driven_and_do_not_eval_request_values() -> None:
    remote = Path(__file__).parents[2] / "workers" / "edge-executor-worker" / "remote"
    for name in ("stage_training.sh", "launch_rank.sh", "stop_training.sh"):
        text = (remote / name).read_text(encoding="utf-8")
        assert "json.load" in text
        assert "eval " not in text
        assert "docker" in text


def test_production_handler_factory_registers_train_resume_and_stop() -> None:
    handlers = build_distributed_handlers(object(), object(), object())  # type: ignore[arg-type]

    assert set(handlers) == {"train", "resume_training", "stop_training"}
    assert isinstance(handlers["train"], DistributedTrainingHandler)
    assert isinstance(handlers["resume_training"], DistributedTrainingHandler)
    assert isinstance(handlers["stop_training"], StopDistributedTrainingHandler)


def test_distributed_training_arguments_remove_global_device_selection() -> None:
    arguments = _training_arguments(  # type: ignore[arg-type]
        SimpleNamespace(id="job-1", params={"epochs": 2, "device": "0,1"}),
        SimpleNamespace(payload={"environment": {"workers": 4}}),
    )

    assert "model=/workspace/model/base.pt" in arguments
    assert "data=/workspace/dataset/data.yaml" in arguments
    assert "epochs=2" in arguments
    assert "workers=4" in arguments
    assert not any(argument.startswith("device=") for argument in arguments)


def test_remote_rank_script_rejects_control_characters() -> None:
    script = load_packaged_script("launch_rank.sh").decode("utf-8")
    embedded = script.split("<<'PY'\n", 1)[1].rsplit("\nPY", 1)[0]
    namespace: dict[str, object] = {"__name__": "visiox_script_test"}
    exec(compile(embedded, "launch_rank.sh", "exec"), namespace)
    validate = namespace["validate"]
    request = {
        "action": "launch",
        "run_id": "run-1",
        "attempt": 1,
        "image_digest": "registry.local/visiox/train@sha256:" + "a" * 64,
        "node_id": "node-1",
        "gpu_uuids": ["GPU-one"],
        "node_rank": 0,
        "nnodes": 1,
        "nproc_per_node": 1,
        "master_addr": "10.10.40.10",
        "master_port": 29500,
        "training_arguments": ["epochs=2\nwhoami"],
        "paths": {
            "model": "/var/lib/visiox/base.pt",
            "dataset": "/var/lib/visiox/dataset",
            "output": "/var/lib/visiox/output",
        },
    }

    with pytest.raises(ValueError, match="request"):
        validate(request)  # type: ignore[operator]


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
