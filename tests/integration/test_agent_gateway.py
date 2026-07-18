import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta
from time import sleep

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker
from starlette.websockets import WebSocketDisconnect

from visiox_common.settings import Settings, get_settings
from visiox_db.models import ComputeNode, NodeEvent, ResourcePool


INVALID_MESSAGE = {
    "protocol_version": 1,
    "type": "error",
    "code": "invalid_message",
    "message": "Message rejected",
    "retryable": False,
}
EVENT_CONFLICT = {
    "protocol_version": 1,
    "type": "error",
    "code": "event_conflict",
    "message": "Event sequence conflicts with stored event",
    "retryable": False,
}
INVALID_CERTIFICATE_REQUEST = {
    "protocol_version": 1,
    "type": "error",
    "code": "invalid_certificate_request",
    "message": "Certificate request rejected",
    "retryable": False,
}


class _StalledAuthenticationWebSocket:
    def __init__(self) -> None:
        self.accepted = False
        self.messages: list[dict[str, object]] = []
        self.closed: tuple[int, str] | None = None

    async def accept(self) -> None:
        self.accepted = True

    async def send_json(self, message: dict[str, object]) -> None:
        self.messages.append(message)

    async def receive(self) -> dict[str, object]:
        await asyncio.Future()
        raise AssertionError("unreachable")

    async def close(self, code: int, reason: str) -> None:
        self.closed = (code, reason)


def new_agent_csr(name: str) -> tuple[ed25519.Ed25519PrivateKey, str]:
    key = ed25519.Ed25519PrivateKey.generate()
    csr = x509.CertificateSigningRequestBuilder().subject_name(
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    ).sign(key, algorithm=None)
    return key, csr.public_bytes(serialization.Encoding.PEM).decode()


def _csr_for_key(key: ed25519.Ed25519PrivateKey, name: str) -> str:
    csr = x509.CertificateSigningRequestBuilder().subject_name(
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    ).sign(key, algorithm=None)
    return csr.public_bytes(serialization.Encoding.PEM).decode()


@pytest.fixture()
def enrolled_agent(agent_api_client: TestClient):
    token = agent_api_client.post(
        "/agent/v1/enrollment-tokens", json={"name": "gateway-test"}
    ).json()["token"]
    private_key, csr_pem = new_agent_csr("edge-gateway-01")
    response = agent_api_client.post(
        "/agent/v1/enroll",
        json={
            "protocol_version": 1,
            "token": token,
            "enrollment_request_id": "enroll-gateway-fixture-001",
            "node_name": "edge-gateway-01",
            "architecture": "arm64",
            "platform_kind": "jetson",
            "agent_version": "0.1.0-test",
            "csr_pem": csr_pem,
        },
    )
    assert response.status_code == 201
    body = response.json()
    return body["node_id"], body["certificate_pem"], private_key


def _send_authentication(
    websocket,
    node_id: str,
    certificate_pem: str,
    private_key: ed25519.Ed25519PrivateKey,
    *,
    protocol_version: int = 1,
) -> None:
    challenge = websocket.receive_json()
    assert challenge["protocol_version"] == 1
    assert challenge["type"] == "challenge"
    nonce = base64.b64decode(challenge["nonce"])
    websocket.send_json(
        {
            "protocol_version": protocol_version,
            "type": "authenticate",
            "node_id": node_id,
            "certificate_pem": certificate_pem,
            "signature": base64.b64encode(private_key.sign(nonce)).decode(),
        }
    )


def _authenticate(
    websocket,
    node_id: str,
    certificate_pem: str,
    private_key: ed25519.Ed25519PrivateKey,
) -> None:
    _send_authentication(websocket, node_id, certificate_pem, private_key)
    authenticated = websocket.receive_json()
    assert authenticated["protocol_version"] == 1
    assert authenticated["type"] == "authenticated"
    assert authenticated["heartbeat_interval_seconds"] > 0


def _event(sequence: int, *, payload: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "sequence": sequence,
        "event_type": "agent_started",
        "payload": payload or {},
        "occurred_at": "2026-07-16T00:00:00+00:00",
    }


def _event_batch(*events: dict[str, object]) -> dict[str, object]:
    return {"protocol_version": 1, "type": "event_batch", "events": list(events)}


def _stored_certificate_identity(
    session_factory: sessionmaker[Session], node_id: str
) -> tuple[str | None, str | None]:
    with session_factory() as session:
        node = session.get(ComputeNode, node_id)
        assert node is not None
        return node.certificate_serial, node.certificate_fingerprint


def _expired_certificate(
    agent_api_client: TestClient,
    node_id: str,
    private_key: ed25519.Ed25519PrivateKey,
) -> str:
    settings = agent_api_client.app.dependency_overrides[get_settings]()
    ca_key = serialization.load_pem_private_key(
        settings.agent_ca_key_path.read_bytes(), password=None
    )
    ca_certificate = x509.load_pem_x509_certificate(settings.agent_ca_cert_path.read_bytes())
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, node_id)]))
        .issuer_name(ca_certificate.subject)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=3))
        .not_valid_after(now - timedelta(minutes=2))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
        .sign(ca_key, algorithm=None)
    )
    return certificate.public_bytes(serialization.Encoding.PEM).decode()


def test_agent_gateway_authenticates_updates_inventory_and_acks_events(
    agent_api_client: TestClient,
    enrolled_agent,
) -> None:
    node_id, certificate_pem, private_key = enrolled_agent
    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _authenticate(websocket, node_id, certificate_pem, private_key)
        websocket.send_json(
            {
                "protocol_version": 1,
                "type": "inventory",
                "architecture": "arm64",
                "platform_kind": "jetson",
                "capabilities": {"tasks": ["detect"]},
                "resources": {"gpu_memory_bytes": 8589934592},
                "fingerprint": {"jetpack": "6.2", "tensorrt": "10.3"},
                "agent_version": "0.1.0",
            }
        )
        websocket.send_json(
            {
                "protocol_version": 1,
                "type": "heartbeat",
                "occurred_at": datetime.now(UTC).isoformat(),
            }
        )
        websocket.send_json(_event_batch(_event(1)))
        assert websocket.receive_json() == {
            "protocol_version": 1,
            "type": "events_acked",
            "through_sequence": 1,
        }

    node = agent_api_client.get(f"/nodes/{node_id}").json()
    assert node["status"] == "online"
    assert node["fingerprint"]["tensorrt"] == "10.3"


def test_agent_gateway_closes_unauthenticated_connection_after_authentication_deadline() -> None:
    from visiox_api.ws.agents import agent_gateway

    websocket = _StalledAuthenticationWebSocket()
    settings = Settings(
        _env_file=None,
        environment="local",
        agent_gateway_enabled=True,
        agent_authentication_timeout_seconds=0.01,
    )

    asyncio.run(
        asyncio.wait_for(
            agent_gateway(websocket, session_factory=lambda: None, settings=settings), timeout=0.1
        )
    )

    assert websocket.accepted is True
    assert websocket.messages[0]["type"] == "challenge"
    assert websocket.closed == (4408, "agent authentication timed out")


def test_agent_gateway_rejects_wrong_signature_opaquely(
    agent_api_client: TestClient,
    enrolled_agent,
) -> None:
    node_id, certificate_pem, _ = enrolled_agent
    wrong_key = ed25519.Ed25519PrivateKey.generate()
    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _send_authentication(websocket, node_id, certificate_pem, wrong_key)
        with pytest.raises(WebSocketDisconnect) as rejected:
            websocket.receive_json()

    assert rejected.value.code == 4403
    assert rejected.value.reason == "agent identity rejected"


def test_agent_gateway_rejects_malformed_signature_base64_opaquely(
    agent_api_client: TestClient,
    enrolled_agent,
) -> None:
    node_id, certificate_pem, _ = enrolled_agent
    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        challenge = websocket.receive_json()
        assert challenge["type"] == "challenge"
        websocket.send_json(
            {
                "protocol_version": 1,
                "type": "authenticate",
                "node_id": node_id,
                "certificate_pem": certificate_pem,
                "signature": "not-valid-base64!",
            }
        )
        with pytest.raises(WebSocketDisconnect) as rejected:
            websocket.receive_json()

    assert rejected.value.code == 4403
    assert rejected.value.reason == "agent identity rejected"


def test_agent_gateway_rejects_certificate_node_mismatch_opaquely(
    agent_api_client: TestClient,
    enrolled_agent,
) -> None:
    _, certificate_pem, private_key = enrolled_agent
    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _send_authentication(websocket, "00000000-0000-0000-0000-000000000000", certificate_pem, private_key)
        with pytest.raises(WebSocketDisconnect) as rejected:
            websocket.receive_json()

    assert rejected.value.code == 4403
    assert rejected.value.reason == "agent identity rejected"


def test_agent_gateway_rejects_expired_certificate_opaquely(
    agent_api_client: TestClient,
    enrolled_agent,
) -> None:
    node_id, _, private_key = enrolled_agent
    expired_certificate = _expired_certificate(agent_api_client, node_id, private_key)
    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _send_authentication(websocket, node_id, expired_certificate, private_key)
        with pytest.raises(WebSocketDisconnect) as rejected:
            websocket.receive_json()

    assert rejected.value.code == 4403
    assert rejected.value.reason == "agent identity rejected"


def test_agent_gateway_event_replays_are_idempotent_and_conflicts_are_rejected(
    agent_api_client: TestClient,
    agent_session_factory: sessionmaker[Session],
    enrolled_agent,
) -> None:
    node_id, certificate_pem, private_key = enrolled_agent
    original_event = _event(1, payload={"boot_id": "boot-a"})
    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _authenticate(websocket, node_id, certificate_pem, private_key)
        websocket.send_json(_event_batch(original_event))
        assert websocket.receive_json()["through_sequence"] == 1

        websocket.send_json(_event_batch(original_event))
        assert websocket.receive_json()["through_sequence"] == 1

        websocket.send_json(_event_batch(_event(1, payload={"boot_id": "boot-b"})))
        assert websocket.receive_json() == EVENT_CONFLICT

    with agent_session_factory() as session:
        assert session.scalar(select(func.count(NodeEvent.id))) == 1
        stored = session.scalar(select(NodeEvent).where(NodeEvent.node_id == node_id))
        assert stored is not None
        assert stored.payload == {"boot_id": "boot-a"}


def test_agent_gateway_event_replay_preserves_json_value_types(
    agent_api_client: TestClient,
    agent_session_factory: sessionmaker[Session],
    enrolled_agent,
) -> None:
    node_id, certificate_pem, private_key = enrolled_agent
    original_event = _event(1, payload={"enabled": True})
    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _authenticate(websocket, node_id, certificate_pem, private_key)
        websocket.send_json(_event_batch(original_event))
        assert websocket.receive_json()["through_sequence"] == 1

        websocket.send_json(_event_batch(original_event))
        assert websocket.receive_json()["through_sequence"] == 1

        websocket.send_json(_event_batch(_event(1, payload={"enabled": 1})))
        assert websocket.receive_json() == EVENT_CONFLICT

    with agent_session_factory() as session:
        stored = session.scalar(select(NodeEvent).where(NodeEvent.node_id == node_id))
        assert stored is not None
        assert stored.payload == {"enabled": True}


@pytest.mark.parametrize(
    "message",
    [
        {"type": "heartbeat", "occurred_at": "2026-07-16T00:00:00+00:00"},
        {
            "protocol_version": 2,
            "type": "heartbeat",
            "occurred_at": "2026-07-16T00:00:00+00:00",
        },
        {"protocol_version": 1, "occurred_at": "2026-07-16T00:00:00+00:00"},
        {"protocol_version": 1, "type": "unknown"},
    ],
)
def test_agent_gateway_rejects_missing_or_unsupported_wire_discriminators(
    agent_api_client: TestClient,
    enrolled_agent,
    message: dict[str, object],
) -> None:
    node_id, certificate_pem, private_key = enrolled_agent
    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _authenticate(websocket, node_id, certificate_pem, private_key)
        websocket.send_json(message)
        assert websocket.receive_json() == INVALID_MESSAGE


def test_agent_gateway_rejects_unsupported_authentication_version(
    agent_api_client: TestClient,
    enrolled_agent,
) -> None:
    node_id, certificate_pem, private_key = enrolled_agent
    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _send_authentication(
            websocket,
            node_id,
            certificate_pem,
            private_key,
            protocol_version=2,
        )
        assert websocket.receive_json() == INVALID_MESSAGE
        with pytest.raises(WebSocketDisconnect) as rejected:
            websocket.receive_json()

    assert rejected.value.code == 4400


def test_agent_gateway_rejects_binary_malformed_and_oversized_frames(
    agent_api_client: TestClient,
    enrolled_agent,
    monkeypatch,
) -> None:
    import visiox_api.ws.agents as agent_gateway

    node_id, certificate_pem, private_key = enrolled_agent
    validated_frames: list[str | bytes] = []
    original_validator = agent_gateway.validate_raw_message_size

    def track_raw_validation(message: str | bytes) -> None:
        validated_frames.append(message)
        original_validator(message)

    monkeypatch.setattr(agent_gateway, "validate_raw_message_size", track_raw_validation)
    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _authenticate(websocket, node_id, certificate_pem, private_key)
        validated_frames.clear()

        websocket.send_bytes(b"not-text")
        assert websocket.receive_json() == INVALID_MESSAGE

        malformed = "{not-json"
        websocket.send_text(malformed)
        assert websocket.receive_json() == INVALID_MESSAGE
        assert validated_frames == [malformed]

        settings = agent_api_client.app.dependency_overrides[get_settings]()
        raw_heartbeat = json.dumps(
            {
                "protocol_version": 1,
                "type": "heartbeat",
                "occurred_at": "2026-07-16T00:00:00+00:00",
            }
        )
        settings.agent_max_ws_message_bytes = len(raw_heartbeat.encode("utf-8")) - 1
        websocket.send_text(raw_heartbeat)
        assert websocket.receive_json() == INVALID_MESSAGE
        assert validated_frames[-1] == raw_heartbeat


def test_agent_gateway_renews_certificate_for_authenticated_node(
    agent_api_client: TestClient,
    agent_session_factory: sessionmaker[Session],
    enrolled_agent,
) -> None:
    node_id, old_certificate_pem, private_key = enrolled_agent
    old_identity = _stored_certificate_identity(agent_session_factory, node_id)
    with agent_session_factory() as session:
        original_node = session.get(ComputeNode, node_id)
        assert original_node is not None
        original_name = original_node.name
        original_pool_id = original_node.resource_pool_id
    renewal_csr = _csr_for_key(private_key, "attempted-node-rename")

    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _authenticate(websocket, node_id, old_certificate_pem, private_key)
        websocket.send_json(
            {
                "protocol_version": 1,
                "type": "certificate_renewal_request",
                "renewal_request_id": "renewal-happy-path-001",
                "csr_pem": renewal_csr,
            }
        )
        candidate = websocket.receive_json()
        assert candidate["type"] == "certificate_renewal_candidate"
        assert _stored_certificate_identity(agent_session_factory, node_id) == old_identity
        websocket.send_json(
            {
                "protocol_version": 1,
                "type": "certificate_renewal_ack",
                "renewal_request_id": candidate["renewal_request_id"],
                "certificate_fingerprint_sha256": candidate["certificate_fingerprint_sha256"],
            }
        )
        activated = websocket.receive_json()

    assert activated == {
        "protocol_version": 1,
        "type": "certificate_renewal_activated",
        "renewal_request_id": candidate["renewal_request_id"],
        "certificate_fingerprint_sha256": candidate["certificate_fingerprint_sha256"],
    }
    new_certificate_pem = candidate["certificate_pem"]
    new_certificate = x509.load_pem_x509_certificate(new_certificate_pem.encode())
    common_names = new_certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    assert [name.value for name in common_names] == [node_id]
    assert _stored_certificate_identity(agent_session_factory, node_id) != old_identity
    with agent_session_factory() as session:
        renewed_node = session.get(ComputeNode, node_id)
        assert renewed_node is not None
        assert renewed_node.name == original_name
        assert renewed_node.resource_pool_id == original_pool_id

    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _send_authentication(websocket, node_id, old_certificate_pem, private_key)
        with pytest.raises(WebSocketDisconnect) as rejected:
            websocket.receive_json()
    assert rejected.value.code == 4403

    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _authenticate(websocket, node_id, new_certificate_pem, private_key)


def test_agent_gateway_replays_candidate_until_renewal_ack_activates_it(
    agent_api_client: TestClient,
    agent_session_factory: sessionmaker[Session],
    enrolled_agent,
) -> None:
    node_id, old_certificate_pem, private_key = enrolled_agent
    request_id = "renewal-response-loss-001"
    renewal_csr = _csr_for_key(private_key, node_id)
    old_identity = _stored_certificate_identity(agent_session_factory, node_id)

    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _authenticate(websocket, node_id, old_certificate_pem, private_key)
        websocket.send_json(
            {
                "protocol_version": 1,
                "type": "certificate_renewal_request",
                "renewal_request_id": request_id,
                "csr_pem": renewal_csr,
            }
        )
        candidate = websocket.receive_json()

    assert candidate["protocol_version"] == 1
    assert candidate["type"] == "certificate_renewal_candidate"
    assert candidate["renewal_request_id"] == request_id
    assert "PRIVATE KEY" not in json.dumps(candidate)
    with agent_session_factory() as session:
        node = session.get(ComputeNode, node_id)
        assert node is not None
        assert (node.certificate_serial, node.certificate_fingerprint) == old_identity
        assert node.pending_certificate_pem == candidate["certificate_pem"]
        assert node.pending_renewal_request_id == request_id

    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _authenticate(websocket, node_id, old_certificate_pem, private_key)
        websocket.send_json(
            {
                "protocol_version": 1,
                "type": "certificate_renewal_request",
                "renewal_request_id": request_id,
                "csr_pem": renewal_csr,
            }
        )
        replayed_candidate = websocket.receive_json()
        assert replayed_candidate == candidate
        websocket.send_json(
            {
                "protocol_version": 1,
                "type": "certificate_renewal_ack",
                "renewal_request_id": request_id,
                "certificate_fingerprint_sha256": candidate["certificate_fingerprint_sha256"],
            }
        )
        activated = websocket.receive_json()

    assert activated == {
        "protocol_version": 1,
        "type": "certificate_renewal_activated",
        "renewal_request_id": request_id,
        "certificate_fingerprint_sha256": candidate["certificate_fingerprint_sha256"],
    }
    new_certificate_pem = candidate["certificate_pem"]
    with agent_session_factory() as session:
        node = session.get(ComputeNode, node_id)
        assert node is not None
        assert node.certificate_fingerprint == candidate["certificate_fingerprint_sha256"]
        assert node.pending_certificate_pem is None
        assert node.pending_renewal_request_id is None

    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _send_authentication(websocket, node_id, old_certificate_pem, private_key)
        with pytest.raises(WebSocketDisconnect) as rejected:
            websocket.receive_json()
    assert rejected.value.code == 4403

    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _authenticate(websocket, node_id, new_certificate_pem, private_key)


def test_agent_gateway_recovers_when_activation_confirmation_is_lost(
    agent_api_client: TestClient,
    agent_session_factory: sessionmaker[Session],
    enrolled_agent,
) -> None:
    node_id, old_certificate_pem, private_key = enrolled_agent
    request_id = "renewal-activation-loss-001"
    renewal_csr = _csr_for_key(private_key, node_id)
    old_identity = _stored_certificate_identity(agent_session_factory, node_id)

    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _authenticate(websocket, node_id, old_certificate_pem, private_key)
        websocket.send_json(
            {
                "protocol_version": 1,
                "type": "certificate_renewal_request",
                "renewal_request_id": request_id,
                "csr_pem": renewal_csr,
            }
        )
        candidate = websocket.receive_json()

    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _authenticate(websocket, node_id, old_certificate_pem, private_key)
        websocket.send_json(
            {
                "protocol_version": 1,
                "type": "certificate_renewal_ack",
                "renewal_request_id": request_id,
                "certificate_fingerprint_sha256": candidate["certificate_fingerprint_sha256"],
            }
        )
        # Deliberately leave certificate_renewal_activated unread, as after a response loss.
        for _ in range(50):
            if _stored_certificate_identity(agent_session_factory, node_id) != old_identity:
                break
            sleep(0.01)
        else:
            pytest.fail("renewal ACK was not committed before the connection closed")

    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _send_authentication(websocket, node_id, old_certificate_pem, private_key)
        with pytest.raises(WebSocketDisconnect) as rejected:
            websocket.receive_json()
    assert rejected.value.code == 4403

    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _authenticate(websocket, node_id, candidate["certificate_pem"], private_key)


def test_agent_gateway_rejects_different_renewal_key_without_state_change(
    agent_api_client: TestClient,
    agent_session_factory: sessionmaker[Session],
    enrolled_agent,
) -> None:
    node_id, certificate_pem, private_key = enrolled_agent
    original_identity = _stored_certificate_identity(agent_session_factory, node_id)
    _, different_key_csr = new_agent_csr(node_id)

    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _authenticate(websocket, node_id, certificate_pem, private_key)
        websocket.send_json(
            {
                "protocol_version": 1,
                "type": "certificate_renewal_request",
                "renewal_request_id": "renewal-other-key-001",
                "csr_pem": different_key_csr,
            }
        )
        assert websocket.receive_json() == INVALID_CERTIFICATE_REQUEST

    assert _stored_certificate_identity(agent_session_factory, node_id) == original_identity
    with agent_session_factory() as session:
        node = session.get(ComputeNode, node_id)
        assert node is not None
        assert node.pending_certificate_pem is None
        assert node.pending_certificate_fingerprint is None
        assert node.pending_renewal_request_id is None


def test_agent_gateway_replays_pending_candidate_for_fresh_same_key_csr(
    agent_api_client: TestClient,
    agent_session_factory: sessionmaker[Session],
    enrolled_agent,
) -> None:
    node_id, certificate_pem, private_key = enrolled_agent
    first_request_id = "renewal-fresh-csr-001"
    second_request_id = "renewal-fresh-csr-002"
    first_csr = _csr_for_key(private_key, node_id)
    fresh_csr = _csr_for_key(private_key, "fresh-csr-encoding")
    assert fresh_csr != first_csr
    first_request = {
        "protocol_version": 1,
        "type": "certificate_renewal_request",
        "renewal_request_id": first_request_id,
        "csr_pem": first_csr,
    }
    assert "PRIVATE KEY" not in json.dumps(first_request)

    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _authenticate(websocket, node_id, certificate_pem, private_key)
        websocket.send_json(first_request)
        candidate = websocket.receive_json()

    assert candidate["type"] == "certificate_renewal_candidate"
    assert "private" not in json.dumps(candidate).lower()

    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _authenticate(websocket, node_id, certificate_pem, private_key)
        websocket.send_json(
            {
                "protocol_version": 1,
                "type": "certificate_renewal_request",
                "renewal_request_id": second_request_id,
                "csr_pem": fresh_csr,
            }
        )
        replayed = websocket.receive_json()
        assert replayed["renewal_request_id"] == second_request_id
        assert replayed["certificate_pem"] == candidate["certificate_pem"]
        assert (
            replayed["certificate_fingerprint_sha256"]
            == candidate["certificate_fingerprint_sha256"]
        )
        websocket.send_json(
            {
                "protocol_version": 1,
                "type": "certificate_renewal_ack",
                "renewal_request_id": second_request_id,
                "certificate_fingerprint_sha256": replayed["certificate_fingerprint_sha256"],
            }
        )
        assert websocket.receive_json()["type"] == "certificate_renewal_activated"

    with agent_session_factory() as session:
        node = session.get(ComputeNode, node_id)
        assert node is not None
        assert node.certificate_fingerprint == candidate["certificate_fingerprint_sha256"]
        assert node.pending_certificate_pem is None


def test_agent_gateway_reissues_expired_pending_candidate(
    agent_api_client: TestClient,
    agent_session_factory: sessionmaker[Session],
    enrolled_agent,
) -> None:
    node_id, certificate_pem, private_key = enrolled_agent
    first_csr = _csr_for_key(private_key, node_id)

    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _authenticate(websocket, node_id, certificate_pem, private_key)
        websocket.send_json(
            {
                "protocol_version": 1,
                "type": "certificate_renewal_request",
                "renewal_request_id": "renewal-expired-pending-001",
                "csr_pem": first_csr,
            }
        )
        expired_candidate = websocket.receive_json()

    with agent_session_factory() as session:
        node = session.get(ComputeNode, node_id)
        assert node is not None
        node.pending_certificate_expires_at = datetime.now(UTC) - timedelta(minutes=1)
        session.commit()

    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _authenticate(websocket, node_id, certificate_pem, private_key)
        websocket.send_json(
            {
                "protocol_version": 1,
                "type": "certificate_renewal_request",
                "renewal_request_id": "renewal-expired-pending-002",
                "csr_pem": _csr_for_key(private_key, "reissued-pending"),
            }
        )
        reissued_candidate = websocket.receive_json()

    assert reissued_candidate["type"] == "certificate_renewal_candidate"
    assert (
        reissued_candidate["certificate_fingerprint_sha256"]
        != expired_candidate["certificate_fingerprint_sha256"]
    )
    with agent_session_factory() as session:
        node = session.get(ComputeNode, node_id)
        assert node is not None
        assert node.pending_renewal_request_id == "renewal-expired-pending-002"
        assert node.pending_certificate_expires_at is not None
        pending_expires_at = node.pending_certificate_expires_at
        if pending_expires_at.tzinfo is None:
            pending_expires_at = pending_expires_at.replace(tzinfo=UTC)
        assert pending_expires_at > datetime.now(UTC)


def test_agent_gateway_promotes_pending_candidate_after_active_certificate_expires(
    agent_api_client: TestClient,
    agent_session_factory: sessionmaker[Session],
    enrolled_agent,
) -> None:
    node_id, certificate_pem, private_key = enrolled_agent
    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _authenticate(websocket, node_id, certificate_pem, private_key)
        websocket.send_json(
            {
                "protocol_version": 1,
                "type": "certificate_renewal_request",
                "renewal_request_id": "renewal-expired-active-001",
                "csr_pem": _csr_for_key(private_key, node_id),
            }
        )
        candidate = websocket.receive_json()

    expired_certificate_pem = _expired_certificate(agent_api_client, node_id, private_key)
    expired_certificate = x509.load_pem_x509_certificate(expired_certificate_pem.encode())
    with agent_session_factory() as session:
        node = session.get(ComputeNode, node_id)
        assert node is not None
        node.certificate_serial = str(expired_certificate.serial_number)
        node.certificate_fingerprint = expired_certificate.fingerprint(hashes.SHA256()).hex()
        node.certificate_expires_at = expired_certificate.not_valid_after_utc
        session.commit()

    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _authenticate(websocket, node_id, candidate["certificate_pem"], private_key)

    with agent_session_factory() as session:
        node = session.get(ComputeNode, node_id)
        assert node is not None
        assert node.certificate_fingerprint == candidate["certificate_fingerprint_sha256"]
        assert node.pending_certificate_pem is None
        assert node.pending_renewal_request_id is None

    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _send_authentication(websocket, node_id, expired_certificate_pem, private_key)
        with pytest.raises(WebSocketDisconnect) as rejected:
            websocket.receive_json()
    assert rejected.value.code == 4403


def test_agent_gateway_rejects_stale_concurrent_sessions_after_renewal_activation(
    agent_api_client: TestClient,
    agent_session_factory: sessionmaker[Session],
    enrolled_agent,
) -> None:
    node_id, certificate_pem, private_key = enrolled_agent
    with (
        agent_api_client.websocket_connect("/agent/v1/connect") as acknowledging,
        agent_api_client.websocket_connect("/agent/v1/connect") as stale_heartbeat,
        agent_api_client.websocket_connect("/agent/v1/connect") as stale_inventory,
        agent_api_client.websocket_connect("/agent/v1/connect") as stale_events,
    ):
        _authenticate(acknowledging, node_id, certificate_pem, private_key)
        _authenticate(stale_heartbeat, node_id, certificate_pem, private_key)
        _authenticate(stale_inventory, node_id, certificate_pem, private_key)
        _authenticate(stale_events, node_id, certificate_pem, private_key)

        acknowledging.send_json(
            {
                "protocol_version": 1,
                "type": "certificate_renewal_request",
                "renewal_request_id": "renewal-stale-session-001",
                "csr_pem": _csr_for_key(private_key, node_id),
            }
        )
        candidate = acknowledging.receive_json()
        acknowledging.send_json(
            {
                "protocol_version": 1,
                "type": "certificate_renewal_ack",
                "renewal_request_id": candidate["renewal_request_id"],
                "certificate_fingerprint_sha256": candidate["certificate_fingerprint_sha256"],
            }
        )
        assert acknowledging.receive_json()["type"] == "certificate_renewal_activated"

        acknowledging.send_json(
            {
                "protocol_version": 1,
                "type": "inventory",
                "architecture": "arm64",
                "platform_kind": "jetson",
                "capabilities": {"source": "acknowledging"},
                "resources": {"source": "acknowledging"},
                "fingerprint": {"source": "acknowledging"},
                "agent_version": "0.1.0-test",
            }
        )
        acknowledging.send_json(
            {
                "protocol_version": 1,
                "type": "heartbeat",
                "occurred_at": datetime.now(UTC).isoformat(),
            }
        )
        acknowledging.send_json(_event_batch(_event(1, payload={"source": "acknowledging"})))
        assert acknowledging.receive_json()["through_sequence"] == 1

        with agent_session_factory() as session:
            node = session.get(ComputeNode, node_id)
            assert node is not None
            expected_last_seen_at = node.last_seen_at
            expected_resources = dict(node.resources)
            expected_events = session.scalar(
                select(func.count()).select_from(NodeEvent).where(NodeEvent.node_id == node_id)
            )

        stale_heartbeat.send_json(
            {
                "protocol_version": 1,
                "type": "heartbeat",
                "occurred_at": datetime.now(UTC).isoformat(),
            }
        )
        stale_heartbeat.send_json(_event_batch(_event(2, payload={"source": "stale-heartbeat"})))
        with pytest.raises(WebSocketDisconnect) as heartbeat_rejected:
            stale_heartbeat.receive_json()
        assert heartbeat_rejected.value.code == 4403

        stale_inventory.send_json(
            {
                "protocol_version": 1,
                "type": "inventory",
                "architecture": "arm64",
                "platform_kind": "jetson",
                "capabilities": {"source": "stale"},
                "resources": {"source": "stale"},
                "fingerprint": {"source": "stale"},
                "agent_version": "0.1.0-stale",
            }
        )
        stale_inventory.send_json(_event_batch(_event(3, payload={"source": "stale-inventory"})))
        with pytest.raises(WebSocketDisconnect) as inventory_rejected:
            stale_inventory.receive_json()
        assert inventory_rejected.value.code == 4403

        stale_events.send_json(_event_batch(_event(4, payload={"source": "stale-events"})))
        with pytest.raises(WebSocketDisconnect) as events_rejected:
            stale_events.receive_json()
        assert events_rejected.value.code == 4403

    with agent_session_factory() as session:
        node = session.get(ComputeNode, node_id)
        assert node is not None
        assert node.last_seen_at == expected_last_seen_at
        assert node.resources == expected_resources
        assert session.scalar(
            select(func.count()).select_from(NodeEvent).where(NodeEvent.node_id == node_id)
        ) == expected_events


def test_agent_gateway_rejects_malformed_renewal_without_metadata_change(
    agent_api_client: TestClient,
    agent_session_factory: sessionmaker[Session],
    enrolled_agent,
) -> None:
    node_id, certificate_pem, private_key = enrolled_agent
    original_identity = _stored_certificate_identity(agent_session_factory, node_id)
    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _authenticate(websocket, node_id, certificate_pem, private_key)
        websocket.send_json(
            {
                "protocol_version": 1,
                "type": "certificate_renewal_request",
                "renewal_request_id": "renewal-malformed-001",
                "csr_pem": "x" * 120,
            }
        )
        assert websocket.receive_json() == INVALID_CERTIFICATE_REQUEST

    assert _stored_certificate_identity(agent_session_factory, node_id) == original_identity


def test_heartbeat_does_not_clear_draining_state(
    agent_api_client: TestClient,
    agent_session_factory: sessionmaker[Session],
    enrolled_agent,
) -> None:
    node_id, certificate_pem, private_key = enrolled_agent
    previous_seen_at = datetime.now(UTC) - timedelta(days=1)
    with agent_session_factory() as session:
        node = session.get(ComputeNode, node_id)
        assert node is not None
        node.last_seen_at = previous_seen_at
        session.commit()

    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _authenticate(websocket, node_id, certificate_pem, private_key)
        drained = agent_api_client.post(f"/nodes/{node_id}/drain")
        assert drained.status_code == 200
        websocket.send_json(
            {
                "protocol_version": 1,
                "type": "heartbeat",
                "occurred_at": datetime.now(UTC).isoformat(),
            }
        )
        websocket.send_json(_event_batch(_event(1)))
        websocket.receive_json()

    node = agent_api_client.get(f"/nodes/{node_id}").json()
    assert node["status"] == "draining"
    last_seen_at = datetime.fromisoformat(node["last_seen_at"])
    if last_seen_at.tzinfo is None:
        last_seen_at = last_seen_at.replace(tzinfo=UTC)
    assert last_seen_at > previous_seen_at


def test_inventory_mismatch_marks_node_incompatible(
    agent_api_client: TestClient,
    agent_session_factory: sessionmaker[Session],
    enrolled_agent,
) -> None:
    node_id, certificate_pem, private_key = enrolled_agent
    with agent_session_factory() as session:
        node = session.get(ComputeNode, node_id)
        assert node is not None
        original_pool_id = node.resource_pool_id

    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        _authenticate(websocket, node_id, certificate_pem, private_key)
        websocket.send_json(
            {
                "protocol_version": 1,
                "type": "inventory",
                "architecture": "amd64",
                "platform_kind": "x86_nvidia",
                "capabilities": {"tasks": ["detect"]},
                "resources": {"gpu_memory_bytes": 8589934592},
                "fingerprint": {"driver": "570.1"},
                "agent_version": "0.1.1",
            }
        )
        websocket.send_json(
            {
                "protocol_version": 1,
                "type": "heartbeat",
                "occurred_at": datetime.now(UTC).isoformat(),
            }
        )
        websocket.send_json(_event_batch(_event(1)))
        websocket.receive_json()

    node = agent_api_client.get(f"/nodes/{node_id}").json()
    assert node["status"] == "incompatible"
    assert node["resource_pool_id"] == original_pool_id
    with agent_session_factory() as session:
        pool = session.get(ResourcePool, original_pool_id)
        assert pool is not None
        assert pool.name == "jetson-default"
