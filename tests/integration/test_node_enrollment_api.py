import base64
import json
from uuid import uuid4
from datetime import UTC, datetime, timedelta

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography.x509.oid import NameOID
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from visiox_common.settings import get_settings
from visiox_db.models import AgentEnrollmentToken, ComputeNode, ResourcePool


def new_agent_csr(name: str) -> tuple[ed25519.Ed25519PrivateKey, str]:
    key = ed25519.Ed25519PrivateKey.generate()
    csr = x509.CertificateSigningRequestBuilder().subject_name(
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    ).sign(key, algorithm=None)
    return key, csr.public_bytes(serialization.Encoding.PEM).decode()


def _create_token(client, name: str = "factory-a") -> str:
    response = client.post("/agent/v1/enrollment-tokens", json={"name": name})
    assert response.status_code == 201
    return response.json()["token"]


def _enrollment_payload(
    token: str,
    name: str = "edge-01",
    *,
    enrollment_request_id: str | None = None,
    csr_pem: str | None = None,
) -> dict[str, object]:
    return {
        "protocol_version": 1,
        "token": token,
        "enrollment_request_id": enrollment_request_id or f"enroll-{uuid4().hex}",
        "node_name": name,
        "architecture": "arm64",
        "platform_kind": "jetson",
        "agent_version": "0.1.0",
        "csr_pem": csr_pem or new_agent_csr(name)[1],
    }


def _enroll(client, name: str = "edge-01"):
    token = _create_token(client, f"token-{name}")
    response = client.post("/agent/v1/enroll", json=_enrollment_payload(token, name))
    assert response.status_code == 201
    return response


def test_enrollment_token_is_returned_once_and_consumed(
    agent_api_client,
    agent_session_factory: sessionmaker[Session],
) -> None:
    token_response = agent_api_client.post("/agent/v1/enrollment-tokens", json={"name": "factory-a"})
    raw_token = token_response.json()["token"]
    _, csr_pem = new_agent_csr("edge-01")
    enrolled = agent_api_client.post(
        "/agent/v1/enroll",
        json={
            "protocol_version": 1,
            "token": raw_token,
            "enrollment_request_id": "enroll-token-consumption-001",
            "node_name": "edge-01",
            "architecture": "arm64",
            "platform_kind": "jetson",
            "agent_version": "0.1.0",
            "csr_pem": csr_pem,
        },
    )
    replay = agent_api_client.post(
        "/agent/v1/enroll",
        json={
            "protocol_version": 1,
            "token": raw_token,
            "enrollment_request_id": "enroll-token-consumption-replay-001",
            "node_name": "edge-02",
            "architecture": "arm64",
            "platform_kind": "jetson",
            "agent_version": "0.1.0",
            "csr_pem": new_agent_csr("edge-02")[1],
        },
    )
    nodes = agent_api_client.get("/nodes")

    assert token_response.status_code == 201
    assert enrolled.status_code == 201
    assert enrolled.json()["node_id"]
    assert "PRIVATE KEY" not in enrolled.text
    assert replay.status_code == 409
    assert nodes.json()["items"][0]["name"] == "edge-01"
    assert nodes.json()["items"][0]["status"] == "enrolling"
    with agent_session_factory() as session:
        stored_token = session.scalar(select(AgentEnrollmentToken))
        assert stored_token is not None
        assert stored_token.token_hash != raw_token
        assert raw_token not in repr(stored_token.__dict__)
        assert stored_token.used_at is not None
        assert stored_token.node_id == enrolled.json()["node_id"]


def test_enrollment_replays_the_committed_response_after_response_loss(
    agent_api_client,
    agent_session_factory: sessionmaker[Session],
) -> None:
    raw_token = _create_token(agent_api_client, "response-loss")
    _, csr_pem = new_agent_csr("edge-response-loss")
    request_id = "enroll-response-loss-001"
    payload = _enrollment_payload(
        raw_token,
        "edge-response-loss",
        enrollment_request_id=request_id,
        csr_pem=csr_pem,
    )

    committed = agent_api_client.post("/agent/v1/enroll", json=payload)
    recovered = agent_api_client.post("/agent/v1/enroll", json=payload)

    assert committed.status_code == 201
    assert recovered.status_code == 201
    assert recovered.json() == committed.json()
    assert committed.json()["enrollment_request_id"] == request_id
    assert "PRIVATE KEY" not in committed.text
    with agent_session_factory() as session:
        tokens = list(session.scalars(select(AgentEnrollmentToken)))
        nodes = list(session.scalars(select(ComputeNode)))
        assert len(tokens) == 1
        assert len(nodes) == 1
        assert tokens[0].enrollment_request_id == request_id
        assert tokens[0].enrollment_csr_fingerprint
        assert "PRIVATE KEY" not in (tokens[0].enrollment_certificate_pem or "")


def test_enrollment_rejects_mismatched_replay_after_commit(agent_api_client) -> None:
    raw_token = _create_token(agent_api_client, "mismatched-replay")
    _, csr_pem = new_agent_csr("edge-mismatched-replay")
    payload = _enrollment_payload(
        raw_token,
        "edge-mismatched-replay",
        enrollment_request_id="enroll-mismatched-replay-001",
        csr_pem=csr_pem,
    )

    committed = agent_api_client.post("/agent/v1/enroll", json=payload)
    assert committed.status_code == 201

    mismatches = [
        {"enrollment_request_id": "enroll-mismatched-replay-002"},
        {"csr_pem": new_agent_csr("edge-mismatched-replay")[1]},
        {"node_name": "edge-mismatched-replay-other"},
        {"platform_kind": "x86_nvidia"},
    ]
    for mismatch in mismatches:
        replay = {**payload, **mismatch}
        rejected = agent_api_client.post("/agent/v1/enroll", json=replay)
        assert rejected.status_code == 409


def test_node_listing_marks_stale_online_node_offline(
    agent_api_client,
    agent_session_factory: sessionmaker[Session],
) -> None:
    enrolled = _enroll(agent_api_client)
    node_id = enrolled.json()["node_id"]
    settings = agent_api_client.app.dependency_overrides[get_settings]()
    stale_at = datetime.now(UTC) - timedelta(seconds=settings.agent_offline_after_seconds + 1)
    with agent_session_factory() as session:
        node = session.get(ComputeNode, node_id)
        assert node is not None
        node.status = "online"
        node.last_seen_at = stale_at
        session.commit()

    response = agent_api_client.get(f"/nodes/{node_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "offline"
    with agent_session_factory() as session:
        assert session.get(ComputeNode, node_id).status == "offline"  # type: ignore[union-attr]


def test_drain_is_administrative_state(
    agent_api_client,
    agent_session_factory: sessionmaker[Session],
) -> None:
    enrolled = _enroll(agent_api_client)
    node_id = enrolled.json()["node_id"]

    drained = agent_api_client.post(f"/nodes/{node_id}/drain")
    with agent_session_factory() as session:
        node = session.get(ComputeNode, node_id)
        assert node is not None
        node.last_seen_at = datetime.now(UTC) - timedelta(days=1)
        session.commit()
    listed = agent_api_client.get("/nodes")
    fetched = agent_api_client.get(f"/nodes/{node_id}")

    assert drained.status_code == 200
    assert drained.json()["status"] == "draining"
    assert listed.json()["items"][0]["status"] == "draining"
    assert fetched.json()["status"] == "draining"


def test_malformed_csr_returns_422_without_consuming_token(agent_api_client) -> None:
    raw_token = _create_token(agent_api_client)
    payload = _enrollment_payload(raw_token)
    payload["csr_pem"] = "x" * 120

    malformed = agent_api_client.post("/agent/v1/enroll", json=payload)
    enrolled = agent_api_client.post("/agent/v1/enroll", json=_enrollment_payload(raw_token))

    assert malformed.status_code == 422
    assert enrolled.status_code == 201


def test_enrollment_rejects_duplicate_name_without_consuming_second_token(agent_api_client) -> None:
    _enroll(agent_api_client, "edge-01")
    second_token = _create_token(agent_api_client, "second")

    duplicate = agent_api_client.post("/agent/v1/enroll", json=_enrollment_payload(second_token, "edge-01"))
    accepted = agent_api_client.post("/agent/v1/enroll", json=_enrollment_payload(second_token, "edge-02"))

    assert duplicate.status_code == 409
    assert accepted.status_code == 201


def test_enrollment_rejects_unsupported_protocol_version(agent_api_client) -> None:
    payload = _enrollment_payload(_create_token(agent_api_client))
    payload["protocol_version"] = 2

    response = agent_api_client.post("/agent/v1/enroll", json=payload)

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("architecture", "platform_kind", "pool_name"),
    [
        ("arm64", "jetson", "jetson-default"),
        ("amd64", "x86_nvidia", "x86-nvidia-default"),
    ],
)
def test_enrollment_assigns_compatible_default_pool(
    agent_api_client,
    agent_session_factory: sessionmaker[Session],
    architecture: str,
    platform_kind: str,
    pool_name: str,
) -> None:
    token = _create_token(agent_api_client, pool_name)
    payload = _enrollment_payload(token, pool_name)
    payload.update({"architecture": architecture, "platform_kind": platform_kind})

    enrolled = agent_api_client.post("/agent/v1/enroll", json=payload)
    pools = agent_api_client.get("/resource-pools")

    assert enrolled.status_code == 201
    assert pools.status_code == 200
    assert pool_name in {item["name"] for item in pools.json()["items"]}
    with agent_session_factory() as session:
        node = session.get(ComputeNode, enrolled.json()["node_id"])
        pool = session.get(ResourcePool, node.resource_pool_id)  # type: ignore[union-attr]
        assert pool is not None
        assert pool.name == pool_name


@pytest.mark.parametrize(
    ("architecture", "platform_kind", "valid_architecture"),
    [
        ("amd64", "jetson", "arm64"),
        ("arm64", "x86_nvidia", "amd64"),
    ],
)
def test_enrollment_rejects_incompatible_pool_pair_without_consuming_token(
    agent_api_client,
    architecture: str,
    platform_kind: str,
    valid_architecture: str,
) -> None:
    token = _create_token(agent_api_client, f"invalid-{platform_kind}")
    payload = _enrollment_payload(token, f"invalid-{platform_kind}")
    payload.update({"architecture": architecture, "platform_kind": platform_kind})

    rejected = agent_api_client.post("/agent/v1/enroll", json=payload)
    payload["architecture"] = valid_architecture
    accepted = agent_api_client.post("/agent/v1/enroll", json=payload)

    assert rejected.status_code == 409
    assert accepted.status_code == 201


@pytest.mark.parametrize(
    (
        "enrollment_architecture",
        "enrollment_platform",
        "inventory_architecture",
        "inventory_platform",
    ),
    [
        ("arm64", "jetson", "amd64", "jetson"),
        ("amd64", "x86_nvidia", "arm64", "x86_nvidia"),
    ],
)
def test_inventory_incompatible_pool_pair_preserves_enrollment_pool(
    agent_api_client,
    agent_session_factory: sessionmaker[Session],
    enrollment_architecture: str,
    enrollment_platform: str,
    inventory_architecture: str,
    inventory_platform: str,
) -> None:
    from visiox_api.schemas.agent_protocol import InventoryMessage
    from visiox_api.services.node_registry import NodeRegistryService

    token = _create_token(agent_api_client, f"inventory-{enrollment_platform}")
    payload = _enrollment_payload(token, f"inventory-{enrollment_platform}")
    payload.update(
        {"architecture": enrollment_architecture, "platform_kind": enrollment_platform}
    )
    enrolled = agent_api_client.post("/agent/v1/enroll", json=payload)
    assert enrolled.status_code == 201
    node_id = enrolled.json()["node_id"]
    settings = agent_api_client.app.dependency_overrides[get_settings]()
    with agent_session_factory() as session:
        node = session.get(ComputeNode, node_id)
        original_pool_id = node.resource_pool_id  # type: ignore[union-attr]
        node_name = node.name  # type: ignore[union-attr]
        inventory = InventoryMessage(
            protocol_version=1,
            type="inventory",
            architecture=inventory_architecture,
            platform_kind=inventory_platform,
            capabilities={"gpu": "RTX"},
            resources={"vram_bytes": 1},
            fingerprint={"machine": "redacted"},
            agent_version="0.1.1",
        )

        updated = NodeRegistryService(session, settings).apply_inventory(node_id, inventory)

        assert updated.status == "incompatible"
        assert updated.resource_pool_id == original_pool_id
        assert "compatibility_error" in updated.fingerprint
        assert node_name not in updated.fingerprint["compatibility_error"]


def test_enrollment_recovers_when_concurrent_pool_creation_wins(
    agent_api_client,
    agent_session_factory: sessionmaker[Session],
    monkeypatch,
) -> None:
    from visiox_api.schemas.agent_protocol import EnrollmentRequest
    from visiox_api.services.node_registry import NodeRegistryService

    token = _create_token(agent_api_client, "pool-race")
    request = EnrollmentRequest.model_validate(_enrollment_payload(token, "pool-race"))
    settings = agent_api_client.app.dependency_overrides[get_settings]()
    with agent_session_factory() as session:
        winning_pool = ResourcePool(
            name="jetson-default",
            kind="jetson",
            selector={"architecture": "arm64", "platform_kind": "jetson"},
            compatibility_policy={"architecture": "arm64", "platform_kind": "jetson"},
            enabled=True,
        )
        session.add(winning_pool)
        session.commit()
        original_scalar = session.scalar
        hid_winning_pool = False

        def scalar_with_stale_first_pool_read(statement, *args, **kwargs):
            nonlocal hid_winning_pool
            selects_pool = any(
                description.get("entity") is ResourcePool
                for description in statement.column_descriptions
            )
            if selects_pool and not hid_winning_pool:
                hid_winning_pool = True
                return None
            return original_scalar(statement, *args, **kwargs)

        monkeypatch.setattr(session, "scalar", scalar_with_stale_first_pool_read)

        result = NodeRegistryService(session, settings).enroll(request)

        assert hid_winning_pool
        assert result.node.resource_pool_id == winning_pool.id
        stored_token = session.scalar(
            select(AgentEnrollmentToken).where(AgentEnrollmentToken.node_id == result.node.id)
        )
        assert stored_token is not None
        assert stored_token.used_at is not None


@pytest.mark.parametrize("status", ["incompatible", "draining", "disabled"])
def test_mark_seen_preserves_non_online_administrative_and_compatibility_states(
    agent_api_client,
    agent_session_factory: sessionmaker[Session],
    status: str,
) -> None:
    from visiox_api.services.node_registry import NodeRegistryService

    enrolled = _enroll(agent_api_client)
    node_id = enrolled.json()["node_id"]
    settings = agent_api_client.app.dependency_overrides[get_settings]()
    seen_at = datetime.now(UTC)
    with agent_session_factory() as session:
        node = session.get(ComputeNode, node_id)
        node.status = status  # type: ignore[union-attr]
        session.commit()

        updated = NodeRegistryService(session, settings).mark_seen(node_id, now=seen_at)

        assert updated.status == status
        assert updated.last_seen_at == seen_at


def test_protocol_messages_reject_unknown_fields_and_unsupported_versions() -> None:
    from visiox_api.schemas.agent_protocol import ChallengeMessage, HeartbeatMessage

    nonce = base64.b64encode(b"nonce").decode()
    with pytest.raises(ValidationError):
        ChallengeMessage(protocol_version=1, type="challenge", nonce=nonce, unexpected=True)
    with pytest.raises(ValidationError):
        HeartbeatMessage(protocol_version=2, type="heartbeat", occurred_at=datetime.now(UTC))


@pytest.mark.parametrize(
    ("model_name", "payload"),
    [
        (
            "AuthenticateMessage",
            {
                "protocol_version": 1,
                "type": "authenticate",
                "node_id": "node-1",
                "certificate_pem": "x" * 100,
                "signature": base64.b64encode(b"signature").decode(),
            },
        ),
        (
            "InventoryMessage",
            {
                "protocol_version": 1,
                "type": "inventory",
                "architecture": "arm64",
                "platform_kind": "jetson",
                "capabilities": {},
                "resources": {},
                "fingerprint": {},
                "agent_version": "0.1.0",
            },
        ),
        (
            "HeartbeatMessage",
            {
                "protocol_version": 1,
                "type": "heartbeat",
                "occurred_at": datetime.now(UTC),
            },
        ),
        (
            "EventBatchMessage",
            {"protocol_version": 1, "type": "event_batch", "events": []},
        ),
        (
            "CertificateRenewalRequest",
            {
                "protocol_version": 1,
                "type": "certificate_renewal_request",
                "renewal_request_id": "renewal-discriminator-001",
                "csr_pem": "x" * 100,
            },
        ),
    ],
)
@pytest.mark.parametrize("missing_field", ["protocol_version", "type"])
def test_inbound_messages_require_explicit_wire_discriminators(
    model_name: str,
    payload: dict[str, object],
    missing_field: str,
) -> None:
    from visiox_api.schemas import agent_protocol

    missing_payload = dict(payload)
    missing_payload.pop(missing_field)

    with pytest.raises(ValidationError):
        getattr(agent_protocol, model_name).model_validate(missing_payload)


def test_protocol_messages_enforce_base64_utc_batch_and_json_size_bounds() -> None:
    from visiox_api.schemas.agent_protocol import (
        ChallengeMessage,
        EventBatchMessage,
        InventoryMessage,
    )

    with pytest.raises(ValidationError):
        ChallengeMessage(protocol_version=1, type="challenge", nonce="not base64!")
    with pytest.raises(ValidationError):
        EventBatchMessage(
            protocol_version=1,
            type="event_batch",
            events=[
                {
                    "sequence": index,
                    "event_type": "progress",
                    "payload": {},
                    "occurred_at": datetime.now(UTC),
                }
                for index in range(101)
            ],
        )
    with pytest.raises(ValidationError):
        InventoryMessage(
            protocol_version=1,
            type="inventory",
            architecture="arm64",
            platform_kind="jetson",
            capabilities={"blob": "x" * (64 * 1024)},
            resources={},
            fingerprint={},
            agent_version="0.1.0",
        )
    with pytest.raises(ValidationError):
        EventBatchMessage(
            protocol_version=1,
            type="event_batch",
            events=[
                {
                    "sequence": 0,
                    "event_type": "progress",
                    "payload": {},
                    "occurred_at": datetime.now(UTC),
                }
            ],
        )


def test_raw_message_size_uses_received_utf8_bytes_before_json_parsing() -> None:
    from visiox_api.schemas.agent_protocol import MAX_MESSAGE_BYTES, validate_raw_message_size

    payload = {
        "protocol_version": 1,
        "type": "heartbeat",
        "occurred_at": "2026-07-16T00:00:00Z",
    }
    normalized = json.dumps(payload, separators=(",", ":"))
    raw_message = normalized + (" " * (MAX_MESSAGE_BYTES - len(normalized.encode("utf-8")) + 1))
    assert json.loads(raw_message) == payload

    validate_raw_message_size(raw_message[:-1])

    with pytest.raises(ValueError, match="message exceeds 1 MiB"):
        validate_raw_message_size(raw_message)


def test_pem_limit_uses_utf8_bytes_instead_of_character_count() -> None:
    from visiox_api.schemas.agent_protocol import CertificateRenewalRequest, MAX_PEM_BYTES

    multibyte_pem = "\u754c" * ((MAX_PEM_BYTES // len("\u754c".encode("utf-8"))) + 1)
    assert len(multibyte_pem) < MAX_PEM_BYTES
    assert len(multibyte_pem.encode("utf-8")) > MAX_PEM_BYTES

    CertificateRenewalRequest(
        protocol_version=1,
        type="certificate_renewal_request",
        renewal_request_id="renewal-pem-limit-001",
        csr_pem="x" * MAX_PEM_BYTES,
    )
    with pytest.raises(ValidationError, match="PEM exceeds 16 KiB"):
        CertificateRenewalRequest(
            protocol_version=1,
            type="certificate_renewal_request",
            renewal_request_id="renewal-pem-limit-001",
            csr_pem=multibyte_pem,
        )
