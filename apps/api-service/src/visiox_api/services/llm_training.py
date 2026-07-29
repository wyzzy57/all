from __future__ import annotations

from typing import Any

from sqlalchemy import select

from visiox_db.models import Dataset, DatasetVersion
from sqlalchemy.orm import Session


class LlmTrainingConfigError(ValueError):
    pass


MANAGED_CONFIG_FIELDS = {
    "dataset",
    "dataset_dir",
    "logging_dir",
    "model_name_or_path",
    "output_dir",
    "report_to",
    "resume_from_checkpoint",
}
MODEL_SOURCES = {"huggingface", "modelscope"}


def validate_llamafactory_config(payload: dict[str, Any] | None) -> dict[str, Any]:
    config = dict(payload or {})
    managed = sorted(MANAGED_CONFIG_FIELDS.intersection(config))
    if managed:
        raise LlmTrainingConfigError(
            f"managed LLaMA-Factory fields cannot be overridden: {', '.join(managed)}"
        )

    source = config.get("model_source")
    if source is not None and source not in MODEL_SOURCES:
        raise LlmTrainingConfigError("model_source must be huggingface or modelscope")
    model_id = config.get("model_id")
    if model_id is not None:
        _repository_id("model_id", model_id)
    revision = config.get("model_revision")
    if revision is not None:
        _safe_text("model_revision", revision, max_length=160)

    _positive_number(config, "learning_rate")
    _positive_number(config, "num_train_epochs")
    _positive_integer(config, "cutoff_len")
    _positive_integer(config, "per_device_train_batch_size")
    _positive_integer(config, "gradient_accumulation_steps")
    _ratio(config, "val_size", maximum=0.5)
    _ratio(config, "warmup_ratio", maximum=0.5)
    _positive_integer(config, "lora_rank")
    _positive_integer(config, "lora_alpha")
    _ratio(config, "lora_dropout", maximum=1.0)
    _positive_integer(config, "logging_steps")
    _positive_integer(config, "eval_steps")
    _positive_integer(config, "save_steps")

    if config.get("stage", "sft") != "sft":
        raise LlmTrainingConfigError("the first LLM release supports SFT only")
    if config.get("finetuning_type", "lora") != "lora":
        raise LlmTrainingConfigError("the first LLM release supports LoRA or QLoRA only")
    if config.get("quantization_bit") not in {None, 4}:
        raise LlmTrainingConfigError("quantization_bit must be 4 for QLoRA")
    return config


def validate_llm_dataset(session: Session, dataset_id: str) -> Dataset:
    dataset = session.get(Dataset, dataset_id)
    if dataset is None:
        raise LlmTrainingConfigError("LLM dataset not found")
    if dataset.task != "llm":
        raise LlmTrainingConfigError("dataset task must be llm")
    if dataset.status not in {"validated", "ready"}:
        raise LlmTrainingConfigError("LLM dataset must be validated")
    if dataset.sample_count <= 0 or dataset.annotation_count <= 0:
        raise LlmTrainingConfigError("LLM dataset has no valid training samples")
    return dataset


def resolve_llm_dataset_version(
    session: Session,
    dataset_id: str,
    version_id: str | None = None,
) -> DatasetVersion:
    if version_id:
        version = session.get(DatasetVersion, version_id)
        if version is None or version.dataset_id != dataset_id:
            raise LlmTrainingConfigError("published LLM dataset version not found")
    else:
        version = session.scalar(
            select(DatasetVersion)
            .where(DatasetVersion.dataset_id == dataset_id, DatasetVersion.status == "published")
            .order_by(DatasetVersion.version.desc())
            .limit(1)
        )
    if version is None or version.status != "published":
        raise LlmTrainingConfigError("LLM training requires a published dataset version")
    return version


def validate_llm_environment(payload: dict[str, Any] | None) -> dict[str, Any]:
    environment = dict(payload or {})
    allowed = {"device", "workers", "resource_pool_id", "node_id"}
    unknown = sorted(set(environment) - allowed)
    if unknown:
        raise LlmTrainingConfigError(f"unknown LLM training environment: {', '.join(unknown)}")
    if "workers" in environment:
        workers = environment["workers"]
        if isinstance(workers, bool) or not isinstance(workers, int) or not 0 <= workers <= 64:
            raise LlmTrainingConfigError("workers must be between 0 and 64")
    for key in ("device", "resource_pool_id", "node_id"):
        if key in environment:
            environment[key] = _safe_text(key, environment[key], max_length=128)
    return environment


def _repository_id(key: str, value: Any) -> str:
    text = _safe_text(key, value, max_length=240)
    parts = text.split("/")
    if len(parts) != 2 or not all(part and all(char.isalnum() or char in "-_." for char in part) for part in parts):
        raise LlmTrainingConfigError(f"{key} must be a namespace/repository identifier")
    return text


def _safe_text(key: str, value: Any, *, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LlmTrainingConfigError(f"{key} must be a non-empty string")
    text = value.strip()
    if len(text) > max_length or any(token in text for token in ("\n", "\r", ";", "&", "|")):
        raise LlmTrainingConfigError(f"{key} contains unsupported characters")
    return text


def _positive_number(config: dict[str, Any], key: str) -> None:
    if key not in config:
        return
    value = config[key]
    if isinstance(value, bool) or not isinstance(value, int | float) or value <= 0:
        raise LlmTrainingConfigError(f"{key} must be greater than 0")


def _positive_integer(config: dict[str, Any], key: str) -> None:
    if key not in config:
        return
    value = config[key]
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise LlmTrainingConfigError(f"{key} must be a positive integer")


def _ratio(config: dict[str, Any], key: str, *, maximum: float) -> None:
    if key not in config:
        return
    value = config[key]
    if isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= maximum:
        raise LlmTrainingConfigError(f"{key} must be between 0 and {maximum}")
