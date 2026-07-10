# Model Space Card UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the model space page into a card-based console matching the provided reference, with a create-pipeline action that navigates to training pipelines.

**Architecture:** Keep the change inside the existing Vue page and router. Continue using current base-model and trained-model APIs, normalize them into a single view model, then apply local tabs, type filtering, search, sorting, pagination, and status styling.

**Tech Stack:** Vue 3, TypeScript, Element Plus, Vitest, Vue Test Utils.

---

### Task 1: Model Space Card UI

**Files:**
- Modify: `apps/frontend/src/views/model-space/ModelSpaceView.vue`
- Test: `apps/frontend/tests/model-space-view.spec.ts`

- [ ] **Step 1: Add a failing test**

Create `apps/frontend/tests/model-space-view.spec.ts` with a mounted `ModelSpaceView`, mocked API methods, and assertions that the page renders `模型空间`, has a `创建产线` button, and routes to `/pipelines` when clicked.

- [ ] **Step 2: Run the test**

Run: `npm run test --prefix apps/frontend -- model-space-view`

Expected before implementation: fail because the button or route action does not exist.

- [ ] **Step 3: Implement the card layout**

Replace the table-heavy `ModelSpaceView.vue` template with a header, tab bar, tool bar, five-column responsive card grid, and pagination. Use `router.push("/pipelines")` for the create button.

- [ ] **Step 4: Run frontend checks**

Run: `npm run test --prefix apps/frontend` and `npm run build --prefix apps/frontend`.

Expected after implementation: both commands pass.
