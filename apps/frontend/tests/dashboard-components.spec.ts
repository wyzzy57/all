import { mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ActivitySummaryPanel from "@/components/dashboard/ActivitySummaryPanel.vue";
import AssetTrendChart from "@/components/dashboard/AssetTrendChart.vue";
import assetTrendChartSource from "@/components/dashboard/AssetTrendChart.vue?raw";
import CreationTrendChart from "@/components/dashboard/CreationTrendChart.vue";
import DashboardPanelHeading from "@/components/dashboard/DashboardPanelHeading.vue";
import DatasetTrendChart from "@/components/dashboard/DatasetTrendChart.vue";
import PipelineStatusChart from "@/components/dashboard/PipelineStatusChart.vue";
import pipelineStatusChartSource from "@/components/dashboard/PipelineStatusChart.vue?raw";
import ResourceUsagePanel from "@/components/dashboard/ResourceUsagePanel.vue";
import resourceUsagePanelSource from "@/components/dashboard/ResourceUsagePanel.vue?raw";
import ServiceHealthPanel from "@/components/dashboard/ServiceHealthPanel.vue";
import serviceHealthPanelSource from "@/components/dashboard/ServiceHealthPanel.vue?raw";
import StatisticSummaryStrip from "@/components/dashboard/StatisticSummaryStrip.vue";
import statisticSummaryStripSource from "@/components/dashboard/StatisticSummaryStrip.vue?raw";
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

  it("defines a flat statistic strip with stable responsive columns", () => {
    expect(statisticSummaryStripSource).toMatch(/\.statistic-summary-strip\s*\{[^}]*container:\s*statistic-summary\s*\/\s*inline-size/);
    expect(statisticSummaryStripSource).toMatch(/\.statistic-summary-strip\s*\{[^}]*background:\s*transparent/);
    expect(statisticSummaryStripSource).toMatch(/\.statistic-summary-grid\s*\{[^}]*grid-template-columns:\s*repeat\(5,\s*minmax\(0,\s*1fr\)\)/);
    expect(statisticSummaryStripSource).toMatch(/@container statistic-summary \(max-width:\s*720px\)[\s\S]*?\.statistic-summary-grid\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/);
    expect(statisticSummaryStripSource).toMatch(/@container statistic-summary \(max-width:\s*420px\)[\s\S]*?\.statistic-summary-grid\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/);
    expect(statisticSummaryStripSource).not.toMatch(/@media \(max-width:\s*(?:720|420)px\)/);
    expect(statisticSummaryStripSource).not.toMatch(/font-size:\s*[^;]*(?:vw|clamp\()/);
  });

  it("wraps seven admin metrics with coherent wide-row separators", () => {
    const wrapper = mount(StatisticSummaryStrip, {
      props: {
        items: Array.from({ length: 7 }, (_, index) => ({ label: `指标 ${index + 1}`, value: index + 1 })),
      },
    });
    const items = wrapper.findAll("[role='listitem']");
    const [wideStyles] = statisticSummaryStripSource.split("@container statistic-summary (max-width: 720px)");

    expect(wrapper.find(".statistic-summary-grid").exists()).toBe(true);
    expect(items).toHaveLength(7);
    expect(items[5].text()).toContain("指标 6");
    expect(items[6].text()).toContain("指标 7");
    expect(wideStyles).toMatch(/\.summary-item:nth-child\(5n \+ 1\)\s*\{\s*border-left:\s*0/);
    expect(wideStyles).toMatch(/\.summary-item:nth-child\(n \+ 6\)\s*\{\s*border-top:\s*1px solid #dfe3e8/);
  });

  it("uses transparent roots and borderless neutral empty states", () => {
    const componentSources = [
      statisticSummaryStripSource,
      resourceUsagePanelSource,
      serviceHealthPanelSource,
      pipelineStatusChartSource,
    ];

    componentSources.forEach((source) => {
      expect(source).toMatch(/background:\s*transparent/);
      expect(source).not.toMatch(/border:\s*1px dashed/);
      expect(source).not.toMatch(/#8b98aa/i);
    });
    expect(componentSources.filter((source) => /background:\s*rgb\(255 255 255 \/ 34%\)/.test(source))).toHaveLength(4);
  });

  it("renders pipeline status legend, safe tooltip, hover emphasis, and first-mount animation only", async () => {
    const wrapper = mount(PipelineStatusChart, { props: { buckets: statusBuckets } });
    const firstOption = latestOption();
    const pie = firstOption.series[0];

    expect(firstOption.animation).toBe(true);
    expect(firstOption.aria).toEqual({ enabled: true });
    expect(firstOption.color).toEqual(["#2563eb", "#16835b", "#b26a00", "#c2413a", "#6b7280"]);
    expect(firstOption.legend.data).toEqual(statusBuckets.map((item) => item.label));
    expect(pie.emphasis).toEqual(expect.objectContaining({ scale: true }));
    expect(pie.selectedMode).toBe(false);
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

  it("translates backend status values into Chinese business labels", () => {
    const summary = mount(StatusSummaryRow, {
      props: { buckets: [{ label: "running", value: 2 }, { label: "success", value: 1 }] },
    });
    expect(summary.text()).toContain("运行中");
    expect(summary.text()).toContain("运行成功");
    expect(summary.text()).not.toContain("running");

    const pipeline = mount(PipelineStatusChart, {
      props: { buckets: [{ label: "draft", value: 2 }, { label: "success", value: 1 }] },
    });
    expect(latestOption().legend.data).toEqual(["配置中", "运行成功"]);
    expect(pipeline.get(".sr-only").text()).toContain("配置中");

    const service = mount(ServiceHealthPanel, {
      props: {
        healthBuckets: [{ label: "healthy", value: 3 }, { label: "unhealthy", value: 1 }],
        calls: 4,
        instances: 2,
      },
    });
    expect(latestOption().legend.data).toEqual(["健康", "异常"]);
    expect(service.get(".health-summary").text()).toContain("健康");
    expect(service.get(".health-summary").text()).toContain("异常");
  });

  it("disables pipeline status animation when reduced motion is preferred", () => {
    const matchMedia = vi.fn((query: string) => ({
      matches: true,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
    vi.stubGlobal("matchMedia", matchMedia);

    mount(PipelineStatusChart, { props: { buckets: [{ label: "运行成功", value: 2 }] } });

    expect(matchMedia).toHaveBeenCalledWith("(prefers-reduced-motion: reduce)");
    expect(latestOption()).toEqual(expect.objectContaining({ animation: false, animationDuration: 0 }));
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

  it("aligns asset trends to a shared insertion-order union time axis", () => {
    const wrapper = mount(AssetTrendChart, {
      props: {
        pipelineTrend: { labels: ["2026-05", "2026-07"], values: [3, 7] },
        datasetTrend: { labels: ["2026-06", "2026-07"], values: [5, 9] },
      },
    });

    const chartOption = latestOption();
    expect(chartOption.aria).toEqual({ enabled: true });
    expect(chartOption.xAxis.data).toEqual(["2026-05", "2026-07", "2026-06"]);
    expect(chartOption.series).toEqual([
      expect.objectContaining({ name: "\u4ea7\u7ebf", data: [3, 7, 0] }),
      expect.objectContaining({ name: "\u6570\u636e\u96c6", data: [0, 9, 5] }),
    ]);

    const accessibleSummary = wrapper.get("[data-testid='asset-trend-a11y']");
    expect(accessibleSummary.element.closest("[aria-hidden='true']")).toBeNull();
    const summaryText = accessibleSummary.text().replace(/\s+/g, " ");
    expect(summaryText).toMatch(/2026-05.*\u4ea7\u7ebf.*3.*\u6570\u636e\u96c6.*0/);
    expect(summaryText).toMatch(/2026-07.*\u4ea7\u7ebf.*7.*\u6570\u636e\u96c6.*9/);
    expect(summaryText).toMatch(/2026-06.*\u4ea7\u7ebf.*0.*\u6570\u636e\u96c6.*5/);
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

  it("disables asset trend animation when reduced motion is preferred", () => {
    const matchMedia = vi.fn((query: string) => ({
      matches: true,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
    vi.stubGlobal("matchMedia", matchMedia);

    mount(AssetTrendChart, {
      props: {
        pipelineTrend: { labels: ["2026-07"], values: [7] },
        datasetTrend: { labels: ["2026-07"], values: [9] },
      },
    });

    expect(matchMedia).toHaveBeenCalledWith("(prefers-reduced-motion: reduce)");
    expect(latestOption()).toEqual(expect.objectContaining({ animation: false, animationDuration: 0 }));
  });

  it("makes the non-empty asset trend keyboard focusable with a visible focus indicator", () => {
    const wrapper = mount(AssetTrendChart, {
      props: {
        pipelineTrend: { labels: ["2026-07"], values: [7] },
        datasetTrend: { labels: ["2026-07"], values: [9] },
      },
    });

    const chart = wrapper.get(".dashboard-chart");
    expect(chart.attributes("tabindex")).toBe("0");
    expect(chart.attributes("role")).toBe("img");
    expect(chart.attributes("aria-label")).toContain("\u8d44\u4ea7\u8d8b\u52bf");
    expect(chart.element.closest("[aria-hidden='true']")).toBeNull();
    expect(assetTrendChartSource).toMatch(/\.dashboard-chart:focus-visible\s*\{[\s\S]*?outline:/);
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

  it("keeps the asset trend canvas and empty state within the compact workbench row", () => {
    const wrapper = mount(AssetTrendChart, {
      props: {
        pipelineTrend: { labels: ["2026-07"], values: [7] },
        datasetTrend: { labels: ["2026-07"], values: [9] },
      },
    });
    const canvasMinHeight = Number(assetTrendChartSource.match(
      /(?=[^{]*\.asset-trend-canvas)[^{]*\{[^}]*min-height:\s*(\d+)px/s,
    )?.[1]);
    const emptyMinHeight = Number(assetTrendChartSource.match(
      /(?=[^{]*\.asset-trend-empty)[^{]*\{[^}]*min-height:\s*(\d+)px/s,
    )?.[1]);

    expect(wrapper.find(".asset-trend-canvas").exists()).toBe(true);
    expect(canvasMinHeight).toBeGreaterThanOrEqual(160);
    expect(canvasMinHeight).toBeLessThanOrEqual(180);
    expect(emptyMinHeight).toBeGreaterThanOrEqual(160);
    expect(emptyMinHeight).toBeLessThanOrEqual(180);
    expect(assetTrendChartSource).not.toMatch(/min-height:\s*250px/);
  });

  it("renders status labels and numeric values as a semantic list", () => {
    const wrapper = mount(StatusSummaryRow, {
      props: {
        buckets: [
          { label: "\u8fd0\u884c\u4e2d", value: 3 },
          { label: "\u6210\u529f", value: 12 },
        ],
      },
    });

    const list = wrapper.get("[role='list']");
    const items = list.findAll("[role='listitem']");
    expect(list.attributes("aria-hidden")).not.toBe("true");
    expect(list.element.closest("[aria-hidden='true']")).toBeNull();
    expect(items).toHaveLength(2);
    expect(items[0].text()).toContain("\u8fd0\u884c\u4e2d");
    expect(items[1].text()).toContain("\u6210\u529f");
    expect(items.map((item) => item.get("strong").text())).toEqual(["3", "12"]);
    items.forEach((item) => {
      expect(item.attributes("aria-hidden")).not.toBe("true");
      expect(item.get("strong").attributes("aria-hidden")).not.toBe("true");
    });
  });

  it("renders an explicit empty status summary", () => {
    const wrapper = mount(StatusSummaryRow, { props: { buckets: [] } });

    expect(wrapper.get("[data-testid='status-summary-empty']").text()).toContain("\u6682\u65e0\u72b6\u6001\u6570\u636e");
  });

  it("renders training, deployment, and anomaly activity values", () => {
    const wrapper = mount(ActivitySummaryPanel, {
      props: { training: 4, deployments: 2, anomalies: 1 },
    });

    const values = wrapper.get("[role='list']");
    const items = values.findAll("[role='listitem']");
    expect(values.attributes("aria-hidden")).not.toBe("true");
    expect(values.element.closest("[aria-hidden='true']")).toBeNull();
    expect(items).toHaveLength(3);
    expect(items[0].text()).toContain("\u8bad\u7ec3");
    expect(items[1].text()).toContain("\u90e8\u7f72");
    expect(items[2].text()).toContain("\u5f02\u5e38");
    expect(items.map((item) => item.get("dd").text())).toEqual(["4", "2", "1"]);
    items.forEach((item) => {
      expect(item.attributes("aria-hidden")).not.toBe("true");
      expect(item.get("dd").attributes("aria-hidden")).not.toBe("true");
    });
  });

  it("keeps zero activity values accessible alongside an explicit empty message", () => {
    const wrapper = mount(ActivitySummaryPanel, {
      props: { training: 0, deployments: 0, anomalies: 0 },
    });

    expect(wrapper.get("[data-testid='activity-summary-empty']").text()).toContain("\u6682\u65e0\u6d3b\u52a8\u6570\u636e");
    const list = wrapper.get("[role='list']");
    const items = list.findAll("[role='listitem']");
    expect(list.attributes("aria-hidden")).not.toBe("true");
    expect(list.element.closest("[aria-hidden='true']")).toBeNull();
    expect(items).toHaveLength(3);
    expect(items[0].text()).toContain("\u8bad\u7ec3");
    expect(items[1].text()).toContain("\u90e8\u7f72");
    expect(items[2].text()).toContain("\u5f02\u5e38");
    items.forEach((item) => {
      expect(item.get("dd").text()).toBe("0");
      expect(item.attributes("aria-hidden")).not.toBe("true");
      expect(item.get("dd").attributes("aria-hidden")).not.toBe("true");
    });
  });

  it("renders a panel heading with optional metadata and description", () => {
    const wrapper = mount(DashboardPanelHeading, {
      props: {
        title: "\u8d44\u4ea7\u8d8b\u52bf",
        meta: "\u8fd1 30 \u5929",
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
          { label: "\u78c1\u76d8", value: 42, unit: "%", available: true, unavailable: true },
          { label: "\u5f02\u5e38\u9065\u6d4b", value: Number.NaN, unit: "%", available: true, unavailable: false },
        ],
        gpuSeries: [{ id: "gpu-0", label: "GPU 0", value: 64, unit: "%", available: true, unavailable: false }],
        stale: true,
      },
    });

    expect(wrapper.text()).toContain("0%");
    expect(wrapper.text()).toContain("\u6682\u65e0\u9065\u6d4b\u6570\u636e");
    expect(wrapper.text()).toContain("GPU 0");
    expect(wrapper.text()).toContain("\u90e8\u5206\u9065\u6d4b\u4e0d\u53ef\u7528");
    expect(wrapper.get("[data-testid='resource-stale']").text()).toContain("\u9010\u6e10\u53d8\u65e7");
    expect(wrapper.get(".resource-usage-panel").attributes("style")).toContain("min-height");

    const meters = wrapper.findAll("[role='meter']");
    const tracks = wrapper.findAll(".usage-track");
    expect(meters).toHaveLength(3);
    meters.forEach((meter) => {
      expect(meter.attributes("aria-valuenow")).toBeDefined();
      expect(meter.attributes("aria-valuemin")).toBe("0");
      expect(meter.attributes("aria-valuemax")).toBe("100");
      expect(meter.attributes("aria-hidden")).toBeUndefined();
    });
    expect(meters[0].attributes("aria-valuenow")).toBe("0");
    expect(meters[0].attributes("aria-valuetext")).toBe("0%");
    expect(meters[1].attributes("aria-valuenow")).toBe("42");
    expect(meters[1].attributes("aria-valuetext")).toContain("42%");
    expect(meters[1].attributes("aria-valuetext")).toContain("\u90e8\u5206\u9065\u6d4b\u4e0d\u53ef\u7528");
    [tracks[1], tracks[3]].forEach((track) => {
      expect(track.attributes("role")).toBeUndefined();
      expect(track.attributes("aria-valuenow")).toBeUndefined();
      expect(track.attributes("aria-valuemin")).toBeUndefined();
      expect(track.attributes("aria-valuemax")).toBeUndefined();
      expect(track.attributes("aria-hidden")).toBe("true");
      expect(track.get("i").attributes("style")).toBe("width: 0%;");
      expect(track.get("i").classes()).toContain("unavailable");
    });
    expect(resourceUsagePanelSource).toMatch(/\.usage-track\s*\{[\s\S]*?height:\s*8px/);
    expect(resourceUsagePanelSource).toMatch(/@media \(prefers-reduced-motion:\s*reduce\)[\s\S]*?transition:\s*none/);
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
    expect(wrapper.findAll(".health-summary [role='listitem']").map((item) => item.text())).toEqual(["\u5065\u5eb76", "\u5f02\u5e381"]);

    const empty = mount(ServiceHealthPanel, { props: { healthBuckets: [], calls: 0, instances: 0 } });
    expect(empty.find("[data-testid='service-health-empty']").exists()).toBe(true);
  });

  it("maps service health colors from normalized labels instead of bucket order", () => {
    const wrapper = mount(ServiceHealthPanel, {
      props: {
        healthBuckets: [
          { label: "healthy", value: 6 },
          { label: "Unhealthy", value: 2 },
          { label: "unknown", value: 1 },
        ],
        calls: 9,
        instances: 3,
      },
    });

    expect(latestOption().series[0].data.map((item: { itemStyle?: { color: string } }) => item.itemStyle?.color)).toEqual([
      "#16835b",
      "#c2413a",
      "#6b7280",
    ]);
    const dots = wrapper.findAll(".health-summary i");
    expect(dots[0].attributes("style")).toContain("background-color: rgb(22, 131, 91)");
    expect(dots[1].attributes("style")).toContain("background-color: rgb(194, 65, 58)");
    expect(dots[2].attributes("style")).toContain("background-color: rgb(107, 114, 128)");
  });

  it("uses a named container for service health responsive layout", () => {
    expect(serviceHealthPanelSource).toMatch(/\.service-health-panel\s*\{[^}]*container:\s*service-health\s*\/\s*inline-size/);
    expect(serviceHealthPanelSource).toMatch(/@container service-health \(max-width:\s*520px\)[\s\S]*?\.health-visual\s*\{[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)/);
    expect(serviceHealthPanelSource).not.toMatch(/@media \(max-width:\s*520px\)/);
  });

  it("disables service health animation when reduced motion is preferred", () => {
    const matchMedia = vi.fn((query: string) => ({
      matches: true,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    }));
    vi.stubGlobal("matchMedia", matchMedia);

    mount(ServiceHealthPanel, {
      props: { healthBuckets: [{ label: "healthy", value: 1 }], calls: 1, instances: 1 },
    });

    expect(matchMedia).toHaveBeenCalledWith("(prefers-reduced-motion: reduce)");
    expect(latestOption()).toEqual(expect.objectContaining({ animation: false, animationDuration: 0 }));
  });
});
