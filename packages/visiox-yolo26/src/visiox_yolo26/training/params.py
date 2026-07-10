from __future__ import annotations

from typing import Any


class TrainingParamsError(ValueError):
    pass


TrainingParamValue = bool | int | float | str | list[int]
TrainingParams = dict[str, TrainingParamValue]
TrainingEnvironment = dict[str, int | str]

_INT_FIELDS = {
    "close_mosaic": (0, 10000),
    "epochs": (1, 10000),
    "line_width": (1, 1000),
    "mask_ratio": (1, 1024),
    "max_det": (1, 100000),
    "nbs": (1, 10000),
    "opset": (1, 1000),
    "patience": (0, 10000),
    "save_period": (-1, 10000),
    "seed": (0, 2_147_483_647),
    "vid_stride": (1, 10000),
    "workers": (0, 256),
}
_FLOAT_FIELDS = {
    "angle": (0.0, 1000.0, True),
    "bgr": (0.0, 1.0, True),
    "box": (0.0, 1000.0, True),
    "cls": (0.0, 1000.0, True),
    "cls_pw": (0.0, 1000.0, True),
    "copy_paste": (0.0, 1.0, True),
    "cutmix": (0.0, 1.0, True),
    "degrees": (0.0, 180.0, True),
    "dfl": (0.0, 1000.0, True),
    "dis": (0.0, 1000.0, True),
    "dropout": (0.0, 1.0, True),
    "erasing": (0.0, 1.0, True),
    "fliplr": (0.0, 1.0, True),
    "flipud": (0.0, 1.0, True),
    "fraction": (0.0, 1.0, False),
    "hsv_h": (0.0, 1.0, True),
    "hsv_s": (0.0, 1.0, True),
    "hsv_v": (0.0, 1.0, True),
    "iou": (0.0, 1.0, True),
    "kobj": (0.0, 1000.0, True),
    "lr0": (0.0, 1.0, False),
    "lrf": (0.0, 1.0, True),
    "mixup": (0.0, 1.0, True),
    "momentum": (0.0, 1.0, True),
    "mosaic": (0.0, 1.0, True),
    "multi_scale": (0.0, 1.0, True),
    "perspective": (0.0, 0.001, True),
    "pose": (0.0, 1000.0, True),
    "rle": (0.0, 1000.0, True),
    "scale": (0.0, 1.0, True),
    "shear": (-180.0, 180.0, True),
    "time": (0.0, 100000.0, False),
    "translate": (0.0, 1.0, True),
    "warmup_bias_lr": (0.0, 1.0, True),
    "warmup_epochs": (0.0, 10000.0, True),
    "warmup_momentum": (0.0, 1.0, True),
    "weight_decay": (0.0, 1.0, True),
    "workspace": (0.0, 100000.0, False),
}
_BOOL_FIELDS = {
    "agnostic_nms",
    "amp",
    "augment",
    "cos_lr",
    "deterministic",
    "dnn",
    "dynamic",
    "end2end",
    "keras",
    "nms",
    "overlap_mask",
    "plots",
    "profile",
    "quantize",
    "rect",
    "retina_masks",
    "resume",
    "save",
    "save_conf",
    "save_crop",
    "save_frames",
    "save_json",
    "save_txt",
    "show",
    "show_boxes",
    "show_conf",
    "show_labels",
    "simplify",
    "single_cls",
    "stream_buffer",
    "val",
    "verbose",
    "visualize",
}
_STRING_FIELDS = {
    "auto_augment": {"randaugment", "autoaugment", "augmix"},
    "cache": {"ram", "disk"},
    "cfg": None,
    "compile": {"default", "reduce-overhead", "max-autotune-no-cudagraphs"},
    "copy_paste_mode": {"flip", "mixup"},
    "distill_model": None,
    "format": {
        "torchscript",
        "onnx",
        "openvino",
        "engine",
        "coreml",
        "saved_model",
        "pb",
        "tflite",
        "edgetpu",
        "tfjs",
        "paddle",
        "mnn",
        "ncnn",
        "imx",
        "rknn",
    },
    "optimizer": {"SGD", "MuSGD", "Adam", "Adamax", "AdamW", "NAdam", "RAdam", "RMSProp", "auto"},
    "pretrained": None,
    "source": None,
    "split": {"train", "val", "test"},
    "tracker": None,
}
_LIST_INT_FIELDS = {"classes", "embed", "freeze", "imgsz"}
_BOOL_OR_STRING_FIELDS = {"cache", "compile", "end2end", "pretrained", "quantize"}
_SPECIAL_FIELDS = {"batch", "device"}
_PARAM_FIELDS = (
    set(_INT_FIELDS)
    | set(_FLOAT_FIELDS)
    | _BOOL_FIELDS
    | set(_STRING_FIELDS)
    | _LIST_INT_FIELDS
    | _SPECIAL_FIELDS
)
_ENVIRONMENT_FIELDS = {"device", "workers"}
_PARAM_ALIASES = {
    "warmup_steps": "warmup_epochs",
}


def validate_training_params(payload: dict[str, Any] | None) -> TrainingParams:
    payload = _normalize_param_aliases(payload or {})
    _reject_unknown(payload, _PARAM_FIELDS, "training params")
    validated: TrainingParams = {}
    for key, value in payload.items():
        if key in _INT_FIELDS:
            minimum, maximum = _INT_FIELDS[key]
            validated[key] = _int_range(key, value, minimum=minimum, maximum=maximum)
        elif key in _FLOAT_FIELDS:
            minimum, maximum, inclusive_min = _FLOAT_FIELDS[key]
            validated[key] = _float_range(key, value, minimum=minimum, maximum=maximum, inclusive_min=inclusive_min)
        elif key in _BOOL_FIELDS:
            validated[key] = _bool(key, value)
        elif key in _BOOL_OR_STRING_FIELDS and isinstance(value, bool):
            validated[key] = value
        elif key in _STRING_FIELDS:
            validated[key] = _choice_or_string(key, value, _STRING_FIELDS[key])
        elif key in _LIST_INT_FIELDS:
            validated[key] = _int_or_int_list(key, value, minimum=32 if key == "imgsz" else 0)
        elif key == "batch":
            validated[key] = _batch(value)
        elif key == "device":
            validated[key] = _device(value)
    return validated


def _normalize_param_aliases(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    for alias, target in _PARAM_ALIASES.items():
        if alias not in normalized:
            continue
        if target in normalized:
            raise TrainingParamsError(f"{alias} cannot be used together with {target}")
        normalized[target] = normalized.pop(alias)
    return normalized


def validate_training_environment(payload: dict[str, Any] | None) -> TrainingEnvironment:
    payload = payload or {}
    _reject_unknown(payload, _ENVIRONMENT_FIELDS, "training environment")
    validated: TrainingEnvironment = {}
    for key, value in payload.items():
        if key == "workers":
            validated[key] = _int_range(key, value, minimum=0, maximum=256)
        elif key == "device":
            validated[key] = _device(value)
    return validated


def merge_training_params(template: dict[str, Any] | None, override: dict[str, Any] | None) -> TrainingParams:
    base = validate_training_params(template)
    extra = validate_training_params(override)
    return {**base, **extra}


def _reject_unknown(payload: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise TrainingParamsError(f"unknown {label}: {', '.join(unknown)}")


def _int_range(key: str, value: Any, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TrainingParamsError(f"{key} must be an integer")
    if value < minimum or value > maximum:
        raise TrainingParamsError(f"{key} must be between {minimum} and {maximum}")
    return value


def _float_range(
    key: str,
    value: Any,
    *,
    minimum: float,
    maximum: float,
    inclusive_min: bool = True,
) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TrainingParamsError(f"{key} must be a number")
    number = float(value)
    if (number < minimum if inclusive_min else number <= minimum) or number > maximum:
        comparator = "between" if inclusive_min else "greater than"
        raise TrainingParamsError(f"{key} must be {comparator} {minimum} and at most {maximum}")
    return number


def _batch(value: Any) -> int | float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TrainingParamsError("batch must be a number")
    if value == -1:
        return -1
    number = float(value)
    if number <= 0 or number > 1024:
        raise TrainingParamsError("batch must be -1 or between 0 and 1024")
    return int(value) if isinstance(value, int) else number


def _bool(key: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise TrainingParamsError(f"{key} must be a boolean")
    return value


def _int_or_int_list(key: str, value: Any, *, minimum: int) -> int | list[int]:
    if isinstance(value, bool):
        raise TrainingParamsError(f"{key} must be an integer or list of integers")
    if isinstance(value, int):
        if value < minimum:
            raise TrainingParamsError(f"{key} must be at least {minimum}")
        return value
    if isinstance(value, list) and all(isinstance(item, int) and not isinstance(item, bool) and item >= minimum for item in value):
        return value
    raise TrainingParamsError(f"{key} must be an integer or list of integers")


def _choice_or_string(key: str, value: Any, choices: set[str] | None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TrainingParamsError(f"{key} must be a non-empty string")
    text = _safe_string(key, value, max_length=260)
    if choices is not None and text not in choices:
        raise TrainingParamsError(f"{key} must be one of: {', '.join(sorted(choices))}")
    return text


def _device(value: Any) -> str:
    if isinstance(value, int) and value >= 0:
        return str(value)
    if not isinstance(value, str) or not value.strip():
        raise TrainingParamsError("device must be a non-empty string or non-negative integer")
    return _safe_string("device", value, max_length=80)


def _safe_string(key: str, value: str, *, max_length: int) -> str:
    text = value.strip()
    if any(token in text for token in (";", "&", "|", "\n", "\r")):
        raise TrainingParamsError(f"{key} contains unsupported characters")
    if len(text) > max_length:
        raise TrainingParamsError(f"{key} is too long")
    return text
