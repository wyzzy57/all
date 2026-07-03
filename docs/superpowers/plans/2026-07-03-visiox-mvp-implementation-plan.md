# Visiox MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the first private-deployment Visiox MVP that supports YOLO26 model space, data preparation, Label Studio sync, training pipelines, Task Center, edge app packaging, Edge Agent deployment, and basic web management.

**Architecture:** Use a Docker Compose monorepo with a FastAPI API service, Python workers, PostgreSQL, Redis Stream/PubSub, MinIO, Registry, Label Studio, Vue 3 frontend, Edge Agent, and a unified YOLO26 inference image. Implement in vertical slices so each subsystem is testable before integration.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.x, Alembic, PostgreSQL, Redis Streams, MinIO SDK, Docker Compose, Vue 3, TypeScript, Vite, Element Plus, Pinia, Vitest, pytest.

---

## Scope Check

The architecture spec covers multiple independent subsystems. Implementing it as one giant plan would create weak boundaries and poor review points. This document is the implementation plan index and execution order. Each task below is a child implementation plan that must be expanded into a code-level plan before coding starts.

Do not start feature implementation until the relevant child plan exists.

## Target Repository Structure

```text
visiox/
├── apps/
│   ├── api-service/
│   ├── frontend/
│   ├── edge-agent/
│   └── yolo26-inference/
├── workers/
│   ├── training-worker/
│   ├── deployment-worker/
│   ├── label-sync-worker/
│   └── camera-worker/
├── packages/
│   ├── visiox-common/
│   ├── visiox-db/
│   ├── visiox-messaging/
│   ├── visiox-storage/
│   └── visiox-yolo26/
├── infra/
│   ├── compose/
│   ├── migrations/
│   └── seed/
├── tests/
│   ├── integration/
│   └── fixtures/
└── docs/
    └── superpowers/
```

Responsibilities:

- `apps/api-service`: FastAPI HTTP API, OpenAPI, WebSocket task progress, business entry points.
- `apps/frontend`: Vue 3 management console.
- `apps/edge-agent`: Device-side control API for Docker/local process lifecycle.
- `apps/yolo26-inference`: Unified YOLO26 inference service image.
- `workers/*`: Long-running async task consumers.
- `packages/visiox-common`: Shared enums, schemas, settings, errors.
- `packages/visiox-db`: SQLAlchemy models, session management, repository helpers.
- `packages/visiox-messaging`: Redis Stream/PubSub abstraction and message contracts.
- `packages/visiox-storage`: MinIO/object storage and local mount abstraction.
- `packages/visiox-yolo26`: YOLO26 task registry, dataset converters, train/export command builders.
- `infra/compose`: Docker Compose files and env templates.
- `infra/migrations`: Alembic migration environment.
- `infra/seed`: Initial YOLO26 base model metadata.
- `tests/integration`: Cross-service and Compose-level tests.
- `tests/fixtures`: Sample datasets, Label Studio payloads, and expected converter outputs.

## Execution Order

### Task 1: Repository Foundation And Compose Skeleton

**Files:**
- Create: `pyproject.toml`
- Create: `package.json`
- Create: `.env.example`
- Create: `infra/compose/docker-compose.yml`
- Create: `infra/compose/docker-compose.dev.yml`
- Create: `apps/api-service/Dockerfile`
- Create: `apps/api-service/src/visiox_api/main.py`
- Create: `packages/visiox-common/src/visiox_common/settings.py`
- Create: `tests/integration/test_api_health.py`
- Modify: `.gitignore`

**Goal:** The repository runs a minimal API with Postgres, Redis, MinIO, Registry, and Label Studio through Docker Compose.

- [ ] Write a child plan at `docs/superpowers/plans/2026-07-03-repo-foundation-compose.md`.
- [ ] Add minimal Python packaging and lint/test commands.
- [ ] Add `GET /health` to `api-service`.
- [ ] Add Compose services for `api-service`, `postgres`, `redis`, `minio`, `registry`, and `label-studio`.
- [ ] Add integration test for `GET /health`.
- [ ] Verify with `docker compose -f infra/compose/docker-compose.yml up --build`.
- [ ] Commit as `chore: scaffold platform foundation`.

**Acceptance:**
- `pytest tests/integration/test_api_health.py -v` passes.
- `GET /health` returns service status and dependency configuration without requiring business tables.
- Compose starts all infrastructure containers.

### Task 2: Database Core, Migrations, And Seed Data

**Files:**
- Create: `packages/visiox-db/src/visiox_db/base.py`
- Create: `packages/visiox-db/src/visiox_db/session.py`
- Create: `packages/visiox-db/src/visiox_db/models/*.py`
- Create: `infra/migrations/env.py`
- Create: `infra/seed/yolo26_base_models.json`
- Create: `tests/integration/test_migrations.py`

**Goal:** Establish PostgreSQL schema for Task Center, model space, data preparation, devices, cameras, edge apps, and deployment records.

- [ ] Write a child plan at `docs/superpowers/plans/2026-07-03-database-core.md`.
- [ ] Define SQLAlchemy models for `Task`, `ModelSource`, `BaseModel`, `TrainedModel`, `TrainingPipeline`, `TrainingJob`, `Dataset`, `DatasetSample`, `Annotation`, `LabelProject`, `Device`, `Camera`, `EdgeApp`, `EdgeAppVersion`, and `Deployment`.
- [ ] Add Alembic migration environment.
- [ ] Seed 30 YOLO26 base model metadata records.
- [ ] Test migration upgrade from empty database.
- [ ] Commit as `feat: add database core schema`.

**Acceptance:**
- Alembic upgrade creates all tables.
- Seed produces exactly 30 YOLO26 base model records: 6 tasks times 5 scales.
- No user, role, or permission tables exist.

### Task 3: Redis Messaging And Task Center

**Files:**
- Create: `packages/visiox-messaging/src/visiox_messaging/streams.py`
- Create: `packages/visiox-messaging/src/visiox_messaging/pubsub.py`
- Create: `packages/visiox-common/src/visiox_common/tasks.py`
- Create: `apps/api-service/src/visiox_api/routes/tasks.py`
- Create: `apps/api-service/src/visiox_api/ws/tasks.py`
- Create: `tests/integration/test_task_center.py`

**Goal:** All long-running operations use a shared Task Center and Redis message contracts.

- [ ] Write a child plan at `docs/superpowers/plans/2026-07-03-task-center-messaging.md`.
- [ ] Implement task status model: `PENDING`, `QUEUED`, `RUNNING`, `SUCCESS`, `FAILED`, `CANCELED`.
- [ ] Implement Redis Stream producer and consumer helpers.
- [ ] Implement Pub/Sub progress event publisher.
- [ ] Add API endpoints to create/list/get/cancel tasks.
- [ ] Add WebSocket endpoint for task progress.
- [ ] Test task creation, stream enqueue, status update, and progress event propagation.
- [ ] Commit as `feat: add task center messaging`.

**Acceptance:**
- Every task has durable database state.
- Redis Stream messages include task id, task type, resource refs, and payload version.
- Redis loss does not erase final task status.

### Task 4: Object Storage And Base Model Download

**Files:**
- Create: `packages/visiox-storage/src/visiox_storage/client.py`
- Create: `packages/visiox-storage/src/visiox_storage/checksum.py`
- Create: `workers/model-worker/src/visiox_model_worker/main.py`
- Create: `apps/api-service/src/visiox_api/routes/base_models.py`
- Create: `tests/integration/test_base_model_download.py`

**Goal:** Manage model sources and download YOLO26 base weights from an internal file server into object storage on first use.

- [ ] Write a child plan at `docs/superpowers/plans/2026-07-03-base-model-storage.md`.
- [ ] Implement configurable model source: `http`, `minio`, `s3`, `local_mount`.
- [ ] Implement base model readiness checks.
- [ ] Add `DOWNLOAD_BASE_MODEL` task producer.
- [ ] Implement download worker with checksum validation.
- [ ] Expose base model list and status API.
- [ ] Commit as `feat: add base model storage`.

**Acceptance:**
- Creating a pipeline can reference a remote base model.
- Training submission is blocked until the selected base model is `ready`.
- Failed download records error details in Task Center.

### Task 5: Data Preparation Module

**Files:**
- Create: `apps/api-service/src/visiox_api/routes/datasets.py`
- Create: `apps/api-service/src/visiox_api/routes/dataset_samples.py`
- Create: `packages/visiox-yolo26/src/visiox_yolo26/datasets/analysis.py`
- Create: `packages/visiox-yolo26/src/visiox_yolo26/datasets/validation.py`
- Create: `tests/fixtures/datasets/*`
- Create: `tests/integration/test_dataset_upload.py`

**Goal:** Upload data, create datasets, manage samples, analyze distributions, and validate data before training.

- [ ] Write a child plan at `docs/superpowers/plans/2026-07-03-data-preparation.md`.
- [ ] Implement image and zip upload into object storage.
- [ ] Create dataset and sample records.
- [ ] Store image dimensions, checksum, split, and annotation status.
- [ ] Implement train/val/test split assignment.
- [ ] Implement analysis: sample count, class distribution, annotation count, image size distribution, empty annotation ratio, invalid samples.
- [ ] Implement format validation hooks by task type.
- [ ] Commit as `feat: add data preparation module`.

**Acceptance:**
- User can create a dataset without Label Studio.
- Uploaded samples are stored in MinIO and indexed in PostgreSQL.
- Dataset analysis runs as a Task Center task.

### Task 6: Label Studio Integration

**Files:**
- Create: `packages/visiox-yolo26/src/visiox_yolo26/labelstudio/client.py`
- Create: `packages/visiox-yolo26/src/visiox_yolo26/labelstudio/templates.py`
- Create: `workers/label-sync-worker/src/visiox_label_sync_worker/main.py`
- Create: `apps/api-service/src/visiox_api/routes/label_projects.py`
- Create: `tests/fixtures/labelstudio/*.json`
- Create: `tests/integration/test_label_studio_sync.py`

**Goal:** Create Label Studio projects from platform datasets, sync samples, import annotations, and store raw plus normalized annotation data.

- [ ] Write a child plan at `docs/superpowers/plans/2026-07-03-label-studio-integration.md`.
- [ ] Implement Label Studio API client.
- [ ] Generate task-specific Label Studio configs for detect, segment, semantic, pose, obb, and classify.
- [ ] Implement sample sync from platform dataset to Label Studio project.
- [ ] Implement annotation import from Label Studio to platform.
- [ ] Store raw Label Studio JSON payload in object storage and normalized annotation in database.
- [ ] Commit as `feat: integrate label studio`.

**Acceptance:**
- Dataset can create or link a Label Studio project.
- Label Studio annotations can be synchronized back into platform records.
- Sync failures are visible in Task Center.

### Task 7: YOLO26 Dataset Converter

**Files:**
- Create: `packages/visiox-yolo26/src/visiox_yolo26/tasks.py`
- Create: `packages/visiox-yolo26/src/visiox_yolo26/converters/internal_schema.py`
- Create: `packages/visiox-yolo26/src/visiox_yolo26/converters/detect.py`
- Create: `packages/visiox-yolo26/src/visiox_yolo26/converters/segment.py`
- Create: `packages/visiox-yolo26/src/visiox_yolo26/converters/semantic.py`
- Create: `packages/visiox-yolo26/src/visiox_yolo26/converters/pose.py`
- Create: `packages/visiox-yolo26/src/visiox_yolo26/converters/obb.py`
- Create: `packages/visiox-yolo26/src/visiox_yolo26/converters/classify.py`
- Create: `tests/fixtures/yolo26_expected/*`
- Create: `tests/integration/test_yolo26_converters.py`

**Goal:** Convert internal annotations into valid Ultralytics YOLO26 dataset formats for all six tasks.

- [ ] Write a child plan at `docs/superpowers/plans/2026-07-03-yolo26-dataset-converters.md`.
- [ ] Implement task registry for six YOLO26 tasks and five scales.
- [ ] Implement detect converter.
- [ ] Implement instance segmentation converter.
- [ ] Implement semantic segmentation mask rasterization from polygon/brush.
- [ ] Implement COCO 17-point pose converter.
- [ ] Implement OBB converter from rotated rectangle or four-point polygon to normalized four-point labels.
- [ ] Implement classification converter.
- [ ] Generate `data.yaml` for every task.
- [ ] Commit as `feat: add yolo26 dataset converters`.

**Acceptance:**
- Fixture conversion output matches expected files byte-for-byte where deterministic.
- Invalid annotations produce actionable validation errors.
- Semantic overlaps produce warnings with deterministic overwrite order.

### Task 8: Training Pipeline And Training Worker

**Files:**
- Create: `apps/api-service/src/visiox_api/routes/pipelines.py`
- Create: `apps/api-service/src/visiox_api/routes/training_jobs.py`
- Create: `packages/visiox-yolo26/src/visiox_yolo26/training/params.py`
- Create: `packages/visiox-yolo26/src/visiox_yolo26/training/commands.py`
- Create: `workers/training-worker/src/visiox_training_worker/main.py`
- Create: `tests/integration/test_training_pipeline.py`

**Goal:** Create training pipelines, validate parameters, submit training jobs, run YOLO26 train/val/export commands, and register trained model versions.

- [ ] Write a child plan at `docs/superpowers/plans/2026-07-03-training-pipeline-worker.md`.
- [ ] Implement pipeline CRUD API.
- [ ] Implement parameter validation and advanced config whitelist.
- [ ] Implement training environment registry.
- [ ] Implement `TRAIN_MODEL` worker flow.
- [ ] Implement training log capture and metrics registration.
- [ ] Implement trained model version creation.
- [ ] Commit as `feat: add training pipeline worker`.

**Acceptance:**
- Training cannot start unless base model, dataset, parameters, and environment pass prechecks.
- Successful training creates a trained model version.
- Failure records stage, error code, and retry flag.

### Task 9: Model Export And Edge App Packaging

**Files:**
- Create: `packages/visiox-yolo26/src/visiox_yolo26/export/commands.py`
- Create: `packages/visiox-yolo26/src/visiox_yolo26/edge_app/package.py`
- Create: `apps/api-service/src/visiox_api/routes/edge_apps.py`
- Create: `workers/training-worker/src/visiox_training_worker/export_flow.py`
- Create: `tests/integration/test_edge_app_package.py`

**Goal:** Export trained YOLO26 models and build edge app packages with model, runtime config, camera config, rules, and image references.

- [ ] Write a child plan at `docs/superpowers/plans/2026-07-03-model-export-edge-app-package.md`.
- [ ] Implement model export task.
- [ ] Implement edge app and edge app version records.
- [ ] Implement package manifest `app.yaml`.
- [ ] Package model, runtime config, camera config, and rules into object storage.
- [ ] Commit as `feat: add edge app packaging`.

**Acceptance:**
- A trained model can become an edge app version.
- Package manifest contains task, model URI, image ref, cameras, rules, and checksum.
- Package can be downloaded by deployment worker.

### Task 10: Edge Agent And Deployment Worker

**Files:**
- Create: `apps/edge-agent/src/visiox_edge_agent/main.py`
- Create: `apps/edge-agent/src/visiox_edge_agent/docker_runtime.py`
- Create: `apps/edge-agent/src/visiox_edge_agent/apps.py`
- Create: `workers/deployment-worker/src/visiox_deployment_worker/main.py`
- Create: `apps/api-service/src/visiox_api/routes/devices.py`
- Create: `apps/api-service/src/visiox_api/routes/deployments.py`
- Create: `tests/integration/test_deployment_flow.py`

**Goal:** Register edge devices, call Edge Agent APIs, deploy/stop/rollback edge apps, and track deployment state.

- [ ] Write a child plan at `docs/superpowers/plans/2026-07-03-edge-agent-deployment.md`.
- [ ] Implement Edge Agent health and device info APIs.
- [ ] Implement Agent app deploy/start/stop/rollback APIs.
- [ ] Implement deployment worker calls to Agent.
- [ ] Implement deployment task states and logs.
- [ ] Implement camera test API proxy through Agent.
- [ ] Commit as `feat: add edge deployment flow`.

**Acceptance:**
- Platform can deploy an edge app to a test Agent.
- Deployment failure is recoverable and visible in Task Center.
- Agent does not contain YOLO inference logic.

### Task 11: Unified YOLO26 Inference Service

**Files:**
- Create: `apps/yolo26-inference/Dockerfile`
- Create: `apps/yolo26-inference/src/visiox_yolo26_inference/main.py`
- Create: `apps/yolo26-inference/src/visiox_yolo26_inference/config.py`
- Create: `apps/yolo26-inference/src/visiox_yolo26_inference/predict.py`
- Create: `tests/integration/test_yolo26_inference_api.py`

**Goal:** Provide one inference service image that loads different YOLO26 task models via runtime config.

- [ ] Write a child plan at `docs/superpowers/plans/2026-07-03-yolo26-inference-service.md`.
- [ ] Implement `GET /health`.
- [ ] Implement `GET /model/info`.
- [ ] Implement `POST /predict/image`.
- [ ] Implement `POST /predict/video-frame`.
- [ ] Implement `POST /runtime/reload`.
- [ ] Implement `GET /metrics`.
- [ ] Commit as `feat: add yolo26 inference service`.

**Acceptance:**
- Same image can load task config for detect, segment, semantic, pose, obb, and classify.
- Invalid model/task mismatch fails fast with clear error.

### Task 12: Frontend Management Console

**Files:**
- Create: `apps/frontend/package.json`
- Create: `apps/frontend/src/main.ts`
- Create: `apps/frontend/src/router/index.ts`
- Create: `apps/frontend/src/stores/taskCenter.ts`
- Create: `apps/frontend/src/views/model-space/*`
- Create: `apps/frontend/src/views/data-preparation/*`
- Create: `apps/frontend/src/views/pipelines/*`
- Create: `apps/frontend/src/views/tasks/*`
- Create: `apps/frontend/src/views/devices/*`
- Create: `apps/frontend/src/views/edge-apps/*`
- Create: `apps/frontend/src/api/client.ts`
- Create: `apps/frontend/tests/*.spec.ts`

**Goal:** Build the admin UI for model space, data preparation, pipeline wizard, task center, devices, edge apps, and deployments.

- [ ] Write a child plan at `docs/superpowers/plans/2026-07-03-frontend-console.md`.
- [ ] Scaffold Vue 3 + TypeScript + Vite + Element Plus.
- [ ] Generate or implement API client from FastAPI OpenAPI.
- [ ] Implement navigation and layout.
- [ ] Implement model space pages.
- [ ] Implement data preparation pages.
- [ ] Implement four-step training pipeline wizard.
- [ ] Implement Task Center list/detail with realtime progress.
- [ ] Implement device, camera, edge app, and deployment pages.
- [ ] Commit as `feat: add frontend console`.

**Acceptance:**
- User can complete the MVP workflow from UI through API.
- Long-running tasks show status and error details.
- No login page or role-based UI exists.

### Task 13: End-to-End MVP Verification

**Files:**
- Create: `tests/integration/test_mvp_yolo26_detect_flow.py`
- Create: `tests/integration/test_mvp_labelstudio_flow.py`
- Create: `docs/runbooks/local-mvp.md`
- Modify: `README.md`

**Goal:** Verify the full workflow from dataset upload to training task, model version, edge app package, and deployment to a test Edge Agent.

- [ ] Write a child plan at `docs/superpowers/plans/2026-07-03-mvp-e2e-verification.md`.
- [ ] Add local MVP runbook.
- [ ] Add deterministic test fixtures for at least one detect workflow.
- [ ] Add integration test for dataset upload, conversion, training task submission, model registration, package build, and deployment call.
- [ ] Add smoke test for Label Studio project creation and annotation sync.
- [ ] Commit as `test: add mvp end-to-end verification`.

**Acceptance:**
- A developer can follow `docs/runbooks/local-mvp.md` on a clean machine.
- End-to-end tests pass against local Compose.
- Known limitations are documented.

## Commit Strategy

Use one commit per task or per small task subset. Do not mix frontend, worker, and database changes in the same commit unless they are required for a single vertical slice. Every commit must leave tests passing for the touched subsystem.

## Self-Review

Spec coverage:

- Deployment shape: covered by Tasks 1 and 13.
- Database and metadata objects: covered by Task 2.
- Redis and Task Center: covered by Task 3.
- Base model download: covered by Task 4.
- Data preparation module: covered by Task 5.
- Label Studio sync: covered by Task 6.
- YOLO26 six-task conversion: covered by Task 7.
- Training pipeline and worker: covered by Task 8.
- Export and edge app package: covered by Task 9.
- Edge Agent and deployment: covered by Task 10.
- Unified YOLO26 inference image: covered by Task 11.
- Frontend console: covered by Task 12.
- MVP verification: covered by Task 13.

Known intentional deferral:

- Each task must be expanded into its own code-level implementation plan before coding. This is required because the current spec spans multiple independent subsystems.
