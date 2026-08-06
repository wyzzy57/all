from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator


class InferenceConfigError(ValueError):
    pass


class InferenceConfig(BaseModel):
    production: bool = False
    task: Literal["detect"] = "detect"
    model_dir: Path = Path("/models")
    model_format: Literal["paddle_inference_bundle"] = "paddle_inference_bundle"
    device: str = "cpu"
    backend: Literal["paddle_inference", "paddlex_hpi_tensorrt"] = "paddle_inference"
    precision: Literal["fp32", "fp16", "int8"] = "fp32"
    input_size: tuple[int, int] = (640, 640)
    optimization: Literal["auto", "paddle_inference", "paddlex_hpi_tensorrt"] = "auto"
    confidence: float = Field(default=0.25, ge=0, le=1)
    class_names: list[str] = Field(default_factory=list)

    @field_validator("device")
    @classmethod
    def validate_device(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized == "cpu" or normalized.startswith("gpu:"):
            return normalized
        raise ValueError("device must be cpu or gpu:<index>")

    @field_validator("input_size")
    @classmethod
    def validate_input_size(cls, value: tuple[int, int]) -> tuple[int, int]:
        if any(item < 32 or item > 4096 or item % 32 for item in value):
            raise ValueError(
                "input_size values must be multiples of 32 between 32 and 4096"
            )
        return value

    @model_validator(mode="after")
    def validate_runtime(self) -> "InferenceConfig":
        if self.optimization != "auto":
            self.backend = self.optimization
        if self.backend == "paddlex_hpi_tensorrt" and not self.device.startswith(
            "gpu:"
        ):
            raise ValueError("PaddleX HPI with TensorRT requires a GPU device")
        if self.backend == "paddlex_hpi_tensorrt" and self.precision not in {
            "fp16",
            "fp32",
        }:
            raise ValueError(
                "PaddleX HPI with TensorRT supports fp16 or fp32 precision"
            )
        if self.production:
            if not self.model_dir.is_absolute():
                raise ValueError(
                    "production inference model directory must be absolute"
                )
            if self.model_dir.is_symlink() or not self.model_dir.is_dir():
                raise ValueError("production inference model directory is unavailable")
            self.model_dir = self.model_dir.resolve(strict=True)
        return self


def load_config(path: str | Path | None = None) -> InferenceConfig:
    resolved = path or os.getenv("VISIOX_INFERENCE_CONFIG")
    if resolved is None:
        return InferenceConfig()
    config_path = Path(resolved)
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        return InferenceConfig.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise InferenceConfigError(
            f"invalid PaddleX inference config: {config_path}"
        ) from exc
