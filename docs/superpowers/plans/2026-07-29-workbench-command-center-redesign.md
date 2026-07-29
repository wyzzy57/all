# Workbench Command Center Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the permission-scoped workbench as a refined operations command center with low-brightness surfaces, clearer hierarchy, responsive charts, and truthful live-state summaries.

**Architecture:** `WorkbenchView.vue` remains the data orchestration boundary and continues to call only the scoped statistics endpoints. Focused dashboard components receive already-derived values through props: a combined trend chart owns ECharts lifecycle, a flat status summary owns status presentation, and an activity summary owns current workload presentation. Existing polling, empty/error states, and permission behavior remain unchanged.

**Tech Stack:** Vue 3 Composition API, TypeScript, Element Plus icons, ECharts 5, Vitest, Vue Test Utils, scoped CSS and container queries.

---

## File Structure

- Create `apps/frontend/src/components/dashboard/AssetTrendChart.vue`: aligned dataset and pipeline trend rendering with one shared time axis.
- Create `apps/frontend/src/components/dashboard/DashboardPanelHeading.vue`: consistent title, metadata, and accessible action alignment.
- Create `apps/frontend/src/components/dashboard/StatusSummaryRow.vue`: flat, non-card-nested status counters with semantic dots.
- Create `apps/frontend/src/components/dashboard/ActivitySummaryPanel.vue`: current training, deployment, and anomaly counts.
- Modify `apps/frontend/src/views/workbench/WorkbenchView.vue`: derive display models, wire navigation, and implement the command-center layout.
- Modify `apps/frontend/src/components/dashboard/StatisticSummaryStrip.vue`: low-brightness summary strip and stable responsive columns.
- Modify `apps/frontend/src/components/dashboard/ResourceUsagePanel.vue`: compact neutral resource telemetry presentation.
- Modify `apps/frontend/src/components/dashboard/ServiceHealthPanel.vue`: compact service-health layout suitable for the lower overview row.
- Modify `apps/frontend/tests/dashboard-components.spec.ts`: component contracts, chart behavior, animation, and empty states.
- Modify `apps/frontend/tests/workbench-view.spec.ts`: orchestration, activity derivation, navigation targets, and layout contract.

### Task 1: Lock the new dashboard component contracts with failing tests

**Files:**
- Modify: `apps/frontend/tests/dashboard-components.spec.ts`
- Modify: `apps/frontend/tests/workbench-view.spec.ts`

- [ ] **Step 1: Add failing component tests**

Add imports and tests for the three new components. Assert that `AssetTrendChart` creates two series on a union of labels, does not initialize for two empty trends, and does not replay animation after updates. Assert that status and activity summaries render explicit values and their empty states.

```ts
import ActivitySummaryPanel from "@/components/dashboard/ActivitySummaryPanel.vue";
import AssetTrendChart from "@/components/dashboard/AssetTrendChart.vue";
import DashboardPanelHeading from "@/components/dashboard/DashboardPanelHeading.vue";
import StatusSummaryRow from "@/components/dashboard/StatusSummaryRow.vue";

it("aligns pipeline and dataset trends on a shared time axis", async () => {
  const wrapper = mount(AssetTrendChart, {
    props: {
      pipelineTrend: { labels: ["2026-06", "2026-07"], values: [1, 2] },
      datasetTrend: { labels: ["2026-07", "2026-08"], values: [3, 4] },
    },
  });
  expect(latestOption().xAxis.data).toEqual(["2026-06", "2026-07", "2026-08"]);
  expect(latestOption().series).toEqual([
    expect.objectContaining({ name: "产线", data: [1, 2, 0] }),
    expect.objectContaining({ name: "数据集", data: [0, 3, 4] }),
  ]);
  await wrapper.setProps({ pipelineTrend: { labels: ["2026-09"], values: [5] } });
  expect(latestOption().animation).toBe(false);
});

it("renders flat status and activity summaries", () => {
  const status = mount(StatusSummaryRow, { props: { buckets: [{ label: "运行成功", value: 6 }] } });
  expect(status.get("[role='list']").text()).toContain("运行成功");
  expect(status.text()).toContain("6");

  const activity = mount(ActivitySummaryPanel, {
    props: { training: 1, deployments: 2, anomalies: 0 },
  });
  expect(activity.text()).toContain("训练中的任务");
  expect(activity.text()).toContain("2");

  const heading = mount(DashboardPanelHeading, {
    props: { title: "服务健康", meta: "刚刚更新", actionLabel: "查看服务" },
  });
  expect(heading.get("h2").text()).toBe("服务健康");
  expect(heading.get("button").attributes("aria-label")).toBe("查看服务");
});
```

- [ ] **Step 2: Replace workbench mocks and assert the approved information architecture**

Mock the three new components and assert that the workbench passes both trends, derived activity counts, five summary values, and exactly three relevant navigation targets: data preparation, model space, and services.

```ts
expect(wrapper.get("[data-testid='asset-trend']").text()).toContain("2026-07");
expect(wrapper.get("[data-testid='activity-summary']").text()).toContain("1|2|0");
await wrapper.get("[data-testid='go-data-assets']").trigger("click");
await wrapper.get("[data-testid='go-model-space']").trigger("click");
await wrapper.get("[data-testid='go-services']").trigger("click");
expect(pushMock.mock.calls).toEqual([
  ["/data-preparation"],
  ["/model-space"],
  ["/services"],
]);
```

- [ ] **Step 3: Run the focused tests and verify they fail**

Run:

```powershell
npm test -- --run tests/dashboard-components.spec.ts tests/workbench-view.spec.ts
```

Expected: FAIL because the new components and workbench test IDs do not exist.

### Task 2: Build the combined trend and flat summary components

**Files:**
- Create: `apps/frontend/src/components/dashboard/AssetTrendChart.vue`
- Create: `apps/frontend/src/components/dashboard/DashboardPanelHeading.vue`
- Create: `apps/frontend/src/components/dashboard/StatusSummaryRow.vue`
- Create: `apps/frontend/src/components/dashboard/ActivitySummaryPanel.vue`
- Test: `apps/frontend/tests/dashboard-components.spec.ts`

- [ ] **Step 1: Implement `AssetTrendChart.vue`**

Use ECharts `BarChart`, `GridComponent`, `LegendComponent`, `TooltipComponent`, and `AriaComponent`. Build labels with insertion-order union and align missing values to zero.

```ts
function labels() {
  return [...new Set([...props.pipelineTrend.labels, ...props.datasetTrend.labels])];
}

function aligned(trend: DashboardTrend, target: string[]) {
  const values = new Map(trend.labels.map((label, index) => [label, trend.values[index] ?? 0]));
  return target.map((label) => values.get(label) ?? 0);
}
```

The chart option must use `animationDuration: 360`, disable animation after first render, show a safe axis tooltip, reserve a stable `min-height: 250px`, register `ResizeObserver`, and expose a screen-reader list.

- [ ] **Step 2: Implement `StatusSummaryRow.vue`**

Render one semantic list with separators rather than nested cards. Normalize status classes without using color as the only signal.

```vue
<div v-for="bucket in buckets" :key="bucket.label" class="status-summary-item" role="listitem">
  <span><i aria-hidden="true" :class="statusTone(bucket.label)" />{{ bucket.label }}</span>
  <strong>{{ bucket.value.toLocaleString("zh-CN") }}</strong>
</div>
```

- [ ] **Step 3: Implement `DashboardPanelHeading.vue`**

Render a compact semantic header. The optional action is a real button with an arrow icon and emits `action`; metadata is plain text and never occupies action space.

```vue
<header class="dashboard-panel-heading">
  <div><h2>{{ title }}</h2><p v-if="description">{{ description }}</p></div>
  <span v-if="meta" class="panel-meta">{{ meta }}</span>
  <button v-if="actionLabel" type="button" :aria-label="actionLabel" @click="$emit('action')">
    {{ actionLabel }}<el-icon><ArrowRight /></el-icon>
  </button>
</header>
```

- [ ] **Step 4: Implement `ActivitySummaryPanel.vue`**

Render training, deployment, and anomaly values as a flat definition list. Show `暂无活动数据` only when all three values are zero, while still exposing the zero values to assistive technology.

- [ ] **Step 5: Run component tests**

Run:

```powershell
npm test -- --run tests/dashboard-components.spec.ts
```

Expected: PASS.

### Task 3: Recompose `WorkbenchView.vue` as the command center

**Files:**
- Modify: `apps/frontend/src/views/workbench/WorkbenchView.vue`
- Test: `apps/frontend/tests/workbench-view.spec.ts`

- [ ] **Step 1: Replace the three legacy page sections with the approved hierarchy**

Use this structure:

```vue
<StatisticSummaryStrip :items="summaryItems" />
<div class="command-grid">
  <div class="command-primary">
    <section class="command-panel trend-panel">
      <DashboardPanelHeading title="资产增长趋势" meta="近 6 个月" />
      <AssetTrendChart :pipeline-trend="pipelineTrend" :dataset-trend="datasetTrend" />
    </section>
    <section class="command-panel pipeline-status-panel">
      <DashboardPanelHeading title="产线运行状态" :meta="`共 ${overview.totals.pipelines} 条`" />
      <StatusSummaryRow :buckets="pipelineStatusBuckets" />
    </section>
  </div>
  <section class="command-panel resource-panel">
    <ResourceUsagePanel :usage="resourceUsage" :gpu-series="gpuUsage" :stale="resourceStale" />
  </section>
</div>
<div class="overview-grid">
  <section class="command-panel dataset-panel">
    <DashboardPanelHeading title="数据集状态" action-label="查看数据资产" @action="goDataPreparation" />
    <PipelineStatusChart :buckets="datasetStatusBuckets" title="数据集状态" />
  </section>
  <section class="command-panel service-panel">
    <DashboardPanelHeading title="服务健康" action-label="查看服务" @action="goServices" />
    <ServiceHealthPanel :health-buckets="serviceHealthBuckets" :calls="serviceCalls" :instances="serviceInstances" />
  </section>
  <section class="command-panel activity-panel">
    <DashboardPanelHeading title="当前活动" action-label="查看模型空间" @action="goModelSpace" />
    <ActivitySummaryPanel v-bind="activity" />
  </section>
</div>
```

Every panel uses a header with title, optional metadata, and one relevant text-plus-arrow action. Do not place framed cards inside these panels.

- [ ] **Step 2: Add truthful current-activity derivation**

Normalize bucket labels to lowercase and sum known active or unhealthy states.

```ts
function sumBuckets(buckets: StatisticsBucket[], labels: Set<string>) {
  return buckets.reduce((sum, bucket) => (
    labels.has(bucket.label.trim().toLowerCase()) ? sum + bucket.value : sum
  ), 0);
}

const activity = computed(() => ({
  training: sumBuckets(overview.value?.status_buckets.training_jobs ?? [], new Set(["running", "training"])),
  deployments: sumBuckets(overview.value?.status_buckets.services ?? [], new Set(["deploying", "starting", "running"])),
  anomalies: (resourceStatistics.value?.nodes.freshness.stale ?? 0)
    + (resourceStatistics.value?.nodes.freshness.unknown ?? 0)
    + sumBuckets(resourceStatistics.value?.services.health_buckets ?? [], new Set(["unhealthy", "failed", "error", "degraded"])),
}));
```

- [ ] **Step 3: Correct navigation targets**

Add `goModelSpace()` and data test IDs. Keep `goDataPreparation()` and `goServices()`.

```ts
function goModelSpace() {
  void router.push("/model-space");
}
```

- [ ] **Step 4: Apply low-brightness command-center styling**

Use scoped custom properties and container queries:

```css
.workbench-view {
  --workbench-surface: #f3f4f6;
  --workbench-surface-raised: #f6f7f8;
  --workbench-border: #e0e2e6;
  container: workbench / inline-size;
}
.command-panel,
.statistic-summary-strip {
  border: 1px solid var(--workbench-border);
  border-radius: 8px;
  background: var(--workbench-surface);
}
.command-grid { grid-template-columns: minmax(0, 1.7fr) minmax(280px, .8fr); }
```

At `1080px`, use one main column and two lower columns. At `720px`, use one column and remove fixed metadata whitespace. At `460px`, reduce padding without reducing body text below 12px.

- [ ] **Step 5: Run workbench tests**

Run:

```powershell
npm test -- --run tests/workbench-view.spec.ts
```

Expected: PASS with scoped endpoint, polling, error, empty, navigation, and layout assertions intact.

### Task 4: Harmonize existing dashboard surfaces and interaction states

**Files:**
- Modify: `apps/frontend/src/components/dashboard/StatisticSummaryStrip.vue`
- Modify: `apps/frontend/src/components/dashboard/ResourceUsagePanel.vue`
- Modify: `apps/frontend/src/components/dashboard/ServiceHealthPanel.vue`
- Modify: `apps/frontend/src/components/dashboard/PipelineStatusChart.vue`
- Test: `apps/frontend/tests/dashboard-components.spec.ts`

- [ ] **Step 1: Make component roots inherit their panel surface**

Change white component backgrounds to transparent and remove internal dashed frames. Keep stable empty-state heights with a subtle neutral fill and plain text.

```css
.resource-usage-panel,
.service-health-panel,
.dashboard-panel { background: transparent; }
.dashboard-empty { border: 0; background: rgb(255 255 255 / 34%); }
```

- [ ] **Step 2: Refine summary and resource density**

Set five stable columns above 920px, two columns below 720px, and one below 420px. Progress bars remain 8px tall, use neutral tracks, and disable width transitions under reduced motion.

- [ ] **Step 3: Tune service and dataset chart palettes**

Use blue, green, amber, red, and neutral gray with visible legends and hover tooltips. Keep `emphasis.scale` and remove any selected offset or layout-shifting interaction.

- [ ] **Step 4: Run all dashboard tests**

Run:

```powershell
npm test -- --run tests/dashboard-components.spec.ts tests/admin-overview-view.spec.ts tests/workbench-view.spec.ts
```

Expected: PASS without changing the admin overview data contract.

### Task 5: Complete verification and visual acceptance

**Files:**
- Verify: `apps/frontend/src/views/workbench/WorkbenchView.vue`
- Verify: `apps/frontend/src/components/dashboard/*.vue`

- [ ] **Step 1: Run the full frontend suite**

Run:

```powershell
npm test -- --run
npm run typecheck
npm run build
```

Expected: all tests pass, typecheck exits 0, and Vite production build succeeds. Existing bundle-size warnings may remain but no new errors are allowed.

- [ ] **Step 2: Inspect the live page at desktop width**

Open `http://127.0.0.1:5174/workbench`. Verify the 1280px first viewport contains the KPI strip, combined trend, pipeline status, and resource panel; panel surfaces are low-brightness gray rather than pure white; no text overlaps.

- [ ] **Step 3: Inspect narrow layout behavior**

Verify at 720px and 375px widths that panels become one column, the KPI strip wraps predictably, charts keep stable dimensions, actions remain at least 44px high, and no horizontal scrollbar appears.

- [ ] **Step 4: Verify animation and accessibility**

Confirm tooltips identify series and values, keyboard focus is visible, ECharts has ARIA enabled, and `prefers-reduced-motion` suppresses repeated transitions.

- [ ] **Step 5: Review the diff**

Run:

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors and only the intended workbench/dashboard files plus the already-existing uncommitted shell redesign files are modified.
