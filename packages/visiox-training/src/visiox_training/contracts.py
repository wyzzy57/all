from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any, Literal, cast

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


_SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


class _FrozenDict(dict[str, object]):
    def __deepcopy__(self, memo: dict[int, object]) -> _FrozenDict:
        memo[id(self)] = self
        return self

    def __setitem__(self, key: str, value: object) -> None:
        raise TypeError("JSON mappings are immutable")

    def __delitem__(self, key: str) -> None:
        raise TypeError("JSON mappings are immutable")

    def clear(self) -> None:
        raise TypeError("JSON mappings are immutable")

    def pop(self, key: str, default: object = None) -> object:
        raise TypeError("JSON mappings are immutable")

    def popitem(self) -> tuple[str, object]:
        raise TypeError("JSON mappings are immutable")

    def setdefault(self, key: str, default: object = None) -> object:
        raise TypeError("JSON mappings are immutable")

    def update(self, *args: object, **kwargs: object) -> None:
        raise TypeError("JSON mappings are immutable")

    def __ior__(self, other: object) -> _FrozenDict:
        raise TypeError("JSON mappings are immutable")


def _freeze_json(value: JsonValue) -> JsonValue:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("JSON numbers must be finite")
    if isinstance(value, dict):
        frozen = _FrozenDict({key: _freeze_json(item) for key, item in value.items()})
        return cast(JsonValue, frozen)
    if isinstance(value, list):
        return cast(JsonValue, tuple(_freeze_json(item) for item in value))
    return value


def _freeze_json_mapping(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    return cast(Mapping[str, JsonValue], _freeze_json(dict(value)))


def _thaw_json(value: object) -> JsonValue:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return cast(JsonValue, value)


def _validate_safe_relative_posix_path(value: str, *, field_name: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or "\\" in value
        or "\x00" in value
        or (len(value) >= 2 and value[0].isalpha() and value[1] == ":")
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or path.as_posix() != value
    ):
        raise ValueError(f"{field_name} must be a safe relative POSIX path")
    return value


class CanonicalModel(BaseModel):
    model_config = ConfigDict(
        allow_inf_nan=False,
        extra="forbid",
        frozen=True,
        strict=True,
    )

    def canonical_json(self, *, exclude: set[str] | None = None) -> str:
        return json.dumps(
            self.model_dump(mode="json", exclude=exclude),
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    def canonical_checksum_sha256(self) -> str:
        return hashlib.sha256(
            self.canonical_json(exclude={"checksum_sha256"}).encode("utf-8")
        ).hexdigest()

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


class DatasetManifest(CanonicalModel):
    schema_version: Literal["1.0"] = "1.0"
    dataset_id: str = Field(min_length=1)
    task_type: str = Field(min_length=1)
    format: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    metadata: Mapping[str, JsonValue] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def freeze_metadata(cls, value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
        return _freeze_json_mapping(value)

    @field_serializer("metadata")
    def serialize_metadata(self, value: Mapping[str, JsonValue]) -> JsonValue:
        return _thaw_json(value)


class LaunchSpec(CanonicalModel):
    schema_version: Literal["1.0"] = "1.0"
    adapter_key: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    argv: Sequence[str] = Field(min_length=1)
    env: Mapping[str, str] = Field(default_factory=dict)
    working_directory: str | None = None

    @field_validator("argv")
    @classmethod
    def validate_argv(cls, value: Sequence[str]) -> Sequence[str]:
        if any(not argument or "\x00" in argument for argument in value):
            raise ValueError("argv entries must be non-empty and contain no NUL bytes")
        return tuple(value)

    @field_validator("env")
    @classmethod
    def freeze_env(cls, value: Mapping[str, str]) -> Mapping[str, str]:
        if any(not key or "=" in key or "\x00" in key for key in value):
            raise ValueError(
                "environment keys must be non-empty and contain no '=' or NUL"
            )
        if any("\x00" in item for item in value.values()):
            raise ValueError("environment values must contain no NUL")
        return cast(Mapping[str, str], _FrozenDict(dict(value)))

    @field_validator("working_directory")
    @classmethod
    def validate_working_directory(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_safe_relative_posix_path(
            value,
            field_name="working_directory",
        )

    @field_serializer("env")
    def serialize_env(self, value: Mapping[str, str]) -> dict[str, str]:
        return dict(value)


class TelemetryEnvelope(CanonicalModel):
    schema_version: Literal["1.0"] = "1.0"
    adapter_key: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    timestamp: datetime
    event_type: str = Field(min_length=1)
    payload: Mapping[str, JsonValue] = Field(default_factory=dict)

    @field_validator("payload")
    @classmethod
    def freeze_payload(cls, value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
        return _freeze_json_mapping(value)

    @field_serializer("payload")
    def serialize_payload(self, value: Mapping[str, JsonValue]) -> JsonValue:
        return _thaw_json(value)

    @field_validator("timestamp")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a timezone")
        return value.astimezone(UTC)


class ArtifactEntry(CanonicalModel):
    schema_version: Literal["1.0"] = "1.0"
    path: str
    size_bytes: int = Field(ge=0)
    checksum_sha256: str
    artifact_type: str = Field(min_length=1)

    @field_validator("path")
    @classmethod
    def validate_safe_relative_posix_path(cls, value: str) -> str:
        return _validate_safe_relative_posix_path(value, field_name="artifact path")

    @field_validator("checksum_sha256")
    @classmethod
    def validate_checksum(cls, value: str) -> str:
        if _SHA256_PATTERN.fullmatch(value) is None:
            raise ValueError(
                "checksum_sha256 must be a 64-character hexadecimal digest"
            )
        return value.lower()


class ArtifactManifest(CanonicalModel):
    schema_version: Literal["1.0"] = "1.0"
    task_id: str = Field(min_length=1)
    adapter_key: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    artifacts: Sequence[ArtifactEntry] = ()
    checksum_sha256: str | None = None

    @field_validator("checksum_sha256")
    @classmethod
    def validate_checksum(cls, value: str | None) -> str | None:
        if value is not None and _SHA256_PATTERN.fullmatch(value) is None:
            raise ValueError(
                "checksum_sha256 must be a 64-character hexadecimal digest"
            )
        return value.lower() if value is not None else None

    @model_validator(mode="after")
    def require_unique_artifact_paths(self) -> ArtifactManifest:
        paths = [artifact.path for artifact in self.artifacts]
        if len(paths) != len(set(paths)):
            raise ValueError("artifact paths must be unique")
        object.__setattr__(self, "artifacts", tuple(self.artifacts))
        return self
