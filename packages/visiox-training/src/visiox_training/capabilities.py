from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_serializer,
    field_validator,
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


class ParameterCapability(ImmutableCapability):
    name: str = Field(min_length=1)
    value_type: Literal["string", "integer", "number", "boolean"]
    required: bool = False
    default: JsonValue = None
    minimum: float | int | None = None
    maximum: float | int | None = None
    choices: Sequence[str | int | float | bool] = ()
    description: str | None = None

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
    cpu_cores_min: int = Field(default=1, ge=0)
    memory_mb_min: int = Field(default=0, ge=0)
    gpu_count_min: int = Field(default=0, ge=0)
    gpu_memory_mb_min: int = Field(default=0, ge=0)


class TaskCapability(ImmutableCapability):
    task_type: str = Field(min_length=1)
    models: Sequence[ModelCapability] = ()
    resources: ResourceCapability = Field(default_factory=ResourceCapability)
    parameters: Sequence[ParameterCapability] = ()

    @field_validator("models", "parameters")
    @classmethod
    def freeze_sequences(cls, value: Sequence[object]) -> Sequence[object]:
        return tuple(value)


class FrameworkCapabilities(ImmutableCapability):
    adapter_key: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    framework: str = Field(min_length=1)
    tasks: Sequence[TaskCapability] = Field(min_length=1)

    @field_validator("tasks")
    @classmethod
    def freeze_tasks(cls, value: Sequence[TaskCapability]) -> Sequence[TaskCapability]:
        return tuple(value)

    def supports_task(self, task_type: str) -> bool:
        return any(task.task_type == task_type for task in self.tasks)
