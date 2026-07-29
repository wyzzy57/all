# Durable Logs and Service Resume Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist complete training, remote execution, deployment, and inference-service logs, stream them to authorized users, and make stopped services safely startable/restartable from the last successful deployment revision.

**Architecture:** Store log stream metadata and chunk indexes in PostgreSQL, compressed redacted text in MinIO, and live notifications in Redis/SSE. Extend existing deployment handlers and reconciliation with a persisted desired state and immutable deployment revision rather than creating a second service runtime.

**Tech Stack:** SQLAlchemy, Alembic, FastAPI SSE, Redis, MinIO, SSH + Docker, Vue 3, virtualized log rendering, Vitest.

---

### Task 1: Add Log and Service Revision Models

**Files:**
- Create: `packages/visiox-db/src/visiox_db/models/observability.py`
- Modify: `packages/visiox-db/src/visiox_db/models/model_space.py`
- Modify: `packages/visiox-db/src/visiox_db/models/__init__.py`
- Create: `infra/migrations/versions/20260727_0005_logs_service_desired_state.py`
- Modify: `tests/integration/test_migrations.py`

- [ ] Write failing migration assertions for `log_streams`, `log_chunks`, service `desired_state`, `active_revision`, and instance `deployment_revision`.
- [ ] Model log source/status enums, unique stream/sequence chunks, time indexes, checksums, byte ranges, and retention timestamps.
- [ ] Set migration `down_revision = "20260727_0004"` so the schema chain remains linear.
- [ ] Default existing running services to desired `running`, stopped services to `stopped`, and failed services according to their last instance state.
- [ ] Run migration tests and commit with `feat: add durable log and service revision schema`.

### Task 2: Implement Redacted Chunked Log Storage

**Files:**
- Create: `apps/api-service/src/visiox_api/services/log_streams.py`
- Modify: `workers/edge-executor-worker/src/visiox_edge_executor_worker/redaction.py`
- Create: `tests/unit/test_log_streams.py`
- Modify: `tests/unit/test_edge_log_redaction.py`

- [ ] Write tests for ordered chunks, UTF-8 boundaries, gzip content, SHA-256, cursor reads, retention, and recursive redaction of password/token/secret/cookie/private-key/authorization values.
- [ ] Implement `open_stream`, `append_lines`, `close_stream`, `read_after_cursor`, and `build_download` with 256 KiB target chunks.
- [ ] Store objects under `logs/{organization_id}/{resource_type}/{resource_id}/{stream_id}/{sequence}.log.gz`.
- [ ] Ensure every append is redacted before object upload and index commit.
- [ ] Run tests and commit with `feat: persist redacted log streams`.

### Task 3: Capture Remote and Container Logs

**Files:**
- Create: `workers/edge-executor-worker/src/visiox_edge_executor_worker/log_capture.py`
- Modify: `workers/edge-executor-worker/src/visiox_edge_executor_worker/deployment.py`
- Modify: `workers/edge-executor-worker/src/visiox_edge_executor_worker/distributed_execution.py`
- Modify: `workers/edge-executor-worker/src/visiox_edge_executor_worker/state.py`
- Modify: `workers/training-worker/src/visiox_training_worker/main.py`
- Modify: `workers/llm-training-worker/src/visiox_llm_training_worker/entrypoint.py`
- Create: `tests/unit/test_remote_log_capture.py`

- [ ] Write tests that interleave stdout/stderr, preserve source/timestamps, resume from a Docker `--since` cursor, close streams on completion, and retain logs on command failure.
- [ ] Capture SSH command output, `docker logs --timestamps`, Ultralytics output, and LLaMA-Factory output through the shared log service adapter.
- [ ] Publish Redis notifications containing stream ID and last sequence only; never publish log bodies or secrets.
- [ ] Keep existing `redacted_log_uri` as a compatibility pointer to the generated full-log object.
- [ ] Run worker tests and commit with `feat: capture remote training and service logs`.

### Task 4: Add Authorized Log History, SSE, and Download APIs

**Files:**
- Create: `apps/api-service/src/visiox_api/routes/log_streams.py`
- Modify: `apps/api-service/src/visiox_api/main.py`
- Create: `tests/integration/test_log_streams_api.py`

- [ ] Test inherited resource permission, admin access, denial, cursor history, SSE reconnect, completed streams, deleted object handling, and full download filename.
- [ ] Implement `GET /log-streams/{id}`, `/chunks`, `/events`, and `/download`; SSE heartbeats occur every 15 seconds.
- [ ] Resolve resource authorization through the parent training job, remote execution, service, or deployment instance.
- [ ] Cap one history response at 1 MiB or 2,000 lines and require a cursor for additional data.
- [ ] Run tests and commit with `feat: expose authorized live logs`.

### Task 5: Add Start and Restart Service Operations

**Files:**
- Modify: `apps/api-service/src/visiox_api/routes/services.py`
- Modify: `packages/visiox-common/src/visiox_common/tasks.py`
- Modify: `workers/edge-executor-worker/src/visiox_edge_executor_worker/deployment.py`
- Create: `workers/edge-executor-worker/remote/start_deployment.sh`
- Modify: `workers/edge-executor-worker/src/visiox_edge_executor_worker/scripts.py`
- Modify: `pyproject.toml`
- Modify: `tests/integration/test_services_api.py`
- Modify: `tests/unit/test_edge_deployment.py`
- Modify: `tests/unit/test_edge_acceptance_scripts.py`

- [ ] Write failing tests for stopped-to-starting-to-running, restart, already-running idempotency, missing deployment revision, failed remote start, and permission checks.
- [ ] Add controlled task types for service start/restart and `POST /services/{id}/start|restart`.
- [ ] Persist `desired_state` before enqueue; use the last successful revision's image, model checksum, environment allowlist, mounts, port, and health check.
- [ ] Implement a strict start script that accepts structured environment variables, validates the existing container identity, and never evaluates user-provided shell.
- [ ] Run tests and commit with `feat: resume and restart deployed services`.

### Task 6: Reconcile Desired and Remote Service State

**Files:**
- Modify: `workers/edge-executor-worker/src/visiox_edge_executor_worker/reconciliation.py`
- Modify: `workers/edge-executor-worker/src/visiox_edge_executor_worker/runner.py`
- Modify: `tests/unit/test_edge_reconciliation.py`

- [ ] Add tests for platform restart with healthy running container, stopped container desired running, missing container with valid revision, user-stopped service, duplicate reconciliation, unavailable node, and unrecoverable spec.
- [ ] Reconcile desired `running` by inspect/start/redeploy and desired `stopped` by preserving stopped state.
- [ ] Use deterministic idempotency keys `service:{id}:revision:{revision}:desired:{state}`.
- [ ] Set structured failure codes and append all decisions to the service log stream and audit log.
- [ ] Run tests and commit with `feat: reconcile recoverable services`.

### Task 7: Build the Native Log Viewer and Resume Controls

**Files:**
- Create: `apps/frontend/src/components/logs/LogStreamViewer.vue`
- Modify: `apps/frontend/src/api/client.ts`
- Modify: `apps/frontend/src/views/services/ServicesView.vue`
- Modify: `apps/frontend/src/views/training-visualization/TrainingVisualizationView.vue`
- Create: `apps/frontend/tests/log-stream-viewer.spec.ts`
- Modify: `apps/frontend/tests/services-view.spec.ts`
- Modify: `apps/frontend/tests/training-visualization-view.spec.ts`

- [ ] Write tests for history paging, SSE append, reconnect cursor, follow/pause, search, source/level filters, error highlighting, download, resume, and restart buttons.
- [ ] Implement a bounded DOM list that keeps at most 5,000 rendered lines while retaining server cursors for older history.
- [ ] Replace direct `log_uri` text reads with stream metadata and the shared viewer.
- [ ] Show “恢复” only for stopped services and “重启” only for running services; reflect queued state immediately.
- [ ] Run tests, type checking, and commit with `feat: add live logs and service resume UI`.

### Task 8: Verify Logs and Service Recovery

**Files:** Modify only files above when defects appear.

- [ ] Run affected backend unit/integration suites, especially services, workers, reconciliation, redaction, and log APIs.
- [ ] Run affected frontend tests, type checking, and build.
- [ ] Deploy a service to a test SSH node, generate inference logs, stop it, restart the platform, verify it stays stopped, then click resume and verify the same revision becomes healthy.
- [ ] Restart the platform while the service is running and verify reconciliation without creating a duplicate container.
- [ ] Confirm a member with `invoke` but not `view` logs cannot access the stream.
- [ ] Commit verified fixes with `test: verify durable logs and service recovery`.
