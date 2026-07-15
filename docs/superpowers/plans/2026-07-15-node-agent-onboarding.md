# Visiox M1 Node Agent Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first production-shaped edge control-plane slice: one-time node enrollment, device certificates, persistent outbound WebSocket connectivity, resource inventory, heartbeats, durable Agent event spooling, and real Jetson/x86 resource-pool records.

**Architecture:** The existing Python/FastAPI platform owns enrollment, certificate issuance, node state, resource pools, and the Device Gateway. A new Go Node Agent runs on `linux/amd64` and `linux/arm64`, stores identity/events in bbolt, actively connects to the platform, authenticates by signing a server nonce with its device key, and reports inventory and heartbeats. M1 does not control Docker or deploy models yet; it creates the secure, testable protocol and persistent node foundation consumed by M2-M5.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic, `cryptography`, PostgreSQL/SQLite tests, Go 1.26.5 toolchain, `github.com/coder/websocket` v1.8.15, `go.etcd.io/bbolt` v1.5.0, Docker, systemd.

**Pinned-version references (verified 2026-07-15):** [Go release history](https://go.dev/doc/devel/release), [`coder/websocket` package](https://pkg.go.dev/github.com/coder/websocket), and [`bbolt` v1.5.0](https://pkg.go.dev/go.etcd.io/bbolt@v1.5.0).

## Global Constraints

- The platform has no NVIDIA GPU and must complete every M1 API and scheduling operation without CUDA.
- All GPU work remains on edge nodes; M1 performs resource discovery only.
- Jetson and x86 NVIDIA nodes belong to separate resource pools.
- The Agent initiates HTTPS/WSS connections; no SSH, Docker API, Redis, or Agent management port is exposed to the platform.
- Every command/event protocol type includes `protocol_version: 1` and rejects unsupported versions.
- Device private keys never leave the node; enrollment sends only a CSR.
- Local development may auto-generate a CA only when `VISIOX_ENV=local` and `VISIOX_AGENT_AUTO_GENERATE_CA=true`; production fails closed when CA material is absent.
- Agent state is stored below `/var/lib/visiox-agent`; configuration is stored below `/etc/visiox-agent`.
- Go tests and builds run in `golang:1.26.5-alpine`; the Windows host does not need a Go installation.
- Do not modify or stage the existing unrelated Label Studio Compose changes except where Task 8 explicitly integrates the Agent CA volume and local smoke profile.
- Do not add device, camera, or service seed data. Nodes must come from real enrollment.

---

## File Structure

### Platform persistence and protocol

- `packages/visiox-db/src/visiox_db/models/edge_compute.py`: resource pools, nodes, enrollment tokens, commands, and events.
- `infra/migrations/versions/20260715_0001_edge_compute_nodes.py`: M1 schema migration.
- `apps/api-service/src/visiox_api/schemas/agent_protocol.py`: versioned JSON message contracts shared by enrollment and WebSocket code.
- `apps/api-service/src/visiox_api/services/agent_identity.py`: CA bootstrap, token hashing, CSR signing, and challenge verification.
- `apps/api-service/src/visiox_api/services/node_registry.py`: enrollment and node/inventory persistence rules.
- `apps/api-service/src/visiox_api/routes/nodes.py`: admin-facing node and resource-pool APIs.
- `apps/api-service/src/visiox_api/routes/agent_enrollment.py`: one-time token and enrollment APIs.
- `apps/api-service/src/visiox_api/ws/agents.py`: authenticated Agent WebSocket loop.

### Go Node Agent

- `apps/node-agent/go.mod`: isolated Go module and pinned dependencies.
- `apps/node-agent/cmd/visiox-node-agent/main.go`: process composition and signal handling.
- `apps/node-agent/internal/config/config.go`: environment/file configuration.
- `apps/node-agent/internal/state/store.go`: bbolt identity, event sequence, event spool, and ACK cursor.
- `apps/node-agent/internal/identity/identity.go`: Ed25519 key/CSR generation and enrollment.
- `apps/node-agent/internal/inventory/probe.go`: Jetson/x86 inventory and compatibility fingerprint.
- `apps/node-agent/internal/protocol/messages.go`: Go protocol structs matching Python schemas.
- `apps/node-agent/internal/gateway/client.go`: WSS authentication, heartbeat, inventory, reconnect, and event ACKs.

### Packaging, tests, and operations

- `apps/node-agent/Dockerfile`: reproducible test/build image and local Agent runtime.
- `apps/node-agent/packaging/systemd/visiox-node-agent.service`: production boot service.
- `apps/node-agent/packaging/install.sh`: binary/config/systemd installer.
- `scripts/build-node-agent.ps1`: Dockerized amd64/arm64 builds.
- `scripts/smoke-node-agent.ps1`: end-to-end local enrollment and online-node smoke test.
- `docs/runbooks/node-agent-onboarding.md`: provisioning, registration, diagnosis, and removal.

---

### Task 1: Add Edge Compute Persistence Models

**Files:**
- Create: `packages/visiox-db/src/visiox_db/models/edge_compute.py`
- Modify: `packages/visiox-db/src/visiox_db/models/__init__.py`
- Create: `infra/migrations/versions/20260715_0001_edge_compute_nodes.py`
- Modify: `tests/integration/test_migrations.py`
- Create: `tests/unit/test_edge_compute_models.py`

**Interfaces:**
- Produces: `ResourcePool`, `ComputeNode`, `AgentEnrollmentToken`, `NodeCommand`, `NodeEvent` SQLAlchemy models.
- Produces: uniqueness contracts `ResourcePool.name`, `ComputeNode.name`, `AgentEnrollmentToken.token_hash`, `NodeCommand.command_id`, and `(NodeEvent.node_id, NodeEvent.sequence)`.
- Consumed by: Tasks 3-5.

- [ ] **Step 1: Extend the migration test with the exact M1 tables**

```python
EXPECTED_TABLES |= {
    "resource_pools",
    "compute_nodes",
    "agent_enrollment_tokens",
    "node_commands",
    "node_events",
}
```

- [ ] **Step 2: Add failing model contract tests**

```python
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from visiox_db.base import Base
from visiox_db.models import AgentEnrollmentToken, ComputeNode, NodeEvent, ResourcePool


def test_edge_compute_models_persist_inventory_and_event_sequence():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        pool = ResourcePool(name="jetson-orin", kind="jetson", selector={}, compatibility_policy={})
        session.add(pool)
        session.flush()
        node = ComputeNode(
            name="edge-01",
            resource_pool_id=pool.id,
            status="online",
            architecture="arm64",
            platform_kind="jetson",
            capabilities={"tasks": ["detect"]},
            resources={"gpu_memory_bytes": 8589934592},
            fingerprint={"jetpack": "6.2"},
            agent_version="0.1.0",
        )
        session.add(node)
        session.flush()
        session.add(NodeEvent(node_id=node.id, sequence=1, event_type="inventory", payload={"ok": True}))
        session.commit()

        assert session.scalar(select(ComputeNode).where(ComputeNode.name == "edge-01")) is not None


def test_enrollment_token_hash_is_unique():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    expires_at = datetime.now(UTC) + timedelta(minutes=10)
    with Session(engine) as session:
        session.add_all([
            AgentEnrollmentToken(name="first", token_hash="same", expires_at=expires_at),
            AgentEnrollmentToken(name="second", token_hash="same", expires_at=expires_at),
        ])
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
        else:
            raise AssertionError("duplicate token hash must fail")
```

- [ ] **Step 3: Run the focused tests and verify failure**

Run: `python -m pytest tests/unit/test_edge_compute_models.py tests/integration/test_migrations.py -q`

Expected: FAIL because the M1 models and tables do not exist.

- [ ] **Step 4: Implement the focused SQLAlchemy models**

Create `edge_compute.py` with these columns and no ORM relationships in M1:

```python
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from visiox_db.base import Base, IdMixin, TimestampMixin


class ResourcePool(IdMixin, TimestampMixin, Base):
    __tablename__ = "resource_pools"
    name: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    selector: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    compatibility_policy: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ComputeNode(IdMixin, TimestampMixin, Base):
    __tablename__ = "compute_nodes"
    name: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    resource_pool_id: Mapped[str | None] = mapped_column(ForeignKey("resource_pools.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="enrolling", index=True)
    architecture: Mapped[str] = mapped_column(String(32), nullable=False)
    platform_kind: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    resources: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    fingerprint: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    agent_version: Mapped[str] = mapped_column(String(40), nullable=False)
    certificate_serial: Mapped[str | None] = mapped_column(String(80), unique=True)
    certificate_fingerprint: Mapped[str | None] = mapped_column(String(128), unique=True)
    certificate_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class AgentEnrollmentToken(IdMixin, TimestampMixin, Base):
    __tablename__ = "agent_enrollment_tokens"
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    node_id: Mapped[str | None] = mapped_column(ForeignKey("compute_nodes.id"))


class NodeCommand(IdMixin, TimestampMixin, Base):
    __tablename__ = "node_commands"
    command_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("compute_nodes.id"), nullable=False, index=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    action: Mapped[str] = mapped_column(String(80), nullable=False)
    desired_state: Mapped[str | None] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    error: Mapped[str | None] = mapped_column(Text)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NodeEvent(IdMixin, TimestampMixin, Base):
    __tablename__ = "node_events"
    __table_args__ = (UniqueConstraint("node_id", "sequence"),)
    node_id: Mapped[str] = mapped_column(ForeignKey("compute_nodes.id"), nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    command_id: Mapped[str | None] = mapped_column(String(36), index=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    stage: Mapped[str | None] = mapped_column(String(120))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


Index("ix_node_events_node_sequence", NodeEvent.node_id, NodeEvent.sequence)
```

Export all five models from `visiox_db.models.__init__` and add matching Alembic operations with downgrade order `node_events`, `node_commands`, `agent_enrollment_tokens`, `compute_nodes`, `resource_pools`.

- [ ] **Step 5: Run model and migration tests**

Run: `python -m pytest tests/unit/test_edge_compute_models.py tests/integration/test_migrations.py -q`

Expected: PASS.

- [ ] **Step 6: Commit the persistence slice**

```bash
git add packages/visiox-db/src/visiox_db/models/edge_compute.py packages/visiox-db/src/visiox_db/models/__init__.py infra/migrations/versions/20260715_0001_edge_compute_nodes.py tests/unit/test_edge_compute_models.py tests/integration/test_migrations.py
git commit -m "feat: add edge compute persistence"
```

---

### Task 2: Add Device CA, Enrollment Token, and Certificate Services

**Files:**
- Modify: `pyproject.toml`
- Modify: `packages/visiox-common/src/visiox_common/settings.py`
- Create: `apps/api-service/src/visiox_api/services/agent_identity.py`
- Create: `tests/unit/test_agent_identity.py`

**Interfaces:**
- Produces: `hash_enrollment_token(raw_token: str) -> str`.
- Produces: `ensure_agent_ca(settings: Settings) -> None`.
- Produces: `issue_agent_certificate(csr_pem: str, node_id: str, settings: Settings, now: datetime | None = None) -> IssuedAgentCertificate`.
- Produces: `verify_agent_signature(certificate_pem: str, nonce: bytes, signature: bytes, settings: Settings, now: datetime | None = None) -> VerifiedAgentIdentity`.
- Consumed by: Tasks 3-4.

- [ ] **Step 1: Add failing identity tests**

```python
import base64
from datetime import UTC, datetime

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ed25519
from cryptography import x509
from cryptography.x509.oid import NameOID

from visiox_api.services.agent_identity import (
    ensure_agent_ca,
    hash_enrollment_token,
    issue_agent_certificate,
    verify_agent_signature,
)
from visiox_common.settings import Settings


def _csr() -> tuple[ed25519.Ed25519PrivateKey, str]:
    key = ed25519.Ed25519PrivateKey.generate()
    csr = x509.CertificateSigningRequestBuilder().subject_name(
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "edge-01")])
    ).sign(key, algorithm=None)
    return key, csr.public_bytes(serialization.Encoding.PEM).decode()


def test_ca_issues_node_certificate_and_verifies_nonce(tmp_path):
    settings = Settings(
        agent_ca_cert_path=tmp_path / "ca.crt",
        agent_ca_key_path=tmp_path / "ca.key",
        agent_auto_generate_ca=True,
    )
    ensure_agent_ca(settings)
    key, csr_pem = _csr()
    issued = issue_agent_certificate(csr_pem, "node-123", settings, now=datetime.now(UTC))
    nonce = b"challenge"
    verified = verify_agent_signature(issued.certificate_pem, nonce, key.sign(nonce), settings)
    assert verified.node_id == "node-123"
    assert issued.serial_number
    assert hash_enrollment_token("secret") != "secret"
```

- [ ] **Step 2: Run the identity test and verify failure**

Run: `python -m pytest tests/unit/test_agent_identity.py -q`

Expected: FAIL because `cryptography` and `agent_identity` are absent.

- [ ] **Step 3: Add exact settings and dependency bounds**

Add `"cryptography>=45,<47"` to project dependencies and these settings:

```python
agent_ca_cert_path: Path = Path("/var/lib/visiox/pki/ca.crt")
agent_ca_key_path: Path = Path("/var/lib/visiox/pki/ca.key")
agent_auto_generate_ca: bool = False
agent_certificate_ttl_days: int = 365
agent_enrollment_token_ttl_minutes: int = 15
agent_public_ws_url: str = "ws://127.0.0.1:8000/agent/v1/connect"
agent_gateway_enabled: bool = False
agent_heartbeat_interval_seconds: int = 15
agent_offline_after_seconds: int = 45
agent_certificate_renew_before_days: int = 30
agent_max_ws_message_bytes: int = 1024 * 1024
```

Add a settings validator that requires the public WebSocket path `/agent/v1/connect`, permits `ws://` only in `local`, and requires `wss://` in every other environment. The same production rule applies to the Agent's `PlatformURL`: `http://` is test/local only and `https://` is required on deployed nodes.

- [ ] **Step 4: Implement CA and certificate services**

Use Ed25519 keys and X.509 certificates. `ensure_agent_ca()` loads and validates existing CA material in every environment. It may generate missing material only when `settings.environment == "local"` and `agent_auto_generate_ca` is true; otherwise it raises `RuntimeError("Agent CA material is required")`. Generation creates parent directories, writes the private key with mode `0o600`, and includes `BasicConstraints(ca=True)`. `issue_agent_certificate()` rejects an invalid CSR signature, ignores the CSR subject, sets certificate CN to `node_id`, adds `ExtendedKeyUsageOID.CLIENT_AUTH`, and returns:

```python
@dataclass(frozen=True, slots=True)
class IssuedAgentCertificate:
    certificate_pem: str
    ca_certificate_pem: str
    serial_number: str
    fingerprint_sha256: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class VerifiedAgentIdentity:
    node_id: str
    serial_number: str
    fingerprint_sha256: str
```

`verify_agent_signature()` must verify CA signature, validity interval, `CLIENT_AUTH`, Ed25519 nonce signature, and non-empty CN. Raise `AgentIdentityError` for every trust failure without returning cryptography internals to API clients.

- [ ] **Step 5: Run identity tests and lint**

Run: `python -m pytest tests/unit/test_agent_identity.py -q && python -m ruff check apps/api-service/src/visiox_api/services/agent_identity.py packages/visiox-common/src/visiox_common/settings.py tests/unit/test_agent_identity.py`

Expected: PASS and no Ruff findings.

- [ ] **Step 6: Commit identity services**

```bash
git add pyproject.toml packages/visiox-common/src/visiox_common/settings.py apps/api-service/src/visiox_api/services/agent_identity.py tests/unit/test_agent_identity.py
git commit -m "feat: add node identity services"
```

---

### Task 3: Implement Enrollment and Node Registry APIs

**Files:**
- Create: `apps/api-service/src/visiox_api/schemas/__init__.py`
- Create: `apps/api-service/src/visiox_api/schemas/agent_protocol.py`
- Create: `apps/api-service/src/visiox_api/services/node_registry.py`
- Create: `apps/api-service/src/visiox_api/routes/agent_enrollment.py`
- Create: `apps/api-service/src/visiox_api/routes/nodes.py`
- Modify: `apps/api-service/src/visiox_api/main.py`
- Create: `tests/integration/conftest.py`
- Create: `tests/integration/test_node_enrollment_api.py`

**Interfaces:**
- Produces: `POST /agent/v1/enrollment-tokens`, `POST /agent/v1/enroll`, `GET /nodes`, `GET /nodes/{id}`, `GET /resource-pools`, `POST /nodes/{id}/drain`.
- Produces: `NodeRegistryService.create_enrollment_token()`, `.enroll()`, `.apply_inventory()`, `.mark_seen()`.
- Consumed by: Task 4 Device Gateway and Task 7 smoke test.

- [ ] **Step 1: Write failing API tests for one-time enrollment and real node listing**

```python
def test_enrollment_token_is_returned_once_and_consumed(agent_api_client):
    token_response = agent_api_client.post("/agent/v1/enrollment-tokens", json={"name": "factory-a"})
    raw_token = token_response.json()["token"]
    _, csr_pem = new_agent_csr("edge-01")
    enrolled = agent_api_client.post("/agent/v1/enroll", json={
        "protocol_version": 1,
        "token": raw_token,
        "node_name": "edge-01",
        "architecture": "arm64",
        "platform_kind": "jetson",
        "agent_version": "0.1.0",
        "csr_pem": csr_pem,
    })
    replay = agent_api_client.post("/agent/v1/enroll", json={
        "protocol_version": 1,
        "token": raw_token,
        "node_name": "edge-02",
        "architecture": "arm64",
        "platform_kind": "jetson",
        "agent_version": "0.1.0",
        "csr_pem": new_agent_csr("edge-02")[1],
    })
    nodes = agent_api_client.get("/nodes")

    assert token_response.status_code == 201
    assert enrolled.status_code == 201
    assert enrolled.json()["node_id"]
    assert "PRIVATE KEY" not in enrolled.text
    assert replay.status_code == 409
    assert nodes.json()["items"][0]["name"] == "edge-01"
    assert nodes.json()["items"][0]["status"] == "enrolling"
```

Add `test_node_listing_marks_stale_online_node_offline`: enroll a node, set it to `online` with `last_seen_at = now - (offline_after + 1 second)` through the shared `agent_session_factory`, call `GET /nodes/{id}`, and assert status becomes `offline`. Add `test_drain_is_administrative_state`: call `POST /nodes/{id}/drain` and assert repeated list/get refreshes preserve `draining`.

- [ ] **Step 2: Run the test and verify failure**

Run: `python -m pytest tests/integration/test_node_enrollment_api.py -q`

Expected: FAIL with missing routes.

- [ ] **Step 3: Define versioned protocol and API schemas**

```python
class EnrollmentRequest(BaseModel):
    protocol_version: Literal[1]
    token: str = Field(min_length=32, max_length=256)
    node_name: str = Field(min_length=1, max_length=160, pattern=r"^[\w.-]+$")
    architecture: Literal["amd64", "arm64"]
    platform_kind: Literal["jetson", "x86_nvidia"]
    agent_version: str = Field(min_length=1, max_length=40)
    csr_pem: str = Field(min_length=100, max_length=16_384)


class EnrollmentResponse(BaseModel):
    protocol_version: Literal[1] = 1
    node_id: str
    certificate_pem: str
    ca_certificate_pem: str
    gateway_url: str
    heartbeat_interval_seconds: int
```

Also define `InventoryMessage`, `HeartbeatMessage`, `EventBatchMessage`, `ChallengeMessage`, `AuthenticateMessage`, `AuthenticatedMessage`, `EventsAckMessage`, `CertificateRenewalRequest`, `CertificateRenewedMessage`, and `ErrorMessage` in this file so Task 4 does not invent a second protocol. Their wire fields are fixed as follows:

| `type` | Required payload fields |
|---|---|
| `challenge` | `nonce` (base64) |
| `authenticate` | `node_id`, `certificate_pem`, `signature` (base64) |
| `authenticated` | `heartbeat_interval_seconds` |
| `inventory` | `architecture`, `platform_kind`, `capabilities`, `resources`, `fingerprint`, `agent_version` |
| `heartbeat` | `occurred_at` (UTC RFC3339) |
| `event_batch` | `events[]`, where each event has `sequence`, `event_type`, optional `command_id`, optional `stage`, `payload`, and `occurred_at` |
| `events_acked` | `through_sequence` |
| `certificate_renewal_request` | `csr_pem` generated from the existing Agent key |
| `certificate_renewed` | `certificate_pem` |
| `error` | stable `code`, redacted `message`, and `retryable` |

Every row also carries `protocol_version: 1` and its literal `type`; unknown fields are rejected. Set strict bounds: message body 1 MiB, event batch 100 events, capabilities/resources/fingerprint 64 KiB each after JSON serialization, and CSR/certificate PEM 16 KiB each.

- [ ] **Step 4: Implement registry transaction rules**

Add this exact CSR helper to the API test module so the test owns the private key and never asks the platform to generate one:

```python
def new_agent_csr(name: str) -> tuple[ed25519.Ed25519PrivateKey, str]:
    key = ed25519.Ed25519PrivateKey.generate()
    csr = x509.CertificateSigningRequestBuilder().subject_name(
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    ).sign(key, algorithm=None)
    return key, csr.public_bytes(serialization.Encoding.PEM).decode()
```

Create the uniquely named `agent_session_factory` fixture in `tests/integration/conftest.py`; it runs Alembic against a temporary SQLite database and returns one `sessionmaker`. Create `agent_api_client` on top of it: generate a temporary CA with `ensure_agent_ca()`, override `get_agent_enrollment_session` and `get_node_session` with the shared factory, and override `get_settings` with those CA paths plus `agent_gateway_enabled=True`. Yield `TestClient(create_app())` from a context manager and clear dependency overrides afterward. The unique fixture names avoid changing existing integration tests that define their own `client` fixtures.

`create_enrollment_token()` generates `secrets.token_urlsafe(32)`, stores only SHA256, and returns the raw token once. `enroll()` must:

1. Lock/select the token by hash.
2. Reject missing, expired, or used tokens with one generic `EnrollmentRejected` error.
3. Reject duplicate node names.
4. Create or reuse default pool `jetson-default` or `x86-nvidia-default`.
5. Create `ComputeNode(status="enrolling")`.
6. Issue the certificate for the new node ID.
7. Save serial/fingerprint/expiry and consume the token in the same transaction.

SQLite tests cannot emulate `FOR UPDATE`; keep the transaction boundary and rely on the unique token hash plus `used_at` guard. PostgreSQL is the production concurrency authority.

`apply_inventory()` verifies that reported `architecture` and `platform_kind` still match the enrollment declaration. A mismatch sets node status to `incompatible`, stores a redacted reason in `fingerprint["compatibility_error"]`, and does not reassign the pool. A match updates inventory, assigns the corresponding default pool, sets status to `online` unless the node is administratively `draining` or `disabled`, and updates `last_seen_at` in one transaction. `mark_seen()` changes only `enrolling`, `offline`, or `online` to `online`; it updates time but preserves `incompatible`, `draining`, and `disabled`.

- [ ] **Step 5: Implement routes and register them**

Map `EnrollmentRejected` to HTTP 409, malformed CSR to 422, missing node to 404, and unsupported protocol versions to Pydantic 422. `POST /nodes/{id}/drain` sets status `draining`; it does not stop workloads in M1. Before list/get responses, call `refresh_stale_nodes(session, now)`: nodes in `online` whose `last_seen_at` is older than `agent_offline_after_seconds` become `offline`; `draining` and `disabled` nodes keep their administrative state even while heartbeats update `last_seen_at`.

- [ ] **Step 6: Run enrollment, migration, and existing service tests**

Run: `python -m pytest tests/integration/test_node_enrollment_api.py tests/integration/test_migrations.py tests/integration/test_services_api.py -q`

Expected: PASS; existing service behavior remains unchanged in M1.

- [ ] **Step 7: Commit the registry API slice**

```bash
git add apps/api-service/src/visiox_api/schemas apps/api-service/src/visiox_api/services/node_registry.py apps/api-service/src/visiox_api/routes/agent_enrollment.py apps/api-service/src/visiox_api/routes/nodes.py apps/api-service/src/visiox_api/main.py tests/integration/conftest.py tests/integration/test_node_enrollment_api.py
git commit -m "feat: add node enrollment api"
```

---

### Task 4: Implement the Authenticated Device Gateway

**Files:**
- Create: `apps/api-service/src/visiox_api/ws/agents.py`
- Modify: `apps/api-service/src/visiox_api/main.py`
- Modify: `tests/integration/conftest.py`
- Create: `tests/integration/test_agent_gateway.py`

**Interfaces:**
- Produces: `WS /agent/v1/connect`.
- Consumes: certificate verifier and protocol schemas from Tasks 2-3.
- Consumes: `NodeRegistryService.apply_inventory()` and `.mark_seen()`.
- Produces: event ACK contract `{"protocol_version":1,"type":"events_acked","through_sequence":N}`.
- Produces: authenticated certificate renewal over `CertificateRenewalRequest` and `CertificateRenewedMessage`.
- Consumed by: Go Agent Task 7.

- [ ] **Step 1: Write failing WebSocket authentication and heartbeat tests**

Extend `agent_api_client` so it also overrides `get_agent_gateway_session_factory` with the same test `sessionmaker`; do not hold a generated session open for the lifetime of the WebSocket.

```python
from datetime import UTC, datetime


def test_agent_gateway_authenticates_updates_inventory_and_acks_events(agent_api_client, enrolled_agent):
    node_id, certificate_pem, private_key = enrolled_agent
    with agent_api_client.websocket_connect("/agent/v1/connect") as websocket:
        challenge = websocket.receive_json()
        nonce = base64.b64decode(challenge["nonce"])
        websocket.send_json({
            "protocol_version": 1,
            "type": "authenticate",
            "node_id": node_id,
            "certificate_pem": certificate_pem,
            "signature": base64.b64encode(private_key.sign(nonce)).decode(),
        })
        assert websocket.receive_json()["type"] == "authenticated"
        websocket.send_json({
            "protocol_version": 1,
            "type": "inventory",
            "architecture": "arm64",
            "platform_kind": "jetson",
            "capabilities": {"tasks": ["detect"]},
            "resources": {"gpu_memory_bytes": 8589934592},
            "fingerprint": {"jetpack": "6.2", "tensorrt": "10.3"},
            "agent_version": "0.1.0",
        })
        websocket.send_json({
            "protocol_version": 1,
            "type": "heartbeat",
            "occurred_at": datetime.now(UTC).isoformat(),
        })
        websocket.send_json({
            "protocol_version": 1,
            "type": "event_batch",
            "events": [{
                "sequence": 1,
                "event_type": "agent_started",
                "payload": {},
                "occurred_at": datetime.now(UTC).isoformat(),
            }],
        })
        assert websocket.receive_json() == {
            "protocol_version": 1,
            "type": "events_acked",
            "through_sequence": 1,
        }

    node = agent_api_client.get(f"/nodes/{node_id}").json()
    assert node["status"] == "online"
    assert node["fingerprint"]["tensorrt"] == "10.3"
```

Copy the exact `new_agent_csr()` helper from Task 3 into this test module, then define the fixture using the public enrollment API:

```python
@pytest.fixture
def enrolled_agent(agent_api_client: TestClient):
    token = agent_api_client.post("/agent/v1/enrollment-tokens", json={"name": "gateway-test"}).json()["token"]
    private_key, csr_pem = new_agent_csr("edge-gateway-01")
    response = agent_api_client.post("/agent/v1/enroll", json={
        "protocol_version": 1,
        "token": token,
        "node_name": "edge-gateway-01",
        "architecture": "arm64",
        "platform_kind": "jetson",
        "agent_version": "0.1.0-test",
        "csr_pem": csr_pem,
    })
    assert response.status_code == 201
    body = response.json()
    return body["node_id"], body["certificate_pem"], private_key
```

Add negative tests for wrong signature, certificate/node mismatch, expired certificate, sequence replay, and unsupported `protocol_version`.

Add `test_agent_gateway_renews_certificate_for_authenticated_node`: authenticate with the enrolled key, create a second CSR from that same key with an arbitrary subject, send `certificate_renewal_request`, assert the response type is `certificate_renewed`, parse the returned certificate, and assert its CN remains the authenticated node ID. After disconnect, assert the database serial/fingerprint changed; reconnecting with the old certificate must close with 4403, while reconnecting with the renewed certificate and the same private key succeeds. Add a malformed-CSR case and assert it returns a protocol error without changing the stored serial/fingerprint.

Add `test_heartbeat_does_not_clear_draining_state`: authenticate, put the node into `draining` through the public API, send a heartbeat, then assert `last_seen_at` advanced while `GET /nodes/{id}` still reports `draining`.

Add `test_inventory_mismatch_marks_node_incompatible`: enroll as Jetson/arm64, authenticate successfully, report x86_nvidia/amd64 inventory, and assert the node status is `incompatible`, its pool remains `jetson-default`, and a later heartbeat does not change the status.

- [ ] **Step 2: Run Gateway tests and verify failure**

Run: `python -m pytest tests/integration/test_agent_gateway.py -q`

Expected: FAIL because `/agent/v1/connect` is absent.

- [ ] **Step 3: Implement challenge authentication without holding a DB session across awaits**

```python
@router.websocket("/agent/v1/connect")
async def agent_gateway(
    websocket: WebSocket,
    session_factory: sessionmaker[Session] = Depends(get_agent_gateway_session_factory),
    settings: Settings = Depends(get_settings),
) -> None:
    await websocket.accept()
    nonce = secrets.token_bytes(32)
    await websocket.send_json(ChallengeMessage(nonce=base64.b64encode(nonce).decode()).model_dump())
    raw_auth = await websocket.receive_json()
    auth = AuthenticateMessage.model_validate(raw_auth)
    verified = verify_agent_signature(
        auth.certificate_pem,
        nonce,
        base64.b64decode(auth.signature, validate=True),
        settings,
    )
    if verified.node_id != auth.node_id:
        await websocket.close(code=4403, reason="agent identity rejected")
        return
    with session_factory() as session:
        authenticate_registered_node(session, verified)
    await websocket.send_json(AuthenticatedMessage(
        heartbeat_interval_seconds=settings.agent_heartbeat_interval_seconds
    ).model_dump())
    await receive_agent_messages(websocket, auth.node_id, session_factory)
```

Create a new short-lived SQLAlchemy session for each inventory, heartbeat, and event batch transaction. Receive text frames first, reject any frame larger than `agent_max_ws_message_bytes` before JSON parsing, then validate with the strict Pydantic message model selected by literal `type`. Reject binary frames and unknown message types with `ErrorMessage(code="invalid_message", retryable=False)`. On normal disconnect, set no immediate offline state; a node becomes offline when `last_seen_at` exceeds the configured timeout.

- [ ] **Step 4: Persist events idempotently and ACK the contiguous cursor**

For an event batch, insert unseen `(node_id, sequence)` rows, ignore exact replays, reject a replay whose immutable event content differs, commit, then ACK the highest contiguous sequence stored for the node. Never ACK before commit.

Handle `CertificateRenewalRequest` only after WebSocket authentication. Verify the CSR, issue a replacement certificate for the already authenticated node ID, update `certificate_serial`, `certificate_fingerprint`, and `certificate_expires_at` in one transaction, then return `CertificateRenewedMessage`. A renewal request cannot rename or move the node.

- [ ] **Step 5: Run Gateway and full API tests**

Run: `python -m pytest tests/integration/test_agent_gateway.py tests/integration/test_node_enrollment_api.py tests/integration -q`

Expected: all integration tests PASS.

- [ ] **Step 6: Commit Device Gateway**

```bash
git add apps/api-service/src/visiox_api/ws/agents.py apps/api-service/src/visiox_api/main.py tests/integration/conftest.py tests/integration/test_agent_gateway.py
git commit -m "feat: add authenticated device gateway"
```

---

### Task 5: Create the Go Agent Module, Config, and Durable State

**Files:**
- Create: `apps/node-agent/go.mod`
- Create: `apps/node-agent/internal/config/config.go`
- Create: `apps/node-agent/internal/config/config_test.go`
- Create: `apps/node-agent/internal/state/store.go`
- Create: `apps/node-agent/internal/state/store_test.go`
- Create: `apps/node-agent/internal/protocol/messages.go`
- Create: `apps/node-agent/internal/testsupport/certificates.go`

**Interfaces:**
- Produces: `config.Load() (Config, error)`.
- Produces: `state.Open(path string) (*Store, error)`.
- Produces: `Store.Identity()`, `Store.SaveIdentity()`, `Store.UpdateCertificate()`, `Store.AppendEvent()`, `Store.PendingEvents()`, and `Store.AckEvents()`.
- Produces: Go JSON structs exactly matching Task 3 protocol schemas.
- Produces: deterministic test CA helpers `testsupport.NewCA()` and `testsupport.NewStoredIdentity()` for Tasks 6-7.
- Consumed by: Tasks 6-7.

- [ ] **Step 1: Create the pinned Go module**

```go
module github.com/wyzzy57/all/apps/node-agent

go 1.25.0

require (
    github.com/coder/websocket v1.8.15
    go.etcd.io/bbolt v1.5.0
)
```

Use Go 1.26.5 to build while keeping the module language-compatibility floor at Go 1.25.

- [ ] **Step 2: Write failing config and store tests**

```go
func TestLoadRequiresPlatformURLAndNodeName(t *testing.T) {
    t.Setenv("VISIOX_AGENT_PLATFORM_URL", "")
    t.Setenv("VISIOX_AGENT_NODE_NAME", "")
    _, err := Load()
    if err == nil { t.Fatal("expected validation error") }
}

func TestStoreSpoolsAndAcknowledgesEvents(t *testing.T) {
    store, err := Open(filepath.Join(t.TempDir(), "agent.db"))
    if err != nil { t.Fatal(err) }
    defer store.Close()
    first, _ := store.AppendEvent("agent_started", map[string]any{"ok": true})
    second, _ := store.AppendEvent("inventory", map[string]any{})
    if first.Sequence != 1 || second.Sequence != 2 { t.Fatal("sequences must be monotonic") }
    if err := store.AckEvents(1); err != nil { t.Fatal(err) }
    pending, _ := store.PendingEvents(100)
    if len(pending) != 1 || pending[0].Sequence != 2 { t.Fatal("unexpected pending events") }
}
```

Add `TestStoreReopensWithUnacknowledgedEvents`: append sequences 1 and 2, close the database without ACK, reopen the same path, and assert both events and the next sequence value remain intact. Add `TestStoreRejectsAckAheadOfHighestSequence` so local corruption cannot discard unseen events.

- [ ] **Step 3: Run Go tests in Docker and verify failure**

Run:

```powershell
docker run --rm -v "${PWD}:/src" -w /src/apps/node-agent golang:1.26.5-alpine sh -lc "go test ./internal/config ./internal/state"
```

Expected: FAIL because implementation files are absent.

- [ ] **Step 4: Implement exact Config and protocol contracts**

```go
type Config struct {
    PlatformURL      string
    NodeName         string
    EnrollmentToken string
    StateDir         string
    AgentVersion     string
    AllowInsecureLocal bool
}

type Envelope struct {
    ProtocolVersion int    `json:"protocol_version"`
    Type            string `json:"type"`
}

type Event struct {
    Sequence  uint64         `json:"sequence"`
    EventType string         `json:"event_type"`
    CommandID string         `json:"command_id,omitempty"`
    Stage     string         `json:"stage,omitempty"`
    Payload   map[string]any `json:"payload"`
    OccurredAt time.Time     `json:"occurred_at"`
}

type EnrollmentFacts struct {
    Architecture string
    PlatformKind string
}

type EnrollmentRequest struct {
    ProtocolVersion int    `json:"protocol_version"`
    Token           string `json:"token"`
    NodeName        string `json:"node_name"`
    Architecture    string `json:"architecture"`
    PlatformKind    string `json:"platform_kind"`
    AgentVersion    string `json:"agent_version"`
    CSRPEM          string `json:"csr_pem"`
}

type EnrollmentResponse struct {
    ProtocolVersion          int    `json:"protocol_version"`
    NodeID                   string `json:"node_id"`
    CertificatePEM           string `json:"certificate_pem"`
    CACertificatePEM         string `json:"ca_certificate_pem"`
    GatewayURL               string `json:"gateway_url"`
    HeartbeatIntervalSeconds int    `json:"heartbeat_interval_seconds"`
}

type ChallengeMessage struct { Envelope; Nonce string `json:"nonce"` }
type AuthenticateMessage struct {
    Envelope
    NodeID         string `json:"node_id"`
    CertificatePEM string `json:"certificate_pem"`
    Signature      string `json:"signature"`
}
type AuthenticatedMessage struct {
    Envelope
    HeartbeatIntervalSeconds int `json:"heartbeat_interval_seconds"`
}
type InventoryMessage struct {
    Envelope
    Architecture string         `json:"architecture"`
    PlatformKind string         `json:"platform_kind"`
    Capabilities map[string]any `json:"capabilities"`
    Resources    map[string]any `json:"resources"`
    Fingerprint  map[string]any `json:"fingerprint"`
    AgentVersion string         `json:"agent_version"`
}
type HeartbeatMessage struct { Envelope; OccurredAt time.Time `json:"occurred_at"` }
type EventBatchMessage struct { Envelope; Events []Event `json:"events"` }
type EventsAckMessage struct { Envelope; ThroughSequence uint64 `json:"through_sequence"` }
type CertificateRenewalRequest struct { Envelope; CSRPEM string `json:"csr_pem"` }
type CertificateRenewedMessage struct { Envelope; CertificatePEM string `json:"certificate_pem"` }
type ErrorMessage struct {
    Envelope
    Code      string `json:"code"`
    Message   string `json:"message"`
    Retryable bool   `json:"retryable"`
}
```

Defaults: `StateDir=/var/lib/visiox-agent`, `AgentVersion=0.1.0`. Require HTTPS in non-local URLs and allow HTTP only for `localhost`, `127.0.0.1`, `api-service`, and explicit `VISIOX_AGENT_ALLOW_INSECURE_LOCAL=true` development mode.

- [ ] **Step 5: Implement bbolt storage**

Use this exact identity contract:

```go
type Identity struct {
    NodeID                   string
    PrivateKeyPEM            string
    CertificatePEM           string
    CACertificatePEM         string
    CertificateExpiresAt     time.Time
    GatewayURL               string
    HeartbeatIntervalSeconds int
}

func (s *Store) Identity() (Identity, bool, error)
func (s *Store) SaveIdentity(identity Identity) error
func (s *Store) UpdateCertificate(certificatePEM string, expiresAt time.Time) error
```

Open the bbolt file with mode `0o600`. Use buckets `identity`, `events`, and `meta`. Event keys are big-endian uint64 sequences. `AppendEvent()` increments `next_event_sequence` and writes the event in one transaction. `AckEvents(N)` rejects a cursor above the highest locally known sequence, deletes all event keys `<= N`, and stores `acked_event_sequence=N`. Identity includes node ID, PEM private key, PEM certificate, CA certificate, parsed certificate expiry, Gateway URL, and heartbeat interval. `SaveIdentity()` rejects a certificate whose public key does not match the stored private key. `UpdateCertificate()` repeats that key check and updates PEM plus expiry in one bbolt transaction.

Create `internal/testsupport/certificates.go` with these exact test contracts:

```go
type CA struct {
    certificate    *x509.Certificate
    privateKey     ed25519.PrivateKey
    certificatePEM string
}

func NewCA(t testing.TB) *CA
func (c *CA) PEM() string
func (c *CA) SignCSR(t testing.TB, csrPEM, nodeID string, expiresAt time.Time) string
func NewStoredIdentity(
    t testing.TB,
    store *state.Store,
    nodeID string,
    gatewayURL string,
    expiresAt time.Time,
) (state.Identity, ed25519.PrivateKey, *CA)
```

`SignCSR()` parses and verifies the CSR signature, retains its public key, sets certificate CN to `nodeID`, sets `ExtKeyUsageClientAuth`, and signs with the test CA. `NewStoredIdentity()` generates an Ed25519 key and CSR, uses `SignCSR()`, saves the matching identity including `CertificateExpiresAt`, and returns the identity, signer, and CA for protocol assertions and renewal tests. This package is test support only and must never be imported by `cmd/visiox-node-agent`.

- [ ] **Step 6: Run Go tests, vet, and formatting**

Run:

```powershell
docker run --rm -v "${PWD}:/src" -w /src/apps/node-agent golang:1.26.5-alpine sh -lc "gofmt -w . && go test ./... && go vet ./..."
```

Expected: PASS with no vet findings.

- [ ] **Step 7: Commit the Agent foundation**

```bash
git add apps/node-agent/go.mod apps/node-agent/go.sum apps/node-agent/internal/config apps/node-agent/internal/state apps/node-agent/internal/protocol apps/node-agent/internal/testsupport
git commit -m "feat: add node agent foundation"
```

---

### Task 6: Implement Go Identity Enrollment

**Files:**
- Create: `apps/node-agent/internal/identity/identity.go`
- Create: `apps/node-agent/internal/identity/identity_test.go`

**Interfaces:**
- Produces: `identity.Ensure(ctx context.Context, cfg config.Config, facts protocol.EnrollmentFacts, store *state.Store, client *http.Client) (state.Identity, error)`.
- Consumes: Task 3 `POST /agent/v1/enroll` contract.
- Consumed by: Task 7 Gateway client.

- [ ] **Step 1: Write failing enrollment tests with an HTTP test server**

```go
func TestEnsureGeneratesCSRAndPersistsReturnedIdentity(t *testing.T) {
    ca := testsupport.NewCA(t)
    calls := 0
    var request protocol.EnrollmentRequest
    var server *httptest.Server
    server = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        if r.URL.Path != "/agent/v1/enroll" { http.NotFound(w, r); return }
        calls++
        if err := json.NewDecoder(r.Body).Decode(&request); err != nil { t.Fatal(err) }
        if !strings.Contains(request.CSRPEM, "CERTIFICATE REQUEST") { t.Fatal("missing CSR") }
        w.WriteHeader(http.StatusCreated)
        json.NewEncoder(w).Encode(protocol.EnrollmentResponse{
            ProtocolVersion: 1,
            NodeID: "node-1",
            CertificatePEM: ca.SignCSR(t, request.CSRPEM, "node-1", time.Now().Add(365*24*time.Hour)),
            CACertificatePEM: ca.PEM(),
            GatewayURL: strings.Replace(server.URL, "http://", "ws://", 1) + "/agent/v1/connect",
            HeartbeatIntervalSeconds: 15,
        })
    }))
    defer server.Close()

    store, err := state.Open(filepath.Join(t.TempDir(), "agent.db"))
    if err != nil { t.Fatal(err) }
    defer store.Close()
    cfg := config.Config{
        PlatformURL: server.URL,
        NodeName: "edge-01",
        EnrollmentToken: strings.Repeat("x", 32),
        StateDir: t.TempDir(),
        AgentVersion: "0.1.0-test",
        AllowInsecureLocal: true,
    }
    facts := protocol.EnrollmentFacts{Architecture: "arm64", PlatformKind: "jetson"}

    got, err := Ensure(context.Background(), cfg, facts, store, server.Client())
    if err != nil { t.Fatal(err) }
    if got.NodeID != "node-1" || !strings.Contains(got.PrivateKeyPEM, "PRIVATE KEY") {
        t.Fatalf("identity was not persisted: %#v", got)
    }
    if got.CertificateExpiresAt.Before(time.Now().Add(300*24*time.Hour)) {
        t.Fatal("certificate expiry was not parsed")
    }

    again, err := Ensure(context.Background(), cfg, facts, store, server.Client())
    if err != nil { t.Fatal(err) }
    if again.NodeID != got.NodeID || calls != 1 {
        t.Fatalf("existing identity must be reused; calls=%d", calls)
    }
}
```

Add `TestEnsureRejectsCertificateForDifferentKey` using `testsupport.NewCA()` and a certificate signed for a different CSR; assert `Ensure()` returns an error and `store.Identity()` remains empty.

- [ ] **Step 2: Run the identity package test and verify failure**

Run: Dockerized `go test ./internal/identity -v`.

Expected: FAIL because `Ensure` is absent.

- [ ] **Step 3: Implement Ed25519 key generation, CSR, and enrollment**

Use only Go standard crypto packages. Require `facts` to be one of `(arm64, jetson)` or `(amd64, x86_nvidia)`. Encode the private key as PKCS#8 PEM, create a CSR with the configured node name as provisional CN, include the facts and `cfg.AgentVersion` in the enrollment request, POST it with a 30-second timeout, cap the response body at 1 MiB, and require HTTP 201 and protocol version 1. Parse the returned CA and device certificate; require a valid CA constraint/signature chain, device `CLIENT_AUTH` usage, certificate CN equal to `NodeID`, and certificate public key equal to the generated private key. Require a `wss://` Gateway URL unless `AllowInsecureLocal` is true. Parse and store certificate expiry, and persist identity atomically only after every validation succeeds.

- [ ] **Step 4: Run package and full Go tests**

Run: Dockerized `gofmt -w . && go test ./... && go vet ./...`.

Expected: PASS.

- [ ] **Step 5: Commit enrollment**

```bash
git add apps/node-agent/internal/identity
git commit -m "feat: enroll node agent identity"
```

---

### Task 7: Implement Resource Inventory and Persistent Gateway Connection

**Files:**
- Create: `apps/node-agent/internal/inventory/probe.go`
- Create: `apps/node-agent/internal/inventory/probe_test.go`
- Create: `apps/node-agent/internal/gateway/client.go`
- Create: `apps/node-agent/internal/gateway/client_test.go`
- Create: `apps/node-agent/cmd/visiox-node-agent/main.go`

**Interfaces:**
- Produces: `inventory.Probe(ctx context.Context, runner CommandRunner, fs FileSystem) (protocol.InventoryMessage, error)`.
- Produces: `gateway.New(cfg config.Config, store *state.Store, inventory protocol.InventoryMessage, options ...Option) *Client` and test option `gateway.WithBackoff(func(attempt int) time.Duration)`.
- Produces: `gateway.Client.Run(ctx context.Context) error` with reconnect until context cancellation.
- Consumes: Task 4 WebSocket protocol and Task 5 state store.
- Produces: online node state visible through `GET /nodes/{id}`.

- [ ] **Step 1: Write inventory tests for Jetson and x86 fixtures**

```go
func TestProbeDetectsJetson(t *testing.T) {
    fs := fakeFS{
        "/etc/nv_tegra_release": "# R36 (release), REVISION: 4.3",
        "/proc/device-tree/model": "NVIDIA Jetson AGX Orin\x00",
        "/proc/meminfo": "MemTotal:       32517888 kB\nMemAvailable:   30100000 kB\n",
    }
    runner := fakeRunner{outputs: map[string]string{
        "uname -m": "aarch64\n",
        "dpkg-query -W -f=${Version} nvidia-l4t-core": "36.4.3\n",
        "dpkg-query -W -f=${Version} libnvinfer10": "10.3.0\n",
    }}
    got, err := Probe(context.Background(), runner, fs)
    if err != nil { t.Fatal(err) }
    if got.PlatformKind != "jetson" || got.Architecture != "arm64" { t.Fatalf("unexpected: %#v", got) }
    if got.Resources["memory_total_bytes"] != int64(32517888*1024) { t.Fatal("memory was not normalized") }
}

func TestProbeDetectsX86Nvidia(t *testing.T) {
    runner := fakeRunner{outputs: map[string]string{
        "uname -m": "x86_64\n",
        "nvidia-smi --query-gpu=index,uuid,name,compute_cap,memory.total,memory.free,driver_version --format=csv,noheader,nounits": "0, GPU-abc, NVIDIA RTX 4090, 8.9, 24564, 24000, 550.54.15\n",
        "trtexec --version": "TensorRT version: 10.3.0\n",
    }}
    got, err := Probe(context.Background(), runner, fakeFS{})
    if err != nil { t.Fatal(err) }
    gpus := got.Resources["gpus"].([]map[string]any)
    if got.PlatformKind != "x86_nvidia" || len(gpus) != 1 || gpus[0]["memory_total_bytes"] != int64(24564*1024*1024) {
        t.Fatal("missing normalized GPU data")
    }
}
```

- [ ] **Step 2: Write a Gateway client test with nonce authentication and event ACK**

Use `httptest.NewServer` plus `websocket.Accept` to create a local protocol peer. The handler must:

1. Send `protocol.ChallengeMessage{Envelope: protocol.Envelope{ProtocolVersion: 1, Type: "challenge"}, Nonce: base64.StdEncoding.EncodeToString(nonce)}`.
2. Read `protocol.AuthenticateMessage`, parse its certificate, verify the certificate public key is Ed25519, and verify its signature over the decoded nonce.
3. Send `protocol.AuthenticatedMessage{Envelope: protocol.Envelope{ProtocolVersion: 1, Type: "authenticated"}, HeartbeatIntervalSeconds: 5}`.
4. Read messages until it has observed exactly one inventory, one heartbeat, and an event batch containing sequence 1.
5. Send `protocol.EventsAckMessage{Envelope: protocol.Envelope{ProtocolVersion: 1, Type: "events_acked"}, ThroughSequence: 1}` and signal a buffered `serverDone` channel.

The test itself must assign `storedIdentity, signer, ca := testsupport.NewStoredIdentity(t, store, "node-1", gatewayURL, time.Now().Add(365*24*time.Hour))`, use those values for certificate/signature assertions, append event sequence 1 before starting the client, run `Client.Run()` with a five-second context, wait on `serverDone`, cancel the context, and assert `PendingEvents(100)` is empty. Every receive in the test server uses a five-second timeout so a stalled client fails instead of hanging the suite.

Add `TestClientRenewsCertificateBeforeExpiry`: save an identity expiring in 29 days, authenticate, require the next control message to be `certificate_renewal_request`, sign its CSR with the same test CA for another year, respond with `certificate_renewed`, then assert the stored certificate public key still matches the original private key and `CertificateExpiresAt` moved forward. A certificate expiring in 31 days must not renew.

Add `TestClientReconnectsAndReplaysUnackedEvents`: the test server closes the first authenticated connection before ACK, accepts a second connection, and asserts sequence 1 is replayed byte-for-byte before sending ACK. Inject a deterministic backoff function returning zero in tests; production uses jittered exponential backoff. Assert two authentications occurred and the spool is empty only after the second connection commits its ACK.

- [ ] **Step 3: Run the packages and verify failure**

Run: Dockerized `go test ./internal/inventory ./internal/gateway -v`.

Expected: FAIL because probe and Gateway client are absent.

- [ ] **Step 4: Implement deterministic inventory probing**

Detection order:

1. Jetson when `/etc/nv_tegra_release` exists or device-tree model contains `Jetson`.
2. x86 NVIDIA when architecture is x86_64/amd64 and `nvidia-smi` succeeds.
3. Return `ErrUnsupportedPlatform` otherwise.

Normalize architecture to `arm64` or `amd64`. Store raw version strings only after trimming NUL/newline characters. Convert KiB/MiB values to bytes with overflow checks. `resources` uses stable keys `cpu_logical_cores`, `memory_total_bytes`, `memory_available_bytes`, `disk_total_bytes`, `disk_available_bytes`, and `gpus`. Each GPU item has `index`, `uuid` when available, `name`, `compute_capability`, `memory_total_bytes`, `memory_free_bytes`, and `driver`. Preserve every x86 GPU returned by `nvidia-smi`; for Jetson, report one integrated GPU with the device-tree model and shared-memory semantics rather than inventing dedicated VRAM. Include compatibility fingerprint keys `platform_kind`, `architecture`, `gpu_names`, `compute_capabilities`, `driver`, `cuda`, `tensorrt`, `jetpack`, and `l4t`; missing optional values are omitted, not fabricated.

- [ ] **Step 5: Implement the Gateway connection loop**

For each connection:

1. Load identity from bbolt.
2. Dial `GatewayURL` with the operating-system trust store and append the enrolled platform CA as an additional root; never replace system roots with only the device CA.
3. Read protocol v1 challenge.
4. Sign decoded nonce with stored Ed25519 key.
5. Send authenticate message, require authenticated response, and reject heartbeat intervals outside 5-300 seconds.
6. When `CertificateExpiresAt <= now + 30 days`, generate a CSR with the existing private key, send `certificate_renewal_request`, verify the returned certificate uses that same public key and chains to the enrolled CA, then atomically replace only the certificate and expiry in bbolt.
7. Send inventory, one heartbeat, and up to 100 pending events immediately after authentication/renewal.
8. Repeat heartbeat and pending-event delivery on every server-provided heartbeat interval.
9. Apply `events_acked` only after validating the ACK cursor is not ahead of the highest sent sequence.
10. Reconnect with jittered exponential backoff from 1 second to 30 seconds.
11. Exit promptly on context cancellation.

Set the connection read limit to 1 MiB. After the initial synchronous challenge/authentication/optional-renewal exchange, run exactly one WebSocket reader goroutine and keep all writes in the connection loop. The reader publishes validated server messages/errors to a buffered channel; the main loop selects over context cancellation, heartbeat ticker, and reader results. Cancelling a connection must stop and join the reader before reconnecting, preventing leaked goroutines and concurrent writers.

Do not log enrollment tokens, private keys, certificates, signatures, or complete WSS query strings.

- [ ] **Step 6: Compose the Agent process**

`main.go` loads config, opens `${StateDir}/agent.db`, probes inventory, derives `protocol.EnrollmentFacts` from the probe, enrolls when identity is absent, appends `agent_started`, runs the Gateway client with that inventory, and shuts down on SIGINT/SIGTERM. Probing before first enrollment prevents a node from claiming a pool that does not match its detected architecture/platform. Exit nonzero for invalid config, enrollment rejection, corrupt state, or unsupported hardware; temporary network errors remain inside the reconnect loop.

- [ ] **Step 7: Run all Go checks**

Run:

```powershell
docker run --rm -v "${PWD}:/src" -w /src/apps/node-agent golang:1.26.5-alpine sh -lc "gofmt -w . && go test -race ./... && go vet ./..."
```

Expected: PASS. If Alpine race builds require GCC, install `gcc musl-dev` inside the command before `go test -race`; do not disable the race test.

- [ ] **Step 8: Commit live Agent connectivity**

```bash
git add apps/node-agent/cmd apps/node-agent/internal/inventory apps/node-agent/internal/gateway
git commit -m "feat: connect node agent to platform"
```

---

### Task 8: Package the Agent and Add the Local E2E Smoke Flow

**Files:**
- Create: `apps/node-agent/Dockerfile`
- Create: `apps/node-agent/packaging/systemd/visiox-node-agent.service`
- Create: `apps/node-agent/packaging/install.sh`
- Create: `scripts/build-node-agent.ps1`
- Create: `scripts/smoke-node-agent.ps1`
- Modify: `.gitignore`
- Modify: `infra/compose/docker-compose.yml`
- Modify: `apps/api-service/src/visiox_api/main.py`
- Create: `tests/integration/test_agent_ca_startup.py`

**Interfaces:**
- Produces: `dist/visiox-node-agent-linux-amd64` and `dist/visiox-node-agent-linux-arm64`.
- Produces: systemd unit `visiox-node-agent.service`.
- Produces: one-command local smoke validation.

- [ ] **Step 1: Add a failing local CA startup test**

```python
def test_local_lifespan_generates_agent_ca_when_enabled(tmp_path, monkeypatch):
    settings = Settings(
        VISIOX_ENV="local",
        agent_gateway_enabled=True,
        agent_auto_generate_ca=True,
        agent_ca_cert_path=tmp_path / "ca.crt",
        agent_ca_key_path=tmp_path / "ca.key",
        seed_base_models_on_startup=False,
    )
    monkeypatch.setattr("visiox_api.main.get_settings", lambda: settings)
    with TestClient(create_app()):
        assert settings.agent_ca_cert_path.exists()
        assert settings.agent_ca_key_path.exists()
```

Add this production-mode test:

```python
def test_production_lifespan_refuses_to_generate_missing_ca(tmp_path, monkeypatch):
    settings = Settings(
        VISIOX_ENV="production",
        agent_gateway_enabled=True,
        agent_auto_generate_ca=True,
        agent_public_ws_url="wss://platform.example/agent/v1/connect",
        agent_ca_cert_path=tmp_path / "missing-ca.crt",
        agent_ca_key_path=tmp_path / "missing-ca.key",
        seed_base_models_on_startup=False,
    )
    monkeypatch.setattr("visiox_api.main.get_settings", lambda: settings)
    with pytest.raises(RuntimeError, match="Agent CA material is required"):
        with TestClient(create_app()):
            pass
```

- [ ] **Step 2: Run the startup test and verify failure**

Run: `python -m pytest tests/integration/test_agent_ca_startup.py -q`

Expected: FAIL because lifespan does not manage Agent CA readiness.

- [ ] **Step 3: Integrate fail-closed CA readiness into API lifespan**

At startup:

```python
if settings.agent_gateway_enabled:
    ensure_agent_ca(settings)
```

The `ensure_agent_ca()` service owns the local-generation versus production-fail-closed policy and validates existing CA material. `agent_gateway_enabled` defaults to false so existing tests and deployments remain compatible. Compose sets `VISIOX_AGENT_GATEWAY_ENABLED=true`; when enabled, CA readiness is mandatory.

- [ ] **Step 4: Create reproducible Agent build packaging**

Use `golang:1.26.5-alpine` as the builder and `alpine:3.22` as the local runtime image. Build with `CGO_ENABLED=0`, `-trimpath`, and `-ldflags="-s -w"`. The PowerShell build script invokes two Docker builds or `go build` container commands with `GOARCH=amd64` and `GOARCH=arm64`, then writes binaries under ignored `dist/`. Add only these generated-state rules to `.gitignore`: `/dist/`, `/.local/visiox-agent/`, `agent.db`, and `*.agent-ca.key`; do not broadly ignore all `.pem` files because test fixtures may need tracked public certificates.

The systemd unit must include:

```ini
[Unit]
Description=Visiox Node Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=visiox-agent
Group=visiox-agent
EnvironmentFile=/etc/visiox-agent/agent.env
ExecStart=/usr/local/bin/visiox-node-agent
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/var/lib/visiox-agent
PrivateTmp=true
ProtectHome=true

[Install]
WantedBy=multi-user.target
```

`install.sh` creates the locked system user/group `visiox-agent`, owns `/var/lib/visiox-agent` as `visiox-agent:visiox-agent` mode `0750`, and installs `/etc/visiox-agent/agent.env` as `root:visiox-agent` mode `0640`. M1 neither requires Docker at service startup nor mounts the Docker Socket into the Agent process. Task M3 will add the narrow runtime-management permission, Docker ordering, and corresponding hardening review.

- [ ] **Step 5: Add Compose CA volume without overwriting unrelated changes**

Add named volume `agent-pki` mounted at `/var/lib/visiox/pki` in `api-service`. Set local-only values:

```yaml
VISIOX_AGENT_GATEWAY_ENABLED: "true"
VISIOX_AGENT_AUTO_GENERATE_CA: "true"
VISIOX_AGENT_PUBLIC_WS_URL: ${VISIOX_AGENT_PUBLIC_WS_URL:-ws://host.docker.internal:8000/agent/v1/connect}
```

Preserve the current Label Studio host command and `80:8080` mapping exactly as found at implementation time.

- [ ] **Step 6: Implement the PowerShell smoke flow**

The script must:

1. Verify API `/health`.
2. Create one enrollment token through `POST /agent/v1/enrollment-tokens`.
3. Build the Agent image.
4. Run one disposable Agent with `VISIOX_AGENT_PLATFORM_URL=http://host.docker.internal:8000`, `VISIOX_AGENT_ALLOW_INSECURE_LOCAL=true`, `VISIOX_AGENT_VERSION=0.1.0-test`, the returned token, node name `smoke-x86-node`, and a test-only injected inventory fixture.
5. Poll `GET /nodes` for at most 60 seconds until the node is `online`.
6. Stop/remove the Agent container in `finally`.
7. Exit nonzero and print recent API/Agent logs on failure.

The injected fixture is allowed only when `VISIOX_AGENT_TEST_INVENTORY_JSON` is set and `AgentVersion` ends with `-test`; production builds reject it.

- [ ] **Step 7: Run packaging and smoke verification**

Run:

```powershell
python -m pytest tests/integration/test_agent_ca_startup.py tests/integration/test_agent_gateway.py tests/integration/test_node_enrollment_api.py -q
.\scripts\build-node-agent.ps1
.\scripts\smoke-node-agent.ps1
```

Expected: tests PASS, two binaries exist in `dist/`, and the smoke script reports `smoke-x86-node online`.

- [ ] **Step 8: Commit packaging and E2E support**

```bash
git add apps/node-agent/Dockerfile apps/node-agent/packaging scripts/build-node-agent.ps1 scripts/smoke-node-agent.ps1 .gitignore infra/compose/docker-compose.yml apps/api-service/src/visiox_api/main.py tests/integration/test_agent_ca_startup.py
git commit -m "feat: package node agent onboarding"
```

---

### Task 9: Document Operations and Run the M1 Release Gate

**Files:**
- Create: `docs/runbooks/node-agent-onboarding.md`
- Modify: `README.md`

**Interfaces:**
- Produces: operator procedure for CA provisioning, token creation, systemd installation, status checks, log collection, certificate rotation expectations, draining, and uninstall.
- Produces: verified M1 release evidence in command output; no fake nodes are committed.

- [ ] **Step 1: Write the runbook with exact operator commands**

The runbook must include:

```bash
sudo useradd --system --home-dir /var/lib/visiox-agent --shell /usr/sbin/nologin visiox-agent
sudo install -d -o root -g visiox-agent -m 0750 /etc/visiox-agent
sudo install -d -o visiox-agent -g visiox-agent -m 0750 /var/lib/visiox-agent
sudo install -m 0755 visiox-node-agent-linux-arm64 /usr/local/bin/visiox-node-agent
sudo install -m 0644 visiox-node-agent.service /etc/systemd/system/visiox-node-agent.service
sudo systemctl daemon-reload
sudo systemctl enable --now visiox-node-agent
sudo systemctl status visiox-node-agent --no-pager
sudo journalctl -u visiox-node-agent -n 200 --no-pager
```

Also document that enrollment tokens are one-time secrets, Agent private keys remain under the state directory, putting a node into `draining` does not wipe the device, and uninstall must stop the service before removing state. M1 rotates device certificates under the same CA but does not provide zero-downtime CA rotation; replacing the CA requires a maintenance window and node re-enrollment. Production provisioning must set `VISIOX_AGENT_PUBLIC_WS_URL` to a LAN-reachable `wss://` URL backed by a trusted server certificate; `host.docker.internal` is only the Docker Desktop smoke-test default and is not an edge-device address.

- [ ] **Step 2: Run the complete Python release gate**

Run:

```powershell
python -m pytest -q
python -m ruff check apps packages workers tests
```

Expected: all tests PASS and Ruff reports no findings.

- [ ] **Step 3: Run the complete Go release gate**

Run:

```powershell
docker run --rm -v "${PWD}:/src" -w /src/apps/node-agent golang:1.26.5-alpine sh -lc "apk add --no-cache gcc musl-dev && gofmt -w . && go test -race ./... && go vet ./..."
git diff --exit-code -- apps/node-agent
.\scripts\build-node-agent.ps1
```

Expected: formatting produces no diff, race tests PASS, vet PASS, and both architecture binaries build.

- [ ] **Step 4: Run migrations and local enrollment smoke test**

Run:

```powershell
docker compose -f infra/compose/docker-compose.yml up -d --build api-service postgres redis minio registry
docker compose -f infra/compose/docker-compose.yml exec api-service alembic upgrade head
.\scripts\smoke-node-agent.ps1
```

Expected: migration reaches `20260715_0001`, API stays healthy, and one dynamically enrolled smoke node reaches `online`.

- [ ] **Step 5: Verify no secrets or generated binaries are staged**

Run:

```powershell
git status --short
$forbidden = git ls-files | Select-String -Pattern '(^|/)(ca\.key|agent\.db)$|^dist/|\.agent-ca\.key$'
if ($forbidden) { throw "Generated Agent secret or binary is tracked: $forbidden" }
git diff --cached --quiet
```

Expected: the tracked-file scan finds no generated secret/binary and the M1 commits leave no staged changes. Pre-existing unrelated working-tree changes may remain and must not be reverted or staged.

- [ ] **Step 6: Commit M1 operations documentation**

```bash
git add docs/runbooks/node-agent-onboarding.md README.md
git commit -m "docs: add node agent onboarding runbook"
```

## M1 Completion Criteria

M1 is complete only when all of the following are demonstrated:

- The platform host has no NVIDIA GPU dependency.
- One-time enrollment returns a certificate but never a private key.
- Reusing an enrollment token fails.
- An Agent authenticates by signing a server nonce with its enrolled key.
- A certificate inside the 30-day renewal window rotates over the authenticated Gateway, the private key remains unchanged, and the old certificate is rejected on reconnect.
- A real Agent-created node, not seed data, appears in `GET /nodes`.
- Jetson and x86 inventory fixtures map to separate default resource pools.
- Enrollment/inventory architecture mismatches become `incompatible` instead of entering an incorrect pool.
- Heartbeats update `last_seen_at` and online state.
- Event sequence replay is idempotent and ACK occurs only after database commit.
- Agent events survive process restart until acknowledged.
- The Agent reconnects after temporary platform loss.
- amd64 and arm64 binaries build from the pinned Go container.
- Existing Visiox Python tests remain green.
- No CA private key, device private key, Agent database, or generated binary is committed.

## Deferred to the Next Plans

- M2: immutable `ModelRelease`, edge ONNX/TensorRT optimization, FP16/INT8 evidence, and automatic candidate selection.
- M3: Docker runtime permissions, Edge Router, C++ TensorRT Native service, true `deploying -> running`, image HTTP inference, and health-gated deployment.
- M4: Triton adapter, dynamic batching, and Native/Triton contract parity.
- M5: blue-green routing, rollback, observability, signing, load shedding, and fault injection.
- Distributed edge training: DDP/NCCL job model and scheduler, designed separately after the deployment foundation.
