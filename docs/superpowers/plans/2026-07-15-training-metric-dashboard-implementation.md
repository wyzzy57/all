# Training Metric Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single mixed-unit training chart with TensorBoard-style metric cards and an MLflow-style same-metric run comparison view.

**Architecture:** A pure metric-catalog module normalizes Ultralytics aliases, groups compatible series, applies display smoothing, and calculates summaries. Focused Vue components render one metric card and the run-comparison workspace, while the existing visualization view remains responsible for API loading, polling, job selection, and tab lifecycle.

**Tech Stack:** Vue 3, TypeScript, Element Plus, ECharts 5, Vitest, Vue Test Utils.

## Global Constraints

- Reuse the existing observability summary and scalar endpoints; do not add a backend batch endpoint.
- Never place metrics with different units or meanings on the same y-axis.
- Keep TensorBoard and MLflow as optional advanced-tool links, not embedded iframes.
- Preserve existing polling, stale-response protection, resources, artifacts, and route-selected-job behavior.
- Prefer canonical Ultralytics keys over `*b` aliases when both are present.
- Apply smoothing to displayed values only; preserve raw values for tooltips and summary calculations.
- Keep the two-column card grid responsive down to one column.

---

### Task 1: Metric Catalog and Transformations

**Files:**
- Create: `apps/frontend/src/components/training/trainingMetricCatalog.ts`
- Create: `apps/frontend/tests/training-metric-catalog.spec.ts`

**Interfaces:**
- Consumes: `TrainingObservabilityScalars["series"]` and `TrainingObservabilityScalarPoint` from `@/api/client`.
- Produces: `canonicalMetricKey(key)`, `buildMetricCards(series)`, `smoothMetricPoints(points, coefficient)`, `summarizeMetric(points, direction)`, and the `TrainingMetricCard` type.

- [ ] **Step 1: Write failing catalog tests**

```ts
it("deduplicates Ultralytics B aliases in favor of canonical keys", () => {
  const cards = buildMetricCards({
    "metrics.map50": [{ step: 1, value: 0.7, timestamp: 1 }],
    "metrics.map50b": [{ step: 1, value: 0.6, timestamp: 1 }],
  });
  expect(cards.flatMap((card) => Object.keys(card.series))).toEqual(["mAP50"]);
});

it("pairs only semantically identical train and validation losses", () => {
  const cards = buildMetricCards(lossSeries);
  expect(cards.find((card) => card.id === "box-loss")?.series).toHaveProperty("Train");
  expect(cards.find((card) => card.id === "box-loss")?.series).toHaveProperty("Validation");
  expect(cards.find((card) => card.id === "precision")?.axis).toMatchObject({ min: 0, max: 1 });
});

it("smooths display values without mutating raw points", () => {
  const raw = [{ step: 1, value: 1, timestamp: 1 }, { step: 2, value: 3, timestamp: 2 }];
  expect(smoothMetricPoints(raw, 0.5).map((point) => point.displayValue)).toEqual([1, 2]);
  expect(raw[1].value).toBe(3);
});
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `npm run test --prefix apps/frontend -- training-metric-catalog.spec.ts`

Expected: FAIL because `trainingMetricCatalog.ts` does not exist.

- [ ] **Step 3: Implement canonical metadata and pure transformations**

```ts
const aliases: Record<string, string> = {
  "metrics.precisionb": "metrics.precision",
  "metrics.recallb": "metrics.recall",
  "metrics.map50b": "metrics.map50",
  "metrics.map50_95b": "metrics.map50_95",
};

export function canonicalMetricKey(key: string) {
  const normalized = normalizeKey(key);
  return aliases[normalized] ?? normalized;
}

export function smoothMetricPoints(points: TrainingObservabilityScalarPoint[], coefficient: number) {
  let previous: number | null = null;
  return points.map((point) => {
    const displayValue = previous === null ? point.value : coefficient * previous + (1 - coefficient) * point.value;
    previous = displayValue;
    return { ...point, displayValue };
  });
}
```

Implement catalog entries for box/cls/dfl loss, precision, recall, mAP50, mAP50-95, and learning rate; create one card per unknown scalar.

- [ ] **Step 4: Run catalog tests**

Run: `npm run test --prefix apps/frontend -- training-metric-catalog.spec.ts`

Expected: PASS.

- [ ] **Step 5: Commit Task 1**

```powershell
git add apps/frontend/src/components/training/trainingMetricCatalog.ts apps/frontend/tests/training-metric-catalog.spec.ts
git commit -m "feat: organize training metrics by semantic group"
```

### Task 2: Metric Chart Axis, Raw Tooltip, and Resize Lifecycle

**Files:**
- Modify: `apps/frontend/src/components/training/MetricLineChart.vue`
- Modify: `apps/frontend/tests/training-chart-components.spec.ts`

**Interfaces:**
- Consumes: `series`, `unit`, `height`, `axisMin`, `axisMax`, `valueFormat`, and `smoothing` props.
- Produces: an ECharts line chart whose data items carry both displayed and raw values and whose container is observed by `ResizeObserver`.

- [ ] **Step 1: Add failing component tests**

```ts
it("uses the supplied axis bounds and keeps raw values in chart data", () => {
  mount(MetricLineChart, { props: { series, axisMin: 0, axisMax: 1, smoothing: 0.5 } });
  expect(echartsMocks.setOption).toHaveBeenCalledWith(expect.objectContaining({
    yAxis: expect.objectContaining({ min: 0, max: 1 }),
    series: [expect.objectContaining({ data: [expect.objectContaining({ rawValue: 1 })] })],
  }), true);
});

it("resizes when its own container changes size", () => {
  resizeObserverCallback([], resizeObserver);
  expect(echartsMocks.resize).toHaveBeenCalled();
});
```

- [ ] **Step 2: Run the focused chart test and verify failure**

Run: `npm run test --prefix apps/frontend -- training-chart-components.spec.ts`

Expected: FAIL because the new props and `ResizeObserver` behavior are absent.

- [ ] **Step 3: Implement chart behavior**

```ts
const props = defineProps<{
  series: TrainingObservabilityScalars["series"];
  unit?: string;
  height?: string | number;
  axisMin?: number;
  axisMax?: number;
  valueFormat?: "number" | "ratio" | "scientific" | "percent";
  smoothing?: number;
}>();

const data = smoothMetricPoints(points, props.smoothing ?? 0).map((point) => ({
  value: [point.step, point.displayValue],
  rawValue: point.value,
}));
```

Add a custom tooltip formatter that displays `rawValue`, retain zoom/legend/save-image controls, and observe `chartElement` with `ResizeObserver` while keeping the window resize fallback.

- [ ] **Step 4: Run chart tests**

Run: `npm run test --prefix apps/frontend -- training-chart-components.spec.ts`

Expected: PASS.

- [ ] **Step 5: Commit Task 2**

```powershell
git add apps/frontend/src/components/training/MetricLineChart.vue apps/frontend/tests/training-chart-components.spec.ts
git commit -m "feat: add metric-aware chart axes and resizing"
```

### Task 3: TensorBoard-Style Metric Cards

**Files:**
- Create: `apps/frontend/src/components/training/TrainingMetricCard.vue`
- Create: `apps/frontend/tests/training-metric-card.spec.ts`

**Interfaces:**
- Consumes: one `TrainingMetricCard` and a global smoothing coefficient.
- Produces: a compact card with metric title, latest raw value, best raw value, and one `MetricLineChart`.

- [ ] **Step 1: Write the failing card test**

```ts
it("renders title, latest, best, and compatible chart series", () => {
  const wrapper = mount(TrainingMetricCard, { props: { card, smoothing: 0.4 } });
  expect(wrapper.get("[data-testid='metric-card-title']").text()).toBe("Box Loss");
  expect(wrapper.get("[data-testid='metric-card-latest']").text()).toContain("0.8000");
  expect(wrapper.get("[data-testid='metric-card-best']").text()).toContain("0.8000");
  expect(wrapper.getComponent(MetricLineChart).props("series")).toEqual(card.series);
});
```

- [ ] **Step 2: Run the card test and verify failure**

Run: `npm run test --prefix apps/frontend -- training-metric-card.spec.ts`

Expected: FAIL because `TrainingMetricCard.vue` does not exist.

- [ ] **Step 3: Implement the card**

Render the title and unit in a tight header, latest/best values on the right, and a 260px chart. Pass axis bounds, format, and smoothing through to `MetricLineChart`. Use an 8px-or-less radius and no nested decorative cards.

- [ ] **Step 4: Run the card test**

Run: `npm run test --prefix apps/frontend -- training-metric-card.spec.ts`

Expected: PASS.

- [ ] **Step 5: Commit Task 3**

```powershell
git add apps/frontend/src/components/training/TrainingMetricCard.vue apps/frontend/tests/training-metric-card.spec.ts
git commit -m "feat: add native training metric cards"
```

### Task 4: MLflow-Style Run Comparison

**Files:**
- Create: `apps/frontend/src/components/training/TrainingRunComparison.vue`
- Create: `apps/frontend/tests/training-run-comparison.spec.ts`
- Modify: `apps/frontend/src/views/training-visualization/TrainingVisualizationView.vue`

**Interfaces:**
- Consumes: available jobs, pipeline-name resolver, and `api.getTrainingObservabilitySummary` / `api.getTrainingObservabilityScalars`.
- Produces: a same-metric comparison chart for 2-5 runs and a summary table containing final, best, best step, status, epochs, batch, and learning rate.

- [ ] **Step 1: Write failing comparison tests**

```ts
it("loads one canonical metric for each selected run", async () => {
  const wrapper = mount(TrainingRunComparison, { props: { jobs, metricKeys } });
  await wrapper.get("[data-testid='comparison-run-job-2']").setValue(true);
  await wrapper.get("[data-testid='comparison-run-job-3']").setValue(true);
  await flushPromises();
  expect(apiMock.getTrainingObservabilityScalars).toHaveBeenCalledWith("job-2", expect.objectContaining({ keys: ["metrics.map50"] }));
  expect(apiMock.getTrainingObservabilityScalars).toHaveBeenCalledWith("job-3", expect.objectContaining({ keys: ["metrics.map50"] }));
});

it("refuses to compare fewer than two or more than five runs", () => {
  expect(wrapper.get("[data-testid='comparison-empty']").exists()).toBe(true);
  expect(wrapper.findAll("input:checked")).toHaveLength(5);
});
```

- [ ] **Step 2: Run comparison tests and verify failure**

Run: `npm run test --prefix apps/frontend -- training-run-comparison.spec.ts`

Expected: FAIL because the component does not exist.

- [ ] **Step 3: Implement comparison loading and display**

Use checkboxes for 2-5 runs and a select for one canonical metric. Request only that metric and known aliases for each run, normalize the response, overlay one line per run, and render the summary table. Protect async updates with a local request generation counter.

- [ ] **Step 4: Run comparison tests**

Run: `npm run test --prefix apps/frontend -- training-run-comparison.spec.ts`

Expected: PASS.

- [ ] **Step 5: Commit Task 4**

```powershell
git add apps/frontend/src/components/training/TrainingRunComparison.vue apps/frontend/tests/training-run-comparison.spec.ts apps/frontend/src/views/training-visualization/TrainingVisualizationView.vue
git commit -m "feat: compare training runs by metric"
```

### Task 5: Integrate Metric Cards into the Visualization Page

**Files:**
- Modify: `apps/frontend/src/views/training-visualization/TrainingVisualizationView.vue`
- Modify: `apps/frontend/tests/training-visualization-view.spec.ts`

**Interfaces:**
- Consumes: `buildMetricCards(scalarSeries)`, `TrainingMetricCard`, and `TrainingRunComparison`.
- Produces: `单次训练` and `运行对比` subviews, global smoothing control, responsive metric-card grid, and unchanged lazy loading/polling semantics.

- [ ] **Step 1: Write failing page integration tests**

```ts
it("renders canonical metric cards instead of one mixed chart", async () => {
  await openMetrics(wrapper);
  expect(wrapper.findAll("[data-testid^='metric-card-']")).toHaveLength(2);
  expect(wrapper.text()).toContain("Box Loss");
  expect(wrapper.text()).toContain("mAP50");
});

it("switches to run comparison without changing the selected run", async () => {
  await wrapper.get("[data-testid='metrics-mode-compare']").trigger("click");
  expect(wrapper.get("[data-testid='run-comparison']").exists()).toBe(true);
  expect(wrapper.get("[data-testid='job-job-1']").classes()).toContain("active");
});
```

- [ ] **Step 2: Run page tests and verify failure**

Run: `npm run test --prefix apps/frontend -- training-visualization-view.spec.ts`

Expected: FAIL because the metric modes and cards are absent.

- [ ] **Step 3: Implement the page layout**

Replace the single metrics chart with a compact mode switch, a labeled smoothing slider (`0` to `0.9`), and a responsive two-column `.metric-card-grid`. Render `TrainingRunComparison` in compare mode. Keep `metricsChartMounted` as the lazy mount gate so existing polling lifecycle remains intact.

- [ ] **Step 4: Run all focused tests**

Run: `npm run test --prefix apps/frontend -- training-metric-catalog.spec.ts training-chart-components.spec.ts training-metric-card.spec.ts training-run-comparison.spec.ts training-visualization-view.spec.ts`

Expected: PASS.

- [ ] **Step 5: Commit Task 5**

```powershell
git add apps/frontend/src/views/training-visualization/TrainingVisualizationView.vue apps/frontend/tests/training-visualization-view.spec.ts
git commit -m "feat: rebuild training metrics dashboard"
```

### Task 6: Full Verification and Browser QA

**Files:**
- Modify only if verification exposes a defect in the files above.

**Interfaces:**
- Consumes: completed frontend implementation.
- Produces: passing tests/build and visual evidence at desktop and narrow widths.

- [ ] **Step 1: Run the frontend test suite**

Run: `npm run test --prefix apps/frontend`

Expected: all tests pass.

- [ ] **Step 2: Run typecheck and production build**

Run: `npm run build --prefix apps/frontend`

Expected: `vue-tsc` and Vite build both exit 0.

- [ ] **Step 3: Verify the live page**

Open `http://127.0.0.1:5174/`, enter 可视化训练, select a run with scalar data, and verify:

```text
- Loss, quality, and learning-rate metrics are separated into cards.
- Train/validation lines share a card only for the same loss.
- Smoothing does not alter latest/best raw values.
- Card charts fill their containers after refresh and tab switching.
- Run comparison overlays only one metric across 2-5 selected runs.
- Two columns collapse to one without text or chart overlap.
```

- [ ] **Step 4: Review the final diff and repository status**

Run: `git diff --check` and `git status --short`

Expected: no whitespace errors; unrelated pre-existing changes remain untouched.

- [ ] **Step 5: Commit any verification-only fixes**

```powershell
git add apps/frontend/src apps/frontend/tests
git commit -m "fix: polish training metric dashboard behavior"
```
