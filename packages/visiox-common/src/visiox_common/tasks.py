import json
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
        if self.task_type in EDGE_EXECUTOR_TASK_TYPES:
            if self.payload or any(not key.endswith("_id") for key in self.resource_refs):
                raise ValueError("Edge task commands must use identifier-only resource_refs and an empty payload")
        return self

    def to_stream_fields(self) -> dict[str, str]:
        return {
            "task_id": self.task_id,
            "task_type": self.task_type.value,
            "resource_refs": _json_field(self.resource_refs),
            "payload": _json_field(self.payload),
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
