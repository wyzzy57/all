# Workbench Single-Page Density Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fit the complete Workbench command center inside a 1366x768 desktop viewport with the sidebar expanded, primarily by reducing the oversized asset trend and vertical spacing without scaling the application.

**Architecture:** Keep all data derivation, polling, routes, and component boundaries unchanged. Add a compact presentation contract to the existing dashboard components and compose a denser two-row desktop grid in `WorkbenchView.vue`; below 1100px, retain the existing responsive vertical flow.

**Tech Stack:** Vue 3 Composition API, TypeScript, scoped CSS, CSS container queries, ECharts 5, Vitest, Vue Test Utils, Playwright browser QA.

---

## File Structure

- Modify `apps/frontend/src/views/workbench/WorkbenchView.vue`: compact page rhythm, desktop grid rows, and single-screen panel sizing.
- Modify `apps/frontend/src/components/dashboard/AssetTrendChart.vue`: reduce the main chart and empty-state heights to the approved 160-180px range.
- Keep shared dashboard component defaults unchanged; apply KPI, status, lower-chart, resource, and activity compaction with scoped deep selectors in `WorkbenchView.vue` so administrator views do not regress.
- Modify `apps/frontend/tests/workbench-view.spec.ts`: lock the dense layout and breakpoint contract.
- Modify `apps/frontend/tests/dashboard-components.spec.ts`: lock compact chart and summary dimensions.

### Task 1: Lock the compact desktop contract

**Files:**
- Modify: `apps/frontend/tests/workbench-view.spec.ts`
- Modify: `apps/frontend/tests/dashboard-components.spec.ts`

- [ ] **Step 1: Add failing source-contract assertions**

Assert the Workbench uses the feasible 310px/240px desktop row budget, keeps the three lower panels in one row, and moves the responsive collapse threshold to 1100px. Assert the asset trend chart remains within the approved 160-180px range rather than requiring an exact value.

```ts
expect(workbenchSource).toContain("grid-template-rows: minmax(0, 310px) minmax(0, 240px)");
expect(workbenchSource).toContain("@container workbench (max-width: 1100px)");
expect(Number.parseFloat(canvas.style.minHeight)).toBeGreaterThanOrEqual(160);
expect(Number.parseFloat(canvas.style.minHeight)).toBeLessThanOrEqual(180);
expect(assetTrendChartSource).not.toContain("min-height: 250px");
```

- [ ] **Step 2: Run focused tests and confirm the red phase**

Run:

```powershell
npx vitest run tests/workbench-view.spec.ts tests/dashboard-components.spec.ts tests/app-layout.spec.ts
```

Expected: FAIL because the current trend panel is 320px high, the chart is 250px high, and the desktop collapse threshold is 920px.

- [ ] **Step 3: Commit the test contract**

```powershell
git add apps/frontend/tests/workbench-view.spec.ts apps/frontend/tests/dashboard-components.spec.ts apps/frontend/tests/app-layout.spec.ts
git commit -m "test: define single-page workbench density"
```

### Task 2: Implement the dense desktop composition

**Files:**
- Modify: `apps/frontend/src/views/workbench/WorkbenchView.vue`
- Modify: `apps/frontend/src/components/dashboard/AssetTrendChart.vue`

- [ ] **Step 1: Reduce page and KPI vertical rhythm**

Use a 10px page gap and a 58px KPI strip. The trend panel uses 10px padding/4px gap, the pipeline summary uses 8px padding/4px gap, and lower-row panels use 10px padding/6px gap. Preserve 12px or larger body text and existing 44px action hit areas.

```css
.workbench-view { gap: 10px; }
.statistic-summary-strip,
.statistic-summary-grid { min-height: 58px; }
.summary-item { gap: 2px; padding: 8px 16px; }
.trend-panel { gap: 4px; padding: 10px; }
.pipeline-status-panel { gap: 4px; padding: 8px; }
.dataset-panel, .service-panel, .activity-panel { gap: 6px; padding: 10px; }
```

- [ ] **Step 2: Reduce the asset trend to a compact chart**

Set both the chart canvas and empty state to 168px. Keep the existing ECharts legend, tooltip, resize observer, ARIA description, and first-render animation behavior unchanged. Remove the desktop aspect-ratio expansion so the declared height is the rendered height.

```css
.asset-trend-canvas,
.asset-trend-empty { min-height: 168px; }
.asset-trend-canvas { height: 168px; aspect-ratio: auto; }
.asset-trend-empty { padding: 12px; }
```

- [ ] **Step 3: Fit the desktop page into two bounded content rows**

Keep the existing left-wide/right-narrow main area, but use a feasible 310px main row and 240px lower overview row. The pipeline status remains directly under the compact trend inside the left column; the resource panel spans the main row height and scrolls vertically only when additional GPU telemetry exceeds its budget.

```css
.workbench-view { grid-template-rows: auto 58px minmax(0, 310px) minmax(0, 240px) auto; }
.command-grid { min-height: 0; }
.command-primary { grid-template-rows: minmax(0, 208px) minmax(0, 94px); gap: 8px; }
.overview-grid { min-height: 0; }
.resource-panel { overflow-y: auto; scrollbar-gutter: stable; }
.resource-panel .resource-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
```

- [ ] **Step 4: Compact lower charts and summaries only inside Workbench panels**

Use Workbench-scoped deep selectors so the administrator overview retains its existing chart sizes.

```css
.dataset-panel :deep(.dashboard-chart),
.dataset-panel :deep(.dashboard-empty) { min-height: 168px !important; }
.service-panel :deep(.dashboard-chart),
.service-panel :deep(.dashboard-empty) { min-height: 128px !important; }
.resource-panel :deep(.resource-usage-panel) { gap: 8px; }
.activity-panel :deep(.activity-summary-item) { padding-block: 8px; }
```

- [ ] **Step 5: Restore natural flow below 1100px**

At the Workbench container breakpoint, remove bounded grid rows and minimum heights so tablet and phone layouts scroll normally and keep the existing 720px/460px rules.

```css
@container workbench (max-width: 1100px) {
  .workbench-view { grid-template-rows: none; }
  .command-grid,
  .command-primary,
  .overview-grid { min-height: 0; }
  .resource-panel .resource-grid { grid-template-columns: minmax(0, 1fr); }
}
```

- [ ] **Step 6: Run focused tests**

Run:

```powershell
npx vitest run tests/workbench-view.spec.ts tests/dashboard-components.spec.ts tests/app-layout.spec.ts
```

Expected: all focused tests pass.

- [ ] **Step 7: Commit the implementation**

```powershell
git add apps/frontend/src/views/workbench/WorkbenchView.vue apps/frontend/src/components/dashboard apps/frontend/tests
git commit -m "feat: compact workbench into a single desktop page"
```

### Task 3: Verify single-screen behavior

**Files:**
- Verify: `apps/frontend/src/views/workbench/WorkbenchView.vue`
- Verify: `apps/frontend/src/components/dashboard/*.vue`

- [ ] **Step 1: Run full automated verification**

Run:

```powershell
npm test -- --run
npm run typecheck
npm run build
```

Expected: all tests pass, typecheck exits 0, and the production build succeeds.

- [ ] **Step 2: Inspect 1366x768 with the sidebar expanded**

Open `/workbench` at exactly 1366x768. Verify `document.documentElement.scrollHeight <= document.documentElement.clientHeight`, all five KPI values are visible, the full asset trend and resource telemetry are visible, and all three lower modules are fully visible.

- [ ] **Step 3: Guard responsive behavior**

Inspect 1024x768 and 375x812. Verify natural vertical scrolling resumes, no horizontal overflow appears, charts remain readable, and no action target becomes smaller than 44px.

- [ ] **Step 4: Check runtime errors and final diff**

Verify the browser console has no errors, then run:

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors and a clean worktree after the final commit.
