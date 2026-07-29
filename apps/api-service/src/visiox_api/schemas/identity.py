from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


Role = Literal["admin", "member"]
UserStatus = Literal["active", "disabled"]
UserFilterStatus = Literal["active", "disabled", "deleted"]
PrincipalType = Literal["user", "group", "organization"]
AllocationPrincipalType = Literal["user", "group"]
Permission = Literal["view", "use", "edit", "delete", "manage", "invoke"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    display_name: str
    email: str
    role: str
    status: str
    must_change_password: bool


class AccessResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserResponse


class UserListResponse(BaseModel):
    items: list[UserResponse]
    total: int
    offset: int
    limit: int


class UserCreateRequest(StrictModel):
    username: str = Field(min_length=1, max_length=120)
    display_name: str = Field(min_length=1, max_length=160)
    email: str = Field(min_length=1, max_length=320)
    role: Role
    temporary_password: str | None = Field(default=None, min_length=12, max_length=256)


class UserCreateResponse(UserResponse):
    temporary_password: str


class UserUpdateRequest(StrictModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    email: str | None = Field(default=None, min_length=1, max_length=320)
    role: Role | None = None
    status: UserStatus | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_nulls(cls, value: Any) -> Any:
        if isinstance(value, dict):
            for field in ("display_name", "email", "role", "status"):
                if field in value and value[field] is None:
                    raise ValueError(f"{field} cannot be null")
        return value


class PasswordResetRequest(StrictModel):
    temporary_password: str | None = Field(default=None, min_length=12, max_length=256)


class PasswordResetResponse(BaseModel):
    user: UserResponse
    temporary_password: str


class ProfileUpdateRequest(StrictModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    email: str | None = Field(default=None, min_length=1, max_length=320)

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_nulls(cls, value: Any) -> Any:
        if isinstance(value, dict):
            for field in ("display_name", "email"):
                if field in value and value[field] is None:
                    raise ValueError(f"{field} cannot be null")
        return value


class PasswordChangeRequest(StrictModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class GroupCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None


class GroupUpdateRequest(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_explicit_null_name(cls, value: Any) -> Any:
        if isinstance(value, dict) and "name" in value and value["name"] is None:
            raise ValueError("name cannot be null")
        return value


class GroupMembersRequest(StrictModel):
    user_ids: list[str]


class GroupResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None
    status: str
    member_ids: list[str]
    member_count: int


class GroupListResponse(BaseModel):
    items: list[GroupResponse]
    total: int
    offset: int
    limit: int


class ExpiringModel(StrictModel):
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def validate_expiry(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        normalized = (
            value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        )
        if normalized <= datetime.now(UTC):
            raise ValueError("expires_at must be in the future")
        return normalized


class ResourceGrantUpsertRequest(ExpiringModel):
    resource_type: str = Field(min_length=1, max_length=80)
    resource_id: str = Field(min_length=1, max_length=36)
    principal_type: PrincipalType
    principal_id: str = Field(min_length=1, max_length=36)
    permissions: list[Permission] = Field(min_length=1)


class ResourceGrantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    resource_type: str
    resource_id: str
    principal_type: str
    principal_id: str
    permissions: list[str]
    expires_at: datetime | None


class ResourceGrantListResponse(BaseModel):
    items: list[ResourceGrantResponse]
    total: int
    offset: int
    limit: int


class ResourceAllocationUpsertRequest(ExpiringModel):
    principal_type: AllocationPrincipalType
    principal_id: str = Field(min_length=1, max_length=36)
    resource_pool_id: str = Field(min_length=1, max_length=36)
    max_concurrent_training_jobs: int | None = Field(default=None, ge=0)
    max_gpu_count: int | None = Field(default=None, ge=0)
    max_service_instances: int | None = Field(default=None, ge=0)


class ResourceAllocationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    principal_type: str
    principal_id: str
    resource_pool_id: str
    max_concurrent_training_jobs: int | None
    max_gpu_count: int | None
    max_service_instances: int | None
    expires_at: datetime | None


class ResourceAllocationListResponse(BaseModel):
    items: list[ResourceAllocationResponse]
    total: int
    offset: int
    limit: int


class AuditLogResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    actor_user_id: str | None
    action: str
    resource_type: str | None
    resource_id: str | None
    result: str
    request_id: str | None
    metadata_json: dict[str, Any]
    created_at: datetime


class AuditLogListResponse(BaseModel):
    items: list[AuditLogResponse]
    total: int
    next_cursor: str | None
