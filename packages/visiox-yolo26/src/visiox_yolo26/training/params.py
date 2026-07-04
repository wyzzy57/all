from __future__ import annotations

from typing import Any


class TrainingParamsError(ValueError):
    pass


TrainingParams = dict[str, int | float | str]
TrainingEnvironment = dict[str, int | str]

_PARAM_FIELDS = {
    "epochs",
    "batch",
    "imgsz",
    "lr0",
    "patience",
    "workers",
    "device",
    "seed",
}
_ENVIRONMENT_FIELDS = {"device", "workers"}


def validate_training_params(payload: dict[str, Any] | None) -> TrainingParams:
    payload = payload or {}
    _reject_unknown(payload, _PARAM_FIELDS, "training params")
    validated: TrainingParams = {}
    for key, value in payload.items():
        if key in {"epochs", "batch"}:
            validated[key] = _int_range(key, value, minimum=1, maximum=10000 if key == "epochs" else 1024)
        elif key == "imgsz":
            validated[key] = _int_range(key, value, minimum=32, maximum=4096)
        elif key == "lr0":
            validated[key] = _float_range(key, value, minimum=0.0, maximum=1.0, inclusive_min=False)
        elif key == "patience":
            validated[key] = _int_range(key, value, minimum=0, maximum=10000)
        elif key == "workers":
            validated[key] = _int_range(key, value, minimum=0, maximum=256)
        elif key == "seed":
            validated[key] = _int_range(key, value, minimum=0, maximum=2_147_483_647)
        elif key == "device":
            validated[key] = _device(value)
    return validated


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


def _device(value: Any) -> str:
    if isinstance(value, int) and value >= 0:
        return str(value)
    if not isinstance(value, str) or not value.strip():
        raise TrainingParamsError("device must be a non-empty string or non-negative integer")
    device = value.strip()
    if any(token in device for token in (";", "&", "|", "\n", "\r")):
        raise TrainingParamsError("device contains unsupported characters")
    if len(device) > 40:
        raise TrainingParamsError("device is too long")
    return device
