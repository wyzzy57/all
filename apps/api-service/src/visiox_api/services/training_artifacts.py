from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from visiox_db.models import (
    TrainedModel,
    TrainingJob,
    TrainingJobAttempt,
    TrainingPipeline,
)


ARTIFACT_COLLECTION_FAILED = "ARTIFACT_COLLECTION_FAILED"

_MODEL_ROLES = {
    "best_weights",
    "last_weights",
    "checkpoint_weights",
    "best_dynamic_weights",
    "best_static_inference",
    "adapter_weights",
}
_BEST_ROLES = (
    "best_static_inference",
    "best_dynamic_weights",
    "best_weights",
    "adapter_weights",
)
_DISPLAY_NAMES = {
    "best_weights": "Best weights",
    "last_weights": "Last weights",
    "checkpoint_weights": "Checkpoint weights",
    "best_dynamic_weights": "Best dynamic weights",
    "best_static_inference": "Best static inference",
    "adapter_weights": "Adapter weights",
}


class ArtifactCollectionError(ValueError):
    code = ARTIFACT_COLLECTION_FAILED


@dataclass(frozen=True)
class ArtifactIngestionResult:
    model_ids: tuple[str, ...]
    best_model_id: str | None


def ingest_training_artifacts(
    session: Session,
    *,
    job: TrainingJob,
    pipeline: TrainingPipeline,
    attempt: TrainingJobAttempt,
    manifest: Mapping[str, Any],
    artifact_uris: Mapping[str, str],
    artifact_roles: Mapping[str, str] | None = None,
) -> ArtifactIngestionResult:
    _validate_identity(job, pipeline, manifest)
    entries = manifest.get("artifacts")
    if not isinstance(entries, list):
        raise ArtifactCollectionError("artifact manifest entries are invalid")

    roles = dict(artifact_roles or {})
    by_role: dict[str, list[Mapping[str, Any]]] = {}
    for raw_entry in entries:
        if not isinstance(raw_entry, Mapping):
            raise ArtifactCollectionError("artifact manifest entry is invalid")
        path = raw_entry.get("path")
        if not isinstance(path, str) or path not in artifact_uris:
            raise ArtifactCollectionError("collected artifact URI is missing")
        role = roles.get(path) or _entry_role(raw_entry)
        if role in _MODEL_ROLES:
            by_role.setdefault(role, []).append(raw_entry)

    if pipeline.task in {"detect", "detection", "object_detection"} and not any(
        role in by_role for role in _BEST_ROLES[:3]
    ):
        raise ArtifactCollectionError(
            "successful detection training requires a deployable best artifact"
        )
    if pipeline.framework == "llamafactory" and "adapter_weights" not in by_role:
        raise ArtifactCollectionError("successful LLM training requires adapter weights")

    models: list[TrainedModel] = []
    for role, candidates in by_role.items():
        entry = _primary_entry(role, candidates)
        path = str(entry["path"])
        statement = select(TrainedModel).where(
            TrainedModel.training_job_attempt_id == attempt.id,
            TrainedModel.artifact_role == role,
        )
        existing = session.scalar(statement)
        if existing is not None:
            _require_same_artifact(existing, entry, artifact_uris[path])
            models.append(existing)
            continue
        checksum = entry.get("checksum_sha256")
        size_bytes = entry.get("size_bytes")
        if not isinstance(checksum, str) or not isinstance(size_bytes, int):
            raise ArtifactCollectionError("artifact checksum or size is invalid")
        model = TrainedModel(
            pipeline_id=pipeline.id,
            organization_id=job.organization_id or pipeline.organization_id,
            owner_user_id=job.owner_user_id or pipeline.owner_user_id,
            visibility="private",
            training_job_id=job.id,
            training_job_attempt_id=attempt.id,
            name=PurePosixPath(path).name,
            display_name=_DISPLAY_NAMES[role],
            version=f"attempt-{attempt.attempt_number}",
            task=pipeline.task,
            framework=pipeline.framework,
            adapter_key=pipeline.adapter_key,
            model_family=pipeline.model_family,
            model_format=_model_format(path),
            artifact_role=role,
            artifact_uri=artifact_uris[path],
            checksum=checksum,
            size_bytes=size_bytes,
            artifact_manifest={
                **dict(manifest),
                "role_artifacts": [dict(item) for item in candidates],
            },
            metrics={"checksum": checksum},
            status="ready",
        )
        try:
            with session.begin_nested():
                session.add(model)
                session.flush()
        except IntegrityError:
            existing = session.scalar(statement)
            if existing is None:
                raise
            _require_same_artifact(existing, entry, artifact_uris[path])
            model = existing
        models.append(model)

    best = next(
        (model for role in _BEST_ROLES for model in models if model.artifact_role == role),
        None,
    )
    attempt.artifact_manifest = dict(manifest)
    session.add(attempt)
    return ArtifactIngestionResult(
        model_ids=tuple(model.id for model in models),
        best_model_id=best.id if best is not None else None,
    )


def sync_pipeline_status(session: Session, pipeline: TrainingPipeline) -> str:
    jobs = list(
        session.scalars(
            select(TrainingJob).where(TrainingJob.pipeline_id == pipeline.id)
        )
    )
    if not jobs:
        return pipeline.status
    attempts = list(
        session.scalars(
            select(TrainingJobAttempt).where(
                TrainingJobAttempt.training_job_id.in_([job.id for job in jobs])
            )
        )
    )
    attempts_by_job: dict[str, list[TrainingJobAttempt]] = {}
    for attempt in attempts:
        attempts_by_job.setdefault(attempt.training_job_id, []).append(attempt)
    active_jobs = [job for job in jobs if job.status not in {"success", "failed", "canceled"}]
    candidates = active_jobs or [job for job in jobs if job.id in attempts_by_job] or jobs
    active_job = max(candidates, key=lambda item: (item.updated_at, item.created_at, item.id))
    active_attempts = attempts_by_job.get(active_job.id, [])
    source_status = (
        max(active_attempts, key=lambda item: item.attempt_number).status
        if active_attempts
        else active_job.status
    )
    if active_job.status in {"success", "failed", "canceled"}:
        source_status = active_job.status
    pipeline.status = _pipeline_status(source_status)
    session.add(pipeline)
    return pipeline.status


def _pipeline_status(status: str) -> str:
    if status in {"success", "succeeded"}:
        return "success"
    if status in {"failed", "canceled", "stopping"}:
        return status
    if status in {"evaluating", "artifact_collecting"}:
        return status
    return "running"


def _validate_identity(
    job: TrainingJob,
    pipeline: TrainingPipeline,
    manifest: Mapping[str, Any],
) -> None:
    if (
        manifest.get("task_id") != job.id
        or manifest.get("adapter_key") != pipeline.adapter_key
        or manifest.get("adapter_version") != pipeline.adapter_version
    ):
        raise ArtifactCollectionError("artifact manifest identity did not match")


def _entry_role(entry: Mapping[str, Any]) -> str | None:
    artifact_type = entry.get("artifact_type")
    if isinstance(artifact_type, str) and artifact_type in _MODEL_ROLES:
        return artifact_type
    return None


def _primary_entry(
    role: str, entries: list[Mapping[str, Any]]
) -> Mapping[str, Any]:
    if role == "best_static_inference":
        return min(
            entries,
            key=lambda entry: (
                0 if str(entry["path"]).endswith(".json") else 1,
                str(entry["path"]),
            ),
        )
    return min(entries, key=lambda entry: str(entry["path"]))


def _require_same_artifact(
    model: TrainedModel, entry: Mapping[str, Any], artifact_uri: str
) -> None:
    if (
        model.artifact_uri != artifact_uri
        or model.checksum != entry.get("checksum_sha256")
        or model.size_bytes != entry.get("size_bytes")
    ):
        raise ArtifactCollectionError(
            "artifact reconciliation cannot overwrite an existing attempt artifact"
        )


def _model_format(path: str) -> str:
    suffix = PurePosixPath(path).suffix.casefold().removeprefix(".")
    return suffix or "directory"
