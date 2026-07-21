from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


_GPU_UUID = re.compile(r"GPU-[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
_NODE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_LAN_ADDRESS = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:-]{0,253}\Z")


class IncompatibleResourcePoolError(ValueError):
    pass


class DistributedNode(BaseModel):
    model_config = ConfigDict(frozen=True)

    node_id: str
    resource_pool_id: str
    platform_kind: str
    architecture: str
    compatibility_key: str
    lan_address: str
    gpu_uuids: tuple[str, ...]
    status: str = "online"
    draining: bool = False

    @field_validator("node_id", "resource_pool_id")
    @classmethod
    def _validate_identifier(cls, value: str) -> str:
        if not _NODE_ID.fullmatch(value):
            raise ValueError("distributed node identifier is invalid")
        return value

    @field_validator("lan_address")
    @classmethod
    def _validate_lan_address(cls, value: str) -> str:
        if not _LAN_ADDRESS.fullmatch(value) or ".." in value:
            raise ValueError("distributed node LAN address is invalid")
        return value

    @field_validator("gpu_uuids")
    @classmethod
    def _validate_gpu_uuids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or len(value) != len(set(value)):
            raise ValueError("distributed node GPU inventory is invalid")
        if any(not _GPU_UUID.fullmatch(item) for item in value):
            raise ValueError("distributed node GPU inventory is invalid")
        return value


class RankAssignment(BaseModel):
    model_config = ConfigDict(frozen=True)

    node_id: str
    node_rank: int = Field(ge=0)
    lan_address: str
    gpu_uuids: tuple[str, ...]

    @property
    def local_world_size(self) -> int:
        return len(self.gpu_uuids)


class DistributedPlan(BaseModel):
    model_config = ConfigDict(frozen=True)

    resource_pool_id: str
    platform_kind: Literal["x86_nvidia", "jetson"]
    compatibility_key: str
    nodes: tuple[RankAssignment, ...]
    master_addr: str
    master_port: int = Field(ge=1024, le=65535)
    world_size: int = Field(ge=1)
    rendezvous_backend: Literal["c10d"] = "c10d"

    @property
    def nnodes(self) -> int:
        return len(self.nodes)


def build_distributed_plan(
    nodes: list[DistributedNode] | tuple[DistributedNode, ...],
    *,
    requested_gpus: int,
    master_port: int = 29500,
) -> DistributedPlan:
    if requested_gpus < 1:
        raise IncompatibleResourcePoolError("requested GPU count must be positive")
    if not 1024 <= master_port <= 65535:
        raise IncompatibleResourcePoolError("master port must be between 1024 and 65535")
    if not nodes:
        raise IncompatibleResourcePoolError("no compatible online nodes are available")

    ordered = tuple(sorted(nodes, key=lambda node: node.node_id))
    if any(node.status != "online" for node in ordered):
        raise IncompatibleResourcePoolError("all distributed nodes must be online")
    if any(node.draining for node in ordered):
        raise IncompatibleResourcePoolError("draining nodes cannot run distributed training")

    first = ordered[0]
    identity = (
        first.resource_pool_id,
        first.platform_kind,
        first.architecture,
        first.compatibility_key,
    )
    if first.platform_kind not in {"x86_nvidia", "jetson"}:
        raise IncompatibleResourcePoolError("resource pool platform is unsupported")
    if any(
        (
            node.resource_pool_id,
            node.platform_kind,
            node.architecture,
            node.compatibility_key,
        )
        != identity
        for node in ordered[1:]
    ):
        raise IncompatibleResourcePoolError("nodes must belong to one compatible resource pool")

    selected, gpus_per_node = _select_homogeneous_gpu_layout(ordered, requested_gpus)
    assignments = tuple(
        RankAssignment(
            node_id=node.node_id,
            node_rank=node_rank,
            lan_address=node.lan_address,
            gpu_uuids=node.gpu_uuids[:gpus_per_node],
        )
        for node_rank, node in enumerate(selected)
    )
    return DistributedPlan(
        resource_pool_id=first.resource_pool_id,
        platform_kind=first.platform_kind,
        compatibility_key=first.compatibility_key,
        nodes=assignments,
        master_addr=assignments[0].lan_address,
        master_port=master_port,
        world_size=requested_gpus,
    )


def _select_homogeneous_gpu_layout(
    nodes: tuple[DistributedNode, ...],
    requested_gpus: int,
) -> tuple[tuple[DistributedNode, ...], int]:
    if requested_gpus <= len(nodes[0].gpu_uuids):
        return nodes[:1], requested_gpus
    for node_count in range(2, len(nodes) + 1):
        if requested_gpus % node_count:
            continue
        gpus_per_node = requested_gpus // node_count
        selected = nodes[:node_count]
        if all(len(node.gpu_uuids) >= gpus_per_node for node in selected):
            return selected, gpus_per_node
    raise IncompatibleResourcePoolError(
        "compatible online nodes do not provide a homogeneous GPU layout"
    )


def torchrun_argv(
    plan: DistributedPlan,
    assignment: RankAssignment,
    *,
    training_arguments: tuple[str, ...],
) -> tuple[str, ...]:
    if assignment not in plan.nodes:
        raise ValueError("rank assignment is not part of the distributed plan")
    if any(not argument or any(character in argument for character in ("\x00", "\r", "\n")) for argument in training_arguments):
        raise ValueError("training argument contains an invalid control character")
    return (
        "torchrun",
        f"--nnodes={plan.nnodes}",
        f"--nproc-per-node={assignment.local_world_size}",
        f"--node-rank={assignment.node_rank}",
        f"--master-addr={plan.master_addr}",
        f"--master-port={plan.master_port}",
        "-m",
        "visiox_training_worker.train_entrypoint",
        *training_arguments,
    )
