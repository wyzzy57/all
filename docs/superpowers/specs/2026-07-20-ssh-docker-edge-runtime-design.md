# Visiox SSH/Docker Edge Runtime Design

## 1. Goal

Build the first usable Visiox edge execution plane for NVIDIA Jetson and x86 GPU hosts. The Visiox control plane has no GPU compute. Dataset staging, YOLO26 training, TensorRT optimization, evaluation, and inference all execute on edge hosts connected through a directly reachable high-speed LAN.

The control channel is SSH. Docker is the workload runtime. The existing custom Node Agent HTTPS/WSS and certificate protocol is frozen and is not extended by this project.

## 2. Scope

This branch delivers:

- Per-device SSH bootstrap with platform-generated Ed25519 keys.
- Docker, NVIDIA runtime, GPU, CUDA, TensorRT, JetPack, disk, and network probing.
- YOLO26 object-detection deployment through TensorRT FP16, with optional INT8 calibration.
- Image HTTP inference and real online-experience routing to the edge endpoint.
- Single-GPU, single-node multi-GPU, and multi-node multi-GPU training through Ultralytics, PyTorch DDP, `torchrun`, and NCCL.
- Automatic compatible-node selection with manual override.
- Deployment stop, upgrade, rollback, health checks, and logs.
- Distributed-training stop, peer cleanup, checkpoint upload, and resume.
- Integration tests with disposable SSH targets and a two-node CPU/Gloo orchestration smoke test.
- Hardware acceptance scripts for one x86 NVIDIA GPU host and one Jetson host.

The branch does not add Kubernetes, custom WSS commands, custom mTLS renewal, video streaming, cross-Internet/NAT management, or mixed Jetson/x86 jobs.

## 3. Evaluated Approaches

### 3.1 Selected: Edge Executor Worker

The API persists desired state and publishes task identifiers to Redis. A dedicated `edge-executor-worker` resolves credentials, connects over SSH, runs versioned remote scripts, and records results.

This follows the repository's API, Redis, worker, and database boundaries. Long-running SSH work never blocks API request handlers, and worker restarts can reconcile database state against real remote containers.

### 3.2 Rejected: Ansible Runner

Ansible provides mature configuration management but introduces a second job and state model. Dynamic rank orchestration, streamed training logs, task cancellation, and product-facing status transitions would still require substantial custom integration.

### 3.3 Rejected: Direct SSH from API Requests

Direct execution is initially smaller but blocks API workers, loses lifecycle ownership on API restart, and does not provide a durable boundary for multi-node jobs.

## 4. Architecture

```text
Visiox API
   |
   | create job / query state
   v
Redis task queue
   |
   v
Edge Executor Worker
   |-- SSH bootstrap and key rotation
   |-- environment and GPU probe
   |-- artifact staging
   |-- TensorRT optimization and deployment
   |-- torchrun distributed orchestration
   `-- logs, stop, cleanup, and rollback
        |
        |-- SSH --> Jetson hosts
        `-- SSH --> x86 NVIDIA GPU hosts

MinIO   --> datasets, weights, checkpoints, engines, results
Registry --> immutable training and inference images
```

The platform never exposes Docker TCP. Edge hosts expose SSH and the explicitly deployed inference service ports. All Docker operations execute locally on the edge host through SSH.

The one-time password bootstrap is the only operation that does not travel through Redis. The API and Edge Executor share a private Unix-domain socket volume. The API forwards one bounded bootstrap request over that socket; the worker consumes the password in memory and returns only non-secret status. All post-bootstrap work uses durable identifier-only Redis jobs.

## 5. SSH Bootstrap and Identity

Each device receives a unique Ed25519 key pair.

1. The user submits host and SSH port for an unauthenticated host-key scan.
2. The worker returns the observed host-key type and fingerprint. The user compares and confirms it.
3. The user submits the confirmed fingerprint, administrator username, and one-time password in the bootstrap request.
4. The API forwards that request over the private Unix-domain socket. The worker creates the dedicated `visiox-edge` account, installs the public key, and grants Docker group membership.
5. Subsequent connections use the per-device private key and strict host-key verification.
6. The one-time password exists only in the API request, Unix-socket request, and worker memory. It is never persisted in PostgreSQL, Redis, task logs, or audit payloads.
7. Key rotation installs the new public key, verifies a second connection, and only then removes the previous key.

Private keys are encrypted with AES-256-GCM using a platform master key supplied as a Docker secret. The database stores ciphertext, nonce, key version, and public metadata. Only the Edge Executor Worker receives the master key. Decryption occurs in memory for one connection scope.

Paramiko provides password bootstrap, Ed25519 authentication, SFTP, and host-key verification. `AutoAddPolicy` and disabled host-key validation are forbidden.

## 6. Remote Execution Safety

- Redis messages contain identifiers, never passwords, private keys, or registry credentials.
- Remote behavior is implemented by versioned, static scripts uploaded through SFTP.
- Job parameters are written to validated JSON configuration files instead of being interpolated into shell commands.
- Every remote execution has an idempotency key and stable Visiox Docker labels.
- Logs redact passwords, private keys, MinIO credentials, registry tokens, signed URLs, and authorization headers.
- SSH connect, command, transfer, and idle timeouts are bounded.
- Retries use bounded exponential backoff and query actual remote state before repeating a mutation.
- All bootstrap, probe, deployment, training, stop, rollback, and key-rotation operations create audit records.

## 7. Device Inventory and Resource Pools

The worker probes:

- OS, architecture, kernel, CPU, and memory.
- NVIDIA GPU model, UUID, count, memory, and compute capability.
- Driver, CUDA, cuDNN, TensorRT, JetPack, and L4T versions.
- Docker Engine, NVIDIA Container Runtime, and available runtimes.
- Disk capacity, data-cache capacity, network interfaces, and reachable ports.

Scheduled SSH probes update online state. A resource pool is compatible only when architecture, device family, runtime, CUDA/PyTorch/Ultralytics image, and model requirements match. A distributed job never mixes Jetson and x86 nodes.

## 8. High-Performance Deployment

The first inference workload is YOLO26 object detection with an image HTTP API.

1. The user selects `best.pt` or `last.pt`, a target host, an instance name, and automatic or manual optimization.
2. The worker validates GPU memory, disk, Docker, NVIDIA runtime, CUDA/TensorRT compatibility, image availability, and service port availability.
3. The edge host pulls an architecture-compatible immutable image from the Registry and downloads weights from MinIO.
4. Optimization runs on the target device:
   - Default: PT to ONNX to TensorRT FP16.
   - Optional: TensorRT INT8 when a calibration dataset is selected.
   - Manual overrides: precision, input size, batch, workspace, and dynamic shapes.
5. Engines are cached by model SHA256, GPU architecture, TensorRT/CUDA versions, precision, and input profile.
6. The inference container starts with GPU access, resource limits, immutable image digest, model/engine mounts, and stable Visiox labels.
7. The worker checks GPU visibility, performs standard-image warmup, calls `/health` and `/ready`, and executes one real image inference.
8. Only after all gates pass does the service become `running`.

Upgrades start and validate a candidate container before switching the endpoint. Failure preserves or restores the previous container. The online-experience API calls the real edge endpoint and never loads the model on the control plane.

## 9. Distributed Training

Training uses Ultralytics, PyTorch DDP, `torchrun`, and NCCL.

1. The scheduler selects or validates manually selected nodes from one compatible pool.
2. It assigns the leader, `node_rank`, GPU allocation, `master_addr`, `master_port`, and NCCL interface.
3. Every node pulls the same immutable training image digest and downloads the same dataset and initial weights from MinIO.
4. Dataset and weight SHA256 values must match on every node before launch.
5. The worker starts all ranks in parallel with host networking and a shared rendezvous configuration:

```bash
torchrun \
  --nnodes=<node-count> \
  --nproc-per-node=<gpu-count> \
  --node-rank=<rank> \
  --master-addr=<leader-ip> \
  --master-port=<allocated-port> \
  -m visiox_yolo26.training
```

Each container receives explicit GPU devices, NCCL settings, shared-memory limits, a read-only dataset cache, and an isolated output volume. Rank 0 uploads checkpoints, final weights, metrics, and artifacts. Per-rank logs and resource metrics are preserved and exposed through the existing training-visualization APIs.

If one rank fails, the worker stops all peers and records the task as failed after cleanup. User cancellation stops every rank and uploads any valid checkpoint. Resume verifies the checkpoint and resource-pool compatibility before starting a new distributed attempt.

## 10. Data Model and APIs

New persistence responsibilities:

- `EdgeSshCredential`: node, encrypted private key, public key, fingerprints, SSH user/port, key version, and rotation timestamps.
- `RemoteExecution`: operation, node, related task/service, idempotency key, phase, exit code, redacted log reference, and timestamps.
- `DeploymentInstance`: node, container ID, image digest, model/engine, port, endpoint, health, and rollback metadata.
- `DistributedTrainingRun`: pool, nodes, ranks, rendezvous settings, containers, checkpoint, and attempt metadata.

Primary APIs:

```text
POST /edge-nodes/scan-host-key
POST /edge-nodes/bootstrap
POST /edge-nodes/{id}/test-connection
POST /edge-nodes/{id}/probe
POST /edge-nodes/{id}/rotate-key

POST /deployments
POST /deployments/{id}/stop
POST /deployments/{id}/rollback
GET  /deployments/{id}/logs

POST /training-jobs/{id}/launch
POST /training-jobs/{id}/stop
POST /training-jobs/{id}/resume
GET  /training-jobs/{id}/nodes
```

Existing node, resource-pool, pipeline, service, trained-model, training-job, and visualization surfaces remain the product entry points. Their execution backend changes from local/control-plane behavior to durable Edge Executor jobs.

## 11. State Machines

Deployment:

```text
queued -> connecting -> probing -> preparing -> optimizing
       -> starting -> warming_up -> running
```

Failures enter `failed`; user stop enters `stopped`; failed upgrade enters `rolling_back` and returns to `running` only after the old instance is healthy.

Training:

```text
queued -> selecting_nodes -> staging -> rendezvous_ready
       -> launching -> training -> uploading_artifacts -> succeeded
```

A rank failure enters `stopping_peers -> failed`. Cancellation enters `stopping_peers -> stopped`. A disconnected worker reconciles task state against remote container labels before deciding to retry, resume, or fail.

## 12. Error Handling

- Partial staging failure cleans all already staged nodes.
- SSH loss does not immediately mark a running container failed; reconciliation first checks remote state.
- A service remains running when the control plane is temporarily unavailable.
- Training peers are always stopped after a confirmed rank failure.
- Hosts that cannot be reached during stop are marked `cleanup_required` and surfaced to the user.
- Engine incompatibility invalidates the cache entry and triggers one controlled rebuild.
- Artifact checksum mismatch aborts before container launch.
- Host-key change blocks all operations until an operator explicitly re-confirms the device identity.

## 13. Testing and Acceptance

### Unit

- Credential encryption and key rotation.
- Host-key verification.
- Structured remote configuration and command safety.
- Scheduler compatibility and rank assignment.
- State-machine transitions, retry, reconciliation, redaction, and idempotency.

### Docker SSH Integration

Disposable SSH targets validate bootstrap, strict host-key behavior, probe, transfer, deployment, logs, stop, rollback, failure cleanup, and key rotation. Deterministic Docker and NVIDIA command fixtures cover failure paths without requiring a GPU.

### Distributed Smoke

Two disposable SSH targets execute a real two-node `torchrun` CPU/Gloo job. The smoke verifies staging, rendezvous, rank logs, peer failure propagation, checkpoint upload, stop, and resume orchestration. Production commands substitute NCCL and explicit GPU devices.

### Hardware Acceptance

Acceptance scripts run on at least one x86 NVIDIA GPU host and one Jetson host. They verify Docker/NVIDIA runtime, YOLO26 FP16 TensorRT build, image inference, service health, training, artifact upload, and cleanup. Multi-node NCCL acceptance uses only one compatible resource pool.

## 14. Completion Criteria

The branch is complete when:

- Device bootstrap and key rotation require no permanent password storage.
- Real device inventory populates resource pools.
- A trained YOLO26 weight deploys as a healthy TensorRT image API on x86 and Jetson.
- Optional INT8 deployment works with a selected calibration dataset.
- Single-GPU, single-node multi-GPU, and multi-node training can launch, stop, fail cleanly, and resume from checkpoint.
- Existing service online experience and training visualization use real edge execution data.
- Disposable SSH and distributed smoke tests pass.
- x86 and Jetson hardware acceptance scripts are available and documented.
