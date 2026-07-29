from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RunIdentity:
    training_job_id: str
    distributed_run_id: str
    attempt: int
    organization_id: str | None
    owner_user_id: str | None
    pipeline_id: str
    node_ids: tuple[str, ...]
    model_revision: str
    dataset_version_id: str | None
    dataset_checksum: str
    training_image_digest: str
    mlflow_tracking_uri: str | None = None

    @property
    def run_name(self) -> str:
        return f"visiox-{self.training_job_id}-attempt-{self.attempt}"

    def tags(self) -> dict[str, str]:
        values = {
            "visiox.training_job_id": self.training_job_id,
            "visiox.distributed_run_id": self.distributed_run_id,
            "visiox.attempt": str(self.attempt),
            "visiox.organization_id": self.organization_id or "",
            "visiox.owner_user_id": self.owner_user_id or "",
            "visiox.pipeline_id": self.pipeline_id,
            "visiox.node_ids": ",".join(self.node_ids),
            "visiox.model_revision": self.model_revision,
            "visiox.dataset_version_id": self.dataset_version_id or "",
            "visiox.dataset_checksum": self.dataset_checksum,
            "visiox.training_image_digest": self.training_image_digest,
        }
        return {key: value for key, value in values.items() if value}


def load_run_identity(config_path: Path) -> RunIdentity:
    path = config_path.parent / "visiox-run.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("VisiOX run identity must be a JSON object")
    required = {
        "training_job_id",
        "distributed_run_id",
        "attempt",
        "pipeline_id",
        "node_ids",
        "model_revision",
        "dataset_checksum",
        "training_image_digest",
    }
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"VisiOX run identity is missing: {', '.join(missing)}")
    node_ids = payload["node_ids"]
    if not isinstance(node_ids, list) or not all(isinstance(item, str) and item for item in node_ids):
        raise ValueError("VisiOX run identity node_ids are invalid")
    attempt = payload["attempt"]
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ValueError("VisiOX run identity attempt is invalid")
    return RunIdentity(
        training_job_id=_required_text(payload, "training_job_id"),
        distributed_run_id=_required_text(payload, "distributed_run_id"),
        attempt=attempt,
        organization_id=_optional_text(payload.get("organization_id")),
        owner_user_id=_optional_text(payload.get("owner_user_id")),
        pipeline_id=_required_text(payload, "pipeline_id"),
        node_ids=tuple(node_ids),
        model_revision=_required_text(payload, "model_revision"),
        dataset_version_id=_optional_text(payload.get("dataset_version_id")),
        dataset_checksum=_required_text(payload, "dataset_checksum"),
        training_image_digest=_required_text(payload, "training_image_digest"),
        mlflow_tracking_uri=_optional_text(payload.get("mlflow_tracking_uri")),
    )


def apply_managed_config(config: dict[str, Any], identity: RunIdentity) -> dict[str, Any]:
    managed = dict(config)
    output_dir = f"/workspace/output/job-{identity.training_job_id}"
    managed.update(
        {
            "output_dir": output_dir,
            "logging_dir": output_dir,
            "report_to": "tensorboard",
            "run_name": identity.run_name,
            "overwrite_output_dir": True,
            "do_train": True,
            "plot_loss": True,
        }
    )
    return managed


def _required_text(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"VisiOX run identity {key} is invalid")
    return value.strip()


def _optional_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
