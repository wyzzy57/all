# Unified Node and Resource Management Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let administrators add SSH servers manually, inventory all compute capacity as unified nodes, allocate pools and quotas to users/groups, and expose only authorized compatible capacity to training and deployment.

**Architecture:** Extend the existing `ComputeNode`, `ResourcePool`, `EdgeSshCredential`, host-key scan, SSH bootstrap, and inventory probe code instead of creating a second node stack. Add a scheduler-facing authorization service and native Vue node/allocation pages while preserving the current SSH + Docker execution worker.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Paramiko, Docker over SSH, Redis tasks, Vue 3, Element Plus, Vitest.

---

### Task 1: Extend Unified Node Inventory State

**Files:**
- Modify: `packages/visiox-db/src/visiox_db/models/edge_compute.py`
- Create: `infra/migrations/versions/20260727_0002_unified_node_inventory.py`
- Modify: `packages/visiox-db/src/visiox_db/models/__init__.py`
- Test: `tests/unit/test_edge_compute_models.py`
- Modify: `tests/integration/test_migrations.py`

- [ ] Write failing assertions for `enabled`, `labels`, `connection_method`, `inventory_refreshed_at`, and `resource_revision` on `ComputeNode`.
- [ ] Run `python -m pytest tests/unit/test_edge_compute_models.py tests/integration/test_migrations.py -q` and confirm missing-column failures.
- [ ] Add fields with defaults `enabled=True`, `labels={}`, `connection_method="ssh"`, and `resource_revision=0`; index `enabled` and `inventory_refreshed_at`.
- [ ] Create the additive migration with `down_revision = "20260727_0001"` and backfill existing nodes as enabled SSH nodes unless `agent_version` identifies an active agent node.
- [ ] Re-run focused tests and commit with `feat: unify compute node inventory state`.

### Task 2: Add a Transactional Manual Node Onboarding API

**Files:**
- Modify: `apps/api-service/src/visiox_api/routes/nodes.py`
- Modify: `apps/api-service/src/visiox_api/routes/edge_ssh.py`
- Modify: `apps/api-service/src/visiox_api/services/edge_bootstrap.py`
- Create: `apps/api-service/src/visiox_api/services/manual_node_onboarding.py`
- Modify: `apps/api-service/src/visiox_api/schemas/agent_protocol.py`
- Test: `tests/integration/test_edge_ssh_api.py`
- Test: `tests/integration/test_edge_node_probe_api.py`

- [ ] Write a failing `POST /nodes/manual` integration test that scans a fixed host key, installs the controlled SSH key, probes inventory, creates one pool/node/credential, and returns no private key material.
- [ ] Run the two focused test files and confirm the route is missing.
- [ ] Implement `ManualNodeOnboardingService` by composing the existing host-key scanner, bootstrap service, and `_persist_probe_inventory` logic; do not copy Paramiko connection code into the route.
- [ ] Require administrator authentication and record `node.create`, `node.host_key.confirm`, and `node.probe` audits.
- [ ] Make retries idempotent on normalized `host + port`; reject a changed host fingerprint with `SSH_HOST_KEY_CHANGED`.
- [ ] Run focused tests and commit with `feat: add manual SSH node onboarding`.

### Task 3: Add Periodic Resource Refresh

**Files:**
- Create: `apps/api-service/src/visiox_api/services/node_inventory.py`
- Modify: `apps/api-service/src/visiox_api/routes/nodes.py`
- Modify: `workers/edge-executor-worker/src/visiox_edge_executor_worker/runner.py`
- Modify: `workers/edge-executor-worker/src/visiox_edge_executor_worker/inventory.py`
- Test: `tests/unit/test_edge_inventory.py`
- Test: `tests/integration/test_edge_node_probe_api.py`

- [ ] Write tests for active interval 15 seconds, idle interval 60 seconds, monotonic `resource_revision`, stale-node transition to `offline`, and preservation of the last valid inventory on transient SSH failure.
- [ ] Run focused tests and confirm failures.
- [ ] Implement inventory normalization for CPU, memory, disk, GPU utilization, VRAM, temperature, power, Docker version, CUDA, and TensorRT.
- [ ] Add `POST /nodes/{id}/refresh` for administrators and enqueue periodic refreshes through the existing edge execution stream.
- [ ] Keep probe errors in node metadata and audits; never overwrite valid resources with an empty failure payload.
- [ ] Run tests and commit with `feat: refresh unified node resources`.

### Task 4: Enforce Allocation Policies in Resource Selection

**Files:**
- Create: `apps/api-service/src/visiox_api/services/resource_scheduler.py`
- Modify: `apps/api-service/src/visiox_api/routes/training_jobs.py`
- Modify: `apps/api-service/src/visiox_api/routes/services.py`
- Create: `tests/unit/test_resource_scheduler.py`
- Modify: `tests/integration/test_training_pipeline.py`
- Modify: `tests/integration/test_services_api.py`

- [ ] Write failing tests for pool `use` permission, expired allocation, concurrent-job limit, GPU-count limit, service-instance limit, offline/disabled node, architecture mismatch, and insufficient VRAM.
- [ ] Implement `list_schedulable_nodes(session: Session, actor: User, workload: WorkloadRequirements) -> list[ComputeNode]` and `assert_allocation_available(session: Session, actor: User, resource_pool_id: str, workload: WorkloadRequirements) -> None` using Phase 1 grants and allocation policies.
- [ ] Replace direct online-node queries in training and service creation with scheduler results; administrators retain bypass but still must satisfy hardware compatibility.
- [ ] Return structured `RESOURCE_QUOTA_EXCEEDED` and `NO_COMPATIBLE_NODE` errors with non-sensitive constraint details.
- [ ] Run tests and commit with `feat: enforce resource allocations`.

### Task 5: Add Node and Allocation API Types to the Frontend

**Files:**
- Modify: `apps/frontend/src/api/client.ts`
- Modify: `apps/frontend/tests/api-client.spec.ts`

- [ ] Add failing request tests for scan host key, create manual node, refresh, enable/disable, delete, assign pool, list allocations, and upsert allocation.
- [ ] Add `ComputeNodeRecord` fields matching the migration and typed payloads that never contain stored private keys.
- [ ] Implement client methods and one normalized `ApiError` path for host-key and quota codes.
- [ ] Run `npm test -- --run tests/api-client.spec.ts` and commit with `feat: add node administration client`.

### Task 6: Build the Resource and Node Page

**Files:**
- Create: `apps/frontend/src/views/resources/NodeManagementView.vue`
- Create: `apps/frontend/src/components/resources/NodeOnboardingDialog.vue`
- Create: `apps/frontend/src/components/resources/NodeResourcePanel.vue`
- Modify: `apps/frontend/src/router/index.ts`
- Modify: `apps/frontend/src/App.vue`
- Create: `apps/frontend/tests/node-management-view.spec.ts`

- [ ] Write component tests for administrator-only navigation, host-key confirmation, probe progress, resource cards, refresh, enable/disable, pool assignment, and delete confirmation.
- [ ] Build the onboarding dialog as explicit steps: connection, host fingerprint, probe result, pool assignment, confirmation.
- [ ] Display architecture and capability badges without using local/edge as a navigation category.
- [ ] Use stable grids for CPU, memory, disk, GPU, CUDA, TensorRT, active jobs, and services.
- [ ] Run the focused Vitest file and type checking; commit with `feat: add unified node management UI`.

### Task 7: Build Administrator Resource Allocation

**Files:**
- Modify: `apps/frontend/src/views/admin/AuthorizationView.vue`
- Create: `apps/frontend/src/components/resources/ResourceAllocationEditor.vue`
- Create: `apps/frontend/tests/resource-allocation-view.spec.ts`

- [ ] Write tests for principal selection, pool selection, non-negative quotas, expiry, save, revoke, and administrator-only access.
- [ ] Implement one table per principal with pool, concurrent training, GPU, service-instance, and expiry columns.
- [ ] Show current use beside each limit using API-provided usage values; do not calculate global usage from frontend lists.
- [ ] Run tests and type checking; commit with `feat: add resource allocation administration`.

### Task 8: Verify Unified Resource Management

**Files:** Modify only files above when verification exposes defects.

- [ ] Run `python -m pytest tests/unit/test_edge_compute_models.py tests/unit/test_edge_inventory.py tests/unit/test_resource_scheduler.py tests/integration/test_edge_ssh_api.py tests/integration/test_edge_node_probe_api.py tests/integration/test_training_pipeline.py tests/integration/test_services_api.py -q`.
- [ ] Run `npm test -- --run tests/node-management-view.spec.ts tests/resource-allocation-view.spec.ts tests/api-client.spec.ts` and `npm run typecheck` from `apps/frontend`.
- [ ] On a test SSH GPU server, add the node, confirm the host fingerprint, refresh resources, authorize one group, and verify an unauthorized member cannot select it.
- [ ] Disable the node and verify new training/deployment submissions exclude it without changing historical records.
- [ ] Commit verified fixes with `test: verify unified resource management`.
