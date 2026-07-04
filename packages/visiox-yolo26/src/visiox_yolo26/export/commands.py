from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


SUPPORTED_EXPORT_FORMATS = frozenset({"onnx", "torchscript"})


@dataclass(frozen=True)
class ExportCommand:
    argv: list[str]


class ExportParamsError(ValueError):
    pass


def build_export_command(
    model_path: Path,
    format: str,
    output_dir: Path,
    imgsz: int | None = None,
    half: bool = False,
    device: str | None = None,
) -> ExportCommand:
    export_format = format.lower().strip()
    if export_format not in SUPPORTED_EXPORT_FORMATS:
        raise ExportParamsError(f"unsupported export format: {format}")
    if imgsz is not None and (isinstance(imgsz, bool) or imgsz <= 0):
        raise ExportParamsError("imgsz must be a positive integer")
    if not isinstance(half, bool):
        raise ExportParamsError("half must be a boolean")

    argv = [
        "yolo",
        "export",
        f"model={model_path}",
        f"format={export_format}",
        f"project={output_dir}",
    ]
    if imgsz is not None:
        argv.append(f"imgsz={imgsz}")
    if half:
        argv.append("half=True")
    if device is not None:
        normalized_device = device.strip()
        if not normalized_device:
            raise ExportParamsError("device must not be empty")
        argv.append(f"device={normalized_device}")
    return ExportCommand(argv=argv)
