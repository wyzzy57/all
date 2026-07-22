from __future__ import annotations

import hashlib
import shutil
import time
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, field_validator
from sqlalchemy.orm import Session

from visiox_db.models import (
    BaseModel as StoredBaseModel,
    Dataset,
    DistributedTrainingRun,
    RemoteExecution,
    Task,
    TrainingJob,
    TrainingPipeline,
    TrainedModel,
)
from visiox_storage.client import ObjectStorageClient
from visiox_yolo26.converters import export_yolo26_dataset
from visiox_yolo26.training.commands import build_train_command

from .deployment import _DeploymentHandlerBase, validate_image_digest
from .startup import EdgeExecutorSecurityContext
from .state import ExecutionResult


_PRESIGNED_URL_TTL = timedelta(minutes=30)


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


class _CollectedArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    checksum: str
    size_bytes: int


class _CollectResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    artifacts: dict[str, _CollectedArtifact]


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
        self._stage = _ScriptRunner("stage_training.sh", session_factory, security)
        self._launch = _ScriptRunner("launch_rank.sh", session_factory, security)
        self._stop = _ScriptRunner("stop_training.sh", session_factory, security)

    def execute(self, execution: RemoteExecution) -> ExecutionResult:
        launched: list[tuple[str, int]] = []
        try:
            context = self._load(execution)
            run, job, task, pipeline, base_model, dataset = context
            ranks = tuple(_Rank.model_validate(item) for item in run.ranks)
            if not ranks or {rank.node_id for rank in ranks} != set(run.node_ids):
                raise ValueError("distributed rank plan is incomplete")
            self._transition(execution.id, "staging", 20)
            artifacts = self._prepare_artifacts(run, base_model, dataset)
            staged: dict[str, _StageResult] = {}
            for rank in ranks:
                target = self._stage._load_target(rank.node_id)
                response = self._stage._run_script(
                    target,
                    _staging_request(run, artifacts),
                )
                staged[rank.node_id] = _StageResult.model_validate(response)

            self._transition(execution.id, "launching", 55)
            arguments = _training_arguments(job, task)
            for rank in ranks:
                stage = staged[rank.node_id]
                target = self._launch._load_target(rank.node_id)
                response = self._launch._run_script(
                    target,
                    _launch_request(run, rank, ranks, stage, arguments),
                )
                launched_rank = _LaunchResult.model_validate(response)
                if launched_rank.node_rank != rank.node_rank:
                    raise ValueError("remote rank did not match the persisted plan")
                launched.append((rank.node_id, rank.node_rank))
                self._persist_container(run.id, launched_rank.container_id)

            self._mark_training(execution.id)
            containers = tuple(
                (rank, container_id)
                for rank, container_id in zip(ranks, self._container_ids(run.id), strict=True)
            )
            if not self._wait_for_completion(execution.id, containers):
                if self._cancel_requested(execution.id):
                    return ExecutionResult.succeeded(phase="canceled")
                self._stop_peers(execution, launched)
                self._try_collect_checkpoint(run, ranks[0], staged[ranks[0].node_id])
                self._mark_failed(execution.id)
                return ExecutionResult.failed(
                    error_code="EDGE_TRAIN_FAILED",
                    error_message="Distributed edge training failed",
                    phase="failed",
                )
            collected = self._collect_rank_zero(run, job, ranks[0], staged[ranks[0].node_id])
            self._mark_succeeded(execution.id, collected)
            return ExecutionResult.succeeded(phase="succeeded")
        except Exception:
            self._stop_peers(execution, launched)
            self._mark_failed(execution.id)
            return ExecutionResult.failed(
                error_code="EDGE_TRAIN_FAILED",
                error_message="Distributed edge training failed",
                phase="failed",
            )

    def _load(self, execution: RemoteExecution):
        with self._session_factory() as session:
            current = session.get(RemoteExecution, execution.id)
            if current is None or current.status != "running" or current.resource_id is None:
                raise ValueError("distributed execution is not runnable")
            run = session.get(DistributedTrainingRun, current.resource_id)
            job = session.get(TrainingJob, current.training_job_id)
            task = session.get(Task, current.task_id)
            pipeline = session.get(TrainingPipeline, job.pipeline_id if job else None)
            base_model = session.get(StoredBaseModel, pipeline.base_model_id if pipeline else None)
            dataset = session.get(Dataset, pipeline.dataset_id if pipeline else None)
            if not all((run, job, task, pipeline, base_model, dataset)):
                raise ValueError("distributed training resources are incomplete")
            session.expunge_all()
            return run, job, task, pipeline, base_model, dataset

    def _prepare_artifacts(
        self,
        run: DistributedTrainingRun,
        base_model: StoredBaseModel,
        dataset: Dataset,
    ) -> list[dict[str, str]]:
        checksum = (base_model.checksum or "").lower()
        if len(checksum) != 64 or any(char not in "0123456789abcdef" for char in checksum):
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
            archive = Path(shutil.make_archive(str(root / "dataset"), "gztar", dataset_dir))
            dataset_checksum = _sha256(archive)
            dataset_uri = self._storage.put_file(
                "training",
                f"distributed/{run.id}/{run.attempt}/dataset.tar.gz",
                archive,
                content_type="application/gzip",
            )
        artifacts = [
            {
                "name": "model",
                "download_url": self._storage.presigned_get_url(base_model.local_uri, expires=_PRESIGNED_URL_TTL),
                "checksum": checksum,
                "filename": "base.pt",
            },
            {
                "name": "dataset",
                "download_url": self._storage.presigned_get_url(dataset_uri, expires=_PRESIGNED_URL_TTL),
                "checksum": dataset_checksum,
                "filename": "dataset.tar.gz",
            },
        ]
        if run.checkpoint_uri and run.checkpoint_checksum:
            artifacts.append(
                {
                    "name": "checkpoint",
                    "download_url": self._storage.presigned_get_url(run.checkpoint_uri, expires=_PRESIGNED_URL_TTL),
                    "checksum": run.checkpoint_checksum,
                    "filename": "last.pt",
                }
            )
        return artifacts

    def _transition(self, execution_id: str, phase: str, progress: int) -> None:
        with self._session_factory() as session:
            execution = session.get(RemoteExecution, execution_id)
            run = session.get(DistributedTrainingRun, execution.resource_id if execution else None)
            job = session.get(TrainingJob, execution.training_job_id if execution else None)
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
    ) -> bool:
        while True:
            if self._cancel_requested(execution_id):
                return False
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
                if result.exit_code != 0:
                    return False
                completed += 1
            if completed == len(containers):
                return True
            time.sleep(5)

    def _cancel_requested(self, execution_id: str) -> bool:
        with self._session_factory() as session:
            execution = session.get(RemoteExecution, execution_id)
            run = session.get(DistributedTrainingRun, execution.resource_id if execution else None)
            return run is None or run.status in {"stopping", "canceled"}

    def _collect_rank_zero(
        self,
        run: DistributedTrainingRun,
        job: TrainingJob,
        rank: _Rank,
        staged: _StageResult,
    ) -> _CollectResult:
        uris = {
            "best.pt": f"minio://models/trained/{job.id}/best.pt",
            "last.pt": f"minio://models/trained/{job.id}/last.pt",
            "results.csv": f"minio://training/jobs/{job.id}/results.csv",
            "results.png": f"minio://training/jobs/{job.id}/results.png",
        }
        uploads = {
            name: self._storage.presigned_put_url(uri, expires=_PRESIGNED_URL_TTL)
            for name, uri in uris.items()
        }
        target = self._launch._load_target(rank.node_id)
        result = _CollectResult.model_validate(
            self._launch._run_script(
                target,
                {
                    "action": "collect",
                    "output_path": staged.paths["output"],
                    "uploads": uploads,
                },
            )
        )
        if not {"best.pt", "last.pt"}.issubset(result.artifacts):
            raise ValueError("rank zero did not upload required weights")
        return result

    def _try_collect_checkpoint(
        self,
        run: DistributedTrainingRun,
        rank: _Rank,
        staged: _StageResult,
    ) -> None:
        checkpoint_uri = f"minio://training/checkpoints/{run.id}/{run.attempt}/last.pt"
        try:
            target = self._launch._load_target(rank.node_id)
            result = _CollectResult.model_validate(
                self._launch._run_script(
                    target,
                    {
                        "action": "collect",
                        "output_path": staged.paths["output"],
                        "uploads": {
                            "last.pt": self._storage.presigned_put_url(
                                checkpoint_uri,
                                expires=_PRESIGNED_URL_TTL,
                            )
                        },
                    },
                )
            )
            artifact = result.artifacts.get("last.pt")
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
            run = session.get(DistributedTrainingRun, execution.resource_id if execution else None)
            job = session.get(TrainingJob, execution.training_job_id if execution else None)
            task = session.get(Task, execution.task_id if execution else None)
            pipeline = session.get(TrainingPipeline, job.pipeline_id if job else None)
            if not all((execution, run, job, task, pipeline)):
                raise ValueError("distributed completion state is incomplete")
            weights = {
                name: f"minio://models/trained/{job.id}/{name}"
                for name in ("best.pt", "last.pt")
            }
            visualizations = {
                name: f"minio://training/jobs/{job.id}/{name}"
                for name in ("results.csv", "results.png")
                if name in collected.artifacts
            }
            best = TrainedModel(
                pipeline_id=pipeline.id,
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
            job.metrics = {**(job.metrics or {}), "weights": weights, "visualizations": visualizations}
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

    def _stop_peers(self, execution: RemoteExecution, launched: list[tuple[str, int]]) -> None:
        with self._session_factory() as session:
            run = session.get(DistributedTrainingRun, execution.resource_id)
            if run is None:
                return
            attempt = run.attempt
            run_id = run.id
        for node_id, _rank in launched:
            try:
                target = self._stop._load_target(node_id)
                self._stop._run_script(target, {"run_id": run_id, "attempt": attempt})
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
            session.commit()


class StopDistributedTrainingHandler(DistributedTrainingHandler):
    def execute(self, execution: RemoteExecution) -> ExecutionResult:
        try:
            with self._session_factory() as session:
                current = session.get(RemoteExecution, execution.id)
                run = session.get(DistributedTrainingRun, current.resource_id if current else None)
                if current is None or run is None:
                    raise ValueError("distributed run was not found")
                nodes = tuple(run.node_ids)
                run_id = run.id
                attempt = run.attempt
            for node_id in nodes:
                target = self._stop._load_target(node_id)
                _StopResult.model_validate(
                    self._stop._run_script(target, {"run_id": run_id, "attempt": attempt})
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
                run = session.get(DistributedTrainingRun, current.resource_id if current else None)
                job = session.get(TrainingJob, current.training_job_id if current else None)
                task = session.get(Task, current.task_id if current else None)
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
        "resume_training": DistributedTrainingHandler(session_factory, security, storage),
        "stop_training": StopDistributedTrainingHandler(session_factory, security, storage),
    }


def _training_arguments(job: TrainingJob, task: Task) -> list[str]:
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
    artifacts: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "run_id": run.id,
        "attempt": run.attempt,
        "image_digest": validate_image_digest(str(run.training_image_digest)),
        "artifacts": artifacts,
    }


def _launch_request(
    run: DistributedTrainingRun,
    rank: _Rank,
    ranks: tuple[_Rank, ...],
    stage: _StageResult,
    arguments: list[str],
) -> dict[str, Any]:
    return {
        "action": "launch",
        "run_id": run.id,
        "attempt": run.attempt,
        "image_digest": validate_image_digest(str(run.training_image_digest)),
        "node_id": rank.node_id,
        "gpu_uuids": list(rank.gpu_uuids),
        "node_rank": rank.node_rank,
        "nnodes": len(ranks),
        "nproc_per_node": len(rank.gpu_uuids),
        "master_addr": run.master_addr,
        "master_port": run.master_port,
        "training_arguments": arguments,
        "paths": stage.paths,
    }


def _ensure_split_directories(root: Path) -> None:
    for relative in ("images/train", "images/val", "images/test", "labels/train", "labels/val", "labels/test"):
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
