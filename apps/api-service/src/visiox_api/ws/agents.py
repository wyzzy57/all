import base64
import hashlib
import json
import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, WebSocket
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker
from starlette.websockets import WebSocketDisconnect

from visiox_api.schemas.agent_protocol import (
    AuthenticateMessage,
    AuthenticatedMessage,
    CertificateRenewalRequest,
    CertificateRenewedMessage,
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
    IssuedAgentCertificate,
    VerifiedAgentIdentity,
    issue_agent_certificate,
    verify_agent_signature,
)
from visiox_api.services.node_registry import NodeNotFound, NodeRegistryService
from visiox_common.settings import Settings, get_settings
from visiox_db.models import ComputeNode, NodeEvent
from visiox_db.session import create_session_factory


router = APIRouter(tags=["agent-gateway"])

_MESSAGE_MODELS = {
    "inventory": InventoryMessage,
    "heartbeat": HeartbeatMessage,
    "event_batch": EventBatchMessage,
    "certificate_renewal_request": CertificateRenewalRequest,
}


class InvalidAgentMessage(Exception):
    """Raised when an inbound frame cannot be accepted as protocol input."""


class AgentAuthenticationRejected(Exception):
    """Raised for every untrusted or stale registered agent identity."""


class EventSequenceConflict(Exception):
    """Raised when a stored event sequence is replayed with different content."""


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
        raw_auth = await _receive_text_frame(websocket, settings)
        auth, signature = _parse_authentication(raw_auth)
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
        with session_factory() as session:
            _authenticate_registered_node(session, verified)
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
                with session_factory() as session:
                    NodeRegistryService(session, settings).apply_inventory(
                        identity.node_id, message
                    )
            elif isinstance(message, HeartbeatMessage):
                with session_factory() as session:
                    NodeRegistryService(session, settings).mark_seen(identity.node_id)
            elif isinstance(message, EventBatchMessage):
                with session_factory() as session:
                    through_sequence = _persist_event_batch(
                        session, identity.node_id, message
                    )
                await websocket.send_json(
                    EventsAckMessage(through_sequence=through_sequence).model_dump(mode="json")
                )
            elif isinstance(message, CertificateRenewalRequest):
                with session_factory() as session:
                    renewed = _renew_agent_certificate(session, identity, message, settings)
                await websocket.send_json(
                    CertificateRenewedMessage(
                        certificate_pem=renewed.certificate_pem
                    ).model_dump(mode="json")
                )
        except EventSequenceConflict:
            await _send_error(
                websocket,
                "event_conflict",
                "Event sequence conflicts with stored event",
            )
        except AgentIdentityError:
            await _send_error(
                websocket,
                "invalid_certificate_request",
                "Certificate request rejected",
            )
        except (AgentAuthenticationRejected, NodeNotFound):
            await websocket.close(code=4403, reason="agent identity rejected")
            return


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
) -> InventoryMessage | HeartbeatMessage | EventBatchMessage | CertificateRenewalRequest:
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
) -> None:
    node = session.get(ComputeNode, identity.node_id)
    if (
        node is None
        or node.certificate_serial != identity.serial_number
        or node.certificate_fingerprint != identity.fingerprint_sha256
    ):
        raise AgentAuthenticationRejected


def _persist_event_batch(
    session: Session,
    node_id: str,
    batch: EventBatchMessage,
) -> int:
    try:
        node = session.scalar(
            select(ComputeNode).where(ComputeNode.id == node_id).with_for_update()
        )
        if node is None:
            raise NodeNotFound(node_id)

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


def _renew_agent_certificate(
    session: Session,
    identity: VerifiedAgentIdentity,
    request: CertificateRenewalRequest,
    settings: Settings,
) -> IssuedAgentCertificate:
    try:
        node = session.scalar(
            select(ComputeNode)
            .where(ComputeNode.id == identity.node_id)
            .with_for_update()
        )
        if (
            node is None
            or node.certificate_serial != identity.serial_number
            or node.certificate_fingerprint != identity.fingerprint_sha256
        ):
            raise AgentAuthenticationRejected
        renewed = issue_agent_certificate(request.csr_pem, identity.node_id, settings)
        node.certificate_serial = renewed.serial_number
        node.certificate_fingerprint = renewed.fingerprint_sha256
        node.certificate_expires_at = renewed.expires_at
        session.commit()
        return renewed
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
