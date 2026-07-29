import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import AsyncState from "@/components/common/AsyncState.vue";
import AdminOverviewView from "@/views/admin/AdminOverviewView.vue";
import adminOverviewSource from "@/views/admin/AdminOverviewView.vue?raw";

const pushMock = vi.hoisted(() => vi.fn());
const apiMock = vi.hoisted(() => ({
  getAdminOverviewStatistics: vi.fn(),
  getAdminResourceStatistics: vi.fn(),
}));

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: pushMock }),
}));

vi.mock("@/api/client", () => ({ api: apiMock }));

vi.mock("@/components/dashboard/StatisticSummaryStrip.vue", () => ({
  default: { props: ["items"], template: '<div data-testid="summary-strip">{{ items.map((item) => item.label + item.value).join("|") }}</div>' },
}));
vi.mock("@/components/dashboard/PipelineStatusChart.vue", () => ({
  default: { props: ["buckets"], template: '<div data-testid="pipeline-chart">{{ buckets.map((item) => item.label + item.value).join("|") }}</div>' },
}));
vi.mock("@/components/dashboard/CreationTrendChart.vue", () => ({
  default: { props: ["trend"], template: '<div data-testid="creation-chart">{{ trend.labels.join("|") }}</div>' },
}));
vi.mock("@/components/dashboard/DatasetTrendChart.vue", () => ({
  default: { props: ["trend"], template: '<div data-testid="dataset-chart">{{ trend.labels.join("|") }}</div>' },
}));
vi.mock("@/components/dashboard/ResourceUsagePanel.vue", () => ({
  default: { props: ["usage", "gpuSeries", "stale"], template: '<div data-testid="resource-panel">{{ usage.map((item) => item.label + item.value).join("|") }}|{{ gpuSeries.length }}|{{ stale }}</div>' },
}));
vi.mock("@/components/dashboard/ServiceHealthPanel.vue", () => ({
  default: { props: ["healthBuckets", "calls", "instances"], template: '<div data-testid="service-panel">{{ calls }}|{{ instances }}|{{ healthBuckets.map((item) => item.label + item.value).join("|") }}</div>' },
}));

const overview = {
  generated_at: "2026-07-29T08:00:00Z",
  totals: { pipelines: 6, datasets: 4, training_jobs: 3, services: 2, nodes: 5, users: 8, groups: 2 },
  status_buckets: {
    pipelines: [{ label: "running", value: 2 }, { label: "failed", value: 1 }],
    datasets: [{ label: "validated", value: 4 }], training_jobs: [], services: [], nodes: [],
  },
  creation_trends: {
    pipelines: { labels: ["2026-06", "2026-07"], values: [2, 4] },
    datasets: { labels: ["2026-06", "2026-07"], values: [1, 3] },
    training_jobs: { labels: [], values: [] }, services: { labels: [], values: [] }, nodes: { labels: [], values: [] },
  },
  recent_failures: [
    { resource_type: "training_job", resource_id: "job-1", name: "failed-pipeline", status: "failed", updated_at: "2026-07-29T08:00:00Z" },
    { resource_type: "service", resource_id: "service-1", name: "failed-service", status: "failed", updated_at: "2026-07-29T07:00:00Z" },
  ],
};

const resources = {
  generated_at: "2026-07-29T08:00:01Z",
  staleness_threshold_seconds: 300,
  nodes: {
    status_buckets: [{ label: "online", value: 4 }],
    freshness: { fresh: 4, stale: 1, unknown: 0, oldest_fresh_at: "2026-07-29T08:00:00Z", newest_fresh_at: "2026-07-29T08:00:01Z" },
    resource_usage: {
      cpu_utilization_percent: { value: 12.5, available: 4, unavailable: 0 },
      memory_utilization_percent: { value: 25, available: 4, unavailable: 0 },
      disk_utilization_percent: { value: 0, available: 4, unavailable: 0 },
    },
  },
  gpus: { series: [{ key: "gpu:anonymous", refreshed_at: "2026-07-29T08:00:01Z", utilization_percent: { value: 40, available: true }, memory_used_mib: { value: 2048, available: true }, memory_total_mib: { value: 8192, available: true }, memory_utilization_percent: { value: 25, available: true } }] },
  services: { calls: 18, instances: 2, health_buckets: [{ label: "healthy", value: 2 }], latest_health_checked_at: "2026-07-29T08:00:01Z" },
  group_allocation_usage: { policy_count: 2, resource_pool_count: 1, active_training_runs: 1, active_service_instances: 1, active_workloads: 2, limitation: "aggregated" },
};

describe("AdminOverviewView", () => {
  const wrappers: Array<ReturnType<typeof mount>> = [];

  function mountView() {
    const wrapper = mount(AdminOverviewView);
    wrappers.push(wrapper);
    return wrapper;
  }

  beforeEach(() => {
    vi.useFakeTimers();
    vi.clearAllMocks();
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    apiMock.getAdminOverviewStatistics.mockResolvedValue(overview);
    apiMock.getAdminResourceStatistics.mockResolvedValue(resources);
  });

  afterEach(() => {
    wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it("renders only administrator aggregates, resources, group allocation and safe recent failures", async () => {
    const wrapper = mountView();
    await flushPromises();

    expect(apiMock.getAdminOverviewStatistics).toHaveBeenCalledOnce();
    expect(apiMock.getAdminResourceStatistics).toHaveBeenCalledOnce();
    expect(wrapper.get("[data-testid='summary-strip']").text()).toContain("产线数量6");
    expect(wrapper.get("[data-testid='pipeline-chart']").text()).toContain("failed1");
    expect(wrapper.get("[data-testid='dataset-chart']").text()).toContain("2026-06|2026-07");
    expect(wrapper.get("[data-testid='service-panel']").text()).toContain("18|2|healthy2");
    expect(wrapper.get("[data-testid='resource-panel']").text()).toContain("CPU12.5");
    expect(wrapper.get("[data-testid='resource-panel']").text()).toContain("|1|true");
    expect(wrapper.get("[data-testid='group-allocation']").text()).toContain("2");
    expect(wrapper.get("[data-testid='recent-failures']").text()).toContain("failed-pipeline");
  });

  it("uses the shared async state component for page-level states", () => {
    expect(adminOverviewSource).toContain('import AsyncState from "@/components/common/AsyncState.vue"');
    expect(adminOverviewSource).toContain("<AsyncState");
    expect(adminOverviewSource).toMatch(/\.section-heading button::before,\s*\.failures-section button::before\s*\{[^}]*width:\s*44px;[^}]*height:\s*44px/s);
  });

  it("routes each management section and failure row to its actionable target", async () => {
    const wrapper = mountView();
    await flushPromises();

    await wrapper.get("[data-testid='go-model-space']").trigger("click");
    await wrapper.get("[data-testid='go-data-preparation']").trigger("click");
    await wrapper.get("[data-testid='go-services']").trigger("click");
    await wrapper.get("[data-testid='go-resources']").trigger("click");
    await wrapper.get("[data-testid='failure-training_job-job-1']").trigger("click");
    await wrapper.get("[data-testid='failure-service-service-1']").trigger("click");

    expect(pushMock).toHaveBeenNthCalledWith(1, "/model-space");
    expect(pushMock).toHaveBeenNthCalledWith(2, "/data-preparation");
    expect(pushMock).toHaveBeenNthCalledWith(3, "/services");
    expect(pushMock).toHaveBeenNthCalledWith(4, "/admin/resources");
    expect(pushMock).toHaveBeenNthCalledWith(5, "/training-visualization?job=job-1");
    expect(pushMock).toHaveBeenNthCalledWith(6, "/services/service-1");
  });

  it("polls admin resources, pauses while hidden, and preserves a usable error state", async () => {
    const wrapper = mountView();
    await flushPromises();
    await vi.advanceTimersByTimeAsync(5_000);
    expect(apiMock.getAdminResourceStatistics).toHaveBeenCalledTimes(2);

    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    document.dispatchEvent(new Event("visibilitychange"));
    await vi.advanceTimersByTimeAsync(10_000);
    expect(apiMock.getAdminResourceStatistics).toHaveBeenCalledTimes(2);

    apiMock.getAdminResourceStatistics.mockRejectedValueOnce(new Error("resource unavailable"));
    Object.defineProperty(document, "hidden", { configurable: true, value: false });
    document.dispatchEvent(new Event("visibilitychange"));
    await flushPromises();
    expect(wrapper.get("[role='alert']").text()).toContain("resource unavailable");
    expect(wrapper.get("[data-testid='resource-panel']").text()).toContain("CPU12.5");
  });

  it("shows loading, error and empty states without invented platform values", async () => {
    let resolveOverview: (value: typeof overview) => void;
    apiMock.getAdminOverviewStatistics.mockImplementationOnce(() => new Promise((resolve) => { resolveOverview = resolve; }));
    const loading = mountView();
    expect(loading.get("[data-testid='admin-overview-loading']").text()).toContain("正在加载");
    expect(loading.getComponent(AsyncState).props("state")).toBe("loading");
    resolveOverview!(overview);
    await flushPromises();

    apiMock.getAdminOverviewStatistics.mockRejectedValueOnce(new Error("admin overview unavailable"));
    const failure = mountView();
    await flushPromises();
    expect(failure.get("[role='alert']").text()).toContain("admin overview unavailable");
    expect(failure.getComponent(AsyncState).props("state")).toBe("error");
    await failure.get(".async-state__retry").trigger("click");
    await flushPromises();
    expect(apiMock.getAdminOverviewStatistics).toHaveBeenCalledTimes(3);

    apiMock.getAdminOverviewStatistics.mockRejectedValueOnce(Object.assign(new Error("forbidden"), { status: 403 }));
    const denied = mountView();
    await flushPromises();
    expect(denied.getComponent(AsyncState).props("state")).toBe("denied");
    expect(denied.find("button").exists()).toBe(false);

    apiMock.getAdminOverviewStatistics.mockResolvedValueOnce({ ...overview, totals: { pipelines: 0, datasets: 0, training_jobs: 0, services: 0, nodes: 0, users: 0, groups: 0 }, recent_failures: [] });
    const empty = mountView();
    await flushPromises();
    expect(empty.get("[data-testid='admin-overview-empty']").text()).toContain("暂无平台资源");
    expect(empty.getComponent(AsyncState).props("state")).toBe("empty");
  });
});
