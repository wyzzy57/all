# Resource Ownership and Sharing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Attach every primary VisiOX resource to an organization and owner, enforce object permissions in backend queries and actions, and replace decorative public settings with real user/group/platform grants.

**Architecture:** Add ownership columns in a nullable compatibility migration, bootstrap and backfill existing rows, enable route-level authorization, then apply non-null constraints. Use a generic sharing service and dialog while child records inherit permissions from their parent resource.

**Tech Stack:** SQLAlchemy, Alembic, FastAPI dependencies, Vue 3, Element Plus, Vitest.

---

### Task 1: Add Nullable Ownership Columns

**Files:**
- Modify: `packages/visiox-db/src/visiox_db/models/datasets.py`
- Modify: `packages/visiox-db/src/visiox_db/models/model_space.py`
- Modify: `packages/visiox-db/src/visiox_db/models/edge_compute.py`
- Create: `infra/migrations/versions/20260727_0003_resource_ownership_nullable.py`
- Modify: `tests/integration/test_migrations.py`

- [ ] Write failing migration assertions for nullable `organization_id`, `owner_user_id`, and non-null default `visibility="private"` on datasets, pipelines, jobs, trained models, services, pools, and nodes.
- [ ] Add ORM fields and indexes; child tables remain unchanged and inherit parent authorization.
- [ ] Create the additive migration with `down_revision = "20260727_0002"` and no data deletion.
- [ ] Run migration tests and commit with `feat: add resource ownership columns`.

### Task 2: Backfill Existing Resource Ownership

**Files:**
- Create: `apps/api-service/src/visiox_api/maintenance/backfill_resource_ownership.py`
- Create: `tests/integration/test_resource_ownership_backfill.py`
- Modify: `infra/compose/docker-compose.server.yml`

- [ ] Write tests with unowned existing records, the default organization, and bootstrap admin; assert all primary records are assigned and existing public pipelines receive organization `view` grants.
- [ ] Implement an idempotent CLI that prints before/after counts and exits non-zero if any primary row remains unowned.
- [ ] Add a one-shot compose maintenance command that runs after Phase 1 bootstrap and before authorization enforcement.
- [ ] Run the CLI twice in tests and commit with `feat: backfill existing resource ownership`.

### Task 3: Add Permission-Aware Query Helpers

**Files:**
- Modify: `apps/api-service/src/visiox_api/services/authorization.py`
- Create: `tests/unit/test_authorized_queries.py`

- [ ] Write tests for admin all-resource access, owner access, user grant, group grant, organization grant, expiry, and duplicate-free SQL results.
- [ ] Implement SQLAlchemy predicates for owner IDs and grant subqueries without loading all resources into Python.
- [ ] Add parent-resource resolution for dataset samples, annotations, label projects, deployment instances, evaluations, and artifacts.
- [ ] Run tests and commit with `feat: add authorized resource query filters`.

### Task 4: Enforce Dataset and Label Project Permissions

**Files:**
- Modify: `apps/api-service/src/visiox_api/routes/datasets.py`
- Modify: `apps/api-service/src/visiox_api/routes/dataset_samples.py`
- Modify: `apps/api-service/src/visiox_api/routes/label_projects.py`
- Modify: `apps/api-service/src/visiox_api/routes/llm_datasets.py`
- Modify: `tests/integration/test_dataset_upload.py`
- Modify: `tests/integration/test_label_studio_sync.py`
- Modify: `tests/integration/test_llm_datasets.py`

- [ ] Add authenticated tests for list filtering and `view`, `use`, `edit`, `delete`, and `manage` checks.
- [ ] Set organization/owner from the current user on create; never accept owner IDs from member payloads.
- [ ] Apply inherited dataset permission to samples, annotations, downloads, processing, Label Studio project creation, sync, and conversion.
- [ ] Record grant, conversion, edit, and delete audits.
- [ ] Run tests and commit with `feat: enforce dataset ownership permissions`.

### Task 5: Enforce Pipeline, Training, Model, and Evaluation Permissions

**Files:**
- Modify: `apps/api-service/src/visiox_api/routes/pipelines.py`
- Modify: `apps/api-service/src/visiox_api/routes/training_jobs.py`
- Modify: `apps/api-service/src/visiox_api/routes/trained_models.py`
- Modify: `apps/api-service/src/visiox_api/routes/pipeline_evaluation.py`
- Modify: `apps/api-service/src/visiox_api/routes/pipeline_inference.py`
- Modify: `tests/integration/test_training_pipeline.py`
- Modify: `tests/integration/test_trained_models_api.py`
- Modify: `tests/integration/test_pipeline_evaluation.py`

- [ ] Add tests proving a member can use a granted dataset in their own pipeline but cannot mutate the source dataset or another user's pipeline.
- [ ] Require `use` on selected model/dataset/node and `edit` on the target pipeline before job creation.
- [ ] Inherit trained model and evaluation access from the pipeline, with artifact download requiring `view`.
- [ ] Remove authorization meaning from legacy `is_public/public_scope`; retain response compatibility until the frontend migration completes.
- [ ] Run tests and commit with `feat: enforce model space permissions`.

### Task 6: Enforce Service and Node Permissions

**Files:**
- Modify: `apps/api-service/src/visiox_api/routes/services.py`
- Modify: `apps/api-service/src/visiox_api/routes/nodes.py`
- Modify: `apps/api-service/src/visiox_api/routes/edge_ssh.py`
- Modify: `tests/integration/test_services_api.py`
- Modify: `tests/integration/test_edge_ssh_api.py`

- [ ] Test public service `invoke` without `edit`, owner start/stop/delete, admin bypass, and member denial for node credentials/probe.
- [ ] Set service owner at creation and require `use` on the pipeline/model/node.
- [ ] Require `invoke` for prediction and `edit` for start/stop/restart; require `delete` for deletion.
- [ ] Keep all SSH credential operations administrator-only regardless of grants.
- [ ] Run tests and commit with `feat: enforce service and node permissions`.

### Task 7: Add the Generic Sharing API

**Files:**
- Create: `apps/api-service/src/visiox_api/routes/resource_sharing.py`
- Modify: `apps/api-service/src/visiox_api/main.py`
- Create: `tests/integration/test_resource_sharing_api.py`

- [ ] Write tests for reading grants, replacing user/group/platform grants, invalid permissions, forbidden node publication, owner/admin management, expiry, and audit records.
- [ ] Implement `GET/PUT /resources/{resource_type}/{resource_id}/sharing`; the PUT body is a complete replacement performed transactionally.
- [ ] Validate default platform permissions by resource type and reject `organization` grants for nodes/pools.
- [ ] Mirror effective visibility to the resource's `visibility` field and transitional pipeline `is_public` response.
- [ ] Run tests and commit with `feat: add real resource sharing API`.

### Task 8: Add the Shared Sharing Dialog

**Files:**
- Create: `apps/frontend/src/components/sharing/ResourceSharingDialog.vue`
- Modify: `apps/frontend/src/api/client.ts`
- Modify: `apps/frontend/src/views/data-preparation/DataPreparationView.vue`
- Modify: `apps/frontend/src/views/model-space/ModelSpaceView.vue`
- Modify: `apps/frontend/src/views/services/ServicesView.vue`
- Create: `apps/frontend/tests/resource-sharing-dialog.spec.ts`
- Modify: `apps/frontend/tests/data-preparation-view.spec.ts`
- Modify: `apps/frontend/tests/model-space-view.spec.ts`
- Modify: `apps/frontend/tests/services-view.spec.ts`

- [ ] Write tests for private, user, group, and platform scopes, permission summaries, disabled management for non-managers, and save error handling.
- [ ] Replace current hard-coded public dialogs with the shared component.
- [ ] Show owner and visibility on resource cards and detail pages.
- [ ] Filter all resource selectors using authorized API results; do not hide unauthorized options after loading them.
- [ ] Run focused frontend tests and type checking; commit with `feat: add real resource sharing UI`.

### Task 9: Enforce Non-Null Ownership

**Files:**
- Create: `infra/migrations/versions/20260727_0004_resource_ownership_constraints.py`
- Modify: `tests/integration/test_migrations.py`
- Modify: `tests/integration/test_resource_ownership_backfill.py`

- [ ] Add a migration test that runs the nullable migration, seeds/backfills records, applies the constraint migration, and verifies null inserts fail.
- [ ] Set `down_revision = "20260727_0003"`. In the migration, abort with an explicit count if any primary row is unowned; then set organization and owner columns non-null.
- [ ] Run migration/backfill tests and commit with `feat: enforce resource ownership constraints`.

### Task 10: Verify Ownership and Sharing

**Files:** Modify only the listed route, service, component, and test files when defects appear.

- [ ] Run all affected backend integration suites plus `tests/unit/test_authorization.py tests/unit/test_authorized_queries.py`.
- [ ] Run all affected frontend tests, type checking, and build.
- [ ] In two browser sessions, verify private, group, user, and platform visibility; verify public dataset training use and public service invocation without management rights.
- [ ] Compare administrator list totals with raw database counts and confirm member list totals contain no inaccessible IDs.
- [ ] Commit verified fixes with `test: verify resource ownership and sharing`.
