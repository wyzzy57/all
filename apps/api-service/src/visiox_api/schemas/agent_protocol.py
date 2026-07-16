import base64
import json
from datetime import datetime, timedelta
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


MAX_MESSAGE_BYTES = 1024 * 1024
MAX_INVENTORY_SECTION_BYTES = 64 * 1024
MAX_PEM_BYTES = 16 * 1024
MAX_EVENT_BATCH_SIZE = 100


def _serialized_json_size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def _validate_inventory_section(value: dict[str, Any]) -> dict[str, Any]:
    if _serialized_json_size(value) > MAX_INVENTORY_SECTION_BYTES:
        raise ValueError("inventory section exceeds 64 KiB")
    return value


def _validate_base64(value: str) -> str:
    try:
        decoded = base64.b64decode(value, validate=True)
    except ValueError:
        raise ValueError("value must be valid base64") from None
    if not decoded:
        raise ValueError("base64 value must not be empty")
    return value


def _validate_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be UTC RFC3339")
    return value


class ProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    @model_validator(mode="after")
    def validate_message_size(self) -> Self:
        if len(self.model_dump_json().encode()) > MAX_MESSAGE_BYTES:
            raise ValueError("message exceeds 1 MiB")
        return self


class EnrollmentRequest(ProtocolModel):
    protocol_version: Literal[1]
    token: str = Field(min_length=32, max_length=256)
    node_name: str = Field(min_length=1, max_length=160, pattern=r"^[\w.-]+$")
    architecture: Literal["amd64", "arm64"]
    platform_kind: Literal["jetson", "x86_nvidia"]
    agent_version: str = Field(min_length=1, max_length=40)
    csr_pem: str = Field(min_length=100, max_length=MAX_PEM_BYTES)


class EnrollmentResponse(ProtocolModel):
    protocol_version: Literal[1] = 1
    node_id: str
    certificate_pem: str = Field(min_length=100, max_length=MAX_PEM_BYTES)
    ca_certificate_pem: str = Field(min_length=100, max_length=MAX_PEM_BYTES)
    gateway_url: str
    heartbeat_interval_seconds: int = Field(gt=0)


class ChallengeMessage(ProtocolModel):
    protocol_version: Literal[1] = 1
    type: Literal["challenge"] = "challenge"
    nonce: str = Field(min_length=1, max_length=MAX_PEM_BYTES)

    _nonce_is_base64 = field_validator("nonce")(_validate_base64)


class AuthenticateMessage(ProtocolModel):
    protocol_version: Literal[1] = 1
    type: Literal["authenticate"] = "authenticate"
    node_id: str = Field(min_length=1, max_length=36)
    certificate_pem: str = Field(min_length=100, max_length=MAX_PEM_BYTES)
    signature: str = Field(min_length=1, max_length=MAX_PEM_BYTES)

    _signature_is_base64 = field_validator("signature")(_validate_base64)


class AuthenticatedMessage(ProtocolModel):
    protocol_version: Literal[1] = 1
    type: Literal["authenticated"] = "authenticated"
    heartbeat_interval_seconds: int = Field(gt=0)


class InventoryMessage(ProtocolModel):
    protocol_version: Literal[1] = 1
    type: Literal["inventory"] = "inventory"
    architecture: Literal["amd64", "arm64"]
    platform_kind: Literal["jetson", "x86_nvidia"]
    capabilities: dict[str, Any]
    resources: dict[str, Any]
    fingerprint: dict[str, Any]
    agent_version: str = Field(min_length=1, max_length=40)

    _sections_fit = field_validator("capabilities", "resources", "fingerprint")(_validate_inventory_section)


class HeartbeatMessage(ProtocolModel):
    protocol_version: Literal[1] = 1
    type: Literal["heartbeat"] = "heartbeat"
    occurred_at: datetime

    _occurred_at_is_utc = field_validator("occurred_at")(_validate_utc)


class AgentEvent(ProtocolModel):
    sequence: int = Field(ge=0)
    event_type: str = Field(min_length=1, max_length=80)
    command_id: str | None = Field(default=None, max_length=36)
    stage: str | None = Field(default=None, max_length=120)
    payload: dict[str, Any]
    occurred_at: datetime

    _occurred_at_is_utc = field_validator("occurred_at")(_validate_utc)


class EventBatchMessage(ProtocolModel):
    protocol_version: Literal[1] = 1
    type: Literal["event_batch"] = "event_batch"
    events: list[AgentEvent] = Field(max_length=MAX_EVENT_BATCH_SIZE)


class EventsAckMessage(ProtocolModel):
    protocol_version: Literal[1] = 1
    type: Literal["events_acked"] = "events_acked"
    through_sequence: int = Field(ge=0)


class CertificateRenewalRequest(ProtocolModel):
    protocol_version: Literal[1] = 1
    type: Literal["certificate_renewal_request"] = "certificate_renewal_request"
    csr_pem: str = Field(min_length=100, max_length=MAX_PEM_BYTES)


class CertificateRenewedMessage(ProtocolModel):
    protocol_version: Literal[1] = 1
    type: Literal["certificate_renewed"] = "certificate_renewed"
    certificate_pem: str = Field(min_length=100, max_length=MAX_PEM_BYTES)


class ErrorMessage(ProtocolModel):
    protocol_version: Literal[1] = 1
    type: Literal["error"] = "error"
    code: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_]+$")
    message: str = Field(min_length=1, max_length=512)
    retryable: bool
