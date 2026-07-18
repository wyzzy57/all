import base64
import json
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


MAX_MESSAGE_BYTES = 1024 * 1024
MAX_INVENTORY_SECTION_BYTES = 64 * 1024
MAX_PEM_BYTES = 16 * 1024
MAX_EVENT_BATCH_SIZE = 100
REQUEST_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]{7,127}$"
FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"


def validate_raw_message_size(message: str | bytes) -> None:
    """Validate Task 4 inbound text/bytes before JSON parsing."""
    size = len(message.encode("utf-8")) if isinstance(message, str) else len(message)
    if size > MAX_MESSAGE_BYTES:
        raise ValueError("message exceeds 1 MiB")


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


def _validate_pem_size(value: str) -> str:
    if len(value.encode("utf-8")) > MAX_PEM_BYTES:
        raise ValueError("PEM exceeds 16 KiB")
    return value


def _validate_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError("timestamp must be UTC RFC3339")
    return value


class ProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class EnrollmentRequest(ProtocolModel):
    protocol_version: Literal[1]
    token: str = Field(min_length=32, max_length=256)
    enrollment_request_id: str = Field(pattern=REQUEST_ID_PATTERN)
    node_name: str = Field(min_length=1, max_length=160, pattern=r"^[\w.-]+$")
    architecture: Literal["amd64", "arm64"]
    platform_kind: Literal["jetson", "x86_nvidia"]
    agent_version: str = Field(min_length=1, max_length=40)
    csr_pem: str = Field(min_length=100)

    _csr_fits = field_validator("csr_pem")(_validate_pem_size)


class EnrollmentResponse(ProtocolModel):
    protocol_version: Literal[1] = 1
    enrollment_request_id: str = Field(pattern=REQUEST_ID_PATTERN)
    node_id: str
    certificate_pem: str = Field(min_length=100)
    ca_certificate_pem: str = Field(min_length=100)
    gateway_url: str
    heartbeat_interval_seconds: int = Field(gt=0)

    _certificates_fit = field_validator("certificate_pem", "ca_certificate_pem")(
        _validate_pem_size
    )


class ChallengeMessage(ProtocolModel):
    protocol_version: Literal[1] = 1
    type: Literal["challenge"] = "challenge"
    nonce: str = Field(min_length=1, max_length=MAX_PEM_BYTES)

    _nonce_is_base64 = field_validator("nonce")(_validate_base64)


class AuthenticateMessage(ProtocolModel):
    protocol_version: Literal[1]
    type: Literal["authenticate"]
    node_id: str = Field(min_length=1, max_length=36)
    certificate_pem: str = Field(min_length=100)
    signature: str = Field(min_length=1, max_length=MAX_PEM_BYTES)

    _signature_is_base64 = field_validator("signature")(_validate_base64)
    _certificate_fits = field_validator("certificate_pem")(_validate_pem_size)


class AuthenticatedMessage(ProtocolModel):
    protocol_version: Literal[1] = 1
    type: Literal["authenticated"] = "authenticated"
    heartbeat_interval_seconds: int = Field(gt=0)


class InventoryMessage(ProtocolModel):
    protocol_version: Literal[1]
    type: Literal["inventory"]
    architecture: Literal["amd64", "arm64"]
    platform_kind: Literal["jetson", "x86_nvidia"]
    capabilities: dict[str, Any]
    resources: dict[str, Any]
    fingerprint: dict[str, Any]
    agent_version: str = Field(min_length=1, max_length=40)

    _sections_fit = field_validator("capabilities", "resources", "fingerprint")(_validate_inventory_section)


class HeartbeatMessage(ProtocolModel):
    protocol_version: Literal[1]
    type: Literal["heartbeat"]
    occurred_at: datetime

    _occurred_at_is_utc = field_validator("occurred_at")(_validate_utc)


class AgentEvent(ProtocolModel):
    sequence: int = Field(ge=1)
    event_type: str = Field(min_length=1, max_length=80)
    command_id: str | None = Field(default=None, max_length=36)
    stage: str | None = Field(default=None, max_length=120)
    payload: dict[str, Any]
    occurred_at: datetime

    _occurred_at_is_utc = field_validator("occurred_at")(_validate_utc)


class EventBatchMessage(ProtocolModel):
    protocol_version: Literal[1]
    type: Literal["event_batch"]
    events: list[AgentEvent] = Field(max_length=MAX_EVENT_BATCH_SIZE)


class EventsAckMessage(ProtocolModel):
    protocol_version: Literal[1] = 1
    type: Literal["events_acked"] = "events_acked"
    through_sequence: int = Field(ge=0)


class CertificateRenewalRequest(ProtocolModel):
    protocol_version: Literal[1]
    type: Literal["certificate_renewal_request"]
    renewal_request_id: str = Field(pattern=REQUEST_ID_PATTERN)
    csr_pem: str = Field(min_length=100)

    _csr_fits = field_validator("csr_pem")(_validate_pem_size)


class CertificateRenewalCandidateMessage(ProtocolModel):
    protocol_version: Literal[1] = 1
    type: Literal["certificate_renewal_candidate"] = "certificate_renewal_candidate"
    renewal_request_id: str = Field(pattern=REQUEST_ID_PATTERN)
    certificate_pem: str = Field(min_length=100)
    certificate_fingerprint_sha256: str = Field(pattern=FINGERPRINT_PATTERN)

    _certificate_fits = field_validator("certificate_pem")(_validate_pem_size)


class CertificateRenewalAckMessage(ProtocolModel):
    protocol_version: Literal[1]
    type: Literal["certificate_renewal_ack"]
    renewal_request_id: str = Field(pattern=REQUEST_ID_PATTERN)
    certificate_fingerprint_sha256: str = Field(pattern=FINGERPRINT_PATTERN)


class CertificateRenewalActivatedMessage(ProtocolModel):
    protocol_version: Literal[1] = 1
    type: Literal["certificate_renewal_activated"] = "certificate_renewal_activated"
    renewal_request_id: str = Field(pattern=REQUEST_ID_PATTERN)
    certificate_fingerprint_sha256: str = Field(pattern=FINGERPRINT_PATTERN)


class ErrorMessage(ProtocolModel):
    protocol_version: Literal[1] = 1
    type: Literal["error"] = "error"
    code: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9_]+$")
    message: str = Field(min_length=1, max_length=512)
    retryable: bool
