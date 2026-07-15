import { mount, type VueWrapper } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, expectTypeOf, it, vi } from "vitest";

import {
  api,
  type TrainingObservabilityGraph,
  type TrainingObservabilityHistogram,
  type TrainingObservabilitySummary
} from "@/api/client";
import MetricLineChart from "@/components/training/MetricLineChart.vue";

const echartsMocks = vi.hoisted(() => ({
  dispose: vi.fn(),
  init: vi.fn(),
  resize: vi.fn(),
  setOption: vi.fn(),
  use: vi.fn()
}));

let resizeObserverCallback: ResizeObserverCallback;
const resizeObserverObserve = vi.fn();
const resizeObserverDisconnect = vi.fn();

vi.mock("echarts/core", () => ({
  init: echartsMocks.init,
  use: echartsMocks.use
}));

function chartInstance() {
  return {
    dispose: echartsMocks.dispose,
    resize: echartsMocks.resize,
    setOption: echartsMocks.setOption
  };
}

async function expectChartLifecycle(
  wrapper: VueWrapper,
  updateProps: Record<string, unknown>,
  preservedOptionKeys: string[],
  reactiveSetOptionOptions?: Record<string, unknown>
) {
  expect(echartsMocks.init).toHaveBeenCalledTimes(1);

  const resizeCallsBefore = echartsMocks.resize.mock.calls.length;
  window.dispatchEvent(new Event("resize"));
  expect(echartsMocks.resize).toHaveBeenCalledTimes(resizeCallsBefore + 1);

  const callsBeforeUpdate = echartsMocks.setOption.mock.calls.length;
  await wrapper.setProps(updateProps);
  expect(echartsMocks.setOption).toHaveBeenCalledTimes(callsBeforeUpdate + 1);
  const reactiveUpdateCall = echartsMocks.setOption.mock.calls[echartsMocks.setOption.mock.calls.length - 1];

  wrapper.unmount();
  expect(echartsMocks.dispose).toHaveBeenCalledTimes(1);

  if (reactiveSetOptionOptions) {
    expect(reactiveUpdateCall).toHaveLength(2);
    expect(reactiveUpdateCall[1]).toEqual(reactiveSetOptionOptions);
  } else {
    expect(reactiveUpdateCall).toHaveLength(1);
  }
  preservedOptionKeys.forEach((key) => {
    expect(reactiveUpdateCall[0]).not.toHaveProperty(key);
  });
}

describe("training chart components", () => {
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

  it("renders metric DTO points and responds to its lifecycle", async () => {
    const series = {
      "train.box_loss": [
        { step: 1, value: 1.4, timestamp: 100 },
        { step: 2, value: 0.9, timestamp: 110 }
      ],
      "metrics.map50": [{ step: 2, value: 0.81, timestamp: 110 }]
    };
    const wrapper = mount(MetricLineChart, { props: { series, unit: "loss", height: "300px" } });

    expect(echartsMocks.setOption).toHaveBeenCalledWith(
      expect.objectContaining({
        xAxis: expect.objectContaining({ type: "value" }),
        series: expect.arrayContaining([
          expect.objectContaining({
            name: "train.box_loss",
            type: "line",
            data: [
              { value: [1, 1.4], rawValue: 1.4 },
              { value: [2, 0.9], rawValue: 0.9 }
            ]
          })
        ])
      }),
      true
    );
    expect(wrapper.get(".training-chart").attributes("style")).toContain("height: 300px");

    await expectChartLifecycle(wrapper, {
      series: { "train.box_loss": [{ step: 3, value: 0.7, timestamp: 120 }] }
    }, ["dataZoom", "legend", "toolbox"], { replaceMerge: ["series"] });
    const metricUpdateCall = echartsMocks.setOption.mock.calls[echartsMocks.setOption.mock.calls.length - 1];
    expect(metricUpdateCall[0].series.map((item: { name: string }) => item.name)).toEqual(["train.box_loss"]);
  });

  it("uses metric axis bounds and preserves raw values while smoothing display data", () => {
    const wrapper = mount(MetricLineChart, {
      props: {
        series: {
          mAP50: [
            { step: 1, value: 0.2, timestamp: 100 },
            { step: 2, value: 0.8, timestamp: 110 },
          ],
        },
        axisMin: 0,
        axisMax: 1,
        valueFormat: "ratio",
        smoothing: 0.5,
      },
    });

    expect(echartsMocks.setOption).toHaveBeenCalledWith(
      expect.objectContaining({
        yAxis: expect.objectContaining({ min: 0, max: 1 }),
        series: [expect.objectContaining({
          data: [
            expect.objectContaining({ value: [1, 0.2], rawValue: 0.2 }),
            expect.objectContaining({ value: [2, 0.5], rawValue: 0.8 }),
          ],
        })],
      }),
      true,
    );

    wrapper.unmount();
  });

  it("resizes with its observed chart container and disconnects on unmount", () => {
    const wrapper = mount(MetricLineChart, {
      props: { series: { loss: [{ step: 1, value: 1, timestamp: 100 }] } },
    });

    expect(resizeObserverObserve).toHaveBeenCalledWith(wrapper.get(".training-chart").element);
    resizeObserverCallback([], {} as ResizeObserver);
    expect(echartsMocks.resize).toHaveBeenCalledTimes(1);

    wrapper.unmount();
    expect(resizeObserverDisconnect).toHaveBeenCalledTimes(1);
  });

  it("escapes user-controlled series names in HTML tooltips", () => {
    const wrapper = mount(MetricLineChart, {
      props: { series: { '<img src=x onerror="alert(1)">': [{ step: 1, value: 1, timestamp: 100 }] } },
    });
    const option = echartsMocks.setOption.mock.calls[0][0];
    const tooltip = option.tooltip.formatter([{
      marker: "<span></span>",
      seriesName: '<img src=x onerror="alert(1)">',
      data: { rawValue: 1 },
      value: [1, 1],
    }]);

    expect(tooltip).not.toContain('<img src=x onerror="alert(1)">');
    expect(tooltip).toContain("&lt;img src=x onerror=&quot;alert(1)&quot;&gt;");
    wrapper.unmount();
  });

});

describe("training observability API client", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    fetchMock.mockReset();
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({})
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("encodes scalar keys as one comma-separated query value", async () => {
    await api.getTrainingObservabilityScalars("job-1", {
      keys: ["train.box_loss", "metrics.map50"],
      start_step: 2,
      end_step: 20,
      max_points: 500
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "/training-jobs/job-1/observability/scalars?keys=train.box_loss%2Cmetrics.map50&start_step=2&end_step=20&max_points=500",
      expect.any(Object)
    );
  });

  it("calls the summary, resources, graph, and histogram endpoints", async () => {
    await api.getTrainingObservabilitySummary("job-1");
    await api.getTrainingObservabilityResources("job-1", { max_points: 100 });
    await api.getTrainingObservabilityGraph("job-1");
    await api.getTrainingObservabilityHistogram("job-1", {
      kind: "gradient",
      tag: "gradients/head.bias",
      step: 12
    });

    expect(fetchMock.mock.calls.map(([path]) => path)).toEqual([
      "/training-jobs/job-1/observability/summary",
      "/training-jobs/job-1/observability/resources?max_points=100",
      "/training-jobs/job-1/observability/graph",
      "/training-jobs/job-1/observability/histograms?kind=gradient&tag=gradients%2Fhead.bias&step=12"
    ]);
  });

  it("keeps backend collection URLs out of public DTO keys", () => {
    type ForbiddenUrl = "mlflow_url" | "tensorboard_url";
    type SummaryHasNoCollectionUrl = Extract<keyof TrainingObservabilitySummary, ForbiddenUrl> extends never
      ? true
      : false;
    type GraphHasNoCollectionUrl = Extract<keyof TrainingObservabilityGraph, ForbiddenUrl> extends never ? true : false;
    type HistogramHasNoCollectionUrl = Extract<keyof TrainingObservabilityHistogram, ForbiddenUrl> extends never
      ? true
      : false;

    expectTypeOf<SummaryHasNoCollectionUrl>().toEqualTypeOf<true>();
    expectTypeOf<GraphHasNoCollectionUrl>().toEqualTypeOf<true>();
    expectTypeOf<HistogramHasNoCollectionUrl>().toEqualTypeOf<true>();
  });
});
