import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, nextTick, onMounted, onUnmounted } from "vue";

import TrainingVisualizationView from "@/views/training-visualization/TrainingVisualizationView.vue";
import trainingVisualizationViewSource from "@/views/training-visualization/TrainingVisualizationView.vue?raw";

const apiMock = vi.hoisted(() => ({
  getTrainingObservabilityGraph: vi.fn(),
  getTrainingObservabilityHistogram: vi.fn(),
  getTrainingObservabilityResources: vi.fn(),
  getTrainingObservabilityScalars: vi.fn(),
  getTrainingObservabilitySummary: vi.fn(),
  listPipelines: vi.fn(),
  listTrainingJobs: vi.fn(),
}));

vi.mock("@/api/client", () => ({ api: apiMock }));

const chartLifecycle = {
  mounted: vi.fn(),
  unmounted: vi.fn(),
};

const MetricLineChartStub = defineComponent({
  name: "MetricLineChart",
  props: {
    series: { type: Object, required: true },
    unit: String,
    height: [String, Number],
  },
  setup() {
    onMounted(chartLifecycle.mounted);
    onUnmounted(chartLifecycle.unmounted);
  },
  template: '<div class="metric-line-chart-stub">{{ Object.keys(series).join(",") }}</div>',
});

const jobs = [
  {
    id: "job-1",
    pipeline_id: "pipeline-1",
    status: "running",
    environment: { device: "cpu" },
    params: { epochs: 40, batch: 4, imgsz: 640, lr0: 0.01 },
    metrics: {},
    created_at: "2026-07-13T10:00:00Z",
  },
  {
    id: "job-2",
    pipeline_id: "pipeline-2",
    status: "succeeded",
    environment: { device: "0" },
    params: { epochs: 10, batch: 2, imgsz: 320, lr0: 0.002 },
    metrics: {},
    created_at: "2026-07-12T10:00:00Z",
  },
];

function summary(overrides: Record<string, unknown> = {}) {
  return {
    job_id: "job-1",
    pipeline_id: "pipeline-1",
    pipeline_name: "花椒检测",
    status: "running",
    progress: { current_epoch: 4, total_epochs: 40, percent: 10 },
    timing: { elapsed_seconds: 213.4, eta_seconds: 1920.6 },
    environment: { device: "cpu" },
    latest_metrics: {
      "metrics/precision(B)": 0.8123,
      "metrics/recall(B)": 0.7456,
      "metrics/mAP50(B)": 0.7012,
      "metrics/mAP50-95(B)": 0.5234,
    },
    available_scalar_keys: ["train.box_loss", "metrics.map50"],
    available_histograms: { weight: [], gradient: [] },
    availability: {
      mlflow: { available: false, reason: "offline" },
      tensorboard: { available: true, reason: null },
      progress: { available: true, reason: null },
      artifacts: { available: true, reason: null },
    },
    ...overrides,
  };
}

function mountView() {
  return mount(TrainingVisualizationView, {
    global: {
      stubs: {
        MetricLineChart: MetricLineChartStub,
        "el-empty": { props: ["description"], template: '<div class="el-empty-stub">{{ description }}</div>' },
        "el-icon": { template: "<span><slot /></span>" },
        "el-progress": { props: ["percentage"], template: '<div class="el-progress-stub">{{ percentage }}%</div>' },
        "el-tag": { template: "<span><slot /></span>" },
      },
    },
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve;
  });
  return { promise, resolve };
}

describe("TrainingVisualizationView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.listPipelines.mockResolvedValue({
      items: [
        { id: "pipeline-1", name: "花椒检测", task: "detect", scale: "l", status: "training" },
        { id: "pipeline-2", name: "柑橘检测", task: "detect", scale: "n", status: "trained" },
      ],
      total: 2,
      limit: 100,
      offset: 0,
    });
    apiMock.listTrainingJobs.mockResolvedValue({ items: jobs, total: 2, limit: 200, offset: 0 });
    apiMock.getTrainingObservabilitySummary.mockResolvedValue(summary());
    apiMock.getTrainingObservabilityScalars.mockResolvedValue({
      series: {
        "train.box_loss": [{ step: 1, value: 1.2, timestamp: 100 }],
        "metrics.map50": [{ step: 1, value: 0.7, timestamp: 100 }],
      },
      availability: summary().availability,
    });
    apiMock.getTrainingObservabilityResources.mockResolvedValue({
      series: { "system.cpu_percent": [{ step: 1, value: 36, timestamp: 100 }] },
      availability: summary().availability,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders the real run list and complete native overview without an iframe", async () => {
    const wrapper = mountView();
    await flushPromises();

    expect(apiMock.listTrainingJobs).toHaveBeenCalledWith({ limit: 200 });
    expect(apiMock.getTrainingObservabilitySummary).toHaveBeenCalledWith("job-1");
    expect(wrapper.find("iframe").exists()).toBe(false);
    expect(wrapper.text()).toContain("花椒检测");
    expect(wrapper.text()).toContain("柑橘检测");

    for (const tab of ["overview", "metrics", "resources", "analysis", "graph", "histograms"]) {
      expect(wrapper.find(`[data-testid="tab-${tab}"]`).exists()).toBe(true);
    }

    const text = wrapper.get('[data-testid="overview-panel"]').text();
    expect(text).toContain("训练中");
    expect(text).toContain("10%");
    expect(text).toContain("当前 Epoch");
    expect(text).toContain("4 / 40");
    expect(text).toContain("3分33秒");
    expect(text).toContain("32分1秒");
    expect(text).toContain("CPU");
    expect(text).toContain("4");
    expect(text).toContain("640 x 640");
    expect(text).toContain("0.01");
    expect(text).toContain("Precision");
    expect(text).toContain("81.23%");
    expect(text).toContain("Recall");
    expect(text).toContain("74.56%");
    expect(text).toContain("mAP50");
    expect(text).toContain("70.12%");
    expect(text).toContain("mAP50-95");
    expect(text).toContain("52.34%");
    expect(text).toContain("MLflow");
    expect(text).toContain("不可用");
    expect(text).toContain("TensorBoard");
    expect(text).toContain("可用");

    wrapper.unmount();
  });

  it("requests only advertised scalars and keeps TensorBoard-backed series visible", async () => {
    const wrapper = mountView();
    await flushPromises();

    expect(apiMock.getTrainingObservabilityScalars).not.toHaveBeenCalled();
    await wrapper.get('[data-testid="tab-metrics"]').trigger("click");
    await flushPromises();

    expect(apiMock.getTrainingObservabilityScalars).toHaveBeenCalledTimes(1);
    expect(apiMock.getTrainingObservabilityScalars).toHaveBeenCalledWith("job-1", {
      keys: ["train.box_loss", "metrics.map50"],
      max_points: 1000,
    });
    expect(wrapper.get('[data-testid="metrics-panel"]').text()).toContain("train.box_loss,metrics.map50");
    expect(wrapper.get('[data-testid="metrics-panel"]').text()).toContain("MLflow 不可用");

    wrapper.unmount();
  });

  it("loads resource series only when the resources tab activates", async () => {
    const wrapper = mountView();
    await flushPromises();

    expect(apiMock.getTrainingObservabilityResources).not.toHaveBeenCalled();
    await wrapper.get('[data-testid="tab-metrics"]').trigger("click");
    await flushPromises();
    expect(apiMock.getTrainingObservabilityResources).not.toHaveBeenCalled();

    await wrapper.get('[data-testid="tab-resources"]').trigger("click");
    await flushPromises();
    expect(apiMock.getTrainingObservabilityResources).toHaveBeenCalledWith("job-1", { max_points: 1000 });
    expect(wrapper.get('[data-testid="resources-panel"]').text()).toContain("system.cpu_percent");

    wrapper.unmount();
  });

  it("mounts the metric chart only after data is visible and keeps that instance through polling", async () => {
    vi.useFakeTimers();
    const firstScalars = deferred<{
      series: Record<string, Array<{ step: number; value: number; timestamp: number }>>;
      availability: ReturnType<typeof summary>["availability"];
    }>();
    apiMock.getTrainingObservabilityScalars
      .mockReturnValueOnce(firstScalars.promise)
      .mockResolvedValueOnce({
        series: { "train.box_loss": [{ step: 2, value: 0.9, timestamp: 110 }] },
        availability: summary().availability,
      });

    const wrapper = mountView();
    await flushPromises();
    await wrapper.get('[data-testid="tab-metrics"]').trigger("click");
    await nextTick();

    expect(chartLifecycle.mounted).not.toHaveBeenCalled();

    firstScalars.resolve({
      series: { "train.box_loss": [{ step: 1, value: 1.2, timestamp: 100 }] },
      availability: summary().availability,
    });
    await flushPromises();
    expect(chartLifecycle.mounted).toHaveBeenCalledTimes(1);

    await wrapper.get('[data-testid="tab-overview"]').trigger("click");
    await wrapper.get('[data-testid="tab-metrics"]').trigger("click");
    await vi.advanceTimersByTimeAsync(5000);
    await flushPromises();

    expect(apiMock.getTrainingObservabilityScalars).toHaveBeenCalledTimes(2);
    expect(chartLifecycle.mounted).toHaveBeenCalledTimes(1);

    wrapper.unmount();
    expect(chartLifecycle.unmounted).toHaveBeenCalledTimes(1);
  });

  it("mounts the resource chart only after data is visible and keeps that instance through polling", async () => {
    vi.useFakeTimers();
    const firstResources = deferred<{
      series: Record<string, Array<{ step: number; value: number; timestamp: number }>>;
      availability: ReturnType<typeof summary>["availability"];
    }>();
    apiMock.getTrainingObservabilityResources
      .mockReturnValueOnce(firstResources.promise)
      .mockResolvedValueOnce({
        series: { "system.cpu_percent": [{ step: 2, value: 41, timestamp: 110 }] },
        availability: summary().availability,
      });

    const wrapper = mountView();
    await flushPromises();
    await wrapper.get('[data-testid="tab-resources"]').trigger("click");
    await nextTick();

    expect(chartLifecycle.mounted).not.toHaveBeenCalled();

    firstResources.resolve({
      series: { "system.cpu_percent": [{ step: 1, value: 36, timestamp: 100 }] },
      availability: summary().availability,
    });
    await flushPromises();
    expect(chartLifecycle.mounted).toHaveBeenCalledTimes(1);

    await wrapper.get('[data-testid="tab-overview"]').trigger("click");
    await wrapper.get('[data-testid="tab-resources"]').trigger("click");
    await vi.advanceTimersByTimeAsync(5000);
    await flushPromises();

    expect(apiMock.getTrainingObservabilityResources).toHaveBeenCalledTimes(2);
    expect(chartLifecycle.mounted).toHaveBeenCalledTimes(1);

    wrapper.unmount();
    expect(chartLifecycle.unmounted).toHaveBeenCalledTimes(1);
  });

  it("defines shell-aware collapse and narrow-content overflow constraints", () => {
    expect(trainingVisualizationViewSource).toContain("container: training-view / inline-size");
    expect(trainingVisualizationViewSource).toMatch(
      /@container training-view \(max-width: 760px\)[\s\S]*?\.visualization-layout \{ display: block;/,
    );
    expect(trainingVisualizationViewSource).toMatch(
      /@container training-view \(max-width: 420px\)[\s\S]*?\.source-list \{ grid-template-columns: minmax\(0, 1fr\);/,
    );
    expect(trainingVisualizationViewSource).toMatch(/\.refresh-button \{[^}]*white-space: nowrap;/);
  });

  it("polls running jobs every five seconds and stops as soon as status is terminal", async () => {
    vi.useFakeTimers();
    apiMock.getTrainingObservabilitySummary
      .mockResolvedValueOnce(summary())
      .mockResolvedValueOnce(summary({ status: "succeeded" }));

    const wrapper = mountView();
    await flushPromises();
    expect(apiMock.getTrainingObservabilitySummary).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(5000);
    await flushPromises();
    expect(apiMock.getTrainingObservabilitySummary).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(15000);
    await flushPromises();
    expect(apiMock.getTrainingObservabilitySummary).toHaveBeenCalledTimes(2);

    wrapper.unmount();
  });

  it("ignores stale responses after switching jobs and refreshes every selected-job field", async () => {
    const firstSummary = deferred<ReturnType<typeof summary>>();
    apiMock.getTrainingObservabilitySummary.mockImplementation((jobId: string) => {
      if (jobId === "job-1") return firstSummary.promise;
      return Promise.resolve(summary({
        job_id: "job-2",
        pipeline_id: "pipeline-2",
        pipeline_name: "柑橘检测",
        status: "succeeded",
        progress: { current_epoch: 10, total_epochs: 10, percent: 100 },
        environment: { device: "0" },
        latest_metrics: { "metrics/mAP50-95(B)": 0.91 },
        available_scalar_keys: ["val.box_loss"],
      }));
    });

    const wrapper = mountView();
    await nextTick();
    await flushPromises();
    await wrapper.get('[data-testid="job-job-2"]').trigger("click");
    await flushPromises();

    expect(apiMock.getTrainingObservabilitySummary).toHaveBeenCalledWith("job-2");
    expect(wrapper.get('[data-testid="overview-panel"]').text()).toContain("10 / 10");
    expect(wrapper.get('[data-testid="overview-panel"]').text()).toContain("91.00%");
    expect(wrapper.get('[data-testid="overview-panel"]').text()).toContain("320 x 320");

    firstSummary.resolve(summary({ progress: { current_epoch: 1, total_epochs: 40, percent: 2.5 } }));
    await flushPromises();
    expect(wrapper.get('[data-testid="overview-panel"]').text()).toContain("10 / 10");
    expect(wrapper.get('[data-testid="overview-panel"]').text()).not.toContain("1 / 40");

    wrapper.unmount();
  });

  it("clears the active polling timer when unmounted", async () => {
    vi.useFakeTimers();
    const wrapper = mountView();
    await flushPromises();

    wrapper.unmount();
    await vi.advanceTimersByTimeAsync(10000);
    expect(apiMock.getTrainingObservabilitySummary).toHaveBeenCalledTimes(1);
  });
});
