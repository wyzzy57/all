import json
import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class TaskType(StrEnum):
    ANALYZE_DATASET = "ANALYZE_DATASET"
    SYNC_LABEL_STUDIO_DATA = "SYNC_LABEL_STUDIO_DATA"
    IMPORT_LABEL_STUDIO_ANNOTATION = "IMPORT_LABEL_STUDIO_ANNOTATION"
    VALIDATE_DATASET_FORMAT = "VALIDATE_DATASET_FORMAT"
    PROCESS_DATASET = "PROCESS_DATASET"
    TRAIN_MODEL = "TRAIN_MODEL"
    CONVERT_MODEL = "CONVERT_MODEL"
    EDGE_PROBE = "EDGE_PROBE"
    EDGE_DEPLOY = "EDGE_DEPLOY"
    EDGE_STOP_DEPLOYMENT = "EDGE_STOP_DEPLOYMENT"
    EDGE_ROLLBACK = "EDGE_ROLLBACK"
    EDGE_TRAIN = "EDGE_TRAIN"
    EDGE_STOP_TRAINING = "EDGE_STOP_TRAINING"
    EDGE_RESUME_TRAINING = "EDGE_RESUME_TRAINING"


EDGE_EXECUTOR_TASK_TYPES = {
    TaskType.EDGE_PROBE,
    TaskType.EDGE_DEPLOY,
    TaskType.EDGE_STOP_DEPLOYMENT,
    TaskType.EDGE_ROLLBACK,
    TaskType.EDGE_TRAIN,
    TaskType.EDGE_STOP_TRAINING,
    TaskType.EDGE_RESUME_TRAINING,
}

EDGE_EXECUTOR_RESOURCE_REFS: dict[TaskType, frozenset[str]] = {
    TaskType.EDGE_PROBE: frozenset({"node_id"}),
    TaskType.EDGE_DEPLOY: frozenset({"deployment_service_id"}),
    TaskType.EDGE_STOP_DEPLOYMENT: frozenset({"deployment_service_id"}),
    TaskType.EDGE_ROLLBACK: frozenset({"deployment_service_id"}),
    TaskType.EDGE_TRAIN: frozenset({"training_job_id"}),
    TaskType.EDGE_STOP_TRAINING: frozenset({"training_job_id"}),
    TaskType.EDGE_RESUME_TRAINING: frozenset({"training_job_id"}),
}

_RESOURCE_REF_PREFIXES = {
    "node_id": "node",
    "deployment_service_id": "service",
    "training_job_id": "job",
}
_UUID_PATTERN = r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}"
_SENSITIVE_ID_MARKERS = ("password", "private", "credential", "secret", "token", "bearer", "signature", "x-amz")


def _is_platform_id(value: str, prefix: str) -> bool:
    if not isinstance(value, str):
        return False
    if any(marker in value.casefold() for marker in _SENSITIVE_ID_MARKERS):
        return False
    seeded_id_pattern = rf"{re.escape(prefix)}-[A-Za-z0-9][A-Za-z0-9_-]{{0,63}}"
    return re.fullmatch(rf"(?:{_UUID_PATTERN}|{seeded_id_pattern})", value) is not None


def _validate_edge_boundary(
    task_type: TaskType,
    resource_refs: dict[str, str],
    payload: dict[str, Any],
) -> None:
    if task_type not in EDGE_EXECUTOR_TASK_TYPES:
        return

    expected_refs = EDGE_EXECUTOR_RESOURCE_REFS[task_type]
    refs_are_valid = set(resource_refs) == expected_refs and all(
        _is_platform_id(value, _RESOURCE_REF_PREFIXES[key]) for key, value in resource_refs.items()
    )
    if payload or not refs_are_valid:
        raise ValueError("Edge task commands must use identifier-only resource_refs and an empty payload")


def _json_field(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


class TaskCommand(BaseModel):
    task_id: str
    task_type: TaskType
    resource_refs: dict[str, str] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    payload_version: int = 1

    @model_validator(mode="after")
    def validate_edge_command_is_identifier_only(self) -> "TaskCommand":
        _validate_edge_boundary(self.task_type, self.resource_refs, self.payload)
        if self.task_type in EDGE_EXECUTOR_TASK_TYPES and not _is_platform_id(self.task_id, "task"):
            raise ValueError("Edge task commands must use identifier-only resource_refs and an empty payload")
        return self

    def to_stream_fields(self) -> dict[str, str]:
        resource_refs = dict(self.resource_refs)
        payload = dict(self.payload)
        _validate_edge_boundary(self.task_type, resource_refs, payload)
        if self.task_type in EDGE_EXECUTOR_TASK_TYPES and not _is_platform_id(self.task_id, "task"):
            raise ValueError("Edge task commands must use identifier-only resource_refs and an empty payload")
        return {
            "task_id": self.task_id,
            "task_type": self.task_type.value,
            "resource_refs": _json_field(resource_refs),
            "payload": _json_field(payload),
            "payload_version": str(self.payload_version),
        }


class TaskProgressEvent(BaseModel):
    task_id: str
    status: TaskStatus
    progress: int = Field(ge=0, le=100)
    stage: str | None = None
    message: str | None = None
    error_code: str | None = None
    error_message: str | None = None
