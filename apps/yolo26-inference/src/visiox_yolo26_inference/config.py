from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator

YOLO26_TASKS = ("detect", "segment", "semantic", "pose", "obb", "classify")
SUPPORTED_MODEL_FORMATS = ("onnx", "torchscript", "pt")


class InferenceConfigError(ValueError):
    pass


class InferenceConfig(BaseModel):
    task: str = "detect"
    model_path: Path | None = None
    model_format: str = "onnx"
    device: str = "cpu"
    confidence: float = Field(default=0.25, ge=0.0, le=1.0)
    iou: float = Field(default=0.45, ge=0.0, le=1.0)
    class_names: list[str] = Field(default_factory=list)
    input: dict[str, Any] = Field(default_factory=dict)

    @field_validator("task")
    @classmethod
    def _validate_task(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in YOLO26_TASKS:
            raise ValueError(f"unsupported YOLO26 task: {value}")
        return normalized

    @field_validator("model_format")
    @classmethod
    def _validate_model_format(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in SUPPORTED_MODEL_FORMATS:
            raise ValueError(f"unsupported model format: {value}")
        return normalized


def load_config(path: str | Path | None = None) -> InferenceConfig:
    resolved_path = path or os.getenv("VISIOX_INFERENCE_CONFIG")
    if resolved_path is None:
        return InferenceConfig()

    config_path = Path(resolved_path)
    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InferenceConfigError(f"failed to read inference config: {config_path}") from exc

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise InferenceConfigError(f"inference config must be valid JSON: {config_path}") from exc

    if not isinstance(data, dict):
        raise InferenceConfigError("inference config root must be an object")

    try:
        config = InferenceConfig.model_validate(data)
    except ValidationError as exc:
        raise InferenceConfigError(str(exc)) from exc
    validate_model_task_match(config)
    return config


def validate_model_task_match(config: InferenceConfig) -> None:
    if config.model_path is None:
        return

    filename = config.model_path.stem.lower()
    tokens = {token for token in re.split(r"[^a-z0-9]+", filename) if token}
    matched_tasks = [task for task in YOLO26_TASKS if task in tokens]
    if not matched_tasks:
        return
    if len(matched_tasks) > 1:
        raise InferenceConfigError(f"model task hint is ambiguous: {config.model_path.name}")

    matched_task = matched_tasks[0]
    if matched_task != config.task:
        raise InferenceConfigError(
            f"model task mismatch: filename suggests '{matched_task}', but config.task is '{config.task}': {config.model_path.name}"
        )
