from __future__ import annotations

import hashlib
import json
import shutil
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from visiox_db.models import (
    BaseModel as StoredBaseModel,
    Dataset,
    DistributedTrainingRun,
    RemoteExecution,
    Task,
    TrainingJob,
    TrainingJobAttempt,
    TrainingPipeline,
    TrainedModel,
)
from visiox_common.settings import get_settings
from visiox_storage.client import ObjectStorageClient
from visiox_training.contracts import LaunchSpec
from visiox_yolo26.converters import export_yolo26_dataset
from visiox_yolo26.training.commands import build_train_command

from .deployment import _DeploymentHandlerBase, validate_image_digest
from .log_capture import DurableLogCapture
from .startup import EdgeExecutorSecurityContext
from .state import ExecutionResult


_PRESIGNED_URL_TTL = timedelta(minutes=30)
_VISUALIZATION_ARTIFACTS = (
    "results.csv",
    "results.png",
    "confusion_matrix.png",
    "confusion_matrix_normalized.png",
    "BoxPR_curve.png",
    "BoxP_curve.png",
    "BoxR_curve.png",
    "BoxF1_curve.png",
    "labels.jpg",
    "train_batch0.jpg",
    "train_batch1.jpg",
    "train_batch2.jpg",
    "val_batch0_labels.jpg",
    "val_batch0_pred.jpg",
    "val_batch1_labels.jpg",
    "val_batch1_pred.jpg",
    "val_batch2_labels.jpg",
    "val_batch2_pred.jpg",
)


class _Rank(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    node_id: str
    node_rank: int
    lan_address: str
    gpu_uuids: tuple[str, ...]

    @field_validator("gpu_uuids")
    @classmethod
    def _require_gpus(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value:
            raise ValueError("distributed rank has no GPUs")
        return value


class _StageResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    root: str
    paths: dict[str, str]


class _LaunchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    container_id: str
    container_name: str
    node_rank: int


class _StopResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stopped_container_ids: tuple[str, ...]


class _InspectResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    running: bool
    exit_code: int


class _ContainerLogs(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    stdout: str
    stderr: str


class _CollectedArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    checksum: str
    size_bytes: int


class _CollectResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifacts: dict[str, _CollectedArtifact]


class _ArtifactManifestEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    size_bytes: int
    checksum_sha256: str


class _ArtifactManifestResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str
    artifacts: tuple[_ArtifactManifestEntry, ...]


class _ScriptRunner(_DeploymentHandlerBase):
    failure_code = "EDGE_TRAIN_SCRIPT_FAILED"
    failure_message = "Distributed training remote script failed"

    def __init__(
        self,
        script_name: str,
        session_factory: Callable[[], Session],
        security: EdgeExecutorSecurityContext,
    ) -> None:
        self.script_name = script_name
        super().__init__(session_factory, security)


class DistributedTrainingHandler:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        security: EdgeExecutorSecurityContext,
        storage: ObjectStorageClient,
    ) -> None:
        self._session_factory = session_factory
        self._storage = storage
        self._log_capture = DurableLogCapture(session_factory, storage)
        self._stage = _ScriptRunner("stage_training.sh", session_factory, security)
        self._launch = _ScriptRunner("launch_rank.sh", session_factory, security)
        self._stop = _ScriptRunner("stop_training.sh", session_factory, security)

    def execute(self, execution: RemoteExecution) -> ExecutionResult:
        launched: list[tuple[str, int]] = []
        log_stream_id: str | None = None
        log_job_id: str | None = None
        try:
            if self._converge_existing_success(execution.id):
                return ExecutionResult.succeeded(phase="succeeded")
            context = self._load(execution)
            run, job, attempt, task, pipeline, base_model, dataset = context
            base_launch_spec = LaunchSpec.model_validate(attempt.launch_spec)
            if (
                base_launch_spec.canonical_checksum_sha256()
                != attempt.launch_spec_checksum
            ):
                raise ValueError("persisted launch spec checksum did not match")
            log_job_id = job.id
            log_stream_id = self._ensure_log_stream(job, pipeline)
            ranks = tuple(_Rank.model_validate(item) for item in run.ranks)
            if not ranks or {rank.node_id for rank in ranks} != set(run.node_ids):
                raise ValueError("distributed rank plan is incomplete")
            self._transition(execution.id, "staging", 20)
            artifacts = self._prepare_artifacts(
                run, job, task, pipeline, base_model, dataset
            )
            staged: dict[str, _StageResult] = {}
            arguments = _training_arguments(job, task, pipeline.engine)
            framework_parameters: dict[str, Any] = {}
            if pipeline.engine == "llamafactory":
                framework_parameters["model_source"] = _llm_model_source(task)
            staged_requests: dict[str, dict[str, Any]] = {}
            for rank in ranks:
                staging_request = _staging_request(
                    run,
                    job,
                    rank,
                    ranks,
                    pipeline.framework,
                    base_launch_spec.adapter_key,
                    base_launch_spec.adapter_version,
                    arguments,
                    framework_parameters,
                    artifacts,
                    environment=dict(base_launch_spec.env),
                )
                target = self._stage._load_target(rank.node_id)
                response = self._stage._run_script(
                    target,
                    staging_request,
                )
                staged[rank.node_id] = _StageResult.model_validate(response)
                staged_requests[rank.node_id] = staging_request

            self._transition(execution.id, "launching", 55)
            for rank in ranks:
                stage = staged[rank.node_id]
                target = self._launch._load_target(rank.node_id)
                response = self._launch._run_script(
                    target,
                    _launch_request(run, rank, stage, staged_requests[rank.node_id]),
                )
                launched_rank = _LaunchResult.model_validate(response)
                if launched_rank.node_rank != rank.node_rank:
                    raise ValueError("remote rank did not match the persisted plan")
                launched.append((rank.node_id, rank.node_rank))
                self._persist_container(run.id, launched_rank.container_id)

            self._mark_training(execution.id)
            containers = tuple(
                (rank, container_id)
                for rank, container_id in zip(
                    ranks, self._container_ids(run.id), strict=True
                )
            )
            if not self._wait_for_completion(
                execution.id,
                containers,
                staged,
                log_stream_id=log_stream_id,
            ):
                if self._cancel_requested(execution.id):
                    self._close_log_stream(log_stream_id, "cancelled", log_job_id)
                    return ExecutionResult.succeeded(phase="canceled")
                self._stop_peers(execution, launched)
                self._try_collect_checkpoint(run, ranks[0], staged[ranks[0].node_id])
                self._mark_failed(execution.id)
                self._close_log_stream(log_stream_id, "failed", log_job_id)
                return ExecutionResult.failed(
                    error_code="EDGE_TRAIN_FAILED",
                    error_message="Distributed edge training failed",
                    phase="failed",
                )
            collected = self._collect_rank_zero(
                run, job, pipeline, ranks[0], staged[ranks[0].node_id]
            )
            self._cache_final_observability(job.id, collected)
            self._mark_succeeded(execution.id, collected)
            self._close_log_stream(log_stream_id, "completed", log_job_id)
            return ExecutionResult.succeeded(phase="succeeded")
        except Exception:
            self._stop_peers(execution, launched)
            self._mark_failed(execution.id)
            self._close_log_stream(log_stream_id, "failed", log_job_id)
            return ExecutionResult.failed(
                error_code="EDGE_TRAIN_FAILED",
                error_message="Distributed edge training failed",
                phase="failed",
            )

    def _converge_existing_success(self, execution_id: str) -> bool:
        """Make duplicate delivery idempotent after artifacts were committed."""
        with self._session_factory() as session:
            execution = session.get(RemoteExecution, execution_id)
            run = session.get(
                DistributedTrainingRun, execution.resource_id if execution else None
            )
            job = session.get(
                TrainingJob, execution.training_job_id if execution else None
            )
            task = session.get(Task, execution.task_id if execution else None)
            pipeline = session.get(TrainingPipeline, job.pipeline_id if job else None)
            metrics = (
                job.metrics if job is not None and isinstance(job.metrics, dict) else {}
            )
            weights = metrics.get("weights") if isinstance(metrics, dict) else None
            engine_artifacts_ok = (
                bool(metrics.get("adapter"))
                if pipeline is not None and pipeline.engine == "llamafactory"
                else isinstance(weights, dict)
                and {"best.pt", "last.pt"}.issubset(weights)
            )
            completed = bool(
                execution
                and run
                and job
                and task
                and pipeline
                and job.finished_at
                and job.trained_model_id
                and engine_artifacts_ok
            )
            if not completed:
                return False
            assert execution is not None and run is not None and job is not None
            assert task is not None and pipeline is not None
            finished_at = job.finished_at or datetime.now(UTC)
            job.status = "success"
            run.status = "succeeded"
            run.finished_at = run.finished_at or finished_at
            task.status = "SUCCESS"
            task.stage = "completed"
            task.progress = 100
            task.error_code = None
            task.error_message = None
            task.finished_at = task.finished_at or finished_at
            pipeline.status = "success"
            execution.phase = "succeeded"
            execution.error_code = None
            execution.error_message = None
            session.add_all([execution, run, job, task, pipeline])
            session.commit()
            return True

    def _load(self, execution: RemoteExecution):
        with self._session_factory() as session:
            current = session.get(RemoteExecution, execution.id)
            if (
                current is None
                or current.status != "running"
                or current.resource_id is None
            ):
                raise ValueError("distributed execution is not runnable")
            run = session.get(DistributedTrainingRun, current.resource_id)
            job = session.get(TrainingJob, current.training_job_id)
            attempt = session.scalar(
                select(TrainingJobAttempt).where(
                    TrainingJobAttempt.training_job_id == current.training_job_id,
                    TrainingJobAttempt.attempt_number == run.attempt,
                )
            ) if run is not None else None
            task = session.get(Task, current.task_id)
            pipeline = session.get(TrainingPipeline, job.pipeline_id if job else None)
            base_model = (
                session.get(StoredBaseModel, pipeline.base_model_id)
                if pipeline and pipeline.base_model_id
                else None
            )
            dataset = session.get(Dataset, pipeline.dataset_id if pipeline else None)
            if not all((run, job, attempt, task, pipeline, dataset)):
                raise ValueError("distributed training resources are incomplete")
            if pipeline.engine == "yolo26" and base_model is None:
                raise ValueError("distributed YOLO training model is incomplete")
            session.expunge_all()
            return run, job, attempt, task, pipeline, base_model, dataset

    def _prepare_artifacts(
        self,
        run: DistributedTrainingRun,
        job: TrainingJob,
        task: Task,
        pipeline: TrainingPipeline,
        base_model: StoredBaseModel | None,
        dataset: Dataset,
    ) -> list[dict[str, Any]]:
        if pipeline.engine == "llamafactory":
            return self._prepare_llm_artifacts(run, job, task, dataset)
        if base_model is None:
            raise ValueError("base model is unavailable")
        checksum = (base_model.checksum or "").lower()
        if len(checksum) != 64 or any(
            char not in "0123456789abcdef" for char in checksum
        ):
            raise ValueError("base model checksum is unavailable")
        if not base_model.local_uri:
            raise ValueError("base model artifact is unavailable")
        with TemporaryDirectory(prefix="visiox-distributed-") as temporary:
            root = Path(temporary)
            dataset_dir = root / "dataset"
            with self._session_factory() as session:
                export_yolo26_dataset(session, self._storage, dataset.id, dataset_dir)
            _ensure_split_directories(dataset_dir)
            _set_container_dataset_root(dataset_dir)
            archive = Path(
                shutil.make_archive(str(root / "dataset"), "gztar", dataset_dir)
            )
            dataset_checksum = _sha256(archive)
            dataset_uri = self._storage.put_file(
                "training",
                f"distributed/{run.id}/{run.attempt}/dataset.tar.gz",
                archive,
                content_type="application/gzip",
            )
        artifacts = [
            {
                "role": "model",
                "download_url": self._storage.presigned_get_url(
                    base_model.local_uri, expires=_PRESIGNED_URL_TTL
                ),
                "checksum_sha256": checksum,
                "target_path": "artifacts/base.pt",
                "unpack_to": None,
            },
            {
                "role": "dataset",
                "download_url": self._storage.presigned_get_url(
                    dataset_uri, expires=_PRESIGNED_URL_TTL
                ),
                "checksum_sha256": dataset_checksum,
                "target_path": "archives/dataset.tar.gz",
                "unpack_to": "dataset",
            },
        ]
        if run.checkpoint_uri and run.checkpoint_checksum:
            artifacts.append(
                {
                    "role": "checkpoint",
                    "download_url": self._storage.presigned_get_url(
                        run.checkpoint_uri, expires=_PRESIGNED_URL_TTL
                    ),
                    "checksum_sha256": run.checkpoint_checksum,
                    "target_path": "artifacts/last.pt",
                    "unpack_to": None,
                }
            )
        return artifacts

    def _prepare_llm_artifacts(
        self,
        run: DistributedTrainingRun,
        job: TrainingJob,
        task: Task,
        dataset: Dataset,
    ) -> list[dict[str, Any]]:
        payload = task.payload if isinstance(task.payload, dict) else {}
        manifest_checksum = str(payload.get("dataset_manifest_checksum") or "").lower()
        if len(manifest_checksum) != 64 or any(
            char not in "0123456789abcdef" for char in manifest_checksum
        ):
            raise ValueError("LLM dataset manifest checksum is unavailable")
        if not dataset.storage_uri:
            raise ValueError("LLM dataset artifact is unavailable")
        with TemporaryDirectory(prefix="visiox-llm-distributed-") as temporary:
            root = Path(temporary)
            dataset_dir = root / "dataset"
            dataset_dir.mkdir(parents=True)
            train_path = dataset_dir / "train.jsonl"
            self._download_storage_uri(dataset.storage_uri, train_path)
            if _sha256(train_path) != manifest_checksum:
                raise ValueError("LLM dataset manifest checksum did not match")
            config = _llamafactory_config(job, task, dataset)
            (dataset_dir / "train.yaml").write_text(
                yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )
            (dataset_dir / "dataset_info.json").write_text(
                json.dumps(
                    {"visiox_train": _llamafactory_dataset_info(dataset)},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            model_reference = payload.get("model_reference")
            if not isinstance(model_reference, Mapping):
                raise ValueError("LLM model reference is unavailable")
            (dataset_dir / "visiox-run.json").write_text(
                json.dumps(
                    {
                        "training_job_id": job.id,
                        "distributed_run_id": run.id,
                        "attempt": run.attempt,
                        "organization_id": job.organization_id,
                        "owner_user_id": job.owner_user_id,
                        "pipeline_id": job.pipeline_id,
                        "node_ids": list(run.node_ids),
                        "model_revision": model_reference.get("revision"),
                        "dataset_version_id": payload.get("dataset_version_id"),
                        "dataset_checksum": manifest_checksum,
                        "training_image_digest": run.training_image_digest,
                        "mlflow_tracking_uri": get_settings().mlflow_public_url,
                    },
                    ensure_ascii=True,
                    indent=2,
                ),
                encoding="utf-8",
            )
            archive = Path(
                shutil.make_archive(str(root / "dataset"), "gztar", dataset_dir)
            )
            archive_checksum = _sha256(archive)
            dataset_uri = self._storage.put_file(
                "training",
                f"distributed/{run.id}/{run.attempt}/llm-dataset.tar.gz",
                archive,
                content_type="application/gzip",
            )
        return [
            {
                "role": "dataset",
                "download_url": self._storage.presigned_get_url(
                    dataset_uri, expires=_PRESIGNED_URL_TTL
                ),
                "checksum_sha256": archive_checksum,
                "target_path": "archives/dataset.tar.gz",
                "unpack_to": "dataset",
            }
        ]

    def _download_storage_uri(self, uri: str, destination: Path) -> None:
        remainder = uri.removeprefix("minio://")
        if remainder == uri or "/" not in remainder:
            raise ValueError("LLM dataset must use durable object storage")
        bucket, object_name = remainder.split("/", 1)
        if not bucket or not object_name:
            raise ValueError("LLM dataset storage URI is invalid")
        self._storage.get_file(bucket, object_name, destination)

    def _transition(self, execution_id: str, phase: str, progress: int) -> None:
        with self._session_factory() as session:
            execution = session.get(RemoteExecution, execution_id)
            run = session.get(
                DistributedTrainingRun, execution.resource_id if execution else None
            )
            job = session.get(
                TrainingJob, execution.training_job_id if execution else None
            )
            task = session.get(Task, execution.task_id if execution else None)
            if not all((execution, run, job, task)):
                raise ValueError("distributed training state is incomplete")
            execution.phase = phase
            run.status = phase
            job.status = "running"
            task.status = "RUNNING"
            task.stage = phase
            task.progress = progress
            now = datetime.now(UTC)
            run.started_at = run.started_at or now
            job.started_at = job.started_at or now
            task.started_at = task.started_at or now
            session.add_all([execution, run, job, task])
            session.commit()

    def _persist_container(self, run_id: str, container_id: str) -> None:
        with self._session_factory() as session:
            run = session.get(DistributedTrainingRun, run_id)
            if run is None:
                raise ValueError("distributed run was not found")
            run.container_ids = [*run.container_ids, container_id]
            session.add(run)
            session.commit()

    def _container_ids(self, run_id: str) -> tuple[str, ...]:
        with self._session_factory() as session:
            run = session.get(DistributedTrainingRun, run_id)
            if run is None:
                raise ValueError("distributed run was not found")
            return tuple(run.container_ids)

    def _mark_training(self, execution_id: str) -> None:
        self._transition(execution_id, "training", 65)

    def _wait_for_completion(
        self,
        execution_id: str,
        containers: tuple[tuple[_Rank, str], ...],
        staged: dict[str, _StageResult],
        *,
        log_stream_id: str | None,
    ) -> bool:
        while True:
            if self._cancel_requested(execution_id):
                return False
            rank_zero = containers[0][0]
            self._sync_observability(execution_id, rank_zero, staged[rank_zero.node_id])
            completed = 0
            for rank, container_id in containers:
                target = self._launch._load_target(rank.node_id)
                result = _InspectResult.model_validate(
                    self._launch._run_script(
                        target,
                        {"action": "inspect", "container_id": container_id},
                    )
                )
                if result.running:
                    continue
                self._capture_container_log(log_stream_id, rank, container_id)
                if result.exit_code != 0:
                    return False
                completed += 1
            if completed == len(containers):
                return True
            time.sleep(5)

    def _ensure_log_stream(
        self,
        job: TrainingJob,
        pipeline: TrainingPipeline,
    ) -> str | None:
        organization_id = job.organization_id or pipeline.organization_id
        if organization_id is None:
            return None
        observability = (
            job.metrics.get("observability", {})
            if isinstance(job.metrics, dict)
            else {}
        )
        existing = (
            observability.get("log_stream_id")
            if isinstance(observability, dict)
            else None
        )
        if isinstance(existing, str):
            return existing
        stream_id = self._log_capture.open(
            organization_id=organization_id,
            resource_type="training_job",
            resource_id=job.id,
            source="remote_execution",
        )
        with self._session_factory() as session:
            current = session.get(TrainingJob, job.id)
            if current is not None:
                current_observability = (
                    current.metrics.get("observability", {})
                    if isinstance(current.metrics, dict)
                    else {}
                )
                current.metrics = {
                    **(current.metrics or {}),
                    "observability": {
                        **(
                            current_observability
                            if isinstance(current_observability, dict)
                            else {}
                        ),
                        "log_stream_id": stream_id,
                    },
                }
                session.add(current)
                session.commit()
        return stream_id

    def _capture_container_log(
        self,
        stream_id: str | None,
        rank: _Rank,
        container_id: str,
    ) -> None:
        if stream_id is None:
            return
        try:
            target = self._launch._load_target(rank.node_id)
            output = _ContainerLogs.model_validate(
                self._launch._run_script(
                    target,
                    {"action": "logs", "container_id": container_id},
                )
            )
            self._log_capture.capture_text(
                stream_id,
                stdout=output.stdout,
                stderr=output.stderr,
                timestamp=datetime.now(UTC),
            )
        except Exception:
            return

    def _close_log_stream(
        self,
        stream_id: str | None,
        status: str,
        job_id: str | None,
    ) -> None:
        if stream_id is None:
            return
        try:
            uri = self._log_capture.close(stream_id, status=status)
            if job_id is not None:
                with self._session_factory() as session:
                    job = session.get(TrainingJob, job_id)
                    if job is not None:
                        job.log_uri = uri
                        session.add(job)
                        session.commit()
        except Exception:
            return

    def _sync_observability(
        self,
        execution_id: str,
        rank: _Rank,
        staged: _StageResult,
    ) -> None:
        try:
            with self._session_factory() as session:
                execution = session.get(RemoteExecution, execution_id)
                job = session.get(
                    TrainingJob, execution.training_job_id if execution else None
                )
                if job is None:
                    return
                job_id = job.id
            object_names = {
                "visiox-progress.json": f"jobs/{job_id}/observability/visiox-progress.json",
                "events.out.tfevents.remote": f"jobs/{job_id}/observability/events.out.tfevents.remote",
                "visiox-metrics.jsonl": f"jobs/{job_id}/observability/visiox-metrics.jsonl",
                "resource_metrics.jsonl": f"jobs/{job_id}/observability/resource_metrics.jsonl",
            }
            uploads = {
                name: self._storage.presigned_put_url(
                    f"minio://training/{object_name}",
                    expires=_PRESIGNED_URL_TTL,
                )
                for name, object_name in object_names.items()
            }
            target = self._launch._load_target(rank.node_id)
            manifest = self._remote_artifact_manifest(target, staged.paths["output"])
            manifest_paths = _manifest_paths_by_name(manifest)
            selected_uploads = {
                manifest_paths[name]: url
                for name, url in uploads.items()
                if name in manifest_paths
            }
            if not selected_uploads:
                return
            result = _CollectResult.model_validate(
                self._launch._run_script(
                    target,
                    {
                        "action": "collect",
                        "output_path": staged.paths["output"],
                        "artifact_manifest": manifest.model_dump(mode="json"),
                        "uploads": selected_uploads,
                    },
                )
            )
            run_path = get_settings().training_runs_root / "runs" / f"job-{job_id}"
            run_path.mkdir(parents=True, exist_ok=True)
            for name, object_name in object_names.items():
                path = manifest_paths.get(name)
                if path is None or path not in result.artifacts:
                    continue
                destination = run_path / name
                self._storage.get_file("training", object_name, destination)
            progress_path = run_path / "visiox-progress.json"
            if progress_path.is_file():
                snapshot = json.loads(progress_path.read_text(encoding="utf-8"))
                self._persist_observability_snapshot(execution_id, snapshot)
        except Exception:
            return

    def _remote_artifact_manifest(
        self,
        target: Any,
        output_path: str,
    ) -> _ArtifactManifestResult:
        manifest = _ArtifactManifestResult.model_validate(
            self._launch._run_script(
                target,
                {"action": "manifest", "output_path": output_path},
            )
        )
        if manifest.schema_version != "1.0":
            raise ValueError("remote artifact manifest version is unsupported")
        return manifest

    def _persist_observability_snapshot(self, execution_id: str, snapshot: Any) -> None:
        if not isinstance(snapshot, dict):
            return
        with self._session_factory() as session:
            execution = session.get(RemoteExecution, execution_id)
            job = session.get(
                TrainingJob, execution.training_job_id if execution else None
            )
            run = session.get(
                DistributedTrainingRun, execution.resource_id if execution else None
            )
            task = session.get(Task, execution.task_id if execution else None)
            if job is None or run is None or task is None:
                return
            progress = (
                snapshot.get("progress")
                if isinstance(snapshot.get("progress"), dict)
                else {}
            )
            percent = progress.get("percent")
            if isinstance(percent, int | float):
                task.progress = max(
                    task.progress, min(90, 65 + int(float(percent) * 0.25))
                )
            job.metrics = {
                **(job.metrics or {}),
                "observability": {
                    **(
                        job.metrics.get("observability", {})
                        if isinstance(job.metrics.get("observability"), dict)
                        else {}
                    ),
                    "mlflow_run_name": f"visiox-{job.id}-attempt-{run.attempt}",
                    "tensorboard_run_name": f"visiox-{job.id}-attempt-{run.attempt}",
                },
                "observability_snapshot": snapshot,
            }
            session.add_all([job, task])
            session.commit()

    def _cancel_requested(self, execution_id: str) -> bool:
        with self._session_factory() as session:
            execution = session.get(RemoteExecution, execution_id)
            run = session.get(
                DistributedTrainingRun, execution.resource_id if execution else None
            )
            return run is None or run.status in {"stopping", "canceled"}

    def _collect_rank_zero(
        self,
        run: DistributedTrainingRun,
        job: TrainingJob,
        pipeline: TrainingPipeline,
        rank: _Rank,
        staged: _StageResult,
    ) -> _CollectResult:
        target = self._launch._load_target(rank.node_id)
        manifest = self._remote_artifact_manifest(target, staged.paths["output"])
        paths_by_name = _manifest_paths_by_name(manifest)
        uris = {
            entry.path: _artifact_uri(job.id, entry.path)
            for entry in manifest.artifacts
        }
        uploads = {
            path: self._storage.presigned_put_url(uri, expires=_PRESIGNED_URL_TTL)
            for path, uri in uris.items()
        }
        raw_result = _CollectResult.model_validate(
            self._launch._run_script(
                target,
                {
                    "action": "collect",
                    "output_path": staged.paths["output"],
                    "artifact_manifest": manifest.model_dump(mode="json"),
                    "uploads": uploads,
                },
            )
        )
        required = (
            {"adapter_model.safetensors", "adapter_config.json"}
            if pipeline.engine == "llamafactory"
            else {"best.pt", "last.pt"}
        )
        if not required.issubset(paths_by_name):
            raise ValueError("rank zero did not upload required training artifacts")
        return _CollectResult(
            artifacts={
                PurePosixPath(path).name: artifact
                for path, artifact in raw_result.artifacts.items()
                if path in {entry.path for entry in manifest.artifacts}
            }
        )

    def _try_collect_checkpoint(
        self,
        run: DistributedTrainingRun,
        rank: _Rank,
        staged: _StageResult,
    ) -> None:
        checkpoint_uri = f"minio://training/checkpoints/{run.id}/{run.attempt}/last.pt"
        try:
            target = self._launch._load_target(rank.node_id)
            manifest = self._remote_artifact_manifest(target, staged.paths["output"])
            last_path = _manifest_paths_by_name(manifest).get("last.pt")
            if last_path is None:
                return
            result = _CollectResult.model_validate(
                self._launch._run_script(
                    target,
                    {
                        "action": "collect",
                        "output_path": staged.paths["output"],
                        "artifact_manifest": manifest.model_dump(mode="json"),
                        "uploads": {
                            last_path: self._storage.presigned_put_url(
                                checkpoint_uri,
                                expires=_PRESIGNED_URL_TTL,
                            )
                        },
                    },
                )
            )
            artifact = result.artifacts.get(last_path)
            if artifact is None:
                return
            with self._session_factory() as session:
                current = session.get(DistributedTrainingRun, run.id)
                if current is not None:
                    current.checkpoint_uri = checkpoint_uri
                    current.checkpoint_checksum = artifact.checksum
                    session.add(current)
                    session.commit()
        except Exception:
            return

    def _mark_succeeded(self, execution_id: str, collected: _CollectResult) -> None:
        now = datetime.now(UTC)
        with self._session_factory() as session:
            execution = session.get(RemoteExecution, execution_id)
            run = session.get(
                DistributedTrainingRun, execution.resource_id if execution else None
            )
            job = session.get(
                TrainingJob, execution.training_job_id if execution else None
            )
            task = session.get(Task, execution.task_id if execution else None)
            pipeline = session.get(TrainingPipeline, job.pipeline_id if job else None)
            if not all((execution, run, job, task, pipeline)):
                raise ValueError("distributed completion state is incomplete")
            if pipeline.engine == "llamafactory":
                adapter_uri = (
                    f"minio://models/trained/{job.id}/adapter_model.safetensors"
                )
                adapter = TrainedModel(
                    pipeline_id=pipeline.id,
                    organization_id=job.organization_id or pipeline.organization_id,
                    owner_user_id=job.owner_user_id or pipeline.owner_user_id,
                    visibility="private",
                    training_job_id=job.id,
                    name="adapter_model.safetensors",
                    version="adapter",
                    task=pipeline.task,
                    artifact_uri=adapter_uri,
                    metrics={},
                    status="ready",
                )
                session.add(adapter)
                session.flush()
                job.trained_model_id = adapter.id
                job.status = "success"
                job.metrics = {
                    **(job.metrics or {}),
                    "adapter": adapter_uri,
                    "artifacts": {
                        name: (
                            f"minio://models/trained/{job.id}/{name}"
                            if name == "adapter_config.json"
                            else f"minio://training/jobs/{job.id}/artifacts/{name}"
                        )
                        for name in collected.artifacts
                        if name
                        not in {
                            "adapter_model.safetensors",
                            "visiox-progress.json",
                            "events.out.tfevents.remote",
                            "visiox-metrics.jsonl",
                            "resource_metrics.jsonl",
                        }
                    },
                }
                job.finished_at = now
                run.status = "succeeded"
                run.finished_at = now
                task.status = "SUCCESS"
                task.stage = "completed"
                task.progress = 100
                task.finished_at = now
                pipeline.status = "success"
                execution.phase = "succeeded"
                session.add_all([execution, run, job, task, pipeline])
                session.commit()
                return
            weights = {
                name: f"minio://models/trained/{job.id}/{name}"
                for name in ("best.pt", "last.pt")
            }
            visualizations = {
                name: f"minio://training/jobs/{job.id}/visualizations/{name}"
                for name in _VISUALIZATION_ARTIFACTS
                if name in collected.artifacts
            }
            best = TrainedModel(
                pipeline_id=pipeline.id,
                organization_id=job.organization_id or pipeline.organization_id,
                owner_user_id=job.owner_user_id or pipeline.owner_user_id,
                visibility="private",
                training_job_id=job.id,
                name="best.pt",
                version="best.pt",
                task=pipeline.task,
                artifact_uri=weights["best.pt"],
                metrics={},
                status="ready",
            )
            last = TrainedModel(
                pipeline_id=pipeline.id,
                organization_id=job.organization_id or pipeline.organization_id,
                owner_user_id=job.owner_user_id or pipeline.owner_user_id,
                visibility="private",
                training_job_id=None,
                name="last.pt",
                version="last.pt",
                task=pipeline.task,
                artifact_uri=weights["last.pt"],
                metrics={},
                status="ready",
            )
            session.add_all([best, last])
            session.flush()
            job.trained_model_id = best.id
            job.status = "success"
            job.metrics = {
                **(job.metrics or {}),
                "weights": weights,
                "visualizations": visualizations,
            }
            job.finished_at = now
            run.status = "succeeded"
            run.finished_at = now
            task.status = "SUCCESS"
            task.stage = "completed"
            task.progress = 100
            task.finished_at = now
            pipeline.status = "success"
            execution.phase = "succeeded"
            session.add_all([execution, run, job, task, pipeline])
            session.commit()

    def _cache_final_observability(self, job_id: str, collected: _CollectResult) -> None:
        run_path = get_settings().training_runs_root / "runs" / f"job-{job_id}"
        run_path.mkdir(parents=True, exist_ok=True)
        object_names = {
            "artifact-manifest.json": f"jobs/{job_id}/artifacts/artifact-manifest.json",
            "visiox-metrics.jsonl": f"jobs/{job_id}/observability/visiox-metrics.jsonl",
            "resource_metrics.jsonl": f"jobs/{job_id}/observability/resource_metrics.jsonl",
            "visiox-progress.json": f"jobs/{job_id}/observability/visiox-progress.json",
            "events.out.tfevents.remote": f"jobs/{job_id}/observability/events.out.tfevents.remote",
        }
        for name, object_name in object_names.items():
            if name not in collected.artifacts:
                continue
            try:
                self._storage.get_file("training", object_name, run_path / name)
            except Exception:
                continue

    def _stop_peers(
        self, execution: RemoteExecution, launched: list[tuple[str, int]]
    ) -> None:
        with self._session_factory() as session:
            run = session.get(DistributedTrainingRun, execution.resource_id)
            if run is None:
                return
            attempt = run.attempt
            run_id = run.id
        for node_id, _rank in launched:
            try:
                target = self._stop._load_target(node_id)
                self._stop._run_script(
                    target,
                    {"schema_version": "1.0", "run_id": run_id, "attempt": attempt},
                )
            except Exception:
                continue

    def _mark_failed(self, execution_id: str) -> None:
        now = datetime.now(UTC)
        with self._session_factory() as session:
            execution = session.get(RemoteExecution, execution_id)
            if execution is None:
                return
            run = session.get(DistributedTrainingRun, execution.resource_id)
            job = session.get(TrainingJob, execution.training_job_id)
            task = session.get(Task, execution.task_id)
            pipeline = session.get(TrainingPipeline, job.pipeline_id if job else None)
            execution.phase = "failed"
            execution.error_code = "EDGE_TRAIN_FAILED"
            execution.error_message = "Distributed edge training failed"
            if run:
                run.status = "failed"
                run.finished_at = now
            if job:
                job.status = "failed"
                job.finished_at = now
            if task:
                task.status = "FAILED"
                task.stage = "failed"
                task.error_code = "EDGE_TRAIN_FAILED"
                task.error_message = "Distributed edge training failed"
                task.finished_at = now
            if pipeline:
                pipeline.status = "failed"
            session.commit()


class StopDistributedTrainingHandler(DistributedTrainingHandler):
    def execute(self, execution: RemoteExecution) -> ExecutionResult:
        try:
            with self._session_factory() as session:
                current = session.get(RemoteExecution, execution.id)
                run = session.get(
                    DistributedTrainingRun, current.resource_id if current else None
                )
                if current is None or run is None:
                    raise ValueError("distributed run was not found")
                nodes = tuple(run.node_ids)
                run_id = run.id
                attempt = run.attempt
            for node_id in nodes:
                target = self._stop._load_target(node_id)
                _StopResult.model_validate(
                    self._stop._run_script(
                        target,
                        {"schema_version": "1.0", "run_id": run_id, "attempt": attempt},
                    )
                )
            rank_zero = _Rank.model_validate(run.ranks[0])
            self._try_collect_checkpoint(
                run,
                rank_zero,
                _StageResult(
                    root=f"/var/lib/visiox/training/{run.id}/{run.attempt}",
                    paths={
                        "output": f"/var/lib/visiox/training/{run.id}/{run.attempt}/output"
                    },
                ),
            )
            now = datetime.now(UTC)
            with self._session_factory() as session:
                current = session.get(RemoteExecution, execution.id)
                run = session.get(
                    DistributedTrainingRun, current.resource_id if current else None
                )
                job = session.get(
                    TrainingJob, current.training_job_id if current else None
                )
                task = session.get(Task, current.task_id if current else None)
                pipeline = session.get(
                    TrainingPipeline, job.pipeline_id if job else None
                )
                if run:
                    run.status = "canceled"
                    run.finished_at = now
                if job:
                    job.status = "canceled"
                    job.finished_at = now
                if task:
                    task.status = "CANCELED"
                    task.stage = "canceled"
                    task.finished_at = now
                if pipeline:
                    pipeline.status = "canceled"
                session.commit()
            return ExecutionResult.succeeded(phase="canceled")
        except Exception:
            return ExecutionResult.failed(
                error_code="EDGE_STOP_TRAINING_FAILED",
                error_message="Distributed edge training could not be stopped",
                phase="failed",
            )


def build_distributed_handlers(
    session_factory: Callable[[], Session],
    security: EdgeExecutorSecurityContext,
    storage: ObjectStorageClient,
) -> dict[str, DistributedTrainingHandler]:
    return {
        "train": DistributedTrainingHandler(session_factory, security, storage),
        "resume_training": DistributedTrainingHandler(
            session_factory, security, storage
        ),
        "stop_training": StopDistributedTrainingHandler(
            session_factory, security, storage
        ),
    }


def _training_arguments(job: TrainingJob, task: Task, engine: str) -> list[str]:
    if engine == "llamafactory":
        return []
    params: dict[str, Any] = dict(job.params or {})
    environment = (task.payload or {}).get("environment")
    if isinstance(environment, Mapping):
        params.update(environment)
    params.pop("device", None)
    command = build_train_command(
        base_model_path=PurePosixPath("/workspace/model/base.pt"),
        data_yaml_path=PurePosixPath("/workspace/dataset/data.yaml"),
        params=params,
        project_dir=PurePosixPath("/workspace/output/runs"),
        run_name=f"job-{job.id}",
    )
    return command.argv[2:]


def _staging_request(
    run: DistributedTrainingRun,
    job: TrainingJob,
    rank: _Rank,
    ranks: tuple[_Rank, ...],
    framework: str,
    adapter_key: str,
    adapter_version: str,
    arguments: list[str],
    parameters: Mapping[str, Any],
    artifacts: list[dict[str, Any]],
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    if any(
        not isinstance(argument, str)
        or not argument
        or any(character in argument for character in "\x00\r\n")
        for argument in arguments
    ):
        raise ValueError("training arguments contain unsafe control characters")
    launch_spec = {
        "schema_version": "1.0",
        "training_job_id": job.id,
        "run_id": run.id,
        "attempt": run.attempt,
        "framework": framework,
        "adapter_key": adapter_key,
        "adapter_version": adapter_version,
        "entrypoint": "/usr/local/bin/visiox-train",
        "parameters": {
            "arguments": list(arguments),
            **dict(parameters),
        },
        "environment": dict(environment or {}),
        "distributed": {
            "node_id": rank.node_id,
            "gpu_uuids": list(rank.gpu_uuids),
            "node_rank": rank.node_rank,
            "nnodes": len(ranks),
            "nproc_per_node": len(rank.gpu_uuids),
            "rendezvous": {
                "master_addr": run.master_addr,
                "master_port": run.master_port,
            },
        },
        "paths": {
            "launch_spec": "/workspace/input/launch-spec.json",
            "dataset": "/workspace/input/dataset",
            "model": "/workspace/input/artifacts/base.pt",
            "checkpoint": "/workspace/input/artifacts/last.pt",
            "output": "/workspace/output",
        },
    }
    checksum = hashlib.sha256(
        json.dumps(
            launch_spec,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    return {
        "schema_version": "1.0",
        "run_id": run.id,
        "attempt": run.attempt,
        "runtime_image_digest": validate_image_digest(
            str(run.training_image_digest)
        ),
        "launch_spec": launch_spec,
        "launch_spec_checksum": checksum,
        "artifacts": artifacts,
    }


def _manifest_paths_by_name(
    manifest: _ArtifactManifestResult,
) -> dict[str, str]:
    grouped: dict[str, list[str]] = {}
    for entry in manifest.artifacts:
        grouped.setdefault(PurePosixPath(entry.path).name, []).append(entry.path)
    return {
        name: paths[0]
        for name, paths in grouped.items()
        if len(paths) == 1
    }


def _artifact_uri(job_id: str, path: str) -> str:
    name = PurePosixPath(path).name
    if name in {"best.pt", "last.pt", "adapter_model.safetensors", "adapter_config.json"}:
        return f"minio://models/trained/{job_id}/{name}"
    if name in _VISUALIZATION_ARTIFACTS:
        return f"minio://training/jobs/{job_id}/visualizations/{name}"
    return f"minio://training/jobs/{job_id}/artifacts/{path}"


def _launch_request(
    run: DistributedTrainingRun,
    rank: _Rank,
    stage: _StageResult,
    staging_request: Mapping[str, Any],
) -> dict[str, Any]:
    launch_spec = staging_request["launch_spec"]
    if not isinstance(launch_spec, Mapping):
        raise ValueError("staging launch spec is unavailable")
    return {
        "schema_version": "1.0",
        "action": "launch",
        "run_id": run.id,
        "attempt": run.attempt,
        "runtime_image_digest": validate_image_digest(
            str(run.training_image_digest)
        ),
        "framework": launch_spec["framework"],
        "adapter_key": launch_spec["adapter_key"],
        "gpu_uuids": list(rank.gpu_uuids),
        "node_rank": rank.node_rank,
        "launch_spec_checksum": staging_request["launch_spec_checksum"],
        "paths": stage.paths,
    }


def _llm_model_source(task: Task) -> str:
    payload = task.payload if isinstance(task.payload, dict) else {}
    model_reference = payload.get("model_reference")
    if not isinstance(model_reference, Mapping):
        raise ValueError("LLM model reference is unavailable")
    source = model_reference.get("source")
    if source not in {"huggingface", "modelscope"}:
        raise ValueError("LLM model source is unavailable")
    return str(source)


def _llamafactory_config(
    job: TrainingJob,
    task: Task,
    dataset: Dataset,
) -> dict[str, Any]:
    payload = task.payload if isinstance(task.payload, dict) else {}
    model_reference = payload.get("model_reference")
    if not isinstance(model_reference, Mapping):
        raise ValueError("LLM model reference is unavailable")
    model_id = model_reference.get("model_id")
    revision = model_reference.get("revision")
    if not isinstance(model_id, str) or not model_id:
        raise ValueError("LLM model id is unavailable")
    if not isinstance(revision, str) or not revision:
        raise ValueError("LLM model revision is unavailable")
    internal_fields = {
        "model_source",
        "model_id",
        "model_revision",
        "resolved_revision",
        "auto_optimize",
    }
    config = {
        key: value
        for key, value in dict(job.params or {}).items()
        if key not in internal_fields and value is not None
    }
    config.update(
        {
            "model_name_or_path": model_id,
            "model_revision": revision,
            "dataset": "visiox_train",
            "dataset_dir": "/workspace/dataset",
            "output_dir": f"/workspace/output/job-{job.id}",
            "logging_dir": f"/workspace/output/job-{job.id}/runs",
            "report_to": "tensorboard",
            "overwrite_output_dir": True,
            "do_train": True,
            "plot_loss": True,
        }
    )
    if dataset.format not in {"alpaca", "sharegpt", "openai_messages"}:
        raise ValueError("LLM dataset format is unsupported")
    return config


def _llamafactory_dataset_info(dataset: Dataset) -> dict[str, Any]:
    if dataset.format == "alpaca":
        return {
            "file_name": "train.jsonl",
            "columns": {
                "prompt": "instruction",
                "query": "input",
                "response": "output",
            },
        }
    if dataset.format == "openai_messages":
        return {
            "file_name": "train.jsonl",
            "formatting": "sharegpt",
            "columns": {"messages": "messages"},
            "tags": {
                "role_tag": "role",
                "content_tag": "content",
                "user_tag": "user",
                "assistant_tag": "assistant",
                "system_tag": "system",
            },
        }
    return {
        "file_name": "train.jsonl",
        "formatting": "sharegpt",
        "columns": {"messages": "conversations"},
        "tags": {
            "role_tag": "from",
            "content_tag": "value",
            "user_tag": "human",
            "assistant_tag": "gpt",
            "system_tag": "system",
        },
    }


def _ensure_split_directories(root: Path) -> None:
    for relative in (
        "images/train",
        "images/val",
        "images/test",
        "labels/train",
        "labels/val",
        "labels/test",
    ):
        (root / relative).mkdir(parents=True, exist_ok=True)


def _set_container_dataset_root(root: Path) -> None:
    data_yaml = root / "data.yaml"
    payload = yaml.safe_load(data_yaml.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("exported dataset configuration is invalid")
    payload["path"] = "/workspace/dataset"
    data_yaml.write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()
