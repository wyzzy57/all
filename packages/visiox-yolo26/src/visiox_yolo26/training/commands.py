from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from visiox_yolo26.training.params import TrainingParams


@dataclass(frozen=True)
class YoloTrainCommand:
    argv: list[str]


def build_train_command(
    base_model_path: Path,
    data_yaml_path: Path,
    params: TrainingParams,
    project_dir: Path,
    run_name: str,
) -> YoloTrainCommand:
    argv = [
        "yolo",
        "train",
        f"model={base_model_path}",
        f"data={data_yaml_path}",
        f"project={project_dir}",
        f"name={run_name}",
        "exist_ok=True",
    ]
    for key, value in sorted(params.items()):
        argv.append(f"{key}={_format_arg_value(value)}")
    return YoloTrainCommand(argv=argv)


def _format_arg_value(value: object) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, list):
        return "[" + ",".join(str(item) for item in value) + "]"
    return str(value)
