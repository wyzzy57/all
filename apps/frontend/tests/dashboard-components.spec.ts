import { mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ActivitySummaryPanel from "@/components/dashboard/ActivitySummaryPanel.vue";
import AssetTrendChart from "@/components/dashboard/AssetTrendChart.vue";
import CreationTrendChart from "@/components/dashboard/CreationTrendChart.vue";
import DashboardPanelHeading from "@/components/dashboard/DashboardPanelHeading.vue";
import DatasetTrendChart from "@/components/dashboard/DatasetTrendChart.vue";
import PipelineStatusChart from "@/components/dashboard/PipelineStatusChart.vue";
import ResourceUsagePanel from "@/components/dashboard/ResourceUsagePanel.vue";
import ServiceHealthPanel from "@/components/dashboard/ServiceHealthPanel.vue";
import StatisticSummaryStrip from "@/components/dashboard/StatisticSummaryStrip.vue";
import StatusSummaryRow from "@/components/dashboard/StatusSummaryRow.vue";

const echartsMocks = vi.hoisted(() => ({
  dispose: vi.fn(),
  init: vi.fn(),
  resize: vi.fn(),
  setOption: vi.fn(),
  use: vi.fn(),
}));

let resizeObserverCallback: ResizeObserverCallback;
const resizeObserverObserve = vi.fn();
const resizeObserverDisconnect = vi.fn();

vi.mock("echarts/core", () => ({
  init: echartsMocks.init,
  use: echartsMocks.use,
}));

function chartInstance() {
  return {
    dispose: echartsMocks.dispose,
    resize: echartsMocks.resize,
    setOption: echartsMocks.setOption,
  };
}

function latestOption() {
  return echartsMocks.setOption.mock.calls[echartsMocks.setOption.mock.calls.length - 1][0];
}

const statusBuckets = [
  { label: "\u914d\u7f6e\u4e2d", value: 3 },
  { label: "<img src=x onerror=alert(1)>", value: 2 },
];

describe("dashboard components", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    echartsMocks.init.mockReturnValue(chartInstance());
    vi.stubGlobal("ResizeObserver", class {
      constructor(callback: ResizeObserverCallback) {
        resizeObserverCallback = callback;
      }

      observe = resizeObserverObserve;
      disconnect = resizeObserverDisconnect;
      unobserve = vi.fn();
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders compact statistic values with units and an explicit empty state", () => {
    const wrapper = mount(StatisticSummaryStrip, {
      props: {
        items: [
          { label: "\u4ea7\u7ebf\u6570\u91cf", value: 12, unit: "\u4e2a" },
          { label: "\u6570\u636e\u96c6", value: 4, unit: "\u4e2a" },
        ],
      },
    });

    expect(wrapper.text()).toContain("12\u4e2a");
    expect(wrapper.text()).toContain("\u4ea7\u7ebf\u6570\u91cf");
    expect(wrapper.get(".statistic-summary-strip").attributes("role")).toBe("list");

    const empty = mount(StatisticSummaryStrip, { props: { items: [] } });
    expect(empty.get("[data-testid='summary-empty']").text()).toContain("\u6682\u65e0\u7edf\u8ba1\u6570\u636e");
  });

  it("renders pipeline status legend, safe tooltip, hover emphasis, and first-mount animation only", async () => {
    const wrapper = mount(PipelineStatusChart, { props: { buckets: statusBuckets } });
    const firstOption = latestOption();
    const pie = firstOption.series[0];

    expect(firstOption.animation).toBe(true);
    expect(firstOption.aria).toEqual({ enabled: true });
    expect(firstOption.legend.data).toEqual(statusBuckets.map((item) => item.label));
    expect(pie.emphasis).toEqual(expect.objectContaining({ scale: true }));
    expect(pie.selectedOffset).toBeUndefined();
    expect(pie.radius).toEqual(expect.any(Array));
    expect(wrapper.get(".dashboard-chart").attributes("style")).toContain("aspect-ratio");

    const tooltip = firstOption.tooltip.formatter({ name: statusBuckets[1].label, value: 2, marker: "" });
    expect(tooltip).toContain("&lt;img");
    expect(tooltip).not.toContain("<img");

    await wrapper.setProps({ buckets: [{ label: "\u8fd0\u884c\u6210\u529f", value: 7 }] });
    expect(latestOption().animation).toBe(false);
    wrapper.unmount();
    expect(echartsMocks.dispose).toHaveBeenCalledTimes(1);
  });

  it("renders no-data placeholders instead of initializing empty status and trend charts", () => {
    const status = mount(PipelineStatusChart, { props: { buckets: [] } });
    const trend = mount(CreationTrendChart, { props: { trend: { labels: [], values: [] } } });
    const dataset = mount(DatasetTrendChart, { props: { trend: { labels: [], values: [] } } });

    expect(status.find("[data-testid='pipeline-status-empty']").exists()).toBe(true);
    expect(trend.find("[data-testid='creation-trend-empty']").exists()).toBe(true);
    expect(dataset.find("[data-testid='dataset-trend-empty']").exists()).toBe(true);
  });

  it("initializes charts when asynchronously loaded data replaces an empty state", async () => {
    const wrapper = mount(PipelineStatusChart, { props: { buckets: [] } });
    expect(echartsMocks.init).not.toHaveBeenCalled();

    await wrapper.setProps({ buckets: [{ label: "运行成功", value: 2 }] });

    expect(echartsMocks.init).toHaveBeenCalledTimes(1);
    expect(wrapper.get(".sr-only").text()).toContain("运行成功：2");
  });

  it("renders trend labels and updates data without replaying animation", async () => {
    const wrapper = mount(CreationTrendChart, {
      props: { trend: { labels: ["2026-06", "2026-07"], values: [4, 8] } },
    });
    const firstOption = latestOption();

    expect(firstOption.animation).toBe(true);
    expect(firstOption.aria).toEqual({ enabled: true });
    expect(firstOption.xAxis.data).toEqual(["2026-06", "2026-07"]);
    expect(firstOption.series[0]).toEqual(expect.objectContaining({ type: "bar", data: [4, 8] }));

    await wrapper.setProps({ trend: { labels: ["2026-08"], values: [10] } });
    expect(latestOption()).toEqual(expect.objectContaining({ animation: false, series: [expect.objectContaining({ data: [10] })] }));
  });

  it("uses a separate stable dataset trend chart and responds to ResizeObserver", () => {
    const wrapper = mount(DatasetTrendChart, {
      props: { trend: { labels: ["2026-06"], values: [9] } },
    });

    expect(latestOption().series[0]).toEqual(expect.objectContaining({ type: "bar", data: [9] }));
    expect(resizeObserverObserve).toHaveBeenCalledWith(wrapper.get(".dashboard-chart").element);
    resizeObserverCallback([], {} as ResizeObserver);
    expect(echartsMocks.resize).toHaveBeenCalledTimes(1);
  });

  it("aligns asset trends to a shared union time axis", () => {
    mount(AssetTrendChart, {
      props: {
        pipelineTrend: { labels: ["2026-05", "2026-07"], values: [3, 7] },
        datasetTrend: { labels: ["2026-06", "2026-07"], values: [5, 9] },
      },
    });

    const chartOption = latestOption();
    expect(chartOption.xAxis.data).toEqual(["2026-05", "2026-06", "2026-07"]);
    expect(chartOption.series).toEqual([
      expect.objectContaining({ name: "\u4ea7\u7ebf", data: [3, 0, 7] }),
      expect.objectContaining({ name: "\u6570\u636e\u96c6", data: [0, 5, 9] }),
    ]);
  });

  it("animates the asset trend only on its initial render", async () => {
    const wrapper = mount(AssetTrendChart, {
      props: {
        pipelineTrend: { labels: ["2026-07"], values: [7] },
        datasetTrend: { labels: ["2026-07"], values: [9] },
      },
    });

    expect(latestOption()).toEqual(expect.objectContaining({ animation: true, aria: { enabled: true } }));

    await wrapper.setProps({
      pipelineTrend: { labels: ["2026-08"], values: [8] },
      datasetTrend: { labels: ["2026-08"], values: [10] },
    });

    expect(latestOption()).toEqual(expect.objectContaining({ animation: false }));
  });

  it("renders an explicit empty asset trend without initializing ECharts", () => {
    const wrapper = mount(AssetTrendChart, {
      props: {
        pipelineTrend: { labels: [], values: [] },
        datasetTrend: { labels: [], values: [] },
      },
    });

    expect(wrapper.get("[data-testid='asset-trend-empty']").text()).toContain("\u6682\u65e0\u8d44\u4ea7\u8d8b\u52bf\u6570\u636e");
    expect(echartsMocks.init).not.toHaveBeenCalled();
  });

  it("renders status labels and numeric values as a semantic list", () => {
    const wrapper = mount(StatusSummaryRow, {
      props: {
        items: [
          { label: "\u8fd0\u884c\u4e2d", value: 3 },
          { label: "\u6210\u529f", value: 12 },
        ],
      },
    });

    const list = wrapper.get("[role='list']");
    expect(list.text()).toContain("\u8fd0\u884c\u4e2d");
    expect(list.text()).toContain("3");
    expect(list.text()).toContain("\u6210\u529f");
    expect(list.text()).toContain("12");
  });

  it("renders an explicit empty status summary", () => {
    const wrapper = mount(StatusSummaryRow, { props: { items: [] } });

    expect(wrapper.get("[data-testid='status-summary-empty']").text()).toContain("\u6682\u65e0\u72b6\u6001\u6570\u636e");
  });

  it("renders training, deployment, and anomaly activity values", () => {
    const wrapper = mount(ActivitySummaryPanel, {
      props: { training: 4, deployments: 2, anomalies: 1 },
    });

    const values = wrapper.get("[role='list']");
    expect(values.text()).toContain("\u8bad\u7ec3");
    expect(values.text()).toContain("4");
    expect(values.text()).toContain("\u90e8\u7f72");
    expect(values.text()).toContain("2");
    expect(values.text()).toContain("\u5f02\u5e38");
    expect(values.text()).toContain("1");
  });

  it("keeps zero activity values accessible alongside an explicit empty message", () => {
    const wrapper = mount(ActivitySummaryPanel, {
      props: { training: 0, deployments: 0, anomalies: 0 },
    });

    expect(wrapper.get("[data-testid='activity-summary-empty']").text()).toContain("\u6682\u65e0\u6d3b\u8dc3\u4efb\u52a1");
    const values = wrapper.get("[role='list']").text();
    expect(values).toContain("\u8bad\u7ec3");
    expect(values).toContain("\u90e8\u7f72");
    expect(values).toContain("\u5f02\u5e38");
    expect(values.match(/0/g)).toHaveLength(3);
  });

  it("renders a panel heading with optional metadata and description", () => {
    const wrapper = mount(DashboardPanelHeading, {
      props: {
        title: "\u8d44\u4ea7\u8d8b\u52bf",
        metadata: "\u8fd1 30 \u5929",
        description: "\u4ea7\u7ebf\u4e0e\u6570\u636e\u96c6\u521b\u5efa\u91cf",
      },
    });

    expect(wrapper.get("h2").text()).toBe("\u8d44\u4ea7\u8d8b\u52bf");
    expect(wrapper.text()).toContain("\u8fd1 30 \u5929");
    expect(wrapper.text()).toContain("\u4ea7\u7ebf\u4e0e\u6570\u636e\u96c6\u521b\u5efa\u91cf");
    expect(wrapper.find("button").exists()).toBe(false);
  });

  it("labels the optional panel action and emits it when clicked", async () => {
    const wrapper = mount(DashboardPanelHeading, {
      props: { title: "\u670d\u52a1\u72b6\u6001", actionLabel: "\u67e5\u770b\u670d\u52a1" },
    });

    const action = wrapper.get("button");
    expect(action.attributes("aria-label")).toBe("\u67e5\u770b\u670d\u52a1");
    await action.trigger("click");
    expect(wrapper.emitted("action")).toHaveLength(1);
  });

  it("renders available resource usage, unavailable telemetry, stale state, and GPU series", () => {
    const wrapper = mount(ResourceUsagePanel, {
      props: {
        usage: [
          { label: "CPU", value: 0, unit: "%", available: true, unavailable: false },
          { label: "\u5185\u5b58", value: null, unit: "%", available: false, unavailable: true },
        ],
        gpuSeries: [{ id: "gpu-0", label: "GPU 0", value: 64, unit: "%", available: true, unavailable: false }],
        stale: true,
      },
    });

    expect(wrapper.text()).toContain("0%");
    expect(wrapper.text()).toContain("\u6682\u65e0\u9065\u6d4b\u6570\u636e");
    expect(wrapper.text()).toContain("GPU 0");
    expect(wrapper.get("[data-testid='resource-stale']").text()).toContain("\u9010\u6e10\u53d8\u65e7");
    expect(wrapper.get(".resource-usage-panel").attributes("style")).toContain("min-height");
  });

  it("renders service health buckets, calls, and instances with a no-data state", () => {
    const wrapper = mount(ServiceHealthPanel, {
      props: {
        healthBuckets: [{ label: "\u5065\u5eb7", value: 6 }, { label: "\u5f02\u5e38", value: 1 }],
        calls: 38,
        instances: 7,
      },
    });

    expect(wrapper.text()).toContain("38");
    expect(wrapper.text()).toContain("7");
    expect(latestOption().series[0]).toEqual(expect.objectContaining({ type: "pie" }));

    const empty = mount(ServiceHealthPanel, { props: { healthBuckets: [], calls: 0, instances: 0 } });
    expect(empty.find("[data-testid='service-health-empty']").exists()).toBe(true);
  });
});
