import json
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class TaskStatus(StrEnum):
    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


class TaskType(StrEnum):
    DOWNLOAD_BASE_MODEL = "DOWNLOAD_BASE_MODEL"
    ANALYZE_DATASET = "ANALYZE_DATASET"
    SYNC_LABEL_STUDIO_DATA = "SYNC_LABEL_STUDIO_DATA"
    IMPORT_LABEL_STUDIO_ANNOTATION = "IMPORT_LABEL_STUDIO_ANNOTATION"
    VALIDATE_DATASET_FORMAT = "VALIDATE_DATASET_FORMAT"
    TRAIN_MODEL = "TRAIN_MODEL"
    CONVERT_MODEL = "CONVERT_MODEL"
    BUILD_EDGE_APP_PACKAGE = "BUILD_EDGE_APP_PACKAGE"
    DEPLOY_APP = "DEPLOY_APP"
    ROLLBACK_APP = "ROLLBACK_APP"
    STOP_APP = "STOP_APP"
    CAPTURE_CAMERA_SAMPLE = "CAPTURE_CAMERA_SAMPLE"
    TEST_CAMERA_CONNECTION = "TEST_CAMERA_CONNECTION"


def _json_field(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


class TaskCommand(BaseModel):
    task_id: str
    task_type: TaskType
    resource_refs: dict[str, str] = Field(default_factory=dict)
    payload: dict[str, Any] = Field(default_factory=dict)
    payload_version: int = 1

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
