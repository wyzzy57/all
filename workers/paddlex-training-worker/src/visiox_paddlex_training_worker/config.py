from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from visiox_training.contracts import LaunchSpec
from visiox_training.runtime import load_runtime_inputs


ADAPTER_KEY = "paddlex.object_detection.v1"
ADAPTER_VERSION = "1.0.0"
DATASET_DIR = "/workspace/dataset"
OUTPUT_DIR = "/workspace/output"
CHECKPOINT_PATH = "/workspace/checkpoint/last.pt"
PADDLEX_MAIN_PATH = "/opt/paddlex-runtime/paddlex_main.py"
RESUME_PREFIX = "/workspace/output/.resume/last"
RESUME_WEIGHTS_PATH = f"{RESUME_PREFIX}.pdparams"
BEST_WEIGHTS_PATH = "/workspace/output/best_model/best_model.pdparams"
BEST_INFERENCE_DIR = "/workspace/output/best_model/inference"
MODEL_CONFIG_PATHS = {
    "PP-YOLOE-S": (
        "paddlex/configs/modules/object_detection/PP-YOLOE_plus-S.yaml"
    ),
    "PP-YOLOE_plus-S": (
        "paddlex/configs/modules/object_detection/PP-YOLOE_plus-S.yaml"
    ),
    "RT-DETR-L": "paddlex/configs/modules/object_detection/RT-DETR-L.yaml",
}

_ALIASES = {
    "batch": "batch_size",
    "lr": "learning_rate",
    "imgsz": "image_size",
    "AMP": "amp",
    "device": "devices",
}
_PARAMETER_FIELDS = {
    "epochs",
    "batch_size",
    "learning_rate",
    "image_size",
    "workers",
    "amp",
    "devices",
    "resume",
    "output",
}
_MANAGED_OVERRIDES = {
    "Global.mode",
    "Global.dataset_dir",
    "Global.output",
    "Global.device",
    "Train.resume_path",
    "Train.eval_interval",
    "Train.amp",
}


@dataclass(frozen=True)
class PaddleXTrainingConfig:
    config_path: str
    epochs: int
    batch_size: int
    learning_rate: float
    image_size: int
    workers: int
    amp: bool
    devices: tuple[int, ...]
    resume: bool
    output: str = OUTPUT_DIR
    dataset_dir: str = DATASET_DIR


def build_training_config(payload: dict[str, object]) -> PaddleXTrainingConfig:
    spec = LaunchSpec.model_validate(payload)
    if spec.adapter_key != ADAPTER_KEY or spec.adapter_version != ADAPTER_VERSION:
        raise ValueError("launch spec adapter does not match PaddleX")
    if tuple(spec.argv) != ("/usr/local/bin/visiox-train",):
        raise ValueError("PaddleX runtime only accepts the fixed entrypoint")

    inputs = load_runtime_inputs(spec)
    artifacts = {item["role"]: item["path"] for item in inputs["artifacts"]}
    if artifacts.get("dataset") != DATASET_DIR:
        raise ValueError("PaddleX dataset artifact is unavailable")
    if inputs["dataset"].get("format") != "coco":
        raise ValueError("PaddleX detection dataset format must be coco")

    model = inputs["model"]
    model_id = model.get("runtime_id") or model.get("id")
    if not isinstance(model_id, str) or model_id not in MODEL_CONFIG_PATHS:
        raise ValueError("unsupported PaddleX model")

    parameters = _normalize_parameters(inputs["parameters"])
    resume = _boolean(parameters, "resume", False)
    if resume and artifacts.get("checkpoint") != CHECKPOINT_PATH:
        raise ValueError("resume checkpoint artifact is unavailable")
    output = parameters.get("output", OUTPUT_DIR)
    if output != OUTPUT_DIR:
        raise ValueError("output must use the managed workspace path")

    return PaddleXTrainingConfig(
        config_path=MODEL_CONFIG_PATHS[model_id],
        epochs=_positive_integer(parameters, "epochs", 100),
        batch_size=_positive_integer(parameters, "batch_size", 8),
        learning_rate=_positive_number(parameters, "learning_rate", 0.001),
        image_size=_positive_integer(parameters, "image_size", 640),
        workers=_non_negative_integer(parameters, "workers", 4),
        amp=_boolean(parameters, "amp", True),
        devices=_device_list(parameters.get("devices", [0])),
        resume=resume,
    )


def build_training_command(config: PaddleXTrainingConfig) -> tuple[str, ...]:
    overrides = [
        "Global.mode=train",
        f"Global.dataset_dir={config.dataset_dir}",
        f"Global.output={config.output}",
        "Global.device=gpu:" + ",".join(str(item) for item in config.devices),
        f"Train.epochs_iters={config.epochs}",
        f"Train.batch_size={config.batch_size}",
        f"Train.learning_rate={config.learning_rate}",
        f"Train.amp={'O1' if config.amp else 'OFF'}",
    ]
    if config.resume:
        overrides.append(f"Train.resume_path={RESUME_WEIGHTS_PATH}")
    overrides.append("Train.eval_interval=1")
    command = ["python", PADDLEX_MAIN_PATH, "-c", config.config_path]
    for override in overrides:
        command.extend(("-o", override))
    if any(any(char in item for char in "\x00\r\n") for item in command):
        raise ValueError("translated PaddleX command contains control characters")
    return tuple(command)


def build_export_command(config: PaddleXTrainingConfig) -> tuple[str, ...]:
    overrides = (
        "Global.mode=export",
        f"Global.output={BEST_INFERENCE_DIR}",
        "Global.device=gpu:" + ",".join(str(item) for item in config.devices),
        f"Export.weight_path={BEST_WEIGHTS_PATH}",
    )
    command = ["python", PADDLEX_MAIN_PATH, "-c", config.config_path]
    for override in overrides:
        command.extend(("-o", override))
    return tuple(command)


def _normalize_parameters(raw: dict[str, Any]) -> dict[str, Any]:
    parameters: dict[str, Any] = {}
    for key, value in raw.items():
        normalized = _ALIASES.get(key, key)
        if normalized == "overrides":
            if isinstance(value, dict) and _MANAGED_OVERRIDES.intersection(value):
                raise ValueError("PaddleX managed fields cannot be overridden")
            raise ValueError("unsupported PaddleX parameter: overrides")
        if normalized not in _PARAMETER_FIELDS:
            raise ValueError(f"unsupported PaddleX parameter: {key}")
        if normalized in parameters:
            raise ValueError(f"duplicate PaddleX parameter: {normalized}")
        parameters[normalized] = value
    return parameters


def _positive_integer(values: dict[str, Any], key: str, default: int) -> int:
    value = values.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{key} must be a positive integer")
    return value


def _non_negative_integer(values: dict[str, Any], key: str, default: int) -> int:
    value = values.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{key} must be a non-negative integer")
    return value


def _positive_number(values: dict[str, Any], key: str, default: float) -> float:
    value = values.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise ValueError(f"{key} must be a positive number")
    return float(value)


def _boolean(values: dict[str, Any], key: str, default: bool) -> bool:
    value = values.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _device_list(value: Any) -> tuple[int, ...]:
    if (
        not isinstance(value, list | tuple)
        or not value
        or any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError("devices must be a non-empty list of unique GPU indexes")
    return tuple(value)
