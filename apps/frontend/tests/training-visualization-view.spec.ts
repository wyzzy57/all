import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, nextTick, onMounted, onUnmounted } from "vue";

import TrainingVisualizationView from "@/views/training-visualization/TrainingVisualizationView.vue";
import artifactGallerySource from "@/components/training/ArtifactGallery.vue?raw";
import trainingVisualizationViewSource from "@/views/training-visualization/TrainingVisualizationView.vue?raw";

const apiMock = vi.hoisted(() => ({
  deleteTrainingJob: vi.fn(),
  getTrainingObservabilityResources: vi.fn(),
  getTrainingObservabilityScalars: vi.fn(),
  getTrainingObservabilitySummary: vi.fn(),
  getTrainingObservabilityAnalysis: vi.fn(),
  getTrainingObservabilityArtifacts: vi.fn(),
  listTrainingJobArtifacts: vi.fn(),
  listPipelines: vi.fn(),
  listTrainingJobs: vi.fn(),
  trainingJobArtifactDownloadUrl: vi.fn(),
}));
const routeMock = vi.hoisted(() => ({ query: {} as Record<string, string | undefined> }));

vi.mock("@/api/client", () => ({ api: apiMock }));
vi.mock("vue-router", () => ({ useRoute: () => routeMock }));

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
    axisMin: Number,
    axisMax: Number,
    valueFormat: String,
    smoothing: Number,
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
    engine: "yolo26",
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
    secondary_actions: [
      { source: "mlflow", url: "https://mlflow.example.test" },
      { source: "tensorboard", url: "https://tensorboard.example.test" },
      { source: "visualdl", url: "https://visualdl.example.test" },
    ],
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
        LlmTrainingOverview: { props: ["summary"], template: '<div data-testid="llm-overview-stub">{{ summary.pipeline_name }}</div>' },
        LlmTrainingMetrics: { props: ["response"], template: '<div data-testid="llm-metrics-stub">{{ Object.keys(response.series).join(",") }}</div>' },
        LlmTrainingResources: { props: ["response"], template: '<div data-testid="llm-resources-stub">{{ Object.keys(response.series).join(",") }}</div>' },
        LlmTrainingAnalysis: { props: ["analysis", "artifacts"], template: '<div data-testid="llm-analysis-stub">{{ analysis.findings.length }} / {{ artifacts.items.length }}</div>' },
        PaddleXTrainingAnalysis: { props: ["series", "analysis", "artifacts", "visualdlUrl"], template: '<div data-testid="paddlex-analysis-stub">{{ Object.keys(series).join(",") }} / {{ visualdlUrl }}</div>' },
        "el-dialog": {
          props: ["modelValue", "title"],
          template: '<section v-if="modelValue" class="el-dialog-stub"><h2>{{ title }}</h2><slot /><slot name="footer" /></section>',
        },
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
    routeMock.query = {};
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
    apiMock.deleteTrainingJob.mockResolvedValue(undefined);
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
    apiMock.getTrainingObservabilityAnalysis.mockResolvedValue({
      findings: [],
      availability: summary().availability,
    });
    apiMock.getTrainingObservabilityArtifacts.mockResolvedValue({
      items: [],
      availability: summary().availability,
    });
    apiMock.listTrainingJobArtifacts.mockResolvedValue({
      items: [
        { name: "best.pt", kind: "weight", size_bytes: 1024, download_url: "/ignored/best.pt" },
        { name: "results.png", kind: "visualization", size_bytes: 2048, download_url: "/ignored/results.png" },
        { name: "confusion_matrix.png", kind: "visualization", size_bytes: 3072, download_url: "/ignored/confusion.png" },
        { name: "train_batch0.jpg", kind: "visualization", size_bytes: 4096, download_url: "/ignored/batch.jpg" },
      ],
    });
    apiMock.trainingJobArtifactDownloadUrl.mockImplementation(
      (jobId: string, kind: string, name: string) => `/downloads/${jobId}/${kind}/${name}`,
    );
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("delegates graph and histogram inspection to the existing TensorBoard action", async () => {
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.find('[data-testid="tab-graph"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="tab-histograms"]').exists()).toBe(false);

    await wrapper.get('[data-testid="advanced-menu-toggle"]').trigger("click");
    expect(wrapper.get('[data-testid="open-visualdl"]').text()).toBe("VisualDL");
    await wrapper.get('[data-testid="open-tensorboard"]').trigger("click");
    expect(openSpy).toHaveBeenCalledWith(
      "https://tensorboard.example.test",
      "_blank",
      "noopener,noreferrer",
    );

    wrapper.unmount();
    openSpy.mockRestore();
  });

  it("renders the real run list and complete native overview without an iframe", async () => {
    const wrapper = mountView();
    await flushPromises();

    expect(apiMock.listTrainingJobs).toHaveBeenCalledWith({ limit: 200 });
    expect(apiMock.getTrainingObservabilitySummary).toHaveBeenCalledWith("job-1");
    expect(wrapper.find("iframe").exists()).toBe(false);
    expect(wrapper.text()).toContain("花椒检测");
    expect(wrapper.text()).toContain("柑橘检测");

    for (const tab of ["overview", "metrics", "resources", "analysis", "logs", "artifacts"]) {
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

  it("selects dedicated LLM overview, metrics, resources and analysis by engine", async () => {
    apiMock.listPipelines.mockResolvedValue({
      items: [{ id: "pipeline-1", name: "Qwen SFT", engine: "llamafactory", task: "llm", scale: "7b", status: "training" }],
      total: 1,
      limit: 100,
      offset: 0,
    });
    apiMock.listTrainingJobs.mockResolvedValue({ items: [jobs[0]], total: 1, limit: 200, offset: 0 });
    apiMock.getTrainingObservabilitySummary.mockResolvedValue(summary({
      engine: "llamafactory",
      pipeline_name: "Qwen SFT",
      available_scalar_keys: ["loss", "learning_rate"],
    }));

    const wrapper = mountView();
    await flushPromises();
    expect(wrapper.get('[data-testid="llm-overview-stub"]').text()).toContain("Qwen SFT");

    await wrapper.get('[data-testid="tab-metrics"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="llm-metrics-stub"]').exists()).toBe(true);

    await wrapper.get('[data-testid="tab-resources"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="llm-resources-stub"]').exists()).toBe(true);

    await wrapper.get('[data-testid="tab-analysis"]').trigger("click");
    await flushPromises();
    expect(apiMock.getTrainingObservabilityAnalysis).toHaveBeenCalledWith("job-1");
    expect(apiMock.getTrainingObservabilityArtifacts).toHaveBeenCalledWith("job-1");
    expect(wrapper.find('[data-testid="llm-analysis-stub"]').exists()).toBe(true);
  });

  it("renders framework provenance and a PaddleX analysis in the common native shell", async () => {
    apiMock.listPipelines.mockResolvedValue({
      items: [{
        id: "pipeline-1", name: "PaddleX detection", engine: "paddlex", task: "detect", scale: "s", status: "training",
        model_family: "PP-YOLOE-S", adapter_key: "paddlex.object_detection.v1", adapter_version: "1.2.0",
      }],
      total: 1, limit: 100, offset: 0,
    });
    apiMock.listTrainingJobs.mockResolvedValue({ items: [{
      ...jobs[0],
      resolved_snapshot: {
        model: { runtime_id: "PP-YOLOE-S" },
        dataset: { version: 7 },
        runtime_image_digest: "registry.example/paddlex@sha256:abc123",
      },
      attempts: [{ id: "attempt-1", attempt_number: 1 }, { id: "attempt-2", attempt_number: 2 }],
    }], total: 1, limit: 200, offset: 0 });
    apiMock.getTrainingObservabilitySummary.mockResolvedValue(summary({
      engine: "paddlex", pipeline_name: "PaddleX detection", available_scalar_keys: ["bbox_map", "loss_cls"],
    }));

    const wrapper = mountView();
    await flushPromises();

    const overview = wrapper.get('[data-testid="overview-panel"]').text();
    expect(overview).toContain("PaddleX");
    expect(overview).toContain("PP-YOLOE-S");
    expect(overview).toContain("v7");
    expect(overview).toContain("sha256:abc123");
    expect(overview).toContain("1.2.0");
    expect(wrapper.get('[data-testid="attempt-select"]').findAll("option")).toHaveLength(2);

    await wrapper.get('[data-testid="tab-analysis"]').trigger("click");
    await flushPromises();
    expect(wrapper.get('[data-testid="paddlex-analysis-stub"]').text()).toContain("https://visualdl.example.test");
  });

  it("lists every framework artifact in the shared product tab and opens its download URL", async () => {
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
    apiMock.getTrainingObservabilityArtifacts.mockResolvedValue({
      items: [{ path: "best_model.pdparams", size_bytes: 1024, sha256: "abc", download_url: "https://files.example/best_model.pdparams" }],
      availability: summary().availability,
    });
    const wrapper = mountView();
    await flushPromises();

    await wrapper.get('[data-testid="tab-artifacts"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="observability-artifact-best_model.pdparams"]').trigger("click");
    expect(openSpy).toHaveBeenCalledWith("https://files.example/best_model.pdparams", "_blank", "noopener,noreferrer");
    openSpy.mockRestore();
  });

  it("renders TensorBoard-style semantic metric cards and switches to run comparison", async () => {
    const wrapper = mountView();
    await flushPromises();

    await wrapper.get('[data-testid="tab-metrics"]').trigger("click");
    await flushPromises();

    expect(wrapper.findAll('[data-testid^="metric-card-"]').filter((item) => /^metric-card-(box-loss|map50)$/.test(item.attributes("data-testid") ?? ""))).toHaveLength(2);
    expect(wrapper.text()).toContain("Box Loss");
    expect(wrapper.text()).toContain("mAP50");
    expect(wrapper.find('[data-testid="metric-card-precision"]').exists()).toBe(false);

    await wrapper.get('[data-testid="metrics-mode-compare"]').trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="run-comparison"]').exists()).toBe(true);
    expect(wrapper.get('[data-testid="job-job-1"]').classes()).toContain("active");

    wrapper.unmount();
  });

  it("shows record deletion only for terminal training jobs", async () => {
    apiMock.listTrainingJobs.mockResolvedValue({
      items: [
        ...jobs,
        {
          id: "job-orphan",
          pipeline_id: "pipeline-1",
          status: "queued",
          task_id: null,
          distributed_run_id: null,
          remote_execution_id: null,
          environment: {},
          params: {},
          metrics: {},
          created_at: "2026-07-08T02:32:13Z",
        },
      ],
      total: 3,
      limit: 200,
      offset: 0,
    });
    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.get('[data-testid="delete-job-job-1"]').attributes("disabled")).toBeDefined();
    expect(wrapper.get('[data-testid="delete-job-job-2"]').attributes("disabled")).toBeUndefined();
    expect(wrapper.get('[data-testid="delete-job-job-orphan"]').attributes("disabled")).toBeUndefined();

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
    expect(wrapper.get('[data-testid="metrics-panel"]').text()).toContain("Box Loss");
    expect(wrapper.get('[data-testid="metrics-panel"]').text()).toContain("mAP50");
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

  it("loads real artifacts only for analysis and groups previews with API-backed downloads", async () => {
    const wrapper = mountView();
    await flushPromises();

    expect(apiMock.listTrainingJobArtifacts).not.toHaveBeenCalled();
    await wrapper.get('[data-testid="tab-analysis"]').trigger("click");
    await flushPromises();

    expect(apiMock.listTrainingJobArtifacts).toHaveBeenCalledTimes(1);
    expect(apiMock.listTrainingJobArtifacts).toHaveBeenCalledWith("job-1");
    const panel = wrapper.get('[data-testid="analysis-panel"]');
    for (const group of ["训练结果", "评估曲线", "混淆矩阵", "训练批次"]) {
      expect(panel.text()).toContain(group);
    }
    expect(panel.get('[data-testid="artifact-preview-results.png"]').attributes("src"))
      .toBe("/downloads/job-1/visualization/results.png");
    expect(panel.get('[data-testid="artifact-preview-results.png"]').classes()).toContain("artifact-preview-image");
    const previewButton = panel.get('[data-testid="artifact-preview-button-results.png"]');
    expect(previewButton.element.tagName).toBe("BUTTON");
    expect(previewButton.attributes("href")).toBeUndefined();
    await previewButton.trigger("click");
    await flushPromises();
    expect(wrapper.get('[data-testid="artifact-preview-dialog-image"]').attributes("src"))
      .toBe("/downloads/job-1/visualization/results.png");
    expect(wrapper.get('[data-testid="artifact-preview-dialog-download"]').attributes("href"))
      .toBe("/downloads/job-1/visualization/results.png");
    expect(panel.get('[data-testid="artifact-download-best.pt"]').attributes("href"))
      .toBe("/downloads/job-1/weight/best.pt");
    expect(apiMock.trainingJobArtifactDownloadUrl).toHaveBeenCalledWith("job-1", "weight", "best.pt");

    wrapper.unmount();
  });

  it("does not mount or fetch a new job gallery while Analysis is inactive", async () => {
    apiMock.listTrainingJobArtifacts.mockImplementation((jobId: string) => Promise.resolve({
      items: [
        {
          name: `${jobId}.png`,
          kind: "visualization",
          size_bytes: 2048,
          download_url: `/ignored/${jobId}.png`,
        },
      ],
    }));
    const wrapper = mountView();
    await flushPromises();

    await wrapper.get('[data-testid="tab-analysis"]').trigger("click");
    await flushPromises();
    expect(apiMock.listTrainingJobArtifacts).toHaveBeenCalledTimes(1);
    expect(apiMock.listTrainingJobArtifacts).toHaveBeenLastCalledWith("job-1");
    expect(wrapper.get('[data-testid="analysis-panel"]').text()).toContain("job-1.png");

    await wrapper.get('[data-testid="tab-overview"]').trigger("click");
    await wrapper.get('[data-testid="job-job-2"]').trigger("click");
    await flushPromises();

    expect(apiMock.listTrainingJobArtifacts).toHaveBeenCalledTimes(1);
    expect(wrapper.find('[data-testid="artifact-preview-job-1.png"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="artifact-preview-job-2.png"]').exists()).toBe(false);

    await wrapper.get('[data-testid="tab-analysis"]').trigger("click");
    await flushPromises();
    expect(apiMock.listTrainingJobArtifacts).toHaveBeenCalledTimes(2);
    expect(apiMock.listTrainingJobArtifacts).toHaveBeenLastCalledWith("job-2");
    expect(wrapper.get('[data-testid="analysis-panel"]').text()).toContain("job-2.png");
    expect(wrapper.get('[data-testid="analysis-panel"]').text()).not.toContain("job-1.png");

    await wrapper.get('[data-testid="tab-overview"]').trigger("click");
    await wrapper.get('[data-testid="job-job-1"]').trigger("click");
    await flushPromises();
    expect(apiMock.listTrainingJobArtifacts).toHaveBeenCalledTimes(2);

    await wrapper.get('[data-testid="tab-analysis"]').trigger("click");
    await flushPromises();
    expect(apiMock.listTrainingJobArtifacts).toHaveBeenCalledTimes(2);
    expect(wrapper.get('[data-testid="analysis-panel"]').text()).toContain("job-1.png");
    expect(wrapper.get('[data-testid="analysis-panel"]').text()).not.toContain("job-2.png");

    wrapper.unmount();
  });

  it("opens configured advanced tools only after an explicit menu action", async () => {
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
    const wrapper = mountView();
    await flushPromises();

    expect(openSpy).not.toHaveBeenCalled();
    await wrapper.get('[data-testid="advanced-menu-toggle"]').trigger("click");
    await wrapper.get('[data-testid="open-mlflow"]').trigger("click");
    expect(openSpy).toHaveBeenCalledWith("https://mlflow.example.test", "_blank", "noopener,noreferrer");
    expect(trainingVisualizationViewSource).not.toMatch(/iframe|5001|6006/);

    wrapper.unmount();
    openSpy.mockRestore();
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

  it("pins contained artifact images inside stable aspect-ratio previews", () => {
    expect(artifactGallerySource).toMatch(/\.artifact-preview \{[^}]*position: relative;/);
    expect(artifactGallerySource).toMatch(/\.artifact-preview \{[^}]*aspect-ratio: 16 \/ 10;/);
    expect(artifactGallerySource).toMatch(/\.artifact-preview-image \{[^}]*position: absolute;/);
    expect(artifactGallerySource).toMatch(/\.artifact-preview-image \{[^}]*object-fit: contain;/);
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

  it("recovers polling after a transient summary request failure", async () => {
    vi.useFakeTimers();
    apiMock.getTrainingObservabilitySummary
      .mockResolvedValueOnce(summary())
      .mockRejectedValueOnce(new Error("temporary offline"))
      .mockResolvedValueOnce(summary({ status: "succeeded" }));

    const wrapper = mountView();
    await flushPromises();
    await vi.advanceTimersByTimeAsync(5000);
    await flushPromises();
    expect(apiMock.getTrainingObservabilitySummary).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(5000);
    await flushPromises();
    expect(apiMock.getTrainingObservabilitySummary).toHaveBeenCalledTimes(3);

    await vi.advanceTimersByTimeAsync(10000);
    expect(apiMock.getTrainingObservabilitySummary).toHaveBeenCalledTimes(3);
    wrapper.unmount();
  });

  it("selects the job requested by the native visualization route", async () => {
    routeMock.query = { job: "job-2" };
    apiMock.getTrainingObservabilitySummary.mockResolvedValueOnce(summary({
      job_id: "job-2",
      pipeline_id: "pipeline-2",
      pipeline_name: "柑橘检测",
      status: "succeeded",
    }));

    const wrapper = mountView();
    await flushPromises();

    expect(apiMock.getTrainingObservabilitySummary).toHaveBeenCalledWith("job-2");
    expect(wrapper.get('[data-testid="job-job-2"]').classes()).toContain("active");
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
