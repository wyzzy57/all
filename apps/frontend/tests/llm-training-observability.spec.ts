import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LlmTrainingAnalysis from "@/features/training-observability/llm/LlmTrainingAnalysis.vue";
import LlmTrainingMetrics from "@/features/training-observability/llm/LlmTrainingMetrics.vue";
import LlmTrainingOverview from "@/features/training-observability/llm/LlmTrainingOverview.vue";
import LlmTrainingResources from "@/features/training-observability/llm/LlmTrainingResources.vue";
import {
  buildLlmMetricCharts,
  buildLlmResourceCharts,
  type LlmObservabilityAvailability,
  type LlmScalarSeries,
} from "@/features/training-observability/llm/llmMetricCatalog";

const echartsMocks = vi.hoisted(() => ({
  dispose: vi.fn(),
  init: vi.fn(),
  resize: vi.fn(),
  setOption: vi.fn(),
  use: vi.fn(),
}));

vi.mock("echarts/core", () => ({
  init: echartsMocks.init,
  use: echartsMocks.use,
}));

const availability: LlmObservabilityAvailability = {
  mlflow: { available: false, reason: "连接暂时不可用" },
  tensorboard: { available: true, reason: null },
  progress: { available: true, reason: null },
  artifacts: { available: true, reason: null },
};

const point = (step: number, value: number) => ({ step, value, timestamp: 1_722_000_000 + step });

const metricSeries: LlmScalarSeries = {
  loss: [point(1, 2.4), point(2, 1.8)],
  eval_loss: [point(2, 2.1)],
  learning_rate: [point(1, 0.00005), point(2, 0.00004)],
  grad_norm: [point(1, 1.2), point(2, 0.9)],
  tokens_per_second: [point(1, 820), point(2, 910)],
  samples_per_second: [point(1, 8.2), point(2, 9.1)],
  train_runtime: [point(1, 600), point(2, 1200)],
  epoch: [point(1, 0.5), point(2, 1)],
  step: [point(1, 1), point(2, 2)],
};

describe("LLM metric catalog", () => {
  it("groups loss, optimization, throughput and progress without mixing units", () => {
    const groups = buildLlmMetricCharts(metricSeries);

    expect(groups.map((group) => group.id)).toEqual(["loss", "optimization", "throughput", "runtime", "progress"]);
    expect(groups.find((group) => group.id === "loss")?.charts).toEqual([
      expect.objectContaining({ unit: "Loss", series: expect.objectContaining({ "训练 Loss": metricSeries.loss }) }),
    ]);
    expect(groups.find((group) => group.id === "optimization")?.charts.map((chart) => chart.unit)).toEqual([
      "Learning rate",
      "Gradient norm",
    ]);
    expect(groups.find((group) => group.id === "throughput")?.charts.map((chart) => chart.unit)).toEqual([
      "tokens/s",
      "samples/s",
    ]);
    expect(groups.find((group) => group.id === "runtime")?.charts).toEqual([
      expect.objectContaining({ unit: "seconds", series: { Runtime: metricSeries.train_runtime } }),
    ]);
    expect(groups.find((group) => group.id === "progress")?.charts.map((chart) => chart.unit)).toEqual([
      "Epoch",
      "Step",
    ]);
  });

  it("keeps each GPU as a separate resource series", () => {
    const groups = buildLlmResourceCharts({
      "system.cpu_percent": [point(1, 48)],
      "system.network_bytes_sent": [point(1, 4096)],
      "gpu.GPU-a.utilization_percent": [point(1, 72)],
      "gpu.GPU-b.utilization_percent": [point(1, 61)],
      "gpu.GPU-a.memory_used_mb": [point(1, 12288)],
      "gpu.GPU-b.memory_used_mb": [point(1, 8192)],
      "gpu.GPU-a.power_watts": [point(1, 180)],
    });
    const gpu = groups.find((group) => group.id === "gpu");

    expect(gpu?.charts[0]).toEqual(expect.objectContaining({
      unit: "%",
      series: {
        "GPU-a": [point(1, 72)],
        "GPU-b": [point(1, 61)],
      },
    }));
    expect(gpu?.charts[1].unit).toBe("MiB");
    expect(gpu?.charts.find((chart) => chart.id === "gpu-power")?.series["GPU-a"]).toEqual([point(1, 180)]);
    expect(groups.find((group) => group.id === "node")?.charts.some((chart) => chart.id === "node-network-out")).toBe(true);
  });
});

describe("native LLM observability components", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    echartsMocks.init.mockReturnValue({
      dispose: echartsMocks.dispose,
      resize: echartsMocks.resize,
      setOption: echartsMocks.setOption,
    });
    vi.stubGlobal("ResizeObserver", class {
      observe = vi.fn();
      disconnect = vi.fn();
    });
  });

  it("renders overview progress and unobtrusive source degradation with secondary deep links", () => {
    const wrapper = mount(LlmTrainingOverview, {
      props: {
        summary: {
          job_id: "job-llm-1",
          pipeline_name: "Qwen2.5 SFT",
          status: "running",
          progress: { current_step: 64, total_steps: 200, current_epoch: 1.2, total_epochs: 3, percent: 32 },
          timing: { elapsed_seconds: 3720, eta_seconds: 7860 },
          environment: { node_name: "gpu-node-01", model_id: "Qwen/Qwen2.5-7B", dataset_name: "客服问答 v2" },
          latest_metrics: { loss: 1.82, learning_rate: 0.00004, tokens_per_second: 910 },
          availability,
        },
        mlflowUrl: "https://mlflow.example/runs/1",
        tensorboardUrl: "https://tensorboard.example/#run-1",
      },
    });

    expect(wrapper.get('[data-testid="llm-progress"]').text()).toContain("32%");
    expect(wrapper.text()).toContain("Qwen/Qwen2.5-7B");
    expect(wrapper.get('[data-testid="source-degradation"]').text()).toContain("MLflow");
    expect(wrapper.get('[data-testid="open-mlflow"]').attributes("href")).toBe("https://mlflow.example/runs/1");
    expect(wrapper.get('[data-testid="open-tensorboard"]').attributes("href")).toBe("https://tensorboard.example/#run-1");
  });

  it("renders separate metric charts with tooltip and zoom and supports series toggles", async () => {
    const wrapper = mount(LlmTrainingMetrics, {
      props: { response: { series: metricSeries, availability } },
    });
    await flushPromises();

    expect(wrapper.findAll('[data-testid^="metric-group-"]')).toHaveLength(5);
    const chartOptions = echartsMocks.setOption.mock.calls
      .filter((call) => call[1] === true)
      .map((call) => call[0]);
    expect(chartOptions.length).toBeGreaterThanOrEqual(8);
    expect(chartOptions.every((option) => option.tooltip?.trigger === "axis")).toBe(true);
    expect(chartOptions.every((option) => Array.isArray(option.dataZoom) && option.dataZoom.length === 2)).toBe(true);
    expect(chartOptions.map((option) => option.yAxis?.name)).toEqual(expect.arrayContaining([
      "Loss", "Learning rate", "Gradient norm", "tokens/s", "samples/s", "seconds", "Epoch", "Step",
    ]));

    const callsBeforeToggle = echartsMocks.setOption.mock.calls.length;
    await wrapper.get('[data-testid="toggle-eval_loss"]').trigger("click");
    expect(wrapper.get('[data-testid="toggle-eval_loss"]').attributes("aria-pressed")).toBe("false");
    expect(echartsMocks.setOption.mock.calls.slice(callsBeforeToggle)).toContainEqual([
      expect.objectContaining({ series: [expect.objectContaining({ name: "训练 Loss" })] }),
      { replaceMerge: ["series"] },
    ]);
  });

  it("shows empty and degraded metric states without collapsing the panel", () => {
    const wrapper = mount(LlmTrainingMetrics, {
      props: {
        response: {
          series: {},
          availability: {
            mlflow: { available: false, reason: "offline" },
            tensorboard: { available: false, reason: "event file missing" },
          },
        },
      },
    });

    expect(wrapper.get('[data-testid="metrics-empty"]').text()).toContain("暂无指标");
    expect(wrapper.get('[data-testid="source-degradation"]').text()).toContain("MLflow");
    expect(wrapper.get(".llm-metrics").classes()).toContain("is-empty");
  });

  it("renders node metrics and separate per-GPU utilization and memory series", () => {
    const wrapper = mount(LlmTrainingResources, {
      props: {
        response: {
          series: {
            "system.cpu_percent": [point(1, 48)],
            "system.memory_percent": [point(1, 63)],
            "gpu.GPU-a.utilization_percent": [point(1, 72)],
            "gpu.GPU-b.utilization_percent": [point(1, 61)],
            "gpu.GPU-a.memory_used_mb": [point(1, 12288)],
            "gpu.GPU-b.memory_used_mb": [point(1, 8192)],
          },
          availability,
        },
      },
    });

    expect(wrapper.get('[data-testid="resource-group-node"]').text()).toContain("节点资源");
    expect(wrapper.get('[data-testid="resource-group-gpu"]').text()).toContain("逐 GPU");
    const options = echartsMocks.setOption.mock.calls.filter((call) => call[1] === true).map((call) => call[0]);
    const gpuUtilization = options.find((option) => option.yAxis?.name === "%" && option.series?.length === 2);
    expect(gpuUtilization.series.map((series: { name: string }) => series.name)).toEqual(["GPU-a", "GPU-b"]);
  });

  it("renders deterministic analysis evidence and separates checkpoints from other artifacts", async () => {
    const wrapper = mount(LlmTrainingAnalysis, {
      props: {
        analysis: {
          findings: [{
            code: "LOW_GPU_UTILIZATION",
            severity: "warning",
            title: "GPU 利用率偏低",
            message: "采样平均 GPU 利用率低于阈值。",
            metric_names: ["gpu.GPU-a.utilization_percent"],
            step_range: [20, 80],
            observed_values: { average_gpu_utilization: 14.2 },
          }],
          availability,
        },
        artifacts: {
          items: [
            { path: "checkpoints/checkpoint-100/adapter_model.safetensors", size_bytes: 2048, sha256: "abc" },
            { path: "trainer_state.json", size_bytes: 512, sha256: "def" },
          ],
          availability,
        },
      },
    });

    expect(wrapper.get('[data-testid="analysis-finding-LOW_GPU_UTILIZATION"]').text()).toContain("Step 20 - 80");
    expect(wrapper.get('[data-testid="analysis-evidence-LOW_GPU_UTILIZATION"]').text()).toContain("14.2");
    expect(wrapper.get('[data-testid="checkpoint-list"]').text()).toContain("adapter_model.safetensors");
    expect(wrapper.get('[data-testid="artifact-list"]').text()).toContain("trainer_state.json");

    await wrapper.get('[data-testid="artifact-checkpoints/checkpoint-100/adapter_model.safetensors"]').trigger("click");
    expect(wrapper.emitted("open-artifact")?.[0]).toEqual([expect.objectContaining({ sha256: "abc" })]);
  });
});
