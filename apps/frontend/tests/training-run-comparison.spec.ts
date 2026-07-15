import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import TrainingRunComparison from "@/components/training/TrainingRunComparison.vue";

const apiMock = vi.hoisted(() => ({
  getTrainingObservabilityScalars: vi.fn(),
}));

vi.mock("@/api/client", () => ({ api: apiMock }));

const jobs = Array.from({ length: 6 }, (_, index) => ({
  id: `job-${index + 1}`,
  pipeline_id: `pipeline-${index + 1}`,
  status: index === 0 ? "running" : "succeeded",
  params: { epochs: 10 + index, batch: 4, lr0: 0.001 },
  metrics: {},
  created_at: `2026-07-${14 - index}T10:00:00Z`,
}));

const MetricLineChartStub = {
  name: "MetricLineChart",
  props: ["series", "axisMin", "axisMax", "valueFormat", "smoothing", "height"],
  template: '<div data-testid="comparison-chart">{{ Object.keys(series).join(",") }}</div>',
};

function mountComparison(pipelineNames = Object.fromEntries(jobs.map((job, index) => [job.pipeline_id, `产线 ${index + 1}`]))) {
  return mount(TrainingRunComparison, {
    props: {
      jobs,
      pipelineNames,
      initialJobId: "job-1",
    },
    global: {
      stubs: {
        MetricLineChart: MetricLineChartStub,
        "el-empty": { props: ["description"], template: '<div data-testid="comparison-empty">{{ description }}</div>' },
      },
    },
  });
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((promiseResolve) => { resolve = promiseResolve; });
  return { promise, resolve };
}

describe("TrainingRunComparison", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.getTrainingObservabilityScalars.mockImplementation((jobId: string) => Promise.resolve({
      series: {
        "metrics.map50": [
          { step: 1, value: jobId === "job-1" ? 0.6 : 0.5, timestamp: 100 },
          { step: 2, value: jobId === "job-1" ? 0.7 : 0.65, timestamp: 110 },
        ],
      },
      availability: {},
    }));
  });

  it("loads the same canonical metric for two default runs and compares their summaries", async () => {
    const wrapper = mountComparison();
    await flushPromises();

    expect(apiMock.getTrainingObservabilityScalars).toHaveBeenCalledTimes(2);
    expect(apiMock.getTrainingObservabilityScalars).toHaveBeenCalledWith(
      "job-1",
      expect.objectContaining({ keys: expect.arrayContaining(["metrics.map50", "metrics.map50b"]) }),
    );
    expect(apiMock.getTrainingObservabilityScalars).toHaveBeenCalledWith(
      "job-2",
      expect.objectContaining({ keys: expect.arrayContaining(["metrics.map50", "metrics.map50b"]) }),
    );
    expect(wrapper.get("[data-testid='comparison-chart']").text()).toBe("产线 1,产线 2");
    expect(wrapper.findAll("[data-testid='comparison-summary-row']")).toHaveLength(2);
    expect(wrapper.text()).toContain("0.7000");
    expect(wrapper.text()).toContain("Step 2");
  });

  it("limits comparison selection to five runs", async () => {
    const wrapper = mountComparison();
    await flushPromises();

    for (const jobId of ["job-3", "job-4", "job-5"]) {
      await wrapper.get(`[data-testid='comparison-run-${jobId}']`).setValue(true);
    }

    expect((wrapper.get("[data-testid='comparison-run-job-6']").element as HTMLInputElement).disabled).toBe(true);
    expect(wrapper.findAll("input[type='checkbox']:checked")).toHaveLength(5);
  });

  it("shows a no-data state when fewer than two selected runs expose the metric", async () => {
    apiMock.getTrainingObservabilityScalars.mockResolvedValue({ series: {}, availability: {} });
    const wrapper = mountComparison();
    await flushPromises();

    expect(wrapper.get("[data-testid='comparison-empty']").text()).toContain("暂无可对比数据");
  });

  it("keeps repeated runs from the same pipeline as distinct chart series", async () => {
    const wrapper = mountComparison(Object.fromEntries(jobs.map((job) => [job.pipeline_id, "同一产线"])));
    await flushPromises();

    const labels = wrapper.get("[data-testid='comparison-chart']").text().split(",");
    expect(labels).toHaveLength(2);
    expect(new Set(labels).size).toBe(2);
    expect(labels.every((label) => label.startsWith("同一产线"))).toBe(true);
  });

  it("reloads selected run metrics when the parent polling cycle refreshes jobs", async () => {
    const wrapper = mountComparison();
    await flushPromises();
    expect(apiMock.getTrainingObservabilityScalars).toHaveBeenCalledTimes(2);

    await wrapper.setProps({ jobs: jobs.map((job) => ({ ...job })) });
    await flushPromises();
    expect(apiMock.getTrainingObservabilityScalars).toHaveBeenCalledTimes(4);
  });

  it("clears loading when a pending comparison is reduced below two runs", async () => {
    const pending = deferred<{ series: Record<string, never[]>; availability: Record<string, never> }>();
    apiMock.getTrainingObservabilityScalars.mockReturnValue(pending.promise);
    const wrapper = mountComparison();
    await flushPromises();
    expect(wrapper.text()).toContain("正在加载对比数据");

    await wrapper.get("[data-testid='comparison-run-job-2']").setValue(false);
    await flushPromises();
    expect(wrapper.text()).not.toContain("正在加载对比数据");
    expect(wrapper.find("[data-testid='comparison-empty']").exists()).toBe(true);
  });

  it("clears stale chart data when all refreshed requests fail", async () => {
    const wrapper = mountComparison();
    await flushPromises();
    expect(wrapper.find("[data-testid='comparison-chart']").exists()).toBe(true);

    apiMock.getTrainingObservabilityScalars.mockRejectedValue(new Error("offline"));
    await wrapper.get("[data-testid='comparison-metric-select']").setValue("metrics.recall");
    await flushPromises();

    expect(wrapper.find("[data-testid='comparison-chart']").exists()).toBe(false);
    expect(wrapper.get("[data-testid='comparison-empty']").text()).toContain("暂无可对比数据");
  });
});
