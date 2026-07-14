# Remove Native Graph and Histograms Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Delete only the native computation-graph and histogram frontend surfaces.

**Architecture:** Keep the observability backend and TensorBoard event pipeline unchanged. Remove the two frontend tabs, their state and rendering components, while preserving the existing top-right TensorBoard command.

**Tech Stack:** Vue 3, TypeScript, Vitest

## Global Constraints

- Do not change API routes, services, worker instrumentation, TensorBoard configuration, or the top-right TensorBoard entry.
- Do not stage `infra/compose/docker-compose.yml`.

### Task 1: Remove the Two Frontend Views

**Files:**
- Modify: `apps/frontend/tests/training-visualization-view.spec.ts`
- Modify: `apps/frontend/tests/training-chart-components.spec.ts`
- Modify: `apps/frontend/src/views/training-visualization/TrainingVisualizationView.vue`
- Delete: `apps/frontend/src/components/training/ModelGraphChart.vue`
- Delete: `apps/frontend/src/components/training/HistogramChart.vue`

**Interfaces:**
- Consumes: existing advanced-tools TensorBoard action.
- Produces: `DashboardTab = "overview" | "metrics" | "resources" | "analysis"`.

- [ ] Replace graph, iframe, and histogram interaction tests with assertions that `tab-graph` and `tab-histograms` do not exist; retain the existing test that opens TensorBoard from the advanced-tools menu.
- [ ] Run `npm --prefix apps/frontend test -- training-visualization-view.spec.ts training-chart-components.spec.ts` and confirm the new assertions initially fail.
- [ ] Remove the two tab definitions, their template sections, imports, state, computed values, request handlers, and CSS from `TrainingVisualizationView.vue`; delete both obsolete chart components.
- [ ] Run `npm --prefix apps/frontend test -- training-visualization-view.spec.ts training-chart-components.spec.ts` and `npm --prefix apps/frontend run typecheck`; expect both to pass.
- [ ] Confirm `rg -n "tab-graph|tab-histograms|tensorboard-graph-frame|HistogramChart|ModelGraphChart" apps/frontend/src` returns no matches.
- [ ] Commit only the frontend and test files with `git commit -m "refactor: remove native graph histogram views"`.
