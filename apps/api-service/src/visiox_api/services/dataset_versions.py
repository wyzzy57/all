from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from visiox_db.models import Annotation, Dataset, DatasetSample, DatasetVersion
from visiox_storage.client import ObjectStorageClient


@dataclass(frozen=True)
class DatasetVersionResult:
    version: DatasetVersion
    reused: bool
    total_count: int
    valid_count: int
    invalid_count: int
    skipped_count: int
    issues: list[dict[str, Any]]


def publish_labeled_llm_dataset(
    session: Session,
    storage: ObjectStorageClient,
    dataset: Dataset,
) -> DatasetVersionResult:
    if dataset.task != "llm":
        raise ValueError("Only LLM datasets can be published through the SFT converter")
    samples = session.scalars(
        select(DatasetSample)
        .where(DatasetSample.dataset_id == dataset.id)
        .order_by(DatasetSample.created_at, DatasetSample.id)
    ).all()
    annotations = session.scalars(
        select(Annotation)
        .join(DatasetSample, Annotation.dataset_sample_id == DatasetSample.id)
        .where(DatasetSample.dataset_id == dataset.id, Annotation.source == "label_studio")
        .order_by(Annotation.created_at, Annotation.id)
    ).all()
    annotations_by_sample = {annotation.dataset_sample_id: annotation for annotation in annotations}

    records: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    invalid_count = 0
    skipped_count = 0
    for sample in samples:
        annotation = annotations_by_sample.get(sample.id)
        if annotation is None or sample.annotation_status not in {"labeled", "invalid"}:
            skipped_count += 1
            continue
        if annotation.validation_status != "valid":
            invalid_count += 1
            issues.append(
                {
                    "sample_id": sample.id,
                    "code": "INVALID_ANNOTATION",
                    "message": str((annotation.internal_payload or {}).get("issue") or "assistant response is invalid"),
                }
            )
            continue
        payload = annotation.internal_payload or {}
        messages = payload.get("messages")
        if not _valid_messages(messages):
            invalid_count += 1
            issues.append(
                {
                    "sample_id": sample.id,
                    "code": "INVALID_MESSAGES",
                    "message": "normalized messages must end with a non-empty assistant response",
                }
            )
            continue
        records.append(
            {
                "source_row_id": str(payload.get("source_row_id") or sample.id),
                "messages": messages,
            }
        )

    if not records:
        raise ValueError("Dataset has no valid labeled assistant responses")

    jsonl = _canonical_jsonl(records)
    source_revision = hashlib.sha256(jsonl).hexdigest()
    existing = session.scalar(
        select(DatasetVersion).where(
            DatasetVersion.dataset_id == dataset.id,
            DatasetVersion.source_revision == source_revision,
        )
    )
    if existing is not None:
        return DatasetVersionResult(
            version=existing,
            reused=True,
            total_count=len(samples),
            valid_count=len(records),
            invalid_count=invalid_count,
            skipped_count=skipped_count,
            issues=issues[:100],
        )

    version_number = (session.scalar(select(func.max(DatasetVersion.version)).where(DatasetVersion.dataset_id == dataset.id)) or 0) + 1
    data_checksum = hashlib.sha256(jsonl).hexdigest()
    manifest = {
        "dataset_id": dataset.id,
        "version": version_number,
        "format": "openai_messages",
        "data_checksum": data_checksum,
        "source_revision": source_revision,
        "counts": {
            "total": len(samples),
            "valid": len(records),
            "invalid": invalid_count,
            "skipped": skipped_count,
        },
        "schema": {"messages": "messages", "role": "role", "content": "content"},
    }
    manifest_bytes = json.dumps(
        manifest,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    manifest_checksum = hashlib.sha256(manifest_bytes).hexdigest()
    data_uri = _put_bytes(
        storage,
        "datasets",
        f"{dataset.id}/versions/{version_number}/train.jsonl",
        jsonl,
        "application/x-ndjson",
    )
    manifest_uri = _put_bytes(
        storage,
        "datasets",
        f"{dataset.id}/versions/{version_number}/manifest.json",
        manifest_bytes,
        "application/json",
    )
    now = datetime.now(UTC)
    version = DatasetVersion(
        dataset_id=dataset.id,
        version=version_number,
        status="published",
        format="openai_messages",
        object_uri=data_uri,
        manifest_uri=manifest_uri,
        manifest_checksum=manifest_checksum,
        source_revision=source_revision,
        total_count=len(samples),
        valid_count=len(records),
        invalid_count=invalid_count,
        skipped_count=skipped_count,
        size_bytes=len(jsonl),
        schema_snapshot=manifest["schema"],
        published_at=now,
    )
    session.add(version)
    dataset.asset_role = "published"
    dataset.status = "validated"
    dataset.format = "openai_messages"
    dataset.manifest_checksum = manifest_checksum
    dataset.annotation_count = len(records)
    dataset.storage_uri = data_uri
    session.add(dataset)
    session.commit()
    session.refresh(version)
    return DatasetVersionResult(
        version=version,
        reused=False,
        total_count=len(samples),
        valid_count=len(records),
        invalid_count=invalid_count,
        skipped_count=skipped_count,
        issues=issues[:100],
    )


def _valid_messages(messages: Any) -> bool:
    if not isinstance(messages, list) or not messages:
        return False
    last = messages[-1]
    return (
        isinstance(last, dict)
        and last.get("role") == "assistant"
        and isinstance(last.get("content"), str)
        and bool(last["content"].strip())
    )


def _canonical_jsonl(records: list[dict[str, Any]]) -> bytes:
    return b"".join(
        json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        for record in records
    )


def _put_bytes(
    storage: ObjectStorageClient,
    bucket: str,
    object_name: str,
    payload: bytes,
    content_type: str,
) -> str:
    with NamedTemporaryFile(delete=False) as temporary:
        path = Path(temporary.name)
        temporary.write(payload)
    try:
        return storage.put_file(bucket, object_name, path, content_type=content_type)
    finally:
        path.unlink(missing_ok=True)
