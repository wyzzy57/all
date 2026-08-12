from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from visiox_api.services.framework_adapters import FrameworkAdapterCatalog
from visiox_api.services.pipeline_configuration import (
    PipelineConfigurationError,
    PipelineConfigurationService,
)
from visiox_common.settings import Settings
from visiox_db.models import BaseModel as StoredBaseModel
from visiox_db.models import (
    Dataset,
    DatasetVersion,
    DistributedTrainingRun,
    TrainingJob,
    TrainingPipeline,
)
from visiox_edge_executor_worker.deployment import validate_image_digest
from visiox_training.capabilities import ModelCapability, TaskCapability
from visiox_training.contracts import LaunchSpec


class TrainingSubmissionError(ValueError):
    def __init__(self, detail: str, *, status_code: int = 409) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


class ResolvedModelSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str
    id: str
    family: str
    runtime_id: str
    checksum: str | None = None
    revision: str | None = None


class ResolvedDatasetSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    version_id: str
    version: int
    format: str
    uri: str
    manifest_checksum: str


class ResolvedTrainingSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"] = "1.0"
    task_kind: str
    framework: str
    adapter_key: str
    adapter_version: str
    runtime_image_digest: str
    model: ResolvedModelSnapshot
    dataset: ResolvedDatasetSnapshot
    parameters: dict[str, Any]
    environment: dict[str, Any]
    resource_request: dict[str, Any]
    allocation: dict[str, Any]
    recipe: dict[str, Any]
    runtime_model_id: str

    def canonical_json(self) -> str:
        return _canonical_json(self.model_dump(mode="json"))


class ResolvedTrainingSubmission(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    snapshot: ResolvedTrainingSnapshot
    launch_spec: LaunchSpec
    launch_spec_checksum: str = Field(pattern=r"^[0-9a-f]{64}$")


class TrainingSubmissionService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self._session = session
        self._settings = settings
        self._catalog = FrameworkAdapterCatalog(settings)

    def normalize_parameters(
        self, pipeline: TrainingPipeline, overrides: dict[str, Any]
    ) -> dict[str, Any]:
        adapter = self._catalog.registry.resolve(
            task_type=pipeline.task_kind,
            framework=pipeline.framework,
            adapter_key=pipeline.adapter_key,
            adapter_version=pipeline.adapter_version,
        )
        task = next(
            item
            for item in adapter.capabilities.tasks
            if item.task_type == pipeline.task_kind
        )
        model = self._current_model(task, pipeline)
        try:
            template = PipelineConfigurationService._validate_model_parameters(
                pipeline.framework,
                task,
                model,
                copy.deepcopy(pipeline.params_template or {}),
            )
            override_params = PipelineConfigurationService._validate_model_parameters(
                pipeline.framework,
                task,
                model,
                copy.deepcopy(overrides),
            )
        except PipelineConfigurationError as exc:
            raise TrainingSubmissionError(exc.detail, status_code=422) from exc
        definitions = {item.name: item for item in task.parameters}
        supplied = {
            **template,
            **override_params,
        }
        unknown = sorted(set(supplied) - set(definitions))
        if unknown:
            raise TrainingSubmissionError(
                f"Unknown {pipeline.framework} training parameters: {', '.join(unknown)}",
                status_code=422,
            )
        normalized: dict[str, Any] = {
            name: copy.deepcopy(definition.default)
            for name, definition in definitions.items()
            if definition.default is not None
        }
        for name, value in supplied.items():
            definition = definitions[name]
            if definition.value_type == "integer" and (
                isinstance(value, bool) or not isinstance(value, int)
            ):
                raise TrainingSubmissionError(
                    f"{name} must be an integer", status_code=422
                )
            if definition.value_type == "number" and (
                isinstance(value, bool) or not isinstance(value, int | float)
            ):
                raise TrainingSubmissionError(
                    f"{name} must be a number", status_code=422
                )
            if definition.minimum is not None and value < definition.minimum:
                raise TrainingSubmissionError(
                    f"{name} must be at least {definition.minimum}", status_code=422
                )
            if definition.maximum is not None and value > definition.maximum:
                raise TrainingSubmissionError(
                    f"{name} must be at most {definition.maximum}", status_code=422
                )
            normalized[name] = (
                float(value) if definition.value_type == "number" else value
            )
        return normalized

    @staticmethod
    def _current_model(
        task: TaskCapability, pipeline: TrainingPipeline
    ) -> ModelCapability | None:
        recipe_model = (pipeline.recipe or {}).get("model") or {}
        identifiers = {
            str(recipe_model.get(name)).casefold()
            for name in ("key", "runtime_id")
            if isinstance(recipe_model, dict) and recipe_model.get(name)
        }
        for model in task.models:
            candidates = {
                model.model_key.casefold(),
                str(model.runtime_id or "").casefold(),
            }
            if identifiers & candidates:
                return model
        return None

    def resolve(
        self,
        *,
        pipeline: TrainingPipeline,
        job_id: str,
        attempt_number: int,
        parameters: dict[str, Any],
        environment: dict[str, Any],
        resource_request: dict[str, Any],
        allocation: dict[str, Any],
        runtime_image_digest: str | None = None,
        dataset_version_id: str | None = None,
    ) -> ResolvedTrainingSubmission:
        adapter = self._catalog.registry.resolve(
            task_type=pipeline.task_kind,
            framework=pipeline.framework,
            adapter_key=pipeline.adapter_key,
            adapter_version=pipeline.adapter_version,
        )
        digest = runtime_image_digest or (
            adapter.capabilities.training_runtime_image_digest or ""
        )
        try:
            digest = validate_image_digest(digest)
        except ValueError as exc:
            raise TrainingSubmissionError(
                f"Training runtime image is unavailable: {exc}", status_code=503
            ) from exc

        snapshot = ResolvedTrainingSnapshot(
            task_kind=pipeline.task_kind,
            framework=pipeline.framework,
            adapter_key=adapter.adapter_key,
            adapter_version=adapter.adapter_version,
            runtime_image_digest=digest,
            model=self._resolve_model(pipeline, parameters),
            dataset=self._resolve_dataset(pipeline, dataset_version_id),
            parameters=copy.deepcopy(parameters),
            environment=copy.deepcopy(environment),
            resource_request=copy.deepcopy(resource_request),
            allocation=copy.deepcopy(allocation),
            recipe=copy.deepcopy(pipeline.recipe or {}),
            runtime_model_id=self._runtime_model_id(pipeline, parameters),
        )
        snapshot_checksum = hashlib.sha256(
            snapshot.canonical_json().encode()
        ).hexdigest()
        launch_spec = LaunchSpec(
            adapter_key=adapter.adapter_key,
            adapter_version=adapter.adapter_version,
            argv=("/usr/local/bin/visiox-train",),
            env={
                "VISIOX_TRAINING_JOB_ID": job_id,
                "VISIOX_TRAINING_ATTEMPT": str(attempt_number),
                "VISIOX_RESOLVED_SNAPSHOT_SHA256": snapshot_checksum,
            },
            working_directory="workspace",
        )
        return ResolvedTrainingSubmission(
            snapshot=snapshot,
            launch_spec=launch_spec,
            launch_spec_checksum=launch_spec.canonical_checksum_sha256(),
        )

    def resolve_retry(
        self,
        *,
        job: TrainingJob,
        attempt_number: int,
        checkpoint_run: DistributedTrainingRun,
        checkpoint_framework: str | None = None,
        checkpoint_model_family: str | None = None,
    ) -> ResolvedTrainingSubmission:
        snapshot = ResolvedTrainingSnapshot.model_validate(
            copy.deepcopy(job.resolved_snapshot)
        )
        if checkpoint_run.training_job_id != job.id:
            raise TrainingSubmissionError(
                "Checkpoint is not bound to the frozen training job"
            )
        checkpoint_uri = checkpoint_run.checkpoint_uri
        checkpoint_checksum = checkpoint_run.checkpoint_checksum
        if not checkpoint_uri or not checkpoint_checksum:
            raise TrainingSubmissionError(
                "Executor-managed checkpoint URI and checksum are required"
            )
        if (
            checkpoint_framework
            and checkpoint_framework.casefold() != snapshot.framework.casefold()
        ):
            raise TrainingSubmissionError(
                "Checkpoint framework does not match the frozen training job"
            )
        if (
            checkpoint_model_family
            and checkpoint_model_family.casefold() != snapshot.model.family.casefold()
        ):
            raise TrainingSubmissionError(
                "Checkpoint model family does not match the frozen training job"
            )
        snapshot_checksum = hashlib.sha256(
            snapshot.canonical_json().encode()
        ).hexdigest()
        launch_spec = LaunchSpec(
            adapter_key=snapshot.adapter_key,
            adapter_version=snapshot.adapter_version,
            argv=("/usr/local/bin/visiox-train",),
            env={
                "VISIOX_TRAINING_JOB_ID": job.id,
                "VISIOX_TRAINING_ATTEMPT": str(attempt_number),
                "VISIOX_RESOLVED_SNAPSHOT_SHA256": snapshot_checksum,
                "VISIOX_CHECKPOINT_URI": checkpoint_uri,
                "VISIOX_CHECKPOINT_SHA256": checkpoint_checksum,
            },
            working_directory="workspace",
        )
        return ResolvedTrainingSubmission(
            snapshot=snapshot,
            launch_spec=launch_spec,
            launch_spec_checksum=launch_spec.canonical_checksum_sha256(),
        )

    def _resolve_model(
        self, pipeline: TrainingPipeline, parameters: dict[str, Any]
    ) -> ResolvedModelSnapshot:
        if pipeline.framework == "llamafactory":
            model_id = parameters.get("model_id")
            revision = parameters.get("resolved_revision")
            if not isinstance(model_id, str) or not model_id:
                raise TrainingSubmissionError("LLM model reference is unavailable")
            if not isinstance(revision, str) or not revision:
                raise TrainingSubmissionError("LLM model revision is unavailable")
            return ResolvedModelSnapshot(
                source=str(parameters.get("model_source") or "model_registry"),
                id=model_id,
                family=pipeline.model_family,
                runtime_id=model_id,
                revision=revision,
            )
        if pipeline.framework == "paddlex":
            adapter = self._catalog.registry.resolve(
                task_type=pipeline.task_kind,
                framework=pipeline.framework,
                adapter_key=pipeline.adapter_key,
                adapter_version=pipeline.adapter_version,
            )
            runtime_id = self._runtime_model_id(pipeline, parameters)
            task = next(
                item
                for item in adapter.capabilities.tasks
                if item.task_type == pipeline.task_kind
            )
            model = next(
                (item for item in task.models if item.runtime_id == runtime_id), None
            )
            if model is None:
                raise TrainingSubmissionError(
                    "PaddleX model is not in the adapter catalog"
                )
            return ResolvedModelSnapshot(
                source="paddlex",
                id=model.model_key,
                family=str(model.family),
                runtime_id=runtime_id,
                revision=model.revision,
            )
        if pipeline.framework != "ultralytics":
            raise TrainingSubmissionError(
                f"Framework {pipeline.framework!r} submission is not resolved yet"
            )
        model = self._session.get(StoredBaseModel, pipeline.base_model_id)
        if model is None:
            raise TrainingSubmissionError("Base model not found")
        if not model.checksum:
            raise TrainingSubmissionError("Base model checksum is unavailable")
        return ResolvedModelSnapshot(
            source="base_model",
            id=model.id,
            family=model.model_family,
            runtime_id=self._runtime_model_id(pipeline, parameters),
            checksum=model.checksum,
        )

    def _resolve_dataset(
        self, pipeline: TrainingPipeline, dataset_version_id: str | None
    ) -> ResolvedDatasetSnapshot:
        dataset = self._session.get(Dataset, pipeline.dataset_id)
        if dataset is None:
            raise TrainingSubmissionError("Dataset not found")
        query = select(DatasetVersion).where(DatasetVersion.dataset_id == dataset.id)
        if dataset_version_id:
            query = query.where(DatasetVersion.id == dataset_version_id)
        version = self._session.scalar(
            query.order_by(DatasetVersion.version.desc(), DatasetVersion.id.desc())
        )
        if version is None:
            raise TrainingSubmissionError("Dataset version manifest is unavailable")
        return ResolvedDatasetSnapshot(
            id=dataset.id,
            version_id=version.id,
            version=version.version,
            format=version.format,
            uri=version.object_uri,
            manifest_checksum=version.manifest_checksum,
        )

    @staticmethod
    def _runtime_model_id(
        pipeline: TrainingPipeline, parameters: dict[str, Any]
    ) -> str:
        if pipeline.framework == "llamafactory":
            model_id = parameters.get("model_id")
            if isinstance(model_id, str) and model_id:
                return model_id
        model = (pipeline.recipe or {}).get("model") or {}
        runtime_id = model.get("runtime_id") if isinstance(model, dict) else None
        if not isinstance(runtime_id, str) or not runtime_id:
            raise TrainingSubmissionError("Runtime model id is unavailable")
        return runtime_id


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
