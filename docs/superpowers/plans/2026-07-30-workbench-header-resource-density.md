# Workbench Header and Resource Density Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Tighten the Workbench heading-to-dashboard spacing and render CPU, memory, and disk as separate full-width resource rows without breaking the 1366x768 single-page layout.

**Architecture:** Keep data fetching and dashboard component contracts unchanged. Express the approved behavior through Workbench-scoped CSS overrides so the shared resource component remains reusable elsewhere, and protect the desktop and responsive contracts with focused source assertions plus browser geometry checks.

**Tech Stack:** Vue 3, TypeScript, scoped CSS, CSS grid, Vitest, Vue Test Utils, in-app browser QA.

---

### Task 1: Lock the compact header and resource-row contract

**Files:**
- Modify: `apps/frontend/tests/workbench-view.spec.ts`
- Reference: `apps/frontend/src/views/workbench/WorkbenchView.vue`

- [ ] **Step 1: Add failing source-contract assertions**

Extend the existing compact-layout test so it requires a 48-52px desktop heading row, a 6px Workbench gap, and a single-column resource grid:

```ts
expect(workbenchSource).toMatch(
  /grid-template-rows:\s*minmax\(0,\s*52px\)\s+58px\s+minmax\(0,\s*310px\)\s+minmax\(0,\s*240px\)/,
);
expect(workbenchSource).toMatch(/\.workbench-view\s*\{[^}]*gap:\s*6px/s);
expect(workbenchSource).toMatch(
  /\.resource-panel\s+:deep\(\.resource-grid\)\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/s,
);
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run:

```powershell
npm test -- --run tests/workbench-view.spec.ts
```

Expected: FAIL because the heading row is still `auto`, the Workbench gap is `10px`, and the resource grid uses two columns.

- [ ] **Step 3: Commit the red contract**

```powershell
git add apps/frontend/tests/workbench-view.spec.ts
git commit -m "test: define compact workbench header resources"
```

### Task 2: Implement the approved Workbench composition

**Files:**
- Modify: `apps/frontend/src/views/workbench/WorkbenchView.vue`
- Test: `apps/frontend/tests/workbench-view.spec.ts`

- [ ] **Step 1: Tighten the desktop root grid and heading**

Update the Workbench root and heading without removing any text:

```css
.workbench-view {
  grid-template-rows: minmax(0, 52px) 58px minmax(0, 310px) minmax(0, 240px) auto;
  gap: 6px;
}

.workbench-header {
  align-items: start;
  padding: 0 2px;
}

.workbench-header h1 {
  font-size: 24px;
  line-height: 28px;
}

.workbench-header p,
.generated-at {
  margin-top: 2px;
  line-height: 18px;
}

.generated-at {
  margin-top: 4px;
}
```

Keep the existing `@media (max-width: 1350px)` root rule so narrower viewports restore natural rows. If the 52px row produces a measured overflow, reduce internal margins rather than hiding content.

- [ ] **Step 2: Render resource metrics as full-width rows**

Replace the Workbench-only two-column resource override with:

```css
.resource-panel :deep(.resource-grid) {
  align-content: space-between;
  grid-template-columns: minmax(0, 1fr);
  gap: 8px;
}
```

Do not change `ResourceUsagePanel.vue` data order: its existing `usage` loop already renders CPU, memory, and disk before optional GPU rows.

- [ ] **Step 3: Run focused tests and type checking**

```powershell
npm test -- --run tests/workbench-view.spec.ts tests/dashboard-components.spec.ts
npm run typecheck
```

Expected: all focused tests pass and `vue-tsc` exits with code 0.

- [ ] **Step 4: Commit the implementation**

```powershell
git add apps/frontend/src/views/workbench/WorkbenchView.vue
git commit -m "fix: tighten workbench header resources"
```

### Task 3: Verify the complete page

**Files:**
- Verify: `apps/frontend/src/views/workbench/WorkbenchView.vue`
- Verify: `apps/frontend/tests/workbench-view.spec.ts`

- [ ] **Step 1: Run the complete frontend verification**

```powershell
npm test -- --run
npm run build
git diff --check
```

Expected: all frontend tests pass, the production build succeeds, and `git diff --check` reports no errors.

- [ ] **Step 2: Verify 1366x768 with the sidebar expanded**

Open `/workbench` at 1366x768 and measure:

```js
({
  pageScrollHeight: document.documentElement.scrollHeight,
  viewportHeight: document.documentElement.clientHeight,
  pageScrollWidth: document.documentElement.scrollWidth,
  viewportWidth: document.documentElement.clientWidth,
  resourceColumns: getComputedStyle(document.querySelector('.resource-grid')).gridTemplateColumns,
})
```

Expected: page and viewport dimensions match, the resource grid reports one column, CPU/memory/disk are individually visible, and no Workbench element is clipped.

- [ ] **Step 3: Verify responsive fallback**

Check 1340x768, 1024x768, and 375x812. Expected: natural vertical scrolling, no overlaps, and no horizontal overflow.

- [ ] **Step 4: Record completion**

Update the task checklist and report the final commits, test totals, build result, and local preview URL.
