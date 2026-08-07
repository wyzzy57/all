from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from packaging.version import InvalidVersion, Version
from sqlalchemy.orm import Session

from visiox_api.services.framework_adapters import FrameworkAdapterCatalog
from visiox_api.services.llm_training import (
    LlmTrainingConfigError,
    validate_llamafactory_config,
    validate_llm_dataset,
    validate_llm_environment,
)
from visiox_db.models import BaseModel, Dataset, TrainingPipeline
from visiox_training.capabilities import (
    ModelCapability,
    ParameterCapability,
    TaskCapability,
)
from visiox_training.errors import FrameworkAdapterError
from visiox_yolo26.training.params import (
    TrainingParamsError,
    validate_training_environment,
    validate_training_params,
)
from visiox_yolo26.training.prechecks import (
    TrainingPrecheckError,
    validate_training_resources,
)


IDENTITY_LOCK_DETAIL = (
    "Pipeline framework identity is locked; clone the pipeline to change it"
)


class PipelineConfigurationError(ValueError):
    def __init__(self, detail: str, *, status_code: int = 422) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code


@dataclass(frozen=True)
class ResolvedPipelineConfiguration:
    engine: str
    task: str
    scale: str
    task_kind: str
    framework: str
    adapter_key: str
    adapter_version: str
    model_family: str
    recipe: dict[str, Any]
    base_model_id: str | None
    dataset_id: str | None
    params_template: dict[str, Any]
    default_environment: dict[str, Any]
    status: str

    @property
    def identity(self) -> tuple[str, ...]:
        model = self.recipe.get("model") or {}
        return tuple(
            value.casefold()
            for value in (
                self.task_kind,
                self.framework,
                self.adapter_key,
                self.adapter_version,
                self.model_family,
                str(model.get("key", "")),
                self.engine,
                self.task,
                self.scale,
            )
        )


class PipelineConfigurationService:
    def __init__(self, catalog: FrameworkAdapterCatalog) -> None:
        self._registry = catalog.registry

    def resolve_create(
        self,
        session: Session,
        values: dict[str, Any],
        fields_set: set[str],
    ) -> ResolvedPipelineConfiguration:
        return self._resolve(session, values, fields_set)

    def resolve_update(
        self,
        session: Session,
        pipeline: TrainingPipeline,
        changes: dict[str, Any],
        fields_set: set[str],
    ) -> ResolvedPipelineConfiguration:
        current = self.from_pipeline(pipeline)
        self._reject_locked_identity_changes(pipeline, changes, fields_set)
        values = {
            "engine": pipeline.engine,
            "task": pipeline.task,
            "scale": pipeline.scale,
            "task_kind": pipeline.task_kind,
            "framework": pipeline.framework,
            "adapter_key": pipeline.adapter_key,
            "adapter_version": pipeline.adapter_version,
            "model_family": pipeline.model_family,
            "recipe": dict(pipeline.recipe or {}),
            "base_model_id": pipeline.base_model_id,
            "dataset_id": pipeline.dataset_id,
            "params_template": dict(pipeline.params_template or {}),
            "default_environment": dict(pipeline.default_environment or {}),
        }
        values.update(
            {key: value for key, value in changes.items() if key in fields_set}
        )
        resolved = self._resolve(session, values, fields_set, current=current)
        if (
            pipeline.framework_locked_at is not None
            or pipeline.first_submitted_job_id is not None
        ) and (resolved.identity != current.identity):
            raise PipelineConfigurationError(IDENTITY_LOCK_DETAIL, status_code=409)
        return resolved

    @staticmethod
    def has_effective_change(
        current: ResolvedPipelineConfiguration,
        resolved: ResolvedPipelineConfiguration,
    ) -> bool:
        return current.identity != resolved.identity or any(
            getattr(current, field) != getattr(resolved, field)
            for field in (
                "recipe",
                "base_model_id",
                "dataset_id",
                "params_template",
                "default_environment",
            )
        )

    @staticmethod
    def _reject_locked_identity_changes(
        pipeline: TrainingPipeline,
        changes: dict[str, Any],
        fields_set: set[str],
    ) -> None:
        if (
            pipeline.framework_locked_at is None
            and pipeline.first_submitted_job_id is None
        ):
            return
        current_values = {
            "task_kind": pipeline.task_kind,
            "framework": pipeline.framework,
            "adapter_key": pipeline.adapter_key,
            "adapter_version": pipeline.adapter_version,
            "model_family": pipeline.model_family,
            "engine": pipeline.engine,
            "task": pipeline.task,
            "scale": pipeline.scale,
        }
        for field, current_value in current_values.items():
            if field not in fields_set:
                continue
            requested_value = changes.get(field)
            if field == "adapter_version":
                try:
                    equivalent = Version(str(requested_value)) == Version(current_value)
                except InvalidVersion:
                    equivalent = False
            else:
                equivalent = (
                    requested_value is not None
                    and str(requested_value).casefold() == current_value.casefold()
                )
            if not equivalent:
                raise PipelineConfigurationError(IDENTITY_LOCK_DETAIL, status_code=409)

    def from_pipeline(
        self, pipeline: TrainingPipeline
    ) -> ResolvedPipelineConfiguration:
        return ResolvedPipelineConfiguration(
            engine=pipeline.engine,
            task=pipeline.task,
            scale=pipeline.scale,
            task_kind=pipeline.task_kind,
            framework=pipeline.framework,
            adapter_key=pipeline.adapter_key,
            adapter_version=pipeline.adapter_version,
            model_family=pipeline.model_family,
            recipe=dict(pipeline.recipe or {}),
            base_model_id=pipeline.base_model_id,
            dataset_id=pipeline.dataset_id,
            params_template=dict(pipeline.params_template or {}),
            default_environment=dict(pipeline.default_environment or {}),
            status=pipeline.status,
        )

    def apply(
        self, pipeline: TrainingPipeline, configuration: ResolvedPipelineConfiguration
    ) -> None:
        for field in (
            "engine",
            "task",
            "scale",
            "task_kind",
            "framework",
            "adapter_key",
            "adapter_version",
            "model_family",
            "recipe",
            "base_model_id",
            "dataset_id",
            "params_template",
            "default_environment",
            "status",
        ):
            setattr(pipeline, field, getattr(configuration, field))

    def _resolve(
        self,
        session: Session,
        values: dict[str, Any],
        fields_set: set[str],
        *,
        current: ResolvedPipelineConfiguration | None = None,
    ) -> ResolvedPipelineConfiguration:
        values = dict(values)
        fields_set = set(fields_set)
        task_kind, framework = self._resolve_task_framework(values, fields_set, current)
        if (
            framework == "ultralytics"
            and "base_model_id" in fields_set
            and values.get("base_model_id") is not None
        ):
            base_model = session.get(BaseModel, values["base_model_id"])
            if base_model is not None:
                if "task" not in fields_set:
                    values["task"] = base_model.task
                    fields_set.add("task")
                if "scale" not in fields_set:
                    values["scale"] = base_model.scale
                    fields_set.add("scale")
        requested_adapter_key = values.get("adapter_key")
        requested_adapter_version = values.get("adapter_version")
        if current is not None and current.framework != framework:
            if "adapter_key" not in fields_set:
                requested_adapter_key = None
            if "adapter_version" not in fields_set:
                requested_adapter_version = None
        try:
            adapter = self._registry.resolve(
                task_type=task_kind,
                framework=framework,
                adapter_key=requested_adapter_key,
                adapter_version=requested_adapter_version,
            )
        except FrameworkAdapterError as exc:
            raise PipelineConfigurationError(str(exc)) from exc

        task_capability = next(
            task for task in adapter.capabilities.tasks if task.task_type == task_kind
        )
        model = self._resolve_model(task_capability, values, fields_set, current)
        if values.get("engine") == "yolo26" and "model_family" not in fields_set:
            family = "yolo26"
        else:
            family = (
                model.family
                if model is not None and model.family
                else values.get("model_family")
            )
        if not family:
            family = "legacy-unspecified"
        if "model_family" in fields_set and model is not None:
            claimed = str(values["model_family"])
            if claimed.casefold() != str(model.family).casefold():
                raise PipelineConfigurationError(
                    "model_family does not match the selected model"
                )

        engine, task, scale = self._legacy_identity(framework, model, values, current)
        self._validate_legacy_claims(values, fields_set, engine, task, scale, framework)
        recipe = self._resolved_recipe(values.get("recipe"), model)
        base_model_id = values.get("base_model_id")
        dataset_id = values.get("dataset_id")
        params = dict(values.get("params_template") or {})
        environment = dict(values.get("default_environment") or {})

        if framework == "ultralytics":
            params, environment, pipeline_status = self._validate_ultralytics(
                session,
                task=task,
                scale=scale,
                base_model_id=base_model_id,
                dataset_id=dataset_id,
                params=params,
                environment=environment,
            )
        elif framework == "llamafactory":
            params, environment, pipeline_status = self._validate_llamafactory(
                session,
                base_model_id=base_model_id,
                dataset_id=dataset_id,
                params=params,
                environment=environment,
            )
            base_model_id = None
        elif framework == "paddlex":
            params, environment = self._validate_paddlex(
                task_capability, params, environment
            )
            if base_model_id is not None:
                raise PipelineConfigurationError(
                    "PaddleX official models do not use base_model_id until managed artifacts are supported"
                )
            if dataset_id is not None:
                dataset = session.get(Dataset, dataset_id)
                if dataset is None:
                    raise PipelineConfigurationError("Dataset not found")
                if dataset.task != "detect":
                    raise PipelineConfigurationError(
                        "PaddleX dataset task must be detect"
                    )
                if dataset.status != "validated":
                    raise PipelineConfigurationError(
                        "PaddleX dataset must be validated"
                    )
            pipeline_status = "draft"
        else:  # Registry resolution should make this unreachable.
            raise PipelineConfigurationError(f"Unsupported framework {framework!r}")

        return ResolvedPipelineConfiguration(
            engine=engine,
            task=task,
            scale=scale,
            task_kind=task_kind,
            framework=framework,
            adapter_key=adapter.adapter_key,
            adapter_version=adapter.adapter_version,
            model_family=str(family),
            recipe=recipe,
            base_model_id=base_model_id,
            dataset_id=dataset_id,
            params_template=params,
            default_environment=environment,
            status=pipeline_status,
        )

    def _resolve_task_framework(
        self,
        values: dict[str, Any],
        fields_set: set[str],
        current: ResolvedPipelineConfiguration | None,
    ) -> tuple[str, str]:
        engine = values.get("engine")
        explicit_task = values.get("task_kind") if "task_kind" in fields_set else None
        explicit_framework = (
            values.get("framework") if "framework" in fields_set else None
        )
        if engine and ("engine" in fields_set or current is None):
            try:
                mapping = self._registry.legacy_engine_mapping(engine)
            except FrameworkAdapterError:
                if engine == "paddlex":
                    legacy_task, legacy_framework = "object_detection", "paddlex"
                else:
                    raise PipelineConfigurationError(
                        f"Unknown legacy engine {engine!r}"
                    ) from None
            else:
                legacy_task, legacy_framework = mapping.task_type, mapping.framework
        elif current is not None:
            legacy_task, legacy_framework = current.task_kind, current.framework
        elif not explicit_framework:
            legacy_task, legacy_framework = "object_detection", "ultralytics"
        else:
            legacy_task, legacy_framework = None, None
        task_kind = explicit_task or legacy_task
        framework = explicit_framework or legacy_framework
        if not task_kind or not framework:
            raise PipelineConfigurationError("task_kind and framework are required")
        if (
            "task_kind" in fields_set
            and "engine" in fields_set
            and legacy_task
            and explicit_task != legacy_task
        ):
            raise PipelineConfigurationError("task_kind conflicts with legacy engine")
        if (
            "framework" in fields_set
            and "engine" in fields_set
            and legacy_framework
            and explicit_framework != legacy_framework
        ):
            raise PipelineConfigurationError("framework conflicts with legacy engine")
        return str(task_kind), str(framework)

    @staticmethod
    def _resolve_model(
        task: TaskCapability,
        values: dict[str, Any],
        fields_set: set[str],
        current: ResolvedPipelineConfiguration | None,
    ) -> ModelCapability | None:
        recipe = values.get("recipe") or {}
        use_recipe_selection = (
            current is None or "recipe" in fields_set or "scale" not in fields_set
        )
        selection: Any = (
            recipe.get("model")
            if isinstance(recipe, dict) and use_recipe_selection
            else None
        )
        if isinstance(selection, dict):
            identifiers = [
                selection.get(key)
                for key in ("key", "model_key", "label", "display_name", "runtime_id")
            ]
        else:
            identifiers = [selection]
        identifiers = [str(value).casefold() for value in identifiers if value]

        if (
            not identifiers
            and values.get("scale")
            and task.task_type == "object_detection"
        ):
            identifiers.append(str(values["scale"]).casefold())
        if not identifiers and values.get("engine") == "yolo26":
            identifiers.append("n")
        if not identifiers and current is not None:
            current_model = current.recipe.get("model") or {}
            if current_model.get("key"):
                identifiers.append(str(current_model["key"]).casefold())

        matches = []
        for model in task.models:
            candidates = {
                model.model_key.casefold(),
                model.display_name.casefold(),
                str(model.runtime_id or "").casefold(),
                str(model.variant or "").casefold(),
            }
            if any(identifier in candidates for identifier in identifiers):
                matches.append(model)
        if len(matches) == 1:
            selected = matches[0]
            if isinstance(selection, dict):
                for key, actual in (
                    ("family", selected.family),
                    ("variant", selected.variant),
                    ("runtime_id", selected.runtime_id),
                ):
                    if selection.get(key) is not None and selection[key] != actual:
                        raise PipelineConfigurationError(
                            f"recipe model {key} does not match the adapter catalog"
                        )
            return selected
        if identifiers and task.task_type == "llm_sft" and isinstance(selection, dict):
            runtime_id = str(selection.get("runtime_id") or selection.get("key") or "").strip()
            if runtime_id:
                return ModelCapability(
                    model_key=str(selection.get("key") or runtime_id),
                    display_name=str(selection.get("label") or runtime_id),
                    runtime_id=runtime_id,
                    family=str(selection.get("family") or runtime_id),
                    variant=str(selection.get("variant") or "custom"),
                    source=str(selection.get("source") or "external"),
                    revision=str(selection.get("revision") or "main"),
                )
        if identifiers and task.models:
            raise PipelineConfigurationError(
                "Selected model is not supported by the adapter"
            )
        if len(task.models) == 1:
            return task.models[0]
        return None

    @staticmethod
    def _legacy_identity(
        framework: str,
        model: ModelCapability | None,
        values: dict[str, Any],
        current: ResolvedPipelineConfiguration | None,
    ) -> tuple[str, str, str]:
        if framework == "ultralytics":
            return (
                "yolo26",
                "detect",
                str(model.variant if model else values.get("scale") or "n").lower(),
            )
        if framework == "paddlex":
            return (
                "paddlex",
                "detect",
                str(model.variant if model else values.get("scale") or "n").lower(),
            )
        scale = (
            str(model.variant)
            if model and model.variant
            else str(values.get("scale") or "llm")
        )
        if (
            current is not None
            and "scale" not in values
            and current.framework == framework
        ):
            scale = current.scale
        return "llamafactory", "llm", scale

    @staticmethod
    def _validate_legacy_claims(
        values: dict[str, Any],
        fields_set: set[str],
        engine: str,
        task: str,
        scale: str,
        framework: str,
    ) -> None:
        if "engine" in fields_set and values.get("engine") != engine:
            raise PipelineConfigurationError(
                "engine conflicts with explicit framework identity"
            )
        if "task" in fields_set and values.get("task") != task:
            raise PipelineConfigurationError("task conflicts with task_kind")
        if "scale" in fields_set:
            claimed = str(values.get("scale"))
            accepted = {scale.casefold()}
            if framework == "llamafactory":
                accepted.add("llm")
            if claimed.casefold() not in accepted:
                raise PipelineConfigurationError(
                    "scale conflicts with the selected model"
                )

    @staticmethod
    def _resolved_recipe(recipe: Any, model: ModelCapability | None) -> dict[str, Any]:
        result = dict(recipe or {})
        result.pop("model", None)
        if model is not None:
            result["model"] = {
                "key": model.model_key,
                "label": model.display_name,
                "runtime_id": model.runtime_id,
                "family": model.family,
                "variant": model.variant,
            }
        return result

    @staticmethod
    def _validate_ultralytics(
        session: Session,
        *,
        task: str,
        scale: str,
        base_model_id: str | None,
        dataset_id: str | None,
        params: dict[str, Any],
        environment: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        if dataset_id is not None and base_model_id is None:
            raise PipelineConfigurationError(
                "base_model_id is required when dataset_id is provided"
            )
        if base_model_id:
            base_model = session.get(BaseModel, base_model_id)
            if base_model is None:
                raise PipelineConfigurationError("Base model not found")
            if base_model.status != "ready" or not base_model.local_uri:
                raise PipelineConfigurationError(
                    "Base model is not ready", status_code=409
                )
            if (
                base_model.framework != "ultralytics"
                or base_model.task != task
                or base_model.scale.casefold() != scale.casefold()
            ):
                raise PipelineConfigurationError(
                    "Base model framework, task, or scale does not match pipeline",
                    status_code=409,
                )
        if base_model_id and dataset_id:
            try:
                validate_training_resources(
                    session,
                    task=task,
                    scale=scale,
                    base_model_id=base_model_id,
                    dataset_id=dataset_id,
                )
            except TrainingPrecheckError as exc:
                raise PipelineConfigurationError(str(exc), status_code=409) from exc
        try:
            normalized_params = validate_training_params(params)
            normalized_environment = validate_training_environment(environment)
        except TrainingParamsError as exc:
            raise PipelineConfigurationError(str(exc)) from exc
        status = "ready" if base_model_id and dataset_id else "draft"
        return normalized_params, normalized_environment, status

    @staticmethod
    def _validate_llamafactory(
        session: Session,
        *,
        base_model_id: str | None,
        dataset_id: str | None,
        params: dict[str, Any],
        environment: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any], str]:
        if base_model_id is not None:
            raise PipelineConfigurationError(
                "LLaMA-Factory pipelines use an external model reference"
            )
        try:
            normalized_params = validate_llamafactory_config(params)
            normalized_environment = validate_llm_environment(environment)
            if dataset_id:
                validate_llm_dataset(session, dataset_id)
        except LlmTrainingConfigError as exc:
            raise PipelineConfigurationError(str(exc)) from exc
        status = (
            "ready" if dataset_id and normalized_params.get("model_id") else "draft"
        )
        return normalized_params, normalized_environment, status

    @staticmethod
    def _validate_paddlex(
        task: TaskCapability,
        params: dict[str, Any],
        environment: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        schema = {parameter.name: parameter for parameter in task.parameters}
        unknown = sorted(set(params) - set(schema))
        if unknown:
            raise PipelineConfigurationError(
                f"Unknown PaddleX parameters: {', '.join(unknown)}"
            )
        for name, value in params.items():
            PipelineConfigurationService._validate_parameter(schema[name], value)
        try:
            normalized_environment = validate_training_environment(environment)
        except TrainingParamsError as exc:
            raise PipelineConfigurationError(str(exc)) from exc
        return dict(params), normalized_environment

    @staticmethod
    def _validate_parameter(parameter: ParameterCapability, value: Any) -> None:
        valid_type = {
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "string": isinstance(value, str),
            "boolean": isinstance(value, bool),
        }[parameter.value_type]
        if not valid_type:
            raise PipelineConfigurationError(
                f"PaddleX parameter {parameter.name} must be {parameter.value_type}"
            )
        if parameter.minimum is not None and value < parameter.minimum:
            raise PipelineConfigurationError(
                f"PaddleX parameter {parameter.name} must be at least {parameter.minimum}"
            )
        if parameter.maximum is not None and value > parameter.maximum:
            raise PipelineConfigurationError(
                f"PaddleX parameter {parameter.name} must be at most {parameter.maximum}"
            )
