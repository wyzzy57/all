import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AsyncState from "@/components/common/AsyncState.vue";
import WorkbenchView from "@/views/workbench/WorkbenchView.vue";
import workbenchSource from "@/views/workbench/WorkbenchView.vue?raw";

function collectCssAtRuleBodies(source: string, atRulePattern: RegExp) {
  const bodies: string[] = [];

  for (const match of source.matchAll(atRulePattern)) {
    if (match.index === undefined) continue;
    const openingBrace = source.indexOf("{", match.index + match[0].length);
    if (openingBrace < 0) continue;

    let depth = 0;
    for (let index = openingBrace; index < source.length; index += 1) {
      if (source[index] === "{") depth += 1;
      if (source[index] !== "}") continue;
      depth -= 1;
      if (depth === 0) {
        bodies.push(source.slice(openingBrace + 1, index));
        break;
      }
    }
  }

  return bodies;
}

function collectTopLevelCssRuleBodies(source: string, selector: string) {
  const bodies: string[] = [];
  let depth = 0;
  let preludeStart = 0;
  let selectedBodyStart = -1;

  for (let index = 0; index < source.length; index += 1) {
    if (source[index] === "{" && depth === 0) {
      const prelude = source.slice(preludeStart, index).replace(/\/\*[\s\S]*?\*\//g, "").trim();
      if (prelude === selector) selectedBodyStart = index + 1;
      depth += 1;
      continue;
    }
    if (source[index] === "{") {
      depth += 1;
      continue;
    }
    if (source[index] !== "}") {
      if (source[index] === ";" && depth === 0) preludeStart = index + 1;
      continue;
    }

    depth -= 1;
    if (depth !== 0) continue;
    if (selectedBodyStart >= 0) bodies.push(source.slice(selectedBodyStart, index));
    selectedBodyStart = -1;
    preludeStart = index + 1;
  }

  return bodies;
}

const pushMock = vi.hoisted(() => vi.fn());
const apiMock = vi.hoisted(() => ({
  getWorkbenchStatistics: vi.fn(),
  getResourceStatistics: vi.fn(),
  listDatasets: vi.fn(),
  listPipelines: vi.fn(),
  listServices: vi.fn(),
}));

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: pushMock }),
}));

vi.mock("@/api/client", () => ({ api: apiMock }));

vi.mock("@/components/dashboard/StatisticSummaryStrip.vue", () => ({
  default: { props: ["items"], template: '<div data-testid="summary-strip">{{ items.map((item) => item.label + item.value).join("|") }}</div>' },
}));
vi.mock("@/components/dashboard/PipelineStatusChart.vue", () => ({
  default: { props: ["buckets", "title"], template: '<div data-testid="dataset-status-chart">{{ title }}|{{ buckets.map((item) => item.label + item.value).join("|") }}</div>' },
}));
vi.mock("@/components/dashboard/AssetTrendChart.vue", () => ({
  default: { props: ["pipelineTrend", "datasetTrend"], template: '<div data-testid="asset-trend">{{ pipelineTrend.labels.join("|") }}::{{ datasetTrend.labels.join("|") }}</div>' },
}));
vi.mock("@/components/dashboard/StatusSummaryRow.vue", () => ({
  default: { props: ["buckets"], template: '<div data-testid="status-row">{{ buckets.map((item) => item.label + item.value).join("|") }}</div>' },
}));
vi.mock("@/components/dashboard/DashboardPanelHeading.vue", () => ({
  default: {
    name: "DashboardPanelHeading",
    props: ["title", "meta", "description", "actionLabel"],
    emits: ["action"],
    template: '<header><h2>{{ title }}</h2><span v-if="meta">{{ meta }}</span><p v-if="description">{{ description }}</p><button v-if="actionLabel" type="button" :aria-label="actionLabel" @click="$emit(\'action\')">{{ actionLabel }}</button></header>',
  },
}));
vi.mock("@/components/dashboard/ActivitySummaryPanel.vue", () => ({
  default: { props: ["training", "deployments", "anomalies"], template: '<div data-testid="activity-summary">{{ training }}|{{ deployments }}|{{ anomalies }}</div>' },
}));
vi.mock("@/components/dashboard/ResourceUsagePanel.vue", () => ({
  default: { props: ["usage", "gpuSeries", "stale"], template: '<div data-testid="resource-panel">{{ usage.map((item) => item.label + item.value).join("|") }}{{ stale ? "stale" : "" }}</div>' },
}));
vi.mock("@/components/dashboard/ServiceHealthPanel.vue", () => ({
  default: { props: ["healthBuckets", "calls", "instances"], template: '<div data-testid="service-panel">{{ calls }}|{{ instances }}|{{ healthBuckets.map((item) => item.label + item.value).join("|") }}</div>' },
}));

const overview = {
  generated_at: "2026-07-29T08:00:00Z",
  totals: { pipelines: 3, datasets: 2, training_jobs: 16, services: 29, nodes: 25, users: 0, groups: 0 },
  status_buckets: {
    pipelines: [{ label: "running", value: 1 }, { label: "success", value: 2 }],
    datasets: [{ label: "validated", value: 2 }],
    training_jobs: [{ label: "Running", value: 1 }, { label: "TRAINING", value: 2 }, { label: "queued", value: 13 }],
    services: [{ label: "Deploying", value: 3 }, { label: "STARTING", value: 4 }, { label: "running", value: 5 }, { label: "stopped", value: 17 }],
    nodes: [],
  },
  creation_trends: {
    pipelines: { labels: ["2026-06", "2026-07"], values: [1, 2] },
    datasets: { labels: ["2026-06", "2026-07"], values: [0, 2] },
    training_jobs: { labels: [], values: [] }, services: { labels: [], values: [] }, nodes: { labels: [], values: [] },
  },
};

const resources = {
  generated_at: "2026-07-29T08:00:01Z",
  staleness_threshold_seconds: 300,
  nodes: {
    status_buckets: [{ label: "online", value: 25 }],
    freshness: { fresh: 12, stale: 6, unknown: 7, oldest_fresh_at: "2026-07-29T08:00:00Z", newest_fresh_at: "2026-07-29T08:00:01Z" },
    resource_usage: {
      cpu_utilization_percent: { value: 12.5, available: 2, unavailable: 0 },
      memory_utilization_percent: { value: 25, available: 2, unavailable: 0 },
      disk_utilization_percent: { value: 0, available: 2, unavailable: 0 },
    },
  },
  gpus: { series: [{ key: "gpu:anon", refreshed_at: "2026-07-29T08:00:01Z", utilization_percent: { value: 40, available: true }, memory_used_mib: { value: 2048, available: true }, memory_total_mib: { value: 8192, available: true }, memory_utilization_percent: { value: 25, available: true } }] },
  services: {
    calls: 18,
    instances: 50,
    health_buckets: [
      { label: "healthy", value: 12 },
      { label: "Unhealthy", value: 8 },
      { label: "FAILED", value: 9 },
      { label: "Error", value: 10 },
      { label: "degraded", value: 11 },
    ],
    latest_health_checked_at: "2026-07-29T08:00:01Z",
  },
  group_allocation_usage: {
    policy_count: 0,
    resource_pool_count: 0,
    active_training_runs: 0,
    active_service_instances: 0,
    active_workloads: 0,
    limitation: "",
  },
};

describe("WorkbenchView", () => {
  const wrappers: Array<ReturnType<typeof mount>> = [];

  function mountView() {
    const wrapper = mount(WorkbenchView, {
      global: {
        stubs: ["CreationTrendChart", "DatasetTrendChart"],
      },
    });
    wrappers.push(wrapper);
    return wrapper;
  }

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    apiMock.getWorkbenchStatistics.mockResolvedValue(overview);
    apiMock.getResourceStatistics.mockResolvedValue(resources);
  });

  afterEach(() => {
    wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it("uses only permission-scoped statistics endpoints and renders returned aggregates", async () => {
    const wrapper = mountView();
    await flushPromises();

    expect(apiMock.getWorkbenchStatistics).toHaveBeenCalledTimes(1);
    expect(apiMock.getResourceStatistics).toHaveBeenCalledTimes(1);
    expect(apiMock.listDatasets).not.toHaveBeenCalled();
    expect(apiMock.listPipelines).not.toHaveBeenCalled();
    expect(apiMock.listServices).not.toHaveBeenCalled();
    expect(wrapper.get("[data-testid='summary-strip']").text()).toBe("产线数量3|数据集数量2|训练任务16|服务数量29|可用节点12");
    expect(wrapper.get("[data-testid='status-row']").text()).toBe("running1|success2");
    expect(wrapper.get("[data-testid='dataset-status-chart']").text()).toContain("数据集状态|validated2");
    expect(wrapper.get("[data-testid='asset-trend']").text()).toBe("2026-06|2026-07::2026-06|2026-07");
    expect(wrapper.get("[data-testid='activity-summary']").text()).toBe("3|7|51");
    expect(wrapper.get("[data-testid='resource-panel']").text()).toContain("CPU12.5");
    expect(wrapper.get("[data-testid='service-panel']").text()).toContain("18|50|healthy12|Unhealthy8|FAILED9|Error10|degraded11");
  });

  it("replaces both legacy trend charts with the combined asset trend", async () => {
    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.find("creation-trend-chart-stub").exists()).toBe(false);
    expect(wrapper.find("dataset-trend-chart-stub").exists()).toBe(false);
    expect(wrapper.get("[data-testid='asset-trend']").text()).toBe("2026-06|2026-07::2026-06|2026-07");
  });

  it("composes the approved panel headings and actions", async () => {
    const wrapper = mountView();
    await flushPromises();

    const headings = wrapper.findAllComponents({ name: "DashboardPanelHeading" });
    expect(headings.map((heading) => ({
      title: heading.props("title"),
      meta: heading.props("meta"),
      actionLabel: heading.props("actionLabel"),
      testId: heading.attributes("data-testid"),
    }))).toEqual([
      { title: "资产增长趋势", meta: "近 6 个月", actionLabel: undefined, testId: undefined },
      { title: "产线运行状态", meta: "共 3 条", actionLabel: undefined, testId: undefined },
      { title: "数据集状态", meta: undefined, actionLabel: "查看数据资产", testId: "go-data-assets" },
      { title: "服务健康", meta: undefined, actionLabel: "查看服务", testId: "go-services" },
      { title: "当前活动", meta: undefined, actionLabel: "查看模型空间", testId: "go-model-space" },
    ]);
  });

  it("polls resource statistics every five seconds, pauses hidden pages, and refreshes when visible", async () => {
    mountView();
    await flushPromises();
    await vi.advanceTimersByTimeAsync(5_000);
    expect(apiMock.getResourceStatistics).toHaveBeenCalledTimes(2);

    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(10_000);
    expect(apiMock.getResourceStatistics).toHaveBeenCalledTimes(2);

    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    document.dispatchEvent(new Event("visibilitychange"));
    await flushPromises();
    expect(apiMock.getResourceStatistics).toHaveBeenCalledTimes(3);
  });

  it("keeps the last resource snapshot after a refresh error and exposes retryable errors", async () => {
    const wrapper = mountView();
    await flushPromises();
    apiMock.getResourceStatistics.mockRejectedValueOnce(new Error("resource endpoint unavailable"));

    await vi.advanceTimersByTimeAsync(5_000);
    await flushPromises();

    expect(wrapper.get("[data-testid='resource-panel']").text()).toContain("CPU12.5");
    expect(wrapper.get("[role='alert']").text()).toContain("resource endpoint unavailable");
    expect(wrapper.get("[role='alert']").text()).toContain("正在展示最近一次成功获取的资源统计");
  });

  it("reports initial resource unavailability and keeps the alert stable across repeated failures", async () => {
    apiMock.getResourceStatistics
      .mockRejectedValueOnce(new Error("resource endpoint unavailable"))
      .mockRejectedValueOnce(new Error("resource endpoint unavailable"));
    const wrapper = mountView();
    await flushPromises();

    const initialAlert = wrapper.get("[role='alert']");
    expect(initialAlert.text()).toContain("资源统计暂时不可用，正在重试");
    expect(initialAlert.text()).not.toContain("最近一次成功获取");

    await vi.advanceTimersByTimeAsync(5_000);
    await flushPromises();

    const repeatedAlert = wrapper.get("[role='alert']");
    expect(apiMock.getResourceStatistics).toHaveBeenCalledTimes(2);
    expect(repeatedAlert.element).toBe(initialAlert.element);
    expect(repeatedAlert.text()).toBe(initialAlert.text());
  });

  it("shows an overview loading, error, and empty state without rendering invented data", async () => {
    let resolveOverview: (value: typeof overview) => void;
    apiMock.getWorkbenchStatistics.mockImplementationOnce(() => new Promise((resolve) => { resolveOverview = resolve; }));
    const loading = mountView();
    expect(loading.get("[data-testid='workbench-loading']").text()).toContain("正在加载工作台统计");
    expect(loading.getComponent(AsyncState).props("state")).toBe("loading");
    resolveOverview!(overview);
    await flushPromises();

    apiMock.getWorkbenchStatistics.mockRejectedValueOnce(new Error("overview unavailable"));
    const failure = mountView();
    await flushPromises();
    expect(failure.get("[role='alert']").text()).toContain("overview unavailable");
    expect(failure.getComponent(AsyncState).props("state")).toBe("error");
    await failure.get(".async-state__retry").trigger("click");
    await flushPromises();
    expect(apiMock.getWorkbenchStatistics).toHaveBeenCalledTimes(3);
    expect(failure.get("[data-testid='summary-strip']").text()).toContain("产线数量3");

    apiMock.getWorkbenchStatistics.mockRejectedValueOnce(Object.assign(new Error("denied"), { status: 403 }));
    const denied = mountView();
    await flushPromises();
    expect(denied.getComponent(AsyncState).props("state")).toBe("denied");
    expect(denied.find("button").exists()).toBe(false);

    apiMock.getWorkbenchStatistics.mockResolvedValueOnce({ ...overview, totals: { pipelines: 0, datasets: 0, training_jobs: 0, services: 0, nodes: 0, users: 0, groups: 0 } });
    const empty = mountView();
    await flushPromises();
    expect(empty.get("[data-testid='workbench-empty']").text()).toContain("暂无可访问的资源");
    expect(empty.getComponent(AsyncState).props("state")).toBe("empty");
  });

  it("keeps the three approved quick navigation targets", async () => {
    const wrapper = mountView();
    await flushPromises();
    const quickLinks = wrapper.findAll("[data-testid^='go-']");
    expect(quickLinks).toHaveLength(3);
    await wrapper.get("[data-testid='go-data-assets'] button").trigger("click");
    await wrapper.get("[data-testid='go-model-space'] button").trigger("click");
    await wrapper.get("[data-testid='go-services'] button").trigger("click");
    expect(pushMock.mock.calls).toEqual([
      ["/data-preparation"],
      ["/model-space"],
      ["/services"],
    ]);
  });

  it("renders the command-center containers and has no legacy resource list calls", async () => {
    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.find(".command-grid").exists()).toBe(true);
    expect(wrapper.find(".overview-grid").exists()).toBe(true);
    expect(workbenchSource).not.toContain("api.listDatasets");
    expect(workbenchSource).not.toContain("api.listPipelines");
    expect(workbenchSource).not.toContain("api.listServices");
  });

  it("keeps the desktop command grid dense and flattens only the intended child surfaces", () => {
    const naturalFlowBreakpoint = 1060;
    const naturalFlowViewportBreakpoint = 1350;
    const expandedSidebarContentWidth = 1366 - 224 - (22 * 2);
    const reservedScrollbarGutter = 17;
    const stylesheet = workbenchSource.match(/<style(?:\s[^>]*)?>([\s\S]*?)<\/style>/)?.[1] ?? "";
    const desktopWorkbenchRule = collectTopLevelCssRuleBodies(stylesheet, ".workbench-view").join("\n");
    const desktopResourceGridRule = collectTopLevelCssRuleBodies(
      stylesheet,
      ".resource-panel :deep(.resource-grid)",
    ).join("\n");
    const compactBreakpoint = collectCssAtRuleBodies(
      stylesheet,
      new RegExp(`@container\\s+workbench\\s*\\(\\s*max-width:\\s*${naturalFlowBreakpoint}px\\s*\\)`, "g"),
    ).join("\n");
    const naturalFlowViewport = collectCssAtRuleBodies(
      stylesheet,
      new RegExp(`@media\\s*\\(\\s*max-width:\\s*${naturalFlowViewportBreakpoint}px\\s*\\)(?=\\s*\\{)`, "g"),
    ).join("\n");

    expect(expandedSidebarContentWidth).toBe(1098);
    expect(expandedSidebarContentWidth - reservedScrollbarGutter).toBeGreaterThan(naturalFlowBreakpoint);
    expect(1024).toBeLessThanOrEqual(naturalFlowViewportBreakpoint);
    expect(375).toBeLessThanOrEqual(naturalFlowViewportBreakpoint);
    expect(1350).toBeLessThanOrEqual(naturalFlowViewportBreakpoint);
    expect(1366).toBeGreaterThan(naturalFlowViewportBreakpoint);
    expect(stylesheet).not.toBe("");
    expect(desktopWorkbenchRule).not.toBe("");
    expect(desktopResourceGridRule).not.toBe("");
    expect(workbenchSource).not.toContain("@container workbench (max-width: 1100px)");
    expect.soft(desktopWorkbenchRule).toMatch(
      /grid-template-rows:\s*minmax\(0,\s*52px\)\s+58px\s+minmax\(0,\s*310px\)\s+minmax\(0,\s*240px\)(?:\s|;)/s,
    );
    expect.soft(desktopWorkbenchRule).toMatch(
      /gap:\s*6px\s*;/s,
    );
    expect(workbenchSource).toMatch(
      /\.command-primary\s*\{[^}]*grid-template-rows:\s*minmax\(0,\s*208px\)\s+minmax\(0,\s*94px\)[^}]*gap:\s*8px/s,
    );
    expect(workbenchSource).toContain("grid-template-columns: minmax(0, 1.7fr) minmax(280px, .8fr)");
    expect(workbenchSource).toMatch(/\.overview-grid\s*\{[^}]*grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\)/s);
    expect(workbenchSource).toMatch(/\.asset-trend-canvas\)[^{]*\{[^}]*height:\s*168px;[^}]*aspect-ratio:\s*auto/s);
    expect(workbenchSource).not.toMatch(/\.command-panel\s*\{[^}]*overflow:\s*hidden/s);
    expect(workbenchSource).toMatch(/\.resource-panel\s*\{[^}]*overflow-y:\s*auto;[^}]*scrollbar-gutter:\s*stable/s);
    expect.soft(desktopResourceGridRule).toMatch(
      /grid-template-columns:\s*minmax\(0,\s*1fr\)\s*;/s,
    );
    expect(compactBreakpoint).not.toMatch(/\.workbench-view\s*\{/s);
    expect(naturalFlowViewport).toMatch(/\.workbench-view\s*\{[^}]*grid-template-rows:\s*none/s);
    expect(compactBreakpoint).toMatch(
      /(?=[^{]*\.command-grid)(?=[^{]*\.command-primary)(?=[^{]*\.overview-grid)[^{]*\{[^}]*min-height:\s*0/s,
    );
    expect(compactBreakpoint).toMatch(/\.resource-panel\s*\{[^}]*overflow-y:\s*auto/s);
    expect(compactBreakpoint).toMatch(/\.resource-panel\s+:deep\(\.resource-grid\)\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/s);
    expect(workbenchSource).toContain(".resource-panel :deep(.resource-usage-panel)");
    expect(workbenchSource).toContain(".dataset-panel :deep(.pipeline-status-chart > header > h3)");
    expect(workbenchSource).toContain(".service-panel :deep(.service-health-panel > header > h3)");
    expect(workbenchSource).not.toContain(".resource-panel :deep(.resource-usage-panel > header > h3)");
    expect(workbenchSource).toMatch(/@media \(max-width: 460px\)\s*\{\s*\.workbench-view\s*\{/);
  });

  it("fits KPI and service-health content inside their desktop tracks", () => {
    const naturalFlowBreakpoint = collectCssAtRuleBodies(
      workbenchSource,
      /@container\s+workbench\s*\(\s*max-width:\s*1060px\s*\)/g,
    ).join("\n");

    expect(workbenchSource).toMatch(
      /\.workbench-view\s+:deep\(\.statistic-summary-strip\)\s*\{[^}]*min-height:\s*0/s,
    );
    expect(workbenchSource).toMatch(
      /(?=[^{]*\.statistic-summary-grid)(?=[^{]*\.summary-empty)[^{]*\{[^}]*min-height:\s*56px/s,
    );
    expect(workbenchSource).toMatch(
      /\.workbench-view\s+:deep\(\.summary-item\)\s*\{[^}]*padding:\s*2px\s+14px/s,
    );
    expect(workbenchSource).toMatch(
      /\.service-panel\s+:deep\(\.service-health-panel\)\s*\{[^}]*grid-template-rows:\s*32px\s+minmax\(0,\s*132px\)[^}]*height:\s*168px/s,
    );
    expect(workbenchSource).toMatch(
      /\.service-panel\s+:deep\(\.health-visual\)\s*\{[^}]*grid-template-columns:\s*120px\s+minmax\(0,\s*1fr\)[^}]*height:\s*132px/s,
    );
    expect(workbenchSource).toMatch(
      /\.service-panel\s+:deep\(\.health-summary\)\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/s,
    );
    expect(naturalFlowBreakpoint).toMatch(
      /\.service-panel\s+:deep\(\.service-health-panel\)\s*\{[^}]*grid-template-rows:\s*none;[^}]*height:\s*auto/s,
    );
  });
});
