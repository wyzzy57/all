import base64
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


def _enrollment_payload(token: str, name: str = "edge-01") -> dict[str, object]:
    return {
        "protocol_version": 1,
        "token": token,
        "node_name": name,
        "architecture": "arm64",
        "platform_kind": "jetson",
        "agent_version": "0.1.0",
        "csr_pem": new_agent_csr(name)[1],
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


def test_inventory_mismatch_marks_node_incompatible_without_pool_reassignment(
    agent_api_client,
    agent_session_factory: sessionmaker[Session],
) -> None:
    from visiox_api.schemas.agent_protocol import InventoryMessage
    from visiox_api.services.node_registry import NodeRegistryService

    enrolled = _enroll(agent_api_client)
    node_id = enrolled.json()["node_id"]
    settings = agent_api_client.app.dependency_overrides[get_settings]()
    with agent_session_factory() as session:
        node = session.get(ComputeNode, node_id)
        original_pool_id = node.resource_pool_id  # type: ignore[union-attr]
        inventory = InventoryMessage(
            protocol_version=1,
            type="inventory",
            architecture="amd64",
            platform_kind="x86_nvidia",
            capabilities={"gpu": "RTX"},
            resources={"vram_bytes": 1},
            fingerprint={"machine": "redacted"},
            agent_version="0.1.1",
        )

        updated = NodeRegistryService(session, settings).apply_inventory(node_id, inventory)

        assert updated.status == "incompatible"
        assert updated.resource_pool_id == original_pool_id
        assert "compatibility_error" in updated.fingerprint
        assert "edge-01" not in updated.fingerprint["compatibility_error"]


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
        EventBatchMessage(
            protocol_version=1,
            type="event_batch",
            events=[
                {
                    "sequence": 1,
                    "event_type": "progress",
                    "payload": {"blob": "x" * (1024 * 1024)},
                    "occurred_at": datetime.now(UTC),
                }
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
