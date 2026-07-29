# Scoped Statistics and Product Finish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace remaining static workbench data with permission-scoped aggregates, add administrator platform statistics and audit views, and finish consistent account, loading, error, empty, and responsive states.

**Architecture:** Build SQL aggregation services that reuse object-authorization predicates and live node inventory, return chart-ready series, and feed shared Vue dashboard components for administrators and members. Keep all global calculations on the backend.

**Tech Stack:** SQLAlchemy aggregate queries, FastAPI, Vue 3, ECharts, Element Plus, Vitest, Playwright-compatible browser QA.

---

### Task 1: Implement Permission-Scoped Statistics Queries

**Files:**
- Create: `apps/api-service/src/visiox_api/services/statistics.py`
- Create: `tests/unit/test_statistics_service.py`

- [ ] Write tests for admin totals, member-owned resources, direct grants, group grants, public grants, duplicate-free totals, status buckets, monthly buckets, and empty systems.
- [ ] Implement aggregate methods for pipelines, datasets, training jobs, services, nodes, users/groups, and creation trends using authorization SQL predicates.
- [ ] Use database time bucketing with a SQLite-compatible test path and fill missing months with zeros.
- [ ] Return chart-ready labels/values plus `generated_at`; never expose inaccessible resource IDs.
- [ ] Run tests and commit with `feat: add scoped statistics service`.

### Task 2: Aggregate Live Resource and Health Statistics

**Files:**
- Modify: `apps/api-service/src/visiox_api/services/statistics.py`
- Create: `tests/unit/test_resource_statistics.py`

- [ ] Write tests for online/offline nodes, CPU/RAM/disk, per-GPU utilization/VRAM, service health, unknown/stale samples, and group allocation usage.
- [ ] Read only the latest non-stale inventory revision per authorized node.
- [ ] Distinguish zero utilization from unavailable telemetry and include freshness timestamps.
- [ ] Compute service health from deployment instances and calls from persisted service counters.
- [ ] Run tests and commit with `feat: aggregate live platform resources`.

### Task 3: Add Workbench and Administrator Statistics APIs

**Files:**
- Create: `apps/api-service/src/visiox_api/routes/statistics.py`
- Modify: `apps/api-service/src/visiox_api/main.py`
- Create: `tests/integration/test_statistics_api.py`

- [ ] Write tests for `/statistics/workbench`, `/admin/statistics/overview`, `/admin/statistics/resources`, admin denial for members, and permission-scoped member results.
- [ ] Return one bounded overview payload for initial render and a separate resource payload for polling.
- [ ] Apply a five-second server cache keyed by organization, user, role, and authorization revision; invalidate on grants or ownership changes.
- [ ] Include `generated_at` and source freshness in every response.
- [ ] Run tests and commit with `feat: expose scoped platform statistics`.

### Task 4: Build Shared Dashboard Components

**Files:**
- Create: `apps/frontend/src/components/dashboard/StatisticSummaryStrip.vue`
- Create: `apps/frontend/src/components/dashboard/PipelineStatusChart.vue`
- Create: `apps/frontend/src/components/dashboard/CreationTrendChart.vue`
- Create: `apps/frontend/src/components/dashboard/DatasetTrendChart.vue`
- Create: `apps/frontend/src/components/dashboard/ResourceUsagePanel.vue`
- Create: `apps/frontend/src/components/dashboard/ServiceHealthPanel.vue`
- Create: `apps/frontend/tests/dashboard-components.spec.ts`

- [ ] Write tests for values, units, legends, hover emphasis, tooltip labels, animation on first mount, no-data states, stale telemetry, and resize.
- [ ] Use stable aspect ratios and container resize observers; do not scale fonts with viewport width.
- [ ] Animate chart entry once per page open and update data without replaying distracting full animations.
- [ ] Keep pie hover enlargement within its own radius rather than separating slices.
- [ ] Run tests and commit with `feat: add shared dynamic dashboard charts`.

### Task 5: Replace Workbench Static Data

**Files:**
- Modify: `apps/frontend/src/views/workbench/WorkbenchView.vue`
- Modify: `apps/frontend/src/api/client.ts`
- Modify: `apps/frontend/tests/workbench-view.spec.ts`

- [ ] Write tests proving the view uses the workbench statistics endpoint, shows only returned scoped data, polls resource statistics, and handles loading/error/empty states.
- [ ] Replace list-derived and hard-coded chart values with shared dashboard components.
- [ ] Preserve existing quick-access navigation while removing duplicate API list loading.
- [ ] Stop polling when the page is hidden and refresh immediately when visible again.
- [ ] Run tests, type checking, and commit with `feat: make workbench statistics dynamic`.

### Task 6: Add the Administrator Overview

**Files:**
- Create: `apps/frontend/src/views/admin/AdminOverviewView.vue`
- Modify: `apps/frontend/src/router/index.ts`
- Modify: `apps/frontend/src/components/account/UserAccountMenu.vue`
- Create: `apps/frontend/tests/admin-overview-view.spec.ts`

- [ ] Write tests for administrator-only routing, summary counts, pipeline status, creation trends, dataset trends, service health, node resources, GPU use, group allocation, and recent failures.
- [ ] Match the approved dense analytical layout: summary strip, three top charts, service/resource rows, and actionable failure table.
- [ ] Link each chart/table section to its filtered management page.
- [ ] Run tests and commit with `feat: add administrator platform overview`.

### Task 7: Add Audit Log UI

**Files:**
- Create: `apps/frontend/src/views/admin/AuditLogView.vue`
- Modify: `apps/frontend/src/api/client.ts`
- Modify: `apps/frontend/src/router/index.ts`
- Create: `apps/frontend/tests/audit-log-view.spec.ts`

- [ ] Write tests for actor/resource/action/result/time filters, pagination, details dialog, denied events, and absence of sensitive fields.
- [ ] Use server-side filtering and pagination; truncate metadata to one line in the table and show full redacted JSON in a dialog.
- [ ] Add administrator navigation through the account menu.
- [ ] Run tests and commit with `feat: add audit log administration`.

### Task 8: Standardize Product States and Responsive Layout

**Files:**
- Create: `apps/frontend/src/components/common/AsyncState.vue`
- Modify: `apps/frontend/src/styles.css`
- Modify: all new views from Phases 1-7 only where required.
- Create: `apps/frontend/tests/async-state.spec.ts`

- [ ] Write tests for loading, empty, denied, retryable error, terminal error, keyboard focus, and stable dimensions.
- [ ] Replace ad hoc new-feature state markup with `AsyncState` while leaving unrelated legacy pages unchanged.
- [ ] Verify sidebar expanded/collapsed widths, white page canvas, header continuity, table overflow, dialogs, and 1280/1440/1920 desktop widths plus 1024 narrow width.
- [ ] Run tests, type checking, and commit with `style: standardize platform states and layout`.

### Task 9: Full-System Acceptance

**Files:** Modify only files from the seven phases when defects appear.

- [ ] Run `python -m pytest tests/unit tests/integration -q`.
- [ ] Run `npm test -- --run`, `npm run typecheck`, and `npm run build` from `apps/frontend`.
- [ ] Run production compose startup and migration smoke tests on a copied database.
- [ ] Execute administrator and member browser acceptance for login, groups, grants, node allocation, dataset sharing, LLM labeling, training observability, service invocation, stop/resume, logs, statistics, and audit.
- [ ] Compare platform totals to SQL queries and node inventory snapshots.
- [ ] Inspect browser screenshots at supported widths for overlap, clipping, unstable charts, or inaccessible controls.
- [ ] Verify secrets are absent from API payloads, browser storage, task payloads, audit details, and downloadable logs.
- [ ] Commit verified fixes with `test: complete user resource and observability platform`.
