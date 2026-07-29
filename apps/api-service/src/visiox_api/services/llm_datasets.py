from __future__ import annotations

from dataclasses import dataclass
import csv
import hashlib
import json
from pathlib import Path
import re
from typing import Any


SUPPORTED_LLM_DATASET_FORMATS = {"alpaca", "sharegpt", "openai_messages"}
ALLOWED_MESSAGE_ROLES = {"system", "user", "assistant", "tool"}
_TOKEN_PATTERN = re.compile(r"[\u3400-\u9fff]|[A-Za-z0-9_]+|[^\s]")


class LlmDatasetError(ValueError):
    pass


@dataclass(frozen=True)
class LlmDatasetIssue:
    index: int
    code: str
    message: str


@dataclass(frozen=True)
class LlmDatasetValidation:
    format: str
    records: list[dict[str, Any]]
    issues: list[LlmDatasetIssue]
    schema_config: dict[str, Any]
    manifest_checksum: str

    @property
    def total_count(self) -> int:
        return len(self.records) + len(self.issues)


def validate_llm_dataset_file(path: Path, *, requested_format: str = "auto") -> LlmDatasetValidation:
    raw_records = _load_records(path)
    if not raw_records:
        raise LlmDatasetError("LLM dataset is empty")
    detected_format = _detect_format(raw_records, requested_format)
    records: list[dict[str, Any]] = []
    issues: list[LlmDatasetIssue] = []
    seen_records: set[str] = set()
    for index, record in enumerate(raw_records, start=1):
        try:
            normalized = _validate_record(record, detected_format, source_row_id=f"row-{index}")
            fingerprint = hashlib.sha256(canonical_jsonl([{"messages": normalized["messages"]}])).hexdigest()
            if fingerprint in seen_records:
                issues.append(
                    LlmDatasetIssue(index=index, code="DUPLICATE_RECORD", message="record duplicates an earlier sample")
                )
                continue
            seen_records.add(fingerprint)
            records.append(normalized)
        except LlmDatasetError as exc:
            message = str(exc)
            code = "INVALID_ROLE_ORDER" if message.startswith("invalid message role order") else "INVALID_RECORD"
            issues.append(LlmDatasetIssue(index=index, code=code, message=message))
    canonical = canonical_jsonl(records)
    schema_config = _schema_config(detected_format)
    return LlmDatasetValidation(
        format=detected_format,
        records=records,
        issues=issues,
        schema_config=schema_config,
        manifest_checksum=hashlib.sha256(canonical).hexdigest(),
    )


def canonical_jsonl(records: list[dict[str, Any]]) -> bytes:
    return b"".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        for record in records
    )


def preview_records(records: list[dict[str, Any]], dataset_format: str, *, limit: int) -> list[dict[str, Any]]:
    previews: list[dict[str, Any]] = []
    for index, record in enumerate(records[:limit], start=1):
        messages = _to_messages(record, dataset_format)
        content = "\n".join(str(item["content"]) for item in messages)
        previews.append(
            {
                "index": index,
                "messages": messages,
                "character_count": len(content),
                "token_estimate": len(_TOKEN_PATTERN.findall(content)),
            }
        )
    return previews


def _load_records(path: Path) -> list[Any]:
    suffix = path.suffix.lower()
    if suffix not in {".json", ".jsonl", ".csv"}:
        raise LlmDatasetError("LLM datasets must be JSON, JSONL, or CSV")
    try:
        if suffix == ".csv":
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                return [dict(row) for row in csv.DictReader(stream)]
        if suffix == ".jsonl":
            records = []
            for line_number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    raise LlmDatasetError(f"invalid JSONL at line {line_number}") from exc
            return records
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise LlmDatasetError("LLM dataset must use UTF-8 encoding") from exc
    except json.JSONDecodeError as exc:
        raise LlmDatasetError("invalid JSON dataset") from exc
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    raise LlmDatasetError("JSON dataset root must be an array or contain a data array")


def _detect_format(records: list[Any], requested_format: str) -> str:
    if requested_format != "auto":
        if requested_format not in SUPPORTED_LLM_DATASET_FORMATS:
            raise LlmDatasetError("unsupported LLM dataset format")
        return requested_format
    first = next((record for record in records if isinstance(record, dict)), None)
    if first is None:
        raise LlmDatasetError("LLM dataset has no object records")
    if "messages" in first:
        return "openai_messages"
    if "conversations" in first:
        return "sharegpt"
    if "instruction" in first and "output" in first:
        return "alpaca"
    raise LlmDatasetError("unable to detect Alpaca, ShareGPT, or OpenAI messages format")


def _validate_record(record: Any, dataset_format: str, *, source_row_id: str) -> dict[str, Any]:
    if not isinstance(record, dict):
        raise LlmDatasetError("record must be an object")
    if dataset_format == "alpaca":
        instruction = _non_empty_text(record.get("instruction"), "instruction")
        output = _non_empty_text(record.get("output"), "output")
        input_text = record.get("input", "") if isinstance(record.get("input", ""), str) else ""
        user_content = instruction if not input_text.strip() else f"{instruction}\n\n{input_text.strip()}"
        messages: list[dict[str, str]] = []
        if isinstance(record.get("system"), str) and record["system"].strip():
            messages.append({"role": "system", "content": record["system"].strip()})
        messages.extend(
            [
                {"role": "user", "content": user_content},
                {"role": "assistant", "content": output},
            ]
        )
        return {"source_row_id": str(record.get("source_row_id") or source_row_id), "messages": messages}
    field = "messages" if dataset_format == "openai_messages" else "conversations"
    messages = record.get(field)
    if not isinstance(messages, list) or not messages:
        raise LlmDatasetError(f"{field} must be a non-empty array")
    canonical_messages: list[dict[str, str]] = []
    for message in messages:
        if not isinstance(message, dict):
            raise LlmDatasetError("message must be an object")
        if dataset_format == "sharegpt":
            raw_role = message.get("from", message.get("role"))
            content = message.get("value", message.get("content"))
            role = {"human": "user", "gpt": "assistant"}.get(raw_role, raw_role)
            output = {"from": raw_role, "value": _non_empty_text(content, "message content")}
        else:
            role = message.get("role")
            output = {"role": role, "content": _non_empty_text(message.get("content"), "message content")}
        if role not in ALLOWED_MESSAGE_ROLES:
            raise LlmDatasetError(f"unsupported message role: {role}")
        canonical_messages.append({"role": str(role), "content": str(output.get("content", output.get("value")))})
    roles = [message["role"] for message in canonical_messages]
    conversational_roles = [role for role in roles if role != "system"]
    if "user" not in roles or "assistant" not in roles:
        raise LlmDatasetError("SFT record must contain user and assistant messages")
    if roles.count("system") > 1 or ("system" in roles and roles[0] != "system"):
        raise LlmDatasetError("invalid message role order: system must appear once at the beginning")
    if not conversational_roles or conversational_roles[0] != "user" or conversational_roles[-1] != "assistant":
        raise LlmDatasetError("invalid message role order: conversation must start with user and end with assistant")
    previous = None
    for role in conversational_roles:
        if role == previous and role in {"user", "assistant"}:
            raise LlmDatasetError("invalid message role order: user and assistant turns must alternate")
        previous = role
    return {
        "source_row_id": str(record.get("source_row_id") or source_row_id),
        "messages": canonical_messages,
    }


def _to_messages(record: dict[str, Any], dataset_format: str) -> list[dict[str, str]]:
    if isinstance(record.get("messages"), list):
        return [dict(message) for message in record["messages"]]
    if dataset_format == "alpaca":
        instruction = record["instruction"]
        input_text = record.get("input", "")
        user_content = instruction if not input_text else f"{instruction}\n\n{input_text}"
        messages = []
        if record.get("system"):
            messages.append({"role": "system", "content": str(record["system"])})
        messages.extend(
            [
                {"role": "user", "content": user_content},
                {"role": "assistant", "content": str(record["output"])},
            ]
        )
        return messages
    if dataset_format == "openai_messages":
        return [dict(message) for message in record["messages"]]
    return [
        {
            "role": {"human": "user", "gpt": "assistant"}.get(message["from"], message["from"]),
            "content": message["value"],
        }
        for message in record["conversations"]
    ]


def _schema_config(dataset_format: str) -> dict[str, Any]:
    if dataset_format == "alpaca":
        return {
            "prompt": "instruction",
            "query": "input",
            "response": "output",
            "system": "system",
            "normalized": {"messages": "messages", "role": "role", "content": "content"},
        }
    if dataset_format == "openai_messages":
        return {"messages": "messages", "role": "role", "content": "content"}
    return {
        "messages": "conversations",
        "role": "from",
        "content": "value",
        "role_tags": {"user": "human", "assistant": "gpt"},
    }


def _non_empty_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LlmDatasetError(f"{field} must be a non-empty string")
    return value.strip()
