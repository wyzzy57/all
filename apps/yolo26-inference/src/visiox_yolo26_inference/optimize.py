from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from pathlib import Path
import shutil
import tempfile
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, model_validator


class OptimizationRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: Path
    source_format: Literal["pt", "onnx"]
    target_format: Literal["onnx", "engine"]
    precision: Literal["fp32", "fp16", "int8"]
    input_shape: tuple[int, int, int, int]
    output: Path
    calibration: Path | None = None

    @model_validator(mode="after")
    def _validate_conversion(self) -> "OptimizationRequest":
        batch, channels, height, width = self.input_shape
        if (
            batch != 1
            or channels != 3
            or not 32 <= height <= 4096
            or not 32 <= width <= 4096
            or height % 32
            or width % 32
        ):
            raise ValueError("input shape is invalid")
        if self.source_format == "onnx" and self.target_format == "onnx":
            raise ValueError("source and target formats must differ")
        if self.precision == "int8" and self.calibration is None:
            raise ValueError("INT8 optimization requires a calibration dataset")
        if self.precision == "int8" and self.target_format != "engine":
            raise ValueError("INT8 optimization requires TensorRT engine output")
        return self


def optimize_model(
    request: OptimizationRequest,
    *,
    yolo_factory: Callable[..., Any] | None = None,
) -> Path:
    if not request.source.is_file():
        raise ValueError("source model is unavailable")
    if request.calibration is not None and not request.calibration.exists():
        raise ValueError("calibration dataset is unavailable")
    if yolo_factory is None:
        from ultralytics import YOLO

        yolo_factory = YOLO

    request.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="visiox-optimize-") as temporary_name:
        temporary = Path(temporary_name)
        writable_source = temporary / f"model.{request.source_format}"
        shutil.copyfile(request.source, writable_source)
        model = yolo_factory(str(writable_source))
        export_options: dict[str, Any] = {
            "format": request.target_format,
            "imgsz": (request.input_shape[2], request.input_shape[3]),
            "half": request.precision == "fp16",
            "int8": request.precision == "int8",
            "device": 0,
            "batch": request.input_shape[0],
        }
        if request.precision == "int8":
            export_options["data"] = str(request.calibration)
        produced_value = model.export(**export_options)
        produced = Path(str(produced_value))
        if not produced.is_file():
            raise ValueError("optimized model artifact was not produced")
        staged_output = request.output.with_name(request.output.name + ".tmp")
        try:
            shutil.copyfile(produced, staged_output)
            staged_output.chmod(0o400)
            staged_output.replace(request.output)
        finally:
            staged_output.unlink(missing_ok=True)
    return request.output


def _input_shape(value: str) -> tuple[int, int, int, int]:
    try:
        parts = tuple(int(part) for part in value.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("input shape must contain four integers") from None
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("input shape must contain four integers")
    return parts  # type: ignore[return-value]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--source-format", choices=("pt", "onnx"), required=True)
    parser.add_argument("--target-format", choices=("onnx", "engine"), required=True)
    parser.add_argument("--precision", choices=("fp32", "fp16", "int8"), required=True)
    parser.add_argument("--input-shape", type=_input_shape, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--calibration", type=Path)
    arguments = parser.parse_args(argv)
    request = OptimizationRequest(
        source=arguments.source,
        source_format=arguments.source_format,
        target_format=arguments.target_format,
        precision=arguments.precision,
        input_shape=arguments.input_shape,
        output=arguments.output,
        calibration=arguments.calibration,
    )
    optimize_model(request)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
