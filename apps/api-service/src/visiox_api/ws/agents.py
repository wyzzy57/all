import asyncio
import base64
import hashlib
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, TypeVar

from fastapi import APIRouter, Depends, WebSocket
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.websockets import WebSocketDisconnect

from visiox_api.schemas.agent_protocol import (
    AuthenticateMessage,
    AuthenticatedMessage,
    CertificateRenewalAckMessage,
    CertificateRenewalActivatedMessage,
    CertificateRenewalCandidateMessage,
    CertificateRenewalRequest,
    ChallengeMessage,
    ErrorMessage,
    EventBatchMessage,
    EventsAckMessage,
    HeartbeatMessage,
    InventoryMessage,
    validate_raw_message_size,
)
from visiox_api.services.agent_identity import (
    AgentIdentityError,
    VerifiedAgentIdentity,
    csr_fingerprint_sha256,
    csr_public_key_bytes,
    issue_agent_certificate,
    verify_agent_certificate,
    verify_agent_signature,
)
from visiox_api.services.node_registry import NodeNotFound, NodeRegistryService
from visiox_common.settings import Settings, get_settings
from visiox_db.models import ComputeNode, NodeEvent
from visiox_db.session import create_session_factory


router = APIRouter(tags=["agent-gateway"])

_T = TypeVar("_T")

_MESSAGE_MODELS = {
    "inventory": InventoryMessage,
    "heartbeat": HeartbeatMessage,
    "event_batch": EventBatchMessage,
    "certificate_renewal_request": CertificateRenewalRequest,
    "certificate_renewal_ack": CertificateRenewalAckMessage,
}


class InvalidAgentMessage(Exception):
    """Raised when an inbound frame cannot be accepted as protocol input."""


class AgentAuthenticationRejected(Exception):
    """Raised for every untrusted or stale registered agent identity."""


class EventSequenceConflict(Exception):
    """Raised when a stored event sequence is replayed with different content."""


class CertificateRenewalRejected(Exception):
    """Raised when a renewal candidate cannot be safely replayed or activated."""


@dataclass(frozen=True, slots=True)
class RenewalCandidate:
    certificate_pem: str
    fingerprint_sha256: str


def get_agent_gateway_session_factory() -> sessionmaker[Session]:
    return create_session_factory()


@router.websocket("/agent/v1/connect")
async def agent_gateway(
    websocket: WebSocket,
    session_factory: sessionmaker[Session] = Depends(get_agent_gateway_session_factory),
    settings: Settings = Depends(get_settings),
) -> None:
    await websocket.accept()
    if not settings.agent_gateway_enabled:
        await websocket.close(code=1013, reason="agent gateway unavailable")
        return

    nonce = secrets.token_bytes(32)
    await websocket.send_json(
        ChallengeMessage(nonce=base64.b64encode(nonce).decode()).model_dump(mode="json")
    )
    try:
        async with asyncio.timeout(settings.agent_authentication_timeout_seconds):
            raw_auth = await _receive_text_frame(websocket, settings)
            auth, signature = _parse_authentication(raw_auth)
    except TimeoutError:
        await websocket.close(code=4408, reason="agent authentication timed out")
        return
    except WebSocketDisconnect:
        return
    except AgentAuthenticationRejected:
        await websocket.close(code=4403, reason="agent identity rejected")
        return
    except InvalidAgentMessage:
        await _send_error(websocket, "invalid_message", "Message rejected")
        await websocket.close(code=4400, reason="invalid agent message")
        return

    try:
        verified = verify_agent_signature(
            auth.certificate_pem,
            nonce,
            signature,
            settings,
        )
        if verified.node_id != auth.node_id:
            raise AgentAuthenticationRejected
        verified = await _run_session_operation(
            session_factory,
            _authenticate_registered_node,
            verified,
            settings,
        )
    except (AgentIdentityError, AgentAuthenticationRejected):
        await websocket.close(code=4403, reason="agent identity rejected")
        return

    await websocket.send_json(
        AuthenticatedMessage(
            heartbeat_interval_seconds=settings.agent_heartbeat_interval_seconds
        ).model_dump(mode="json")
    )
    await _receive_agent_messages(websocket, verified, session_factory, settings)


async def _receive_agent_messages(
    websocket: WebSocket,
    identity: VerifiedAgentIdentity,
    session_factory: sessionmaker[Session],
    settings: Settings,
) -> None:
    while True:
        try:
            raw_message = await _receive_text_frame(websocket, settings)
            message = _parse_agent_message(raw_message)
        except WebSocketDisconnect:
            return
        except InvalidAgentMessage:
            await _send_error(websocket, "invalid_message", "Message rejected")
            continue

        try:
            if isinstance(message, InventoryMessage):
                await _run_session_operation(
                    session_factory,
                    _apply_inventory_message,
                    identity,
                    message,
                    settings,
                )
            elif isinstance(message, HeartbeatMessage):
                await _run_session_operation(
                    session_factory,
                    _apply_heartbeat_message,
                    identity,
                    settings,
                )
            elif isinstance(message, EventBatchMessage):
                through_sequence = await _run_session_operation(
                    session_factory,
                    _persist_event_batch,
                    identity,
                    message,
                )
                await websocket.send_json(
                    EventsAckMessage(through_sequence=through_sequence).model_dump(mode="json")
                )
            elif isinstance(message, CertificateRenewalRequest):
                candidate = await _run_session_operation(
                    session_factory,
                    _stage_agent_certificate_renewal,
                    identity,
                    message,
                    settings,
                )
                await websocket.send_json(
                    CertificateRenewalCandidateMessage(
                        renewal_request_id=message.renewal_request_id,
                        certificate_pem=candidate.certificate_pem,
                        certificate_fingerprint_sha256=candidate.fingerprint_sha256,
                    ).model_dump(mode="json")
                )
            elif isinstance(message, CertificateRenewalAckMessage):
                identity = await _run_session_operation(
                    session_factory,
                    _activate_agent_certificate_renewal,
                    identity,
                    message,
                    settings,
                )
                await websocket.send_json(
                    CertificateRenewalActivatedMessage(
                        renewal_request_id=message.renewal_request_id,
                        certificate_fingerprint_sha256=identity.fingerprint_sha256,
                    ).model_dump(mode="json")
                )
        except EventSequenceConflict:
            await _send_error(
                websocket,
                "event_conflict",
                "Event sequence conflicts with stored event",
            )
        except (AgentIdentityError, CertificateRenewalRejected):
            await _send_error(
                websocket,
                "invalid_certificate_request",
                "Certificate request rejected",
            )
        except (AgentAuthenticationRejected, NodeNotFound):
            await websocket.close(code=4403, reason="agent identity rejected")
            return


async def _run_session_operation(
    session_factory: sessionmaker[Session],
    operation: Callable[..., _T],
    *args: object,
) -> _T:
    return await asyncio.to_thread(_run_session_operation_sync, session_factory, operation, args)


def _run_session_operation_sync(
    session_factory: sessionmaker[Session],
    operation: Callable[..., _T],
    args: tuple[object, ...],
) -> _T:
    with session_factory() as session:
        return operation(session, *args)


def _apply_inventory_message(
    session: Session,
    identity: VerifiedAgentIdentity,
    message: InventoryMessage,
    settings: Settings,
) -> None:
    _require_current_session_credential(session, identity)
    NodeRegistryService(session, settings).apply_inventory(identity.node_id, message)


def _apply_heartbeat_message(
    session: Session,
    identity: VerifiedAgentIdentity,
    settings: Settings,
) -> None:
    _require_current_session_credential(session, identity)
    NodeRegistryService(session, settings).mark_seen(identity.node_id)


async def _receive_text_frame(websocket: WebSocket, settings: Settings) -> str:
    message = await websocket.receive()
    if message["type"] == "websocket.disconnect":
        raise WebSocketDisconnect(
            code=message.get("code", 1000),
            reason=message.get("reason", ""),
        )
    raw_message = message.get("text")
    if raw_message is None:
        raise InvalidAgentMessage
    try:
        validate_raw_message_size(raw_message)
    except ValueError:
        raise InvalidAgentMessage from None
    if len(raw_message.encode("utf-8")) > settings.agent_max_ws_message_bytes:
        raise InvalidAgentMessage
    return raw_message


def _parse_authentication(raw_message: str) -> tuple[AuthenticateMessage, bytes]:
    payload = _load_json_object(raw_message)
    raw_signature = payload.get("signature")
    if isinstance(raw_signature, str):
        try:
            signature = base64.b64decode(raw_signature, validate=True)
        except ValueError:
            raise AgentAuthenticationRejected from None
    else:
        signature = b""
    try:
        auth = AuthenticateMessage.model_validate_json(raw_message)
    except ValidationError:
        raise InvalidAgentMessage from None
    return auth, signature


def _parse_agent_message(
    raw_message: str,
) -> InventoryMessage | HeartbeatMessage | EventBatchMessage | CertificateRenewalRequest | CertificateRenewalAckMessage:
    payload = _load_json_object(raw_message)
    model = _MESSAGE_MODELS.get(payload.get("type"))
    if model is None:
        raise InvalidAgentMessage
    try:
        return model.model_validate_json(raw_message)
    except ValidationError:
        raise InvalidAgentMessage from None


def _load_json_object(raw_message: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw_message)
    except json.JSONDecodeError:
        raise InvalidAgentMessage from None
    if not isinstance(payload, dict):
        raise InvalidAgentMessage
    return payload


def _authenticate_registered_node(
    session: Session,
    identity: VerifiedAgentIdentity,
    settings: Settings,
) -> VerifiedAgentIdentity:
    try:
        node = session.scalar(
            select(ComputeNode).where(ComputeNode.id == identity.node_id).with_for_update()
        )
        if node is None:
            raise AgentAuthenticationRejected
        if _matches_active_credential(node, identity):
            return identity

        candidate = _pending_candidate_identity(node, settings)
        if candidate is None:
            if _has_pending_renewal(node):
                _clear_pending_renewal(node)
                session.commit()
            raise AgentAuthenticationRejected
        if (
            candidate.serial_number != identity.serial_number
            or candidate.fingerprint_sha256 != identity.fingerprint_sha256
        ):
            raise AgentAuthenticationRejected

        _promote_pending_renewal(node, candidate)
        session.commit()
        return candidate
    except Exception:
        session.rollback()
        raise


def _require_current_session_credential(
    session: Session,
    identity: VerifiedAgentIdentity,
) -> ComputeNode:
    node = session.scalar(
        select(ComputeNode).where(ComputeNode.id == identity.node_id).with_for_update()
    )
    if node is None or not _matches_active_credential(node, identity):
        raise AgentAuthenticationRejected
    return node


def _matches_active_credential(node: ComputeNode, identity: VerifiedAgentIdentity) -> bool:
    return (
        node.certificate_serial == identity.serial_number
        and node.certificate_fingerprint == identity.fingerprint_sha256
    )


def _pending_candidate_identity(
    node: ComputeNode,
    settings: Settings,
) -> VerifiedAgentIdentity | None:
    expires_at = _as_utc(node.pending_certificate_expires_at)
    if (
        node.pending_certificate_serial is None
        or node.pending_certificate_fingerprint is None
        or node.pending_certificate_pem is None
        or expires_at is None
        or expires_at <= datetime.now(UTC)
    ):
        return None
    try:
        candidate = verify_agent_certificate(node.pending_certificate_pem, settings)
    except AgentIdentityError:
        return None
    if (
        candidate.node_id != node.id
        or candidate.serial_number != node.pending_certificate_serial
        or candidate.fingerprint_sha256 != node.pending_certificate_fingerprint
    ):
        return None
    return candidate


def _has_pending_renewal(node: ComputeNode) -> bool:
    return any(
        value is not None
        for value in (
            node.pending_certificate_serial,
            node.pending_certificate_fingerprint,
            node.pending_certificate_expires_at,
            node.pending_certificate_pem,
            node.pending_renewal_request_id,
            node.pending_renewal_csr_fingerprint,
        )
    )


def _clear_pending_renewal(node: ComputeNode) -> None:
    node.pending_certificate_serial = None
    node.pending_certificate_fingerprint = None
    node.pending_certificate_expires_at = None
    node.pending_certificate_pem = None
    node.pending_renewal_request_id = None
    node.pending_renewal_csr_fingerprint = None


def _promote_pending_renewal(
    node: ComputeNode,
    candidate: VerifiedAgentIdentity,
) -> None:
    if node.pending_certificate_expires_at is None:
        raise CertificateRenewalRejected
    node.certificate_serial = candidate.serial_number
    node.certificate_fingerprint = candidate.fingerprint_sha256
    node.certificate_expires_at = node.pending_certificate_expires_at
    _clear_pending_renewal(node)


def _persist_event_batch(
    session: Session,
    identity: VerifiedAgentIdentity,
    batch: EventBatchMessage,
) -> int:
    try:
        node = _require_current_session_credential(session, identity)
        node_id = node.id

        sequences = {event.sequence for event in batch.events}
        stored_by_sequence = {
            event.sequence: event
            for event in session.scalars(
                select(NodeEvent).where(
                    NodeEvent.node_id == node_id,
                    NodeEvent.sequence.in_(sequences),
                )
            )
        }
        pending_by_sequence: dict[int, NodeEvent] = {}
        for event in batch.events:
            stored = stored_by_sequence.get(event.sequence) or pending_by_sequence.get(
                event.sequence
            )
            if stored is not None:
                if not _same_event(stored, event):
                    raise EventSequenceConflict
                continue
            pending = NodeEvent(
                node_id=node_id,
                sequence=event.sequence,
                command_id=event.command_id,
                event_type=event.event_type,
                stage=event.stage,
                payload=dict(event.payload),
                occurred_at=event.occurred_at,
            )
            pending_by_sequence[event.sequence] = pending
            session.add(pending)

        session.flush()
        all_sequences = set(
            session.scalars(
                select(NodeEvent.sequence)
                .where(NodeEvent.node_id == node_id)
                .order_by(NodeEvent.sequence)
            )
        )
        through_sequence = 0
        while through_sequence + 1 in all_sequences:
            through_sequence += 1
        session.commit()
        return through_sequence
    except Exception:
        session.rollback()
        raise


def _same_event(stored: NodeEvent, received: Any) -> bool:
    return (
        stored.event_type == received.event_type
        and stored.command_id == received.command_id
        and stored.stage == received.stage
        and _canonical_json_digest(stored.payload)
        == _canonical_json_digest(received.payload)
        and _as_utc(stored.occurred_at) == _as_utc(received.occurred_at)
    )


def _canonical_json_digest(value: object) -> bytes:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).digest()


def _stage_agent_certificate_renewal(
    session: Session,
    identity: VerifiedAgentIdentity,
    request: CertificateRenewalRequest,
    settings: Settings,
) -> RenewalCandidate:
    try:
        csr_public_key = csr_public_key_bytes(request.csr_pem)
        csr_fingerprint = csr_fingerprint_sha256(request.csr_pem)
        node = _require_current_session_credential(session, identity)
        if csr_public_key != identity.public_key_bytes:
            raise CertificateRenewalRejected

        candidate = _pending_candidate_identity(node, settings)
        if candidate is not None and candidate.public_key_bytes == identity.public_key_bytes:
            if (
                node.pending_certificate_pem is None
                or node.pending_certificate_fingerprint is None
            ):
                raise CertificateRenewalRejected
            node.pending_renewal_request_id = request.renewal_request_id
            node.pending_renewal_csr_fingerprint = csr_fingerprint
            session.commit()
            return RenewalCandidate(
                certificate_pem=node.pending_certificate_pem,
                fingerprint_sha256=node.pending_certificate_fingerprint,
            )
        if _has_pending_renewal(node):
            _clear_pending_renewal(node)

        renewed = issue_agent_certificate(request.csr_pem, identity.node_id, settings)
        node.pending_certificate_serial = renewed.serial_number
        node.pending_certificate_fingerprint = renewed.fingerprint_sha256
        node.pending_certificate_expires_at = renewed.expires_at
        node.pending_certificate_pem = renewed.certificate_pem
        node.pending_renewal_request_id = request.renewal_request_id
        node.pending_renewal_csr_fingerprint = csr_fingerprint
        session.commit()
        return RenewalCandidate(
            certificate_pem=renewed.certificate_pem,
            fingerprint_sha256=renewed.fingerprint_sha256,
        )
    except Exception:
        session.rollback()
        raise


def _activate_agent_certificate_renewal(
    session: Session,
    identity: VerifiedAgentIdentity,
    acknowledgement: CertificateRenewalAckMessage,
    settings: Settings,
) -> VerifiedAgentIdentity:
    try:
        node = _require_current_session_credential(session, identity)
        candidate = _pending_candidate_identity(node, settings)
        if (
            node.pending_renewal_request_id != acknowledgement.renewal_request_id
            or node.pending_certificate_fingerprint != acknowledgement.certificate_fingerprint_sha256
            or candidate is None
            or candidate.public_key_bytes != identity.public_key_bytes
        ):
            if candidate is None and _has_pending_renewal(node):
                _clear_pending_renewal(node)
                session.commit()
            raise CertificateRenewalRejected
        _promote_pending_renewal(node, candidate)
        session.commit()
        return candidate
    except Exception:
        session.rollback()
        raise


async def _send_error(
    websocket: WebSocket,
    code: str,
    message: str,
) -> None:
    await websocket.send_json(
        ErrorMessage(
            code=code,
            message=message,
            retryable=False,
        ).model_dump(mode="json")
    )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
