from __future__ import annotations

from collections.abc import Mapping, Sequence
import re
from typing import Any, Literal

from typing_extensions import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_serializer,
    field_validator,
    model_validator,
)

from visiox_training.contracts import _freeze_json, _thaw_json


class ImmutableCapability(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )

    def model_copy(
        self,
        *,
        update: Mapping[str, Any] | None = None,
        deep: bool = False,
    ) -> Self:
        if update is None:
            return super().model_copy(deep=deep)
        values = self.model_dump(mode="python")
        values.update(update)
        return type(self).model_validate(values)


class ModelCapability(ImmutableCapability):
    model_key: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    runtime_id: str | None = None
    family: str | None = None
    variant: str | None = None
    source: str | None = None
    revision: str | None = None
    sources: Sequence[str] = ()
    config_format: Literal["yaml"] | None = None
    config_template: str | None = None
    basic_parameter_names: Sequence[str] = ()
    managed_parameter_names: Sequence[str] = ()

    @field_validator(
        "sources",
        "basic_parameter_names",
        "managed_parameter_names",
    )
    @classmethod
    def freeze_sequences(cls, value: Sequence[str]) -> Sequence[str]:
        return tuple(value)


class RuntimeComponentCapability(ImmutableCapability):
    key: str = Field(min_length=1)
    value: str = Field(min_length=1)


class ParameterCapability(ImmutableCapability):
    name: str = Field(min_length=1)
    value_type: Literal["string", "integer", "number", "boolean"]
    required: bool = False
    default: JsonValue = None
    minimum: float | int | None = None
    maximum: float | int | None = None
    choices: Sequence[str | int | float | bool] = ()
    description: str | None = None
    help_text: str | None = None
    advanced_group: str | None = None

    @field_validator("default")
    @classmethod
    def freeze_default(cls, value: JsonValue) -> JsonValue:
        return _freeze_json(value)

    @field_serializer("default")
    def serialize_default(self, value: JsonValue) -> JsonValue:
        return _thaw_json(value)

    @field_validator("choices")
    @classmethod
    def freeze_choices(
        cls, value: Sequence[str | int | float | bool]
    ) -> Sequence[str | int | float | bool]:
        return tuple(value)


class ResourceCapability(ImmutableCapability):
    resource_kinds: Sequence[Literal["cpu", "cuda"]] = ()
    cpu_cores_min: int = Field(default=1, ge=0)
    memory_mb_min: int = Field(default=0, ge=0)
    gpu_count_min: int = Field(default=0, ge=0)
    gpu_memory_mb_min: int = Field(default=0, ge=0)

    @field_validator("resource_kinds")
    @classmethod
    def freeze_resource_kinds(
        cls, value: Sequence[Literal["cpu", "cuda"]]
    ) -> Sequence[Literal["cpu", "cuda"]]:
        return tuple(value)


OperationName = Literal[
    "train",
    "stop",
    "resume",
    "evaluate",
    "image_inference",
    "export",
    "deploy",
]


class OperationCapability(ImmutableCapability):
    name: OperationName
    supported: bool
    implemented: bool
    available: bool
    unavailable_reason: str | None = None

    @field_validator("unavailable_reason")
    @classmethod
    def validate_reason(cls, value: str | None) -> str | None:
        if value == "":
            raise ValueError("unavailable_reason must not be empty")
        return value

    @model_validator(mode="after")
    def validate_availability(self) -> OperationCapability:
        if self.implemented and not self.supported:
            raise ValueError("an unsupported operation cannot be implemented")
        if self.available and not self.supported:
            raise ValueError("an unsupported operation cannot be available")
        if self.available and not self.implemented:
            raise ValueError("an unimplemented operation cannot be available")
        if self.available and self.unavailable_reason is not None:
            raise ValueError("an available operation cannot have an unavailable reason")
        if not self.available and self.unavailable_reason is None:
            raise ValueError("an unavailable operation requires an unavailable reason")
        return self


class TaskCapability(ImmutableCapability):
    task_type: str = Field(min_length=1)
    models: Sequence[ModelCapability] = ()
    accepted_dataset_formats: Sequence[str] = ()
    convertible_dataset_formats: Sequence[str] = ()
    resources: ResourceCapability = Field(default_factory=ResourceCapability)
    parameters: Sequence[ParameterCapability] = ()
    operations: Sequence[OperationCapability] = ()
    observable_metrics: Sequence[str] = ()
    observable_artifacts: Sequence[str] = ()

    @field_validator(
        "models",
        "accepted_dataset_formats",
        "convertible_dataset_formats",
        "parameters",
        "operations",
        "observable_metrics",
        "observable_artifacts",
    )
    @classmethod
    def freeze_sequences(cls, value: Sequence[object]) -> Sequence[object]:
        return tuple(value)


class FrameworkCapabilities(ImmutableCapability):
    adapter_key: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    framework: str = Field(min_length=1)
    display_name: str | None = None
    framework_version: str | None = None
    framework_version_constraint: str | None = None
    base_image_reference: str | None = None
    runtime_components: Sequence[RuntimeComponentCapability] = ()
    training_runtime_image_digest: str | None = None
    inference_runtime_image_digest: str | None = None
    availability_baseline_operation: OperationName = "train"
    available: bool = True
    unavailable_reason: str | None = None
    tasks: Sequence[TaskCapability] = Field(min_length=1)

    @field_validator("tasks", "runtime_components")
    @classmethod
    def freeze_sequences(cls, value: Sequence[object]) -> Sequence[object]:
        return tuple(value)

    @field_validator("training_runtime_image_digest", "inference_runtime_image_digest")
    @classmethod
    def validate_runtime_image_digest(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _canonical_oci_digest_reference(value)

    def supports_task(self, task_type: str) -> bool:
        return any(task.task_type == task_type for task in self.tasks)

    @model_validator(mode="after")
    def validate_availability(self) -> FrameworkCapabilities:
        if self.available and self.unavailable_reason is not None:
            raise ValueError("an available adapter cannot have an unavailable reason")
        if not self.available and self.unavailable_reason is None:
            raise ValueError("an unavailable adapter requires an unavailable reason")
        return self


_OCI_DIGEST_PATTERN = re.compile(r"[0-9a-fA-F]{64}\Z")
_OCI_PATH_COMPONENT_PATTERN = re.compile(r"[a-z0-9]+(?:[._-][a-z0-9]+)*\Z")
_OCI_HOST_LABEL_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")


def _canonical_oci_digest_reference(value: str) -> str:
    if (
        not value
        or not value.isascii()
        or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in value
        )
        or "://" in value
        or value.count("@") != 1
    ):
        raise ValueError("invalid OCI digest reference")

    name, digest_part = value.split("@", 1)
    algorithm, separator, digest = digest_part.partition(":")
    if (
        algorithm != "sha256"
        or not separator
        or _OCI_DIGEST_PATTERN.fullmatch(digest) is None
    ):
        raise ValueError("invalid OCI digest")
    if not name or len(name) > 255 or name != name.lower():
        raise ValueError("invalid OCI repository name")

    components = name.split("/")
    if any(not component for component in components):
        raise ValueError("invalid OCI repository component")
    first = components[0]
    if ":" in first:
        if first.count(":") != 1 or len(components) < 2:
            raise ValueError("invalid OCI registry port")
        host, port = first.split(":", 1)
        if (
            not port.isdecimal()
            or not 1 <= int(port) <= 65535
            or not _valid_oci_host(host)
        ):
            raise ValueError("invalid OCI registry port")
        path_components = components[1:]
    else:
        path_components = components
    if any(":" in component for component in path_components):
        raise ValueError("OCI tags are not allowed in immutable runtime references")
    if any(
        _OCI_PATH_COMPONENT_PATTERN.fullmatch(component) is None
        for component in path_components
    ):
        raise ValueError("invalid OCI repository component")
    return f"{name}@sha256:{digest.lower()}"


def _valid_oci_host(host: str) -> bool:
    return bool(host) and all(
        _OCI_HOST_LABEL_PATTERN.fullmatch(label) is not None
        for label in host.split(".")
    )


def runtime_image_readiness(value: str, env_name: str) -> tuple[str | None, str | None]:
    if not value:
        return None, f"Configure {env_name} with an immutable image digest"
    try:
        canonical = _canonical_oci_digest_reference(value)
    except ValueError:
        return None, (
            f"{env_name} must be a Docker/OCI repository name followed by "
            "@sha256:<64 hex>"
        )
    return canonical, None


def operation_capabilities(
    *,
    supported: set[OperationName],
    implemented: set[OperationName] | None = None,
    training_unavailable_reason: str | None,
    inference_unavailable_reason: str | None,
) -> tuple[OperationCapability, ...]:
    implemented = implemented or set()
    names: tuple[OperationName, ...] = (
        "train",
        "stop",
        "resume",
        "evaluate",
        "image_inference",
        "export",
        "deploy",
    )
    result = []
    for name in names:
        is_supported = name in supported
        is_implemented = name in implemented
        runtime_reason = (
            inference_unavailable_reason
            if name in {"image_inference", "deploy"}
            else training_unavailable_reason
        )
        if not is_supported:
            reason = f"{name} is not supported by this adapter"
        elif not is_implemented:
            reason = "No runtime implementation is registered for this adapter operation"
            if runtime_reason is not None:
                reason = f"{reason}; runtime unavailable: {runtime_reason}"
        else:
            reason = runtime_reason
        result.append(
            OperationCapability(
                name=name,
                supported=is_supported,
                implemented=is_implemented,
                available=is_supported and is_implemented and reason is None,
                unavailable_reason=reason,
            )
        )
    return tuple(result)


def baseline_operation_availability(
    operations: Sequence[OperationCapability],
    baseline: OperationName = "train",
) -> tuple[bool, str | None]:
    operation = next(item for item in operations if item.name == baseline)
    return operation.available, operation.unavailable_reason
