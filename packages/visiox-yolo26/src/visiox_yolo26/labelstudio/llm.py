from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class NormalizedSftAnnotation:
    sample_id: str
    source_row_id: str
    messages: list[dict[str, str]]
    raw_payload: dict[str, Any]


def build_sft_import_task(
    *,
    dataset_id: str,
    sample_id: str,
    record: dict[str, Any],
) -> dict[str, Any]:
    messages = _canonical_messages(record)
    system = "\n\n".join(message["content"] for message in messages if message["role"] == "system")
    user = "\n\n".join(message["content"] for message in messages if message["role"] == "user")
    assistant = next(
        (message["content"] for message in reversed(messages) if message["role"] == "assistant"),
        "",
    )
    return {
        "data": {
            "system": system,
            "user": user,
            "assistant": assistant,
            "source_row_id": str(record.get("source_row_id") or sample_id),
            "visiox_dataset_id": dataset_id,
            "visiox_sample_id": sample_id,
        }
    }


def normalize_sft_export_task(payload: dict[str, Any]) -> NormalizedSftAnnotation:
    data = payload.get("data")
    if not isinstance(data, dict):
        raise ValueError("Label Studio SFT task data is missing")
    sample_id = _required_text(data.get("visiox_sample_id"), "sample ID")
    user = _required_text(data.get("user"), "user prompt")
    assistant = _assistant_result(payload)
    messages: list[dict[str, str]] = []
    system = data.get("system")
    if isinstance(system, str) and system.strip():
        messages.append({"role": "system", "content": system.strip()})
    messages.extend(
        [
            {"role": "user", "content": user},
            {"role": "assistant", "content": assistant},
        ]
    )
    return NormalizedSftAnnotation(
        sample_id=sample_id,
        source_row_id=str(data.get("source_row_id") or sample_id),
        messages=messages,
        raw_payload=payload,
    )


def _canonical_messages(record: dict[str, Any]) -> list[dict[str, str]]:
    messages = record.get("messages")
    if not isinstance(messages, list):
        raise ValueError("SFT record messages are missing")
    canonical = []
    for message in messages:
        if not isinstance(message, dict):
            raise ValueError("SFT message must be an object")
        role = _required_text(message.get("role"), "message role")
        content = _required_text(message.get("content"), "message content")
        canonical.append({"role": role, "content": content})
    return canonical


def _assistant_result(payload: dict[str, Any]) -> str:
    annotations = payload.get("annotations")
    if isinstance(annotations, list):
        for annotation in reversed(annotations):
            if not isinstance(annotation, dict) or not isinstance(annotation.get("result"), list):
                continue
            for result in reversed(annotation["result"]):
                if not isinstance(result, dict) or result.get("from_name") != "assistant":
                    continue
                value = result.get("value")
                texts = value.get("text") if isinstance(value, dict) else None
                if isinstance(texts, list):
                    for text in texts:
                        if isinstance(text, str) and text.strip():
                            return text.strip()
    raise ValueError("assistant response must be non-empty")


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be non-empty")
    return value.strip()
