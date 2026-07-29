# VisiOX User, Resource, and Observability Roadmap Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the approved single-organization user system, unified node resources, real sharing, recoverable services, durable logs, Label Studio SFT data flow, LLM observability, and permission-scoped statistics without breaking existing VisiOX data.

**Architecture:** Build a shared identity and object-authorization control plane first, then attach existing datasets, pipelines, training jobs, services, resource pools, and nodes to it. Reuse the current SSH + Docker execution path, PostgreSQL task state, Redis streams, MinIO artifacts, MLflow, TensorBoard, and Label Studio adapters through stable service boundaries.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL, Redis, MinIO, Paramiko, Docker over SSH, Vue 3, Pinia, Vue Router, Element Plus, Vitest, MLflow, TensorBoard, Label Studio.

---

## Delivery Rule

Each phase below is a separately testable release. Do not begin a phase until the preceding phase's migration, backend tests, frontend tests, and manual acceptance gate pass. Create the detailed plan named in each phase immediately before implementation so that exact line references match the code produced by preceding phases.

## Phase 1: Identity and Authorization Foundation

**Detailed plan:** `docs/superpowers/plans/2026-07-27-identity-rbac-foundation.md`

**Delivers:**

- Default organization and bootstrap administrator.
- Admin/member users, groups, memberships, sessions, grants, allocations, and audit records.
- Argon2id password hashing, short-lived access JWTs, opaque rotating refresh tokens.
- Login, refresh, logout, account settings, user management, and group management.
- Shared backend authentication and authorization dependencies.
- Pinia authentication store, router guards, login page, account page, and bottom-left user menu.

**Acceptance gate:** An administrator creates a member and group; the member can log in but cannot call an admin API or pass an authorization check for an ungranted resource.

## Phase 2: Unified Resource and Node Management

**Detailed plan:** `docs/superpowers/plans/2026-07-27-unified-node-resource-management.md`

**Delivers:**

- Manual SSH node onboarding with host-key confirmation.
- Docker, CPU, memory, disk, NVIDIA GPU, CUDA, and TensorRT probe.
- Unified node inventory independent of local/edge wording.
- Resource-pool assignment and user/group allocation policies.
- Periodic active/idle resource refresh.
- `/resources/nodes` and `/admin/resources` pages.

**Acceptance gate:** An administrator adds an SSH GPU server, assigns it to one group, and only members of that group can select it for a compatible job.

## Phase 3: Resource Ownership and Real Sharing

**Detailed plan:** `docs/superpowers/plans/2026-07-27-resource-ownership-sharing.md`

**Delivers:**

- Organization, owner, and visibility fields on primary resources.
- Backfill of all existing resources to the bootstrap administrator.
- Object-level list filtering and action checks for datasets, pipelines, jobs, models, services, pools, and nodes.
- Unified sharing dialog for users, groups, and platform-public grants.
- Ownership transfer and permission-aware resource selectors.

**Acceptance gate:** A public dataset is selectable by another member for training but cannot be edited or deleted by that member; a public service can be invoked but not managed.

## Phase 4: Durable Logs and Recoverable Service Lifecycle

**Detailed plan:** `docs/superpowers/plans/2026-07-27-durable-logs-service-resume.md`

**Delivers:**

- Log streams and MinIO-backed chunks for training, remote execution, deployment, and service containers.
- Cursor history API, SSE live tail, redaction, download, and retention.
- Persistent `desired_state` and deployment revision.
- Start, stop, restart, and idempotent reconciliation after platform restart.
- Native log viewer with follow, pause, filters, search, and download.

**Acceptance gate:** A running service can be stopped and manually resumed from the saved deployment revision; restarting the platform reconciles the remote container correctly; logs remain readable throughout.

## Phase 5: LLM Label Studio Data Loop

**Detailed plan:** `docs/superpowers/plans/2026-07-27-llm-label-studio-data-loop.md`

**Delivers:**

- SFT import normalization for Alpaca, ShareGPT, OpenAI Messages, JSONL, JSON, and CSV.
- Automatic Label Studio SFT project creation and task import.
- Permission-gated managed-session launch without exposing Label Studio credentials.
- Webhook synchronization plus scheduled compensation.
- Validation and immutable conversion of labeled rows to a versioned training dataset.
- Shared aligned data asset card for CV and LLM records.

**Acceptance gate:** A user imports LLM rows, labels them in Label Studio, synchronizes them, and converts only valid labeled rows into a checksum-pinned LLaMA-Factory dataset version.

## Phase 6: Dedicated LLM Training Observability

**Detailed plan:** `docs/superpowers/plans/2026-07-27-llm-training-observability.md`

**Delivers:**

- Engine-aware overview, metric, resource, and analysis tabs.
- Stable mapping between VisiOX job, MLflow run, TensorBoard events, node samples, checkpoints, logs, and artifacts.
- Separate charts by unit for loss, optimization, throughput, progress, and resources.
- Deterministic training-health analysis with source evidence.
- LLM stop, resume, retry, checkpoint, and artifact actions.

**Acceptance gate:** A real SFT job displays live loss, learning rate, throughput, GPU resources, logs, checkpoints, and final artifacts, and remains inspectable after completion.

## Phase 7: Scoped Statistics and Product Finish

**Detailed plan:** `docs/superpowers/plans/2026-07-27-scoped-statistics-product-finish.md`

**Delivers:**

- Administrator platform statistics and member-scoped workbench statistics.
- Pipeline, dataset, service, node, GPU, group-allocation, and failure aggregations.
- Audit log page and final account-management flows.
- Shared loading, empty, denied, and error states.
- Responsive visual QA for all new pages.

**Acceptance gate:** Administrator totals match PostgreSQL and live node state; a member sees the same dashboard components calculated only from resources they can access.

## Cross-Phase Migration Safety

- [ ] Take a PostgreSQL backup and record MinIO bucket counts before every schema phase.
- [ ] Run `alembic upgrade head` against a copied production database before deployment.
- [ ] Run ownership and grant backfill verification before enabling route enforcement.
- [ ] Never delete existing datasets, samples, annotations, training jobs, trained models, services, nodes, or artifact objects in a migration.
- [ ] Keep a tested downgrade only for additive schema changes; use forward repair for migrations that have already backfilled ownership.
- [ ] Record row counts before and after each migration in the release evidence.

## Cross-Phase Verification

- [ ] Run backend unit and integration tests with `python -m pytest tests/unit tests/integration -q`.
- [ ] Run frontend tests with `npm test -- --run` from `apps/frontend`.
- [ ] Run frontend type checking with `npm run typecheck` from `apps/frontend`.
- [ ] Run the production compose startup smoke test.
- [ ] Capture desktop and narrow-viewport screenshots for every new or materially changed page.
- [ ] Verify administrator and member permissions with separate browser sessions.
- [ ] Verify no secret appears in API responses, task payloads, audit metadata, or logs.
