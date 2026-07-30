import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AsyncState from "@/components/common/AsyncState.vue";
import WorkbenchView from "@/views/workbench/WorkbenchView.vue";
import workbenchSource from "@/views/workbench/WorkbenchView.vue?raw";

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
  default: { props: ["items"], template: '<div data-testid="status-row">{{ items.map((item) => item.label + item.value).join("|") }}</div>' },
}));
vi.mock("@/components/dashboard/DashboardPanelHeading.vue", () => ({
  default: {
    props: ["title", "metadata", "description", "actionLabel"],
    emits: ["action"],
    template: '<header><h2>{{ title }}</h2><span v-if="metadata">{{ metadata }}</span><p v-if="description">{{ description }}</p><button v-if="actionLabel" type="button" :aria-label="actionLabel" @click="$emit(\'action\')">{{ actionLabel }}</button></header>',
  },
}));
vi.mock("@/components/dashboard/ActivitySummaryPanel.vue", () => ({
  default: { props: ["training", "deployments", "anomalies"], template: '<div data-testid="activity-panel">{{ training }}|{{ deployments }}|{{ anomalies }}</div>' },
}));
vi.mock("@/components/dashboard/ResourceUsagePanel.vue", () => ({
  default: { props: ["usage", "gpuSeries", "stale"], template: '<div data-testid="resource-panel">{{ usage.map((item) => item.label + item.value).join("|") }}{{ stale ? "stale" : "" }}</div>' },
}));
vi.mock("@/components/dashboard/ServiceHealthPanel.vue", () => ({
  default: { props: ["healthBuckets", "calls", "instances"], template: '<div data-testid="service-panel">{{ calls }}|{{ instances }}|{{ healthBuckets.map((item) => item.label + item.value).join("|") }}</div>' },
}));

const overview = {
  generated_at: "2026-07-29T08:00:00Z",
  totals: { pipelines: 3, datasets: 2, training_jobs: 4, services: 2, nodes: 2, users: 0, groups: 0 },
  status_buckets: {
    pipelines: [{ label: "running", value: 1 }, { label: "success", value: 2 }],
    datasets: [{ label: "validated", value: 2 }],
    training_jobs: [{ label: "running", value: 1 }],
    services: [{ label: "running", value: 2 }],
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
    status_buckets: [{ label: "online", value: 2 }],
    freshness: { fresh: 2, stale: 0, unknown: 0, oldest_fresh_at: "2026-07-29T08:00:00Z", newest_fresh_at: "2026-07-29T08:00:01Z" },
    resource_usage: {
      cpu_utilization_percent: { value: 12.5, available: 2, unavailable: 0 },
      memory_utilization_percent: { value: 25, available: 2, unavailable: 0 },
      disk_utilization_percent: { value: 0, available: 2, unavailable: 0 },
    },
  },
  gpus: { series: [{ key: "gpu:anon", refreshed_at: "2026-07-29T08:00:01Z", utilization_percent: { value: 40, available: true }, memory_used_mib: { value: 2048, available: true }, memory_total_mib: { value: 8192, available: true }, memory_utilization_percent: { value: 25, available: true } }] },
  services: { calls: 18, instances: 2, health_buckets: [{ label: "healthy", value: 2 }], latest_health_checked_at: "2026-07-29T08:00:01Z" },
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
    expect(wrapper.get("[data-testid='summary-strip']").text()).toContain("产线数量3");
    expect(wrapper.get("[data-testid='status-row']").text()).toContain("success2");
    expect(wrapper.get("[data-testid='dataset-status-chart']").text()).toContain("数据集状态|validated2");
    expect(wrapper.get("[data-testid='asset-trend']").text()).toBe("2026-06|2026-07::2026-06|2026-07");
    expect(wrapper.get("[data-testid='activity-panel']").text()).toBe("1|2|0");
    expect(wrapper.get("[data-testid='resource-panel']").text()).toContain("CPU12.5");
    expect(wrapper.get("[data-testid='service-panel']").text()).toContain("18|2|healthy2");
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
    expect(pushMock).toHaveBeenNthCalledWith(1, "/data-preparation");
    expect(pushMock).toHaveBeenNthCalledWith(2, "/model-space");
    expect(pushMock).toHaveBeenNthCalledWith(3, "/services");
  });

  it("uses stable responsive containers and has no legacy resource list calls", () => {
    expect(workbenchSource).toContain('import AsyncState from "@/components/common/AsyncState.vue"');
    expect(workbenchSource).toContain("<AsyncState");
    expect(workbenchSource).toContain("container: workbench / inline-size");
    expect(workbenchSource).toContain("@container workbench");
    expect(workbenchSource).not.toContain("api.listDatasets");
    expect(workbenchSource).not.toContain("api.listPipelines");
    expect(workbenchSource).not.toContain("api.listServices");
    expect(workbenchSource).toMatch(/\.quick-link::before\s*\{[^}]*width:\s*44px;[^}]*height:\s*44px/s);
  });
});
