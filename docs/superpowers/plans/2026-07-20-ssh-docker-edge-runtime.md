# SSH/Docker Edge Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a usable Visiox edge runtime that bootstraps Ubuntu Jetson/x86 hosts over SSH, deploys YOLO26 image inference in Docker, and launches same-pool Ultralytics distributed training with `torchrun`.

**Architecture:** The existing API remains the product control plane and publishes identifier-only Redis jobs. A new `edge-executor-worker` owns SSH credentials, remote Docker execution, reconciliation, deployment, and distributed training. MinIO remains the artifact plane and the local Registry remains the immutable image plane; the existing Agent/WSS path stays frozen for compatibility but is not used by new SSH-managed nodes.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/Alembic, Redis Streams, Paramiko, cryptography AES-256-GCM, MinIO, Docker Engine, NVIDIA Container Runtime, Ultralytics YOLO26, PyTorch `torchrun`, NCCL/Gloo, Vue 3, Vitest, pytest.

## Global Constraints

- The first production loop supports YOLO26 object detection and image/HTTP API inference only.
- Edge hosts are Ubuntu/Linux Jetson or x86 NVIDIA GPU machines reachable directly on the same high-speed LAN.
- Platform control-plane containers do not require GPU or training compute.
- Docker TCP is never exposed; Docker commands run locally on edge hosts through SSH.
- Each node gets one platform-generated Ed25519 key pair and strict host-key verification; `AutoAddPolicy` is forbidden.
- The bootstrap password is never stored in PostgreSQL, Redis, logs, or audit payloads.
- Private keys use AES-256-GCM with a Docker-secret master key available only to `edge-executor-worker`.
- Redis payloads contain task and resource identifiers only, never passwords, private keys, registry credentials, or signed artifact URLs.
- Remote scripts are static and versioned; dynamic values travel in validated JSON files, never interpolated shell fragments.
- Deployment defaults to automatic PT -> ONNX -> TensorRT FP16 optimization, permits manual format/precision overrides, and optionally supports INT8 with a calibration dataset.
- Distributed training selects only one compatible resource pool per run; Jetson and x86 nodes are never mixed in one run.
- The MVP supports single GPU, single-node multi-GPU, and multi-node multi-GPU launch, stop, failure cleanup, and checkpoint resume.
- Kubernetes, custom WSS commands, custom mTLS renewal, video inference, and cross-Internet/NAT management are out of scope.

---

## File Structure

New worker package:

- `workers/edge-executor-worker/src/visiox_edge_executor_worker/runner.py`: Redis polling and operation dispatch.
- `workers/edge-executor-worker/src/visiox_edge_executor_worker/bootstrap_server.py`: private Unix-socket password bootstrap server.
- `workers/edge-executor-worker/src/visiox_edge_executor_worker/crypto.py`: AES-GCM key encryption/decryption.
- `workers/edge-executor-worker/src/visiox_edge_executor_worker/ssh.py`: strict Paramiko connection and SFTP adapter.
- `workers/edge-executor-worker/src/visiox_edge_executor_worker/scripts.py`: static-script manifest and validated JSON transfer.
- `workers/edge-executor-worker/src/visiox_edge_executor_worker/inventory.py`: probe parsing and compatibility fingerprinting.
- `workers/edge-executor-worker/src/visiox_edge_executor_worker/deployment.py`: deployment state machine and reconciliation.
- `workers/edge-executor-worker/src/visiox_edge_executor_worker/distributed.py`: rank planning and `torchrun` orchestration.
- `workers/edge-executor-worker/remote/*.sh`: versioned Ubuntu remote operations.

Existing surfaces:

- `packages/visiox-db/src/visiox_db/models/edge_compute.py`: SSH credentials and remote executions.
- `packages/visiox-db/src/visiox_db/models/model_space.py`: deployment instances and distributed-run metadata.
- `apps/api-service/src/visiox_api/routes/edge_ssh.py`: scan/bootstrap/probe/key-rotation endpoints.
- `apps/api-service/src/visiox_api/routes/services.py`: asynchronous remote deployment/stop/rollback.
- `apps/api-service/src/visiox_api/routes/training_jobs.py`: edge launch/stop/resume and node allocation.
- `apps/frontend/src/views/model-space/ModelSpaceView.vue`: real environment/pool and deployment status controls.
- `apps/frontend/src/views/services/ServicesView.vue`: remote instance health, endpoint, logs, stop, rollback.

---

### Task 1: Persistence and Task Contracts

**Files:**
- Modify: `packages/visiox-db/src/visiox_db/models/edge_compute.py`
- Modify: `packages/visiox-db/src/visiox_db/models/model_space.py`
- Modify: `packages/visiox-db/src/visiox_db/models/__init__.py`
- Modify: `packages/visiox-common/src/visiox_common/tasks.py`
- Modify: `packages/visiox-messaging/src/visiox_messaging/streams.py`
- Create: `infra/migrations/versions/20260720_0001_ssh_edge_runtime.py`
- Test: `tests/unit/test_ssh_edge_models.py`
- Test: `tests/integration/test_migrations.py`

**Interfaces:**
- Produces: `EdgeSshCredential`, `RemoteExecution`, `DeploymentInstance`, `DistributedTrainingRun` SQLAlchemy models.
- Produces: `TaskType.EDGE_PROBE`, `EDGE_DEPLOY`, `EDGE_STOP_DEPLOYMENT`, `EDGE_ROLLBACK`, `EDGE_TRAIN`, `EDGE_STOP_TRAINING`, and `EDGE_RESUME_TRAINING` routed to `stream:edge_executor.commands`.

- [ ] **Step 1: Write failing model and stream tests**

```python
def test_edge_task_types_use_identifier_only_stream_payload():
    command = TaskCommand(
        task_id="task-1",
        task_type=TaskType.EDGE_DEPLOY,
        resource_refs={"deployment_service_id": "service-1"},
    )
    fields = command.to_stream_fields()
    assert STREAM_BY_TASK_TYPE[TaskType.EDGE_DEPLOY] == "stream:edge_executor.commands"
    assert json.loads(fields["resource_refs"]) == {"deployment_service_id": "service-1"}
    assert json.loads(fields["payload"]) == {}
```

- [ ] **Step 2: Run tests and verify the new contracts are absent**

Run: `python -m pytest tests/unit/test_ssh_edge_models.py tests/integration/test_migrations.py -q`

Expected: FAIL because the new models/task types and migration do not exist.

- [ ] **Step 3: Add the persistence models and migration**

Use these exact ownership fields:

```python
class EdgeSshCredential(IdMixin, TimestampMixin, Base):
    __tablename__ = "edge_ssh_credentials"
    node_id: Mapped[str] = mapped_column(ForeignKey("compute_nodes.id"), unique=True, nullable=False)
    ssh_host: Mapped[str] = mapped_column(String(255), nullable=False)
    ssh_port: Mapped[int] = mapped_column(Integer, nullable=False, default=22)
    ssh_user: Mapped[str] = mapped_column(String(64), nullable=False, default="visiox-edge")
    host_key_type: Mapped[str] = mapped_column(String(40), nullable=False)
    host_key_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    encrypted_private_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    encryption_nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
```

`RemoteExecution` stores operation, phase, status, node/resource IDs, idempotency key, exit code, redacted log URI, error code/message, and timestamps. `DeploymentInstance` stores node/service/container/image digest/engine/endpoint/health/rollback fields. `DistributedTrainingRun` stores job/pool/nodes/ranks/rendezvous/checkpoint/attempt fields.

The migration revision is `20260720_0001` with exact `down_revision = "20260717_0001"`; migration tests must prove upgrade from the current head and downgrade back to it.

- [ ] **Step 4: Add identifier-only Edge task types and stream routing**

```python
EDGE_EXECUTOR_TASK_TYPES = {
    TaskType.EDGE_PROBE,
    TaskType.EDGE_DEPLOY,
    TaskType.EDGE_STOP_DEPLOYMENT,
    TaskType.EDGE_ROLLBACK,
    TaskType.EDGE_TRAIN,
    TaskType.EDGE_STOP_TRAINING,
    TaskType.EDGE_RESUME_TRAINING,
}
```

- [ ] **Step 5: Run migrations and focused tests**

Run: `python -m pytest tests/unit/test_ssh_edge_models.py tests/integration/test_migrations.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add packages/visiox-db packages/visiox-common packages/visiox-messaging infra/migrations tests
git commit -m "feat: add ssh edge runtime persistence"
```

### Task 2: Credential Encryption and Strict SSH Adapter

**Files:**
- Modify: `pyproject.toml`
- Modify: `packages/visiox-common/src/visiox_common/settings.py`
- Create: `workers/edge-executor-worker/src/visiox_edge_executor_worker/__init__.py`
- Create: `workers/edge-executor-worker/src/visiox_edge_executor_worker/crypto.py`
- Create: `workers/edge-executor-worker/src/visiox_edge_executor_worker/ssh.py`
- Test: `tests/unit/test_edge_credential_crypto.py`
- Test: `tests/unit/test_edge_ssh_adapter.py`

**Interfaces:**
- Produces: `CredentialCipher.encrypt(private_key: bytes) -> EncryptedSecret` and `decrypt(secret: EncryptedSecret) -> bytes`.
- Produces: `scan_host_key(host, port, timeout) -> ScannedHostKey` and `StrictSshClient.connect(...) -> SshSession`.

- [ ] **Step 1: Write failing crypto and host-key tests**

```python
def test_credential_cipher_round_trip_and_wrong_key_failure():
    encrypted = CredentialCipher(b"a" * 32, key_version=1).encrypt(b"private")
    assert CredentialCipher(b"a" * 32, key_version=1).decrypt(encrypted) == b"private"
    with pytest.raises(InvalidTag):
        CredentialCipher(b"b" * 32, key_version=1).decrypt(encrypted)

def test_strict_client_rejects_changed_host_key(fake_transport):
    fake_transport.remote_key = TEST_ED25519_KEY_B
    with pytest.raises(HostKeyMismatchError):
        strict_client.connect(expected_fingerprint=FINGERPRINT_A)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/unit/test_edge_credential_crypto.py tests/unit/test_edge_ssh_adapter.py -q`

Expected: FAIL because worker modules are missing.

- [ ] **Step 3: Add Paramiko and secret settings**

Add `paramiko>=3.5,<4.0` and settings `edge_credential_master_key_file`, `edge_bootstrap_socket`, SSH timeout values, and `edge_executor_stream`. Read exactly 32 raw bytes from the Docker secret; reject missing/incorrect keys at worker startup.

- [ ] **Step 4: Implement AES-GCM and strict SSH**

```python
@dataclass(frozen=True)
class EncryptedSecret:
    ciphertext: bytes
    nonce: bytes
    key_version: int

class CredentialCipher:
    def encrypt(self, plaintext: bytes) -> EncryptedSecret:
        nonce = os.urandom(12)
        return EncryptedSecret(self._aes.encrypt(nonce, plaintext, b"visiox-edge-ssh"), nonce, self.key_version)
```

Use `paramiko.RejectPolicy`, verify SHA-256 fingerprints before authentication, cap connect/auth/banner timeouts, and expose only structured command results.

- [ ] **Step 5: Run focused tests**

Run: `python -m pytest tests/unit/test_edge_credential_crypto.py tests/unit/test_edge_ssh_adapter.py tests/unit/test_settings.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml packages/visiox-common workers/edge-executor-worker tests/unit
git commit -m "feat: add encrypted strict ssh client"
```

### Task 3: One-Time Bootstrap Channel and Node APIs

**Files:**
- Create: `workers/edge-executor-worker/src/visiox_edge_executor_worker/bootstrap_server.py`
- Create: `workers/edge-executor-worker/remote/bootstrap_user.sh`
- Create: `apps/api-service/src/visiox_api/services/edge_bootstrap.py`
- Create: `apps/api-service/src/visiox_api/routes/edge_ssh.py`
- Modify: `apps/api-service/src/visiox_api/main.py`
- Test: `tests/unit/test_edge_bootstrap_protocol.py`
- Test: `tests/integration/test_edge_ssh_api.py`

**Interfaces:**
- Consumes: strict SSH and `CredentialCipher` from Task 2.
- Produces: `POST /edge-nodes/scan-host-key`, `POST /edge-nodes/bootstrap`, `POST /edge-nodes/{id}/test-connection`, and `POST /edge-nodes/{id}/rotate-key`.

- [ ] **Step 1: Write failing bounded-protocol and API tests**

```python
def test_bootstrap_request_never_serializes_password_to_task_or_audit(client, fake_socket):
    response = client.post("/edge-nodes/bootstrap", json=BOOTSTRAP_REQUEST)
    assert response.status_code == 201
    assert "password" not in fake_socket.last_response
    assert session.query(Task).count() == 0
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/unit/test_edge_bootstrap_protocol.py tests/integration/test_edge_ssh_api.py -q`

Expected: FAIL with missing route/server modules.

- [ ] **Step 3: Implement length-prefixed Unix-socket protocol**

Requests are UTF-8 JSON capped at 64 KiB and include `request_id`, host, port, administrator, password, confirmed fingerprint, and node name. Responses include only `request_id`, status, node ID, and sanitized error fields. Apply a 60-second request timeout and unlink stale socket files on worker startup.

- [ ] **Step 4: Implement idempotent bootstrap script**

`bootstrap_user.sh` must create `visiox-edge`, create mode-0700 `.ssh`, install one public key into mode-0600 `authorized_keys`, add Docker group membership, and never echo arguments. Upload the script and JSON separately through SFTP; invoke it with fixed paths.

- [ ] **Step 5: Persist only encrypted key material after key-auth verification**

Generate Ed25519 keys in the worker, bootstrap with the one-time password, reconnect using the generated key and confirmed host key, then save the credential and mark the node `online`. On failure, save no private key and return a sanitized error.

- [ ] **Step 6: Run focused tests**

Run: `python -m pytest tests/unit/test_edge_bootstrap_protocol.py tests/integration/test_edge_ssh_api.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add workers/edge-executor-worker apps/api-service tests
git commit -m "feat: bootstrap ssh edge nodes"
```

### Task 4: Inventory Probe and Compatible Resource Pools

**Files:**
- Create: `workers/edge-executor-worker/remote/probe_inventory.sh`
- Create: `workers/edge-executor-worker/src/visiox_edge_executor_worker/inventory.py`
- Create: `workers/edge-executor-worker/src/visiox_edge_executor_worker/scripts.py`
- Modify: `apps/api-service/src/visiox_api/routes/nodes.py`
- Test: `tests/unit/test_edge_inventory.py`
- Test: `tests/integration/test_edge_node_probe_api.py`

**Interfaces:**
- Produces: `InventorySnapshot` and `compatibility_key(snapshot) -> str`.
- Produces: `POST /edge-nodes/{id}/probe` and pool assignment based on architecture, platform, CUDA major, TensorRT major, and GPU compute capability.

- [ ] **Step 1: Write failing Jetson/x86 parsing and pool tests**

```python
@pytest.mark.parametrize((fixture, platform), [("jetson.json", "jetson"), ("x86.json", "x86_nvidia")])
def test_inventory_snapshot_parses_supported_hosts(load_fixture, fixture, platform):
    snapshot = parse_inventory(load_fixture(fixture))
    assert snapshot.platform_kind == platform
    assert snapshot.docker.nvidia_runtime_available is True
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/unit/test_edge_inventory.py tests/integration/test_edge_node_probe_api.py -q`

Expected: FAIL because inventory code is missing.

- [ ] **Step 3: Implement static probe and parser**

Probe `/etc/os-release`, `uname`, `/proc/meminfo`, `docker version`, `docker info`, `nvidia-smi` where available, and Jetson `/etc/nv_tegra_release`/`dpkg` metadata. Emit one JSON object and treat missing mandatory Docker/NVIDIA runtime features as `unsupported` with explicit reasons.

- [ ] **Step 4: Implement compatible pool assignment**

Pool keys use `platform_kind:architecture:cuda_major:tensorrt_major:compute_capability`. Nodes may be manually moved only to pools whose compatibility policy accepts that fingerprint.

- [ ] **Step 5: Run focused tests**

Run: `python -m pytest tests/unit/test_edge_inventory.py tests/integration/test_edge_node_probe_api.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add workers/edge-executor-worker apps/api-service tests
git commit -m "feat: probe edge gpu inventory"
```

### Task 5: Edge Executor Queue, Auditing, and Reconciliation

**Files:**
- Create: `workers/edge-executor-worker/src/visiox_edge_executor_worker/runner.py`
- Create: `workers/edge-executor-worker/src/visiox_edge_executor_worker/redaction.py`
- Create: `workers/edge-executor-worker/src/visiox_edge_executor_worker/state.py`
- Modify: `infra/compose/docker-compose.yml`
- Modify: `.gitignore`
- Create: `scripts/init-edge-runtime.ps1`
- Modify: `apps/api-service/Dockerfile`
- Test: `tests/unit/test_edge_executor_dispatch.py`
- Test: `tests/unit/test_edge_log_redaction.py`

**Interfaces:**
- Consumes: Edge task types and persistence from Task 1.
- Produces: consumer group `edge-executor-workers`, idempotent claim/finalize methods, and startup reconciliation by Visiox Docker labels.

- [ ] **Step 1: Write failing dispatch/idempotency/redaction tests**

```python
def test_dispatch_loads_operation_by_id_not_payload(dispatcher, command):
    command.resource_refs = {"remote_execution_id": "exec-1"}
    command.payload = {}
    dispatcher.dispatch(command)
    assert dispatcher.repository.loaded_ids == ["exec-1"]

def test_redactor_removes_credentials_and_signed_urls():
    assert "secret" not in redact("Authorization: Bearer secret password=secret X-Amz-Signature=secret")
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/unit/test_edge_executor_dispatch.py tests/unit/test_edge_log_redaction.py -q`

Expected: FAIL because dispatcher/redactor are missing.

- [ ] **Step 3: Implement queue runner and state transitions**

Create the Redis consumer group if absent, decode messages through `TaskCommand`, load `RemoteExecution` by ID, atomically claim `queued` rows, dispatch by operation, acknowledge only after durable terminal state, and leave crashed pending messages reclaimable.

- [ ] **Step 4: Add worker Compose service and Docker secret**

The service mounts `/run/visiox-edge` for the Unix socket, `/run/secrets/edge_credential_master_key` read-only, and the remote script directory read-only. The API mounts only the socket volume; it never mounts the master key. `scripts/init-edge-runtime.ps1` creates a cryptographically random 32-byte local secret when absent, writes it beneath ignored `.local-secrets/`, and never prints the value.

- [ ] **Step 5: Run focused tests and Compose validation**

Run: `python -m pytest tests/unit/test_edge_executor_dispatch.py tests/unit/test_edge_log_redaction.py -q`

Run: `docker compose -f infra/compose/docker-compose.yml config --quiet`

Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add workers/edge-executor-worker infra/compose apps/api-service/Dockerfile scripts/init-edge-runtime.ps1 .gitignore tests/unit
git commit -m "feat: run durable edge executor jobs"
```

### Task 6: Production YOLO26 Docker Deployment

**Files:**
- Create: `workers/edge-executor-worker/remote/deploy_inference.sh`
- Create: `workers/edge-executor-worker/remote/inspect_deployment.sh`
- Create: `workers/edge-executor-worker/remote/stop_deployment.sh`
- Create: `workers/edge-executor-worker/src/visiox_edge_executor_worker/deployment.py`
- Modify: `apps/yolo26-inference/src/visiox_yolo26_inference/config.py`
- Modify: `apps/yolo26-inference/Dockerfile`
- Modify: `apps/api-service/src/visiox_api/routes/services.py`
- Test: `tests/unit/test_edge_deployment.py`
- Test: `tests/integration/test_services_api.py`

**Interfaces:**
- Produces: asynchronous `POST /services` returning `queued`, real status/log endpoint fields, stop, and rollback.
- Produces: deployment transitions `queued -> connecting -> probing -> preparing -> optimizing -> starting -> warming_up -> running`.

- [ ] **Step 1: Write failing deployment plan and API-state tests**

```python
def test_auto_plan_prefers_tensorrt_fp16_for_supported_gpu():
    plan = build_deployment_plan(MODEL_PT, X86_TENSORRT_INVENTORY, DeploymentOptions())
    assert plan.export_format == "engine"
    assert plan.precision == "fp16"

def test_create_service_enqueues_remote_deployment(client, fake_producer):
    response = client.post("/services", json=REMOTE_SERVICE_REQUEST)
    assert response.status_code == 201
    assert response.json()["status"] == "queued"
    assert fake_producer.last.resource_refs == {"deployment_service_id": response.json()["id"]}
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/unit/test_edge_deployment.py tests/integration/test_services_api.py -q`

Expected: FAIL because deployment is still local/synthetic.

- [ ] **Step 3: Implement deterministic deployment planning**

Validate detect-task weights, node/pool compatibility, image digest, requested port, TensorRT support, and INT8 calibration dataset. Cache engines by model checksum + TensorRT version + GPU compute capability + precision + input shape.

- [ ] **Step 4: Implement remote deployment state machine**

Stage JSON, pull immutable image digest, stage model from a short-lived MinIO URL, export TensorRT engine when required, launch with explicit `--gpus device=...`, read-only model mount, restart policy, health check, Visiox labels, fixed shared-memory limit, and no privileged mode. Warm up with one real image and require `/health` plus `/predict/image` success before `running`.

- [ ] **Step 5: Implement stop, rollback, and reconciliation**

Stop by stable instance label, preserve the previous healthy container during upgrades, rollback to the previous image/model/engine tuple after failed warmup, and reconcile database status from container ID, labels, health, and endpoint reachability.

- [ ] **Step 6: Run focused tests**

Run: `python -m pytest tests/unit/test_edge_deployment.py tests/integration/test_services_api.py tests/integration/test_yolo26_inference_api.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add workers/edge-executor-worker apps/yolo26-inference apps/api-service tests
git commit -m "feat: deploy yolo26 inference over ssh"
```

### Task 7: Edge Service UI and Real Online Experience

**Files:**
- Modify: `apps/frontend/src/api/client.ts`
- Modify: `apps/frontend/src/views/model-space/ModelSpaceView.vue`
- Modify: `apps/frontend/src/views/services/ServicesView.vue`
- Modify: `apps/frontend/src/components/ServiceExperiencePanel.vue`
- Test: `apps/frontend/tests/model-space-view.spec.ts`
- Test: `apps/frontend/tests/services-view.spec.ts`

**Interfaces:**
- Consumes: node pools and asynchronous service states from Tasks 4 and 6.
- Produces: environment/pool/node selection, optimization override controls, live phase/health/log display, stop/rollback, and inference through the real edge endpoint proxy.

- [ ] **Step 1: Write failing UI tests**

```typescript
it('shows edge deployment phases and enables stop only for active states', async () => {
  api.listServices.mockResolvedValue({ items: [{ ...service, status: 'warming_up' }] })
  render(ServicesView)
  expect(await screen.findByText('预热中')).toBeVisible()
  expect(screen.getByRole('button', { name: '停止' })).toBeEnabled()
})
```

- [ ] **Step 2: Run tests and verify failure**

Run: `npm --prefix apps/frontend test -- --run tests/model-space-view.spec.ts tests/services-view.spec.ts`

Expected: FAIL because new states and controls are absent.

- [ ] **Step 3: Implement edge environment and optimization controls**

Use real resource pools/nodes, show GPU/runtime compatibility, default to automatic FP16, reveal manual format/precision/input-shape and optional INT8 dataset controls only when selected, and use the existing restrained Visiox form layout.

- [ ] **Step 4: Implement real service lifecycle UI**

Poll active services, map every backend state to Chinese labels, expose endpoint/host/container/health, show redacted logs, stop and rollback commands, and keep the existing online image inference panel wired through `/services/{id}/predict/image`.

- [ ] **Step 5: Run frontend tests**

Run: `npm --prefix apps/frontend test -- --run tests/model-space-view.spec.ts tests/services-view.spec.ts`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/frontend
git commit -m "feat: manage edge deployments in console"
```

### Task 8: Same-Pool Distributed Ultralytics Training

**Files:**
- Create: `workers/edge-executor-worker/remote/stage_training.sh`
- Create: `workers/edge-executor-worker/remote/launch_rank.sh`
- Create: `workers/edge-executor-worker/remote/stop_training.sh`
- Create: `workers/edge-executor-worker/src/visiox_edge_executor_worker/distributed.py`
- Modify: `apps/api-service/src/visiox_api/routes/training_jobs.py`
- Modify: `workers/training-worker/src/visiox_training_worker/train_entrypoint.py`
- Test: `tests/unit/test_distributed_training_plan.py`
- Test: `tests/integration/test_training_pipeline.py`

**Interfaces:**
- Produces: `DistributedPlan(nodes, ranks, master_addr, master_port, world_size)` and edge-backed launch/stop/resume APIs.
- Produces: training transitions `queued -> selecting_nodes -> staging -> rendezvous_ready -> launching -> training -> uploading_artifacts -> succeeded`.

- [ ] **Step 1: Write failing scheduler and command tests**

```python
def test_scheduler_rejects_mixed_platform_nodes():
    with pytest.raises(IncompatibleResourcePoolError):
        build_distributed_plan([JETSON_NODE, X86_NODE], requested_gpus=2)

def test_torchrun_command_assigns_stable_ranks():
    plan = build_distributed_plan([X86_NODE_A, X86_NODE_B], requested_gpus=4)
    assert plan.world_size == 4
    assert plan.nodes[0].node_rank == 0
    assert plan.nodes[1].node_rank == 1
```

- [ ] **Step 2: Run tests and verify failure**

Run: `python -m pytest tests/unit/test_distributed_training_plan.py tests/integration/test_training_pipeline.py -q`

Expected: FAIL because distributed orchestration is missing.

- [ ] **Step 3: Implement compatible rank planning**

Select online, non-draining nodes from one pool; reserve explicit GPU UUIDs; choose rank 0's LAN address as rendezvous; allocate a bounded free port; persist every node/rank/container before launch. Reject insufficient or mixed resources before staging.

- [ ] **Step 4: Implement artifact staging and `torchrun` launch**

Each node pulls the same immutable training image, downloads the same dataset/model checksums from MinIO, and launches one container with explicit GPUs, NCCL settings, shared memory, read-only dataset cache, and per-rank output/log directories. Use `torchrun --nnodes --nproc-per-node --node-rank --master-addr --master-port` and Ultralytics training arguments already validated by `visiox_yolo26.training.params`.

- [ ] **Step 5: Implement failure propagation, stop, upload, and resume**

If any rank exits non-zero, stop all peers and mark `stopping_peers -> failed`. Cancellation stops every peer and uploads a valid `last.pt`. Only rank 0 uploads final weights, metrics, TensorBoard logs, and visualizations. Resume validates checkpoint checksum and original pool compatibility, increments attempt, and starts a new set of containers.

- [ ] **Step 6: Run focused tests**

Run: `python -m pytest tests/unit/test_distributed_training_plan.py tests/integration/test_training_pipeline.py -q`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add workers/edge-executor-worker workers/training-worker apps/api-service tests
git commit -m "feat: orchestrate distributed edge training"
```

### Task 9: Disposable SSH and Two-Node Gloo Smoke

**Files:**
- Create: `tests/integration/edge_ssh/docker-compose.test.yml`
- Create: `tests/integration/edge_ssh/Dockerfile`
- Create: `tests/integration/edge_ssh/test_edge_executor_ssh.py`
- Create: `tests/smoke/distributed_gloo_smoke.py`
- Create: `scripts/smoke-edge-runtime.ps1`
- Test: `tests/integration/edge_ssh/test_edge_executor_ssh.py`

**Interfaces:**
- Consumes: bootstrap, strict SSH, deployment, reconciliation, and distributed orchestration.
- Produces: one Windows-friendly command that proves the non-GPU control plane end to end.

- [ ] **Step 1: Build two disposable Ubuntu SSH targets**

Each target runs OpenSSH and a deterministic fake Docker/NVIDIA command fixture. Mount separate host keys, expose internal LAN addresses, and support forced command failures for cleanup/retry tests.

- [ ] **Step 2: Write end-to-end SSH lifecycle tests**

Cover fingerprint scan, bootstrap, key reconnect, changed-host-key rejection, probe, script/config transfer, deploy, warmup, logs, stop, rollback, key rotation, worker restart reconciliation, and redaction.

- [ ] **Step 3: Add real two-node CPU/Gloo `torchrun` smoke**

The smoke launches two ranks over the SSH-target network, performs an `all_reduce`, writes per-rank logs, uploads a checkpoint fixture, injects one rank failure, verifies peer stop, and resumes successfully.

- [ ] **Step 4: Run integration smoke**

Run: `powershell -ExecutionPolicy Bypass -File scripts/smoke-edge-runtime.ps1`

Expected: exits 0 and reports SSH lifecycle PASS plus two-node Gloo PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/edge_ssh tests/smoke scripts/smoke-edge-runtime.ps1
git commit -m "test: cover ssh edge runtime smoke"
```

### Task 10: Hardware Acceptance and Operator Runbook

**Files:**
- Create: `scripts/accept-edge-x86.ps1`
- Create: `scripts/accept-edge-jetson.ps1`
- Create: `docs/runbooks/ssh-docker-edge-runtime.md`
- Modify: `README.md`
- Test: `tests/unit/test_edge_acceptance_scripts.py`

**Interfaces:**
- Produces: repeatable x86 and Jetson acceptance commands with machine-readable JSON summaries.

- [ ] **Step 1: Write acceptance-script contract tests**

```python
def test_acceptance_scripts_require_host_confirmation_and_emit_json():
    for script in (X86_SCRIPT, JETSON_SCRIPT):
        text = script.read_text(encoding="utf-8")
        assert "ExpectedHostFingerprint" in text
        assert "ConvertTo-Json" in text
```

- [ ] **Step 2: Run test and verify failure**

Run: `python -m pytest tests/unit/test_edge_acceptance_scripts.py -q`

Expected: FAIL because acceptance scripts are missing.

- [ ] **Step 3: Implement acceptance scripts and runbook**

The x86 script verifies bootstrap, NVIDIA runtime, FP16 TensorRT export, image inference, service health, one short Ultralytics training job, artifact upload, stop, and cleanup. The Jetson script performs the same flow with JetPack/L4T checks. Both require an expected host fingerprint and never accept a password as a command-line argument; obtain it through `Read-Host -AsSecureString`.

- [ ] **Step 4: Run all software-only verification**

Run: `python -m pytest tests/unit tests/integration -q`

Run: `npm --prefix apps/frontend test -- --run`

Run: `docker compose -f infra/compose/docker-compose.yml config --quiet`

Run: `powershell -ExecutionPolicy Bypass -File scripts/smoke-edge-runtime.ps1`

Expected: all commands exit 0. Record x86/Jetson hardware tests as pending only when physical hosts are unavailable; do not claim hardware acceptance passed without their JSON reports.

- [ ] **Step 5: Commit**

```bash
git add scripts/accept-edge-x86.ps1 scripts/accept-edge-jetson.ps1 docs/runbooks README.md tests/unit/test_edge_acceptance_scripts.py
git commit -m "docs: add edge runtime acceptance runbook"
```

---

## Final Review Gate

- Confirm every Redis Edge command contains only IDs.
- Confirm the API container cannot read the credential master key.
- Confirm every SSH connection rejects unknown or changed host keys.
- Confirm no remote command interpolates request values into shell text.
- Confirm service `running` requires health, warmup, and real inference success.
- Confirm one rank failure stops every distributed peer.
- Confirm Jetson/x86 mixing is rejected before artifact staging.
- Confirm existing local training, service, Label Studio, and visualization tests still pass.
- Run a whole-branch code review against the branch merge base before merge or PR.
