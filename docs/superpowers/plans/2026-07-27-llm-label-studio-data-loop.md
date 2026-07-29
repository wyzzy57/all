# LLM Label Studio Data Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Connect LLM SFT data preparation to Label Studio, synchronize annotations, validate only labeled assistant responses, and convert them into immutable versioned datasets usable by LLaMA-Factory.

**Architecture:** Extend the existing dataset, label project, Label Studio client, label-sync worker, and LLM normalization services with engine-aware SFT adapters. Keep mutable annotation work in data preparation and publish immutable dataset versions with manifests and checksums for training.

**Tech Stack:** FastAPI, SQLAlchemy, Alembic, Redis worker, MinIO, Label Studio API/webhooks, Vue 3, Vitest.

---

### Task 1: Add Dataset Versions and Label Sync Events

**Files:**
- Modify: `packages/visiox-db/src/visiox_db/models/datasets.py`
- Create: `infra/migrations/versions/20260727_0006_dataset_versions_label_events.py`
- Modify: `packages/visiox-db/src/visiox_db/models/__init__.py`
- Modify: `tests/integration/test_migrations.py`

- [ ] Write failing assertions for `dataset_versions` and `label_sync_events` with version uniqueness, immutable manifest checksum, object URI, valid/invalid counts, event deduplication key, processing status, and timestamps.
- [ ] Add `working | published` asset role to datasets without changing existing visual dataset behavior.
- [ ] Create the migration with `down_revision = "20260727_0005"` and backfill existing validated datasets with one published version referencing their current manifest/storage URI.
- [ ] Run migration tests and commit with `feat: add immutable dataset versions`.

### Task 2: Extend LLM Input Normalization

**Files:**
- Modify: `apps/api-service/src/visiox_api/services/llm_datasets.py`
- Modify: `apps/api-service/src/visiox_api/routes/llm_datasets.py`
- Modify: `tests/unit/test_llm_dataset_validation.py`
- Modify: `tests/integration/test_llm_datasets.py`
- Add fixture: `tests/fixtures/labelstudio/llm_sft_export.json`

- [ ] Add failing tests for JSON, JSONL, CSV, Alpaca, ShareGPT, and OpenAI Messages normalization plus empty assistant, role order, duplicate, and invalid UTF-8 errors.
- [ ] Normalize all accepted records to `messages`, preserving optional system text and stable source row ID.
- [ ] Store working records as dataset samples with JSON object URIs and `annotation_status=unlabeled|labeled|invalid`.
- [ ] Return explicit issue codes and cap only the response issue list, not validation itself.
- [ ] Run tests and commit with `feat: normalize LLM SFT data preparation`.

### Task 3: Add an SFT Label Studio Template and Task Adapter

**Files:**
- Modify: `packages/visiox-yolo26/src/visiox_yolo26/labelstudio/templates.py`
- Create: `packages/visiox-yolo26/src/visiox_yolo26/labelstudio/llm.py`
- Modify: `packages/visiox-yolo26/src/visiox_yolo26/labelstudio/client.py`
- Create: `tests/unit/test_label_studio_llm_adapter.py`

- [ ] Write tests for SFT project XML, import task payload, prefilled system/user content, editable assistant response, and normalized exported annotations.
- [ ] Implement task adapters selected by `dataset.task`; keep current image detection/segmentation adapters unchanged.
- [ ] Require one non-empty assistant result and emit canonical OpenAI Messages.
- [ ] Run tests and commit with `feat: add Label Studio SFT adapter`.

### Task 4: Extend Label Project Creation and Synchronization

**Files:**
- Modify: `apps/api-service/src/visiox_api/routes/label_projects.py`
- Modify: `workers/label-sync-worker/src/visiox_label_sync_worker/main.py`
- Modify: `workers/label-sync-worker/src/visiox_label_sync_worker/runner.py`
- Modify: `tests/integration/test_label_studio_sync.py`

- [ ] Add failing tests for LLM project creation, task import, partial labels, full labels, invalid assistant output, idempotent import, and source-row matching.
- [ ] Branch by dataset task in the worker and persist raw Label Studio payloads in MinIO before normalization.
- [ ] Update dataset counts and label project states transactionally after each import.
- [ ] Never mark an unlabeled or invalid sample as trainable.
- [ ] Run tests and commit with `feat: synchronize LLM Label Studio annotations`.

### Task 5: Add Webhook Deduplication and Compensation Sync

**Files:**
- Create: `apps/api-service/src/visiox_api/routes/label_webhooks.py`
- Modify: `apps/api-service/src/visiox_api/main.py`
- Modify: `workers/label-sync-worker/src/visiox_label_sync_worker/runner.py`
- Modify: `packages/visiox-common/src/visiox_common/settings.py`
- Create: `tests/integration/test_label_studio_webhooks.py`

- [ ] Test valid signature, invalid signature, duplicate event, update/delete events, worker retry, and periodic reconciliation after a dropped webhook.
- [ ] Verify a configured HMAC webhook secret and persist the provider event ID before enqueue.
- [ ] Add a compensation scan that compares Label Studio updated timestamps with `last_sync_at` and enqueues stale projects.
- [ ] Keep webhook responses fast and return 202 after durable event persistence.
- [ ] Run tests and commit with `feat: add reliable Label Studio synchronization`.

### Task 6: Add Permission-Gated Managed Label Studio Launch

**Files:**
- Create: `apps/api-service/src/visiox_api/services/label_studio_session.py`
- Modify: `apps/api-service/src/visiox_api/routes/label_projects.py`
- Modify: `apps/frontend/nginx.conf`
- Modify: `infra/compose/docker-compose.server.yml`
- Create: `tests/integration/test_label_studio_managed_launch.py`

- [ ] Write tests for edit/view permission, one-time 60-second launch token, replay rejection, project binding, no administrator token in response, and proxy denial without a valid VisiOX session.
- [ ] Implement `POST /label-projects/{id}/launch` returning a same-origin short-lived URL.
- [ ] Exchange the launch token server-side for the managed Label Studio session and restrict proxy paths to the authorized project.
- [ ] Keep Label Studio credentials in server secrets only and audit launches with the actual VisiOX user.
- [ ] Run tests and commit with `feat: add managed Label Studio launch`.

### Task 7: Convert Valid Labels to an Immutable Training Dataset

**Files:**
- Create: `apps/api-service/src/visiox_api/services/dataset_versions.py`
- Modify: `apps/api-service/src/visiox_api/routes/datasets.py`
- Modify: `apps/api-service/src/visiox_api/services/llm_training.py`
- Create: `tests/integration/test_llm_dataset_conversion.py`

- [ ] Write tests for no labels, partial labels, invalid rows, all valid rows, stable manifest checksum, repeated conversion, new version after annotation changes, and training reference pinning.
- [ ] Implement `POST /datasets/{id}/convert-labeled` to serialize only valid labeled rows, upload JSONL and manifest to MinIO, and create an immutable version.
- [ ] Make LLaMA-Factory job submission require a published version ID and store its manifest checksum in the job snapshot.
- [ ] Return valid/invalid/skipped counts and issue summaries.
- [ ] Run tests and commit with `feat: publish labeled LLM dataset versions`.

### Task 8: Unify Data Asset Cards and LLM Actions

**Files:**
- Create: `apps/frontend/src/components/data/DataAssetCard.vue`
- Modify: `apps/frontend/src/views/data-preparation/DataPreparationView.vue`
- Modify: `apps/frontend/src/api/client.ts`
- Create: `apps/frontend/tests/data-asset-card.spec.ts`
- Modify: `apps/frontend/tests/data-preparation-view.spec.ts`

- [ ] Write tests for fixed metadata rows, created time, Label Studio action, sample/labeled/valid counts, owner/visibility, LLM status, convert button, and issue display.
- [ ] Replace duplicated CV/LLM card markup with the shared component without changing current visual dataset actions.
- [ ] Add managed-launch, manual sync, conversion, and version-list client methods.
- [ ] Ensure cards align at desktop and narrow container widths and all text remains inside fixed rows.
- [ ] Run tests, type checking, and commit with `feat: add aligned LLM data workflow UI`.

### Task 9: Verify the LLM Labeling Loop

**Files:** Modify only the files above when defects appear.

- [ ] Run LLM validation, Label Studio adapter, sync, webhook, launch, conversion, migration, and training contract tests.
- [ ] Run data-preparation frontend tests, type checking, and build.
- [ ] Import an 8-row SFT fixture, label six rows, leave one empty, make one invalid, synchronize, and verify exactly six rows enter version 1.
- [ ] Correct the invalid rows, synchronize, publish version 2, and verify an existing training job remains pinned to version 1.
- [ ] Verify a user without dataset edit permission cannot launch or synchronize the Label Studio project.
- [ ] Commit verified fixes with `test: verify LLM Label Studio data loop`.
