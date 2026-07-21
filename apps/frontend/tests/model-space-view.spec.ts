import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ModelSpaceView from "@/views/model-space/ModelSpaceView.vue";
import modelSpaceViewSource from "@/views/model-space/ModelSpaceView.vue?raw";

const pushMock = vi.hoisted(() => vi.fn());
const apiMock = vi.hoisted(() => ({
  listBaseModels: vi.fn(),
  listTrainedModels: vi.fn(),
  listDatasets: vi.fn(),
  listPipelines: vi.fn(),
  listTrainingJobs: vi.fn(),
  listTrainingJobArtifacts: vi.fn(),
  trainingJobArtifactDownloadUrl: vi.fn(),
  analyzeDataset: vi.fn(),
  listDatasetSamples: vi.fn(),
  datasetSampleContentUrl: vi.fn(),
  createPipeline: vi.fn(),
  createTrainingJob: vi.fn(),
  updatePipeline: vi.fn(),
  deletePipeline: vi.fn(),
  predictPipelineImage: vi.fn(),
  evaluatePipeline: vi.fn(),
  listPipelineEvaluations: vi.fn(),
  markTrainedModelWeight: vi.fn(),
  createService: vi.fn(),
  listResourcePools: vi.fn(),
  listNodes: vi.fn(),
}));

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: pushMock }),
}));

vi.mock("@/api/client", () => ({
  api: apiMock,
}));

vi.mock("element-plus", async () => {
  const actual = await vi.importActual<typeof import("element-plus")>("element-plus");
  return {
    ...actual,
    ElMessage: {
      error: vi.fn(),
      success: vi.fn(),
      warning: vi.fn(),
    },
  };
});

function mountView() {
  return mount(ModelSpaceView, {
    global: {
      stubs: {
        "el-alert": true,
        "el-button": {
          inheritAttrs: false,
          emits: ["click"],
          template: "<button :data-testid=\"$attrs['data-testid']\" @click=\"$emit('click')\"><slot /></button>",
        },
        "el-checkbox": true,
        "el-dialog": {
          props: ["modelValue"],
          template: "<div v-if=\"modelValue\"><slot /><slot name=\"footer\" /></div>",
        },
        "el-empty": true,
        "el-form": { template: "<form><slot /></form>" },
        "el-form-item": { template: "<label><slot /></label>" },
        "el-icon": { template: "<span><slot /></span>" },
        "el-input": {
          props: ["modelValue"],
          emits: ["update:modelValue"],
          template:
            "<input :value=\"modelValue\" v-bind=\"$attrs\" @input=\"$emit('update:modelValue', $event.target.value)\" />",
        },
        "el-input-number": true,
        "el-option": {
          props: ["label", "value"],
          template: "<span>{{ label }}</span>",
        },
        "el-pagination": true,
        "el-radio": {
          props: ["label"],
          template: "<label><input type=\"radio\" :value=\"label\" /><slot /></label>",
        },
        "el-radio-group": { template: "<div><slot /></div>" },
        "el-select": { template: "<div><slot /></div>" },
        "el-slider": true,
        "el-step": true,
        "el-steps": { template: "<div><slot /></div>" },
        "el-switch": true,
      },
    },
  });
}

function mockDeployablePipeline() {
  apiMock.listPipelines.mockResolvedValueOnce({
    items: [
      {
        id: "pipeline-1",
        name: "3413",
        task: "detect",
        scale: "n",
        status: "success",
        base_model_id: "base-1",
        dataset_id: "dataset-1",
        params_template: {},
        default_environment: { device: "0" },
        is_favorite: false,
        is_public: false,
        created_at: "2026-07-03T10:43:14Z",
      },
    ],
  });
}

describe("ModelSpaceView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.listBaseModels.mockResolvedValue({
      items: [
        {
          id: "base-1",
          family: "YOLO26",
          filename: "yolo26n.pt",
          task: "detect",
          scale: "n",
          status: "ready",
          created_at: "2026-07-06T00:00:00Z",
        },
      ],
    });
    apiMock.listDatasets.mockResolvedValue({
      items: [
        {
          id: "dataset-1",
          name: "huajiao",
          task: "detect",
          status: "validated",
          sample_count: 12,
          annotation_count: 12,
          class_schema: { names: ["a", "b"] },
        },
      ],
    });
    apiMock.listTrainedModels.mockResolvedValue({ items: [] });
    apiMock.listPipelines.mockResolvedValue({
      items: [
        {
          id: "pipeline-1",
          name: "3413",
          task: "detect",
          scale: "n",
          status: "ready",
          base_model_id: "base-1",
          dataset_id: "dataset-1",
          params_template: { epochs: 12, batch: 8, lr0: 0.002, warmup_epochs: 4, save_period: 2 },
          default_environment: { device: "0" },
          is_favorite: true,
          is_public: false,
          created_at: "2026-07-03T10:43:14Z",
        },
      ],
    });
    apiMock.listTrainingJobs.mockResolvedValue({ items: [] });
    apiMock.listTrainingJobArtifacts.mockResolvedValue({ items: [] });
    apiMock.trainingJobArtifactDownloadUrl.mockImplementation(
      (jobId: string, kind: string, name: string) => `/training-jobs/${jobId}/artifacts/${kind}/${name}`,
    );
    apiMock.analyzeDataset.mockResolvedValue({
      payload: {
        result: {
          sample_count: 12,
          split_distribution: { train: 7, val: 3, test: 2 },
          class_distribution: { pepper: 9, leaf: 3 },
          class_distribution_by_split: {
            train: { pepper: 7, leaf: 2 },
            val: { pepper: 2, leaf: 1 },
          },
        },
      },
    });
    apiMock.listDatasetSamples.mockResolvedValue({
      items: [{ id: "sample-1", dataset_id: "dataset-1", file_uri: "sample-1.jpg", annotation_status: "annotated" }],
    });
    apiMock.datasetSampleContentUrl.mockImplementation((datasetId: string, sampleId: string) => `/datasets/${datasetId}/samples/${sampleId}/content`);
    apiMock.createPipeline.mockResolvedValue({
      id: "pipeline-new",
      name: "新建产线",
      task: "detect",
      scale: "n",
      status: "draft",
      params_template: {},
      default_environment: {},
    });
    apiMock.createTrainingJob.mockResolvedValue({ id: "job-1" });
    apiMock.updatePipeline.mockResolvedValue({ id: "pipeline-1" });
    apiMock.deletePipeline.mockResolvedValue(undefined);
    apiMock.evaluatePipeline.mockResolvedValue({
      id: "eval-new",
      pipeline_id: "pipeline-1",
      dataset_id: "dataset-1",
      evaluation_set: "val",
      model_weight: "best.pt",
      environment: "cpu",
      status: "completed",
      score: 13.57,
      metrics: { "metrics/mAP50(B)": 13.57 },
      created_at: "2026-07-09T10:24:33Z",
    });
    apiMock.listPipelineEvaluations.mockResolvedValue({
      items: [
        {
          id: "eval-1",
          pipeline_id: "pipeline-1",
          dataset_id: "dataset-1",
          evaluation_set: "val",
          model_weight: "best.pt",
          environment: "cpu",
          status: "completed",
          score: 0.5341,
          metrics: {
            "metrics/precision(B)": 0.3603,
            "metrics/recall(B)": 0.8057,
            "metrics/mAP50(B)": 0.5341,
            "metrics/mAP50-95(B)": 0.4205,
            fitness: 0.4205,
          },
          created_at: "2026-07-09T10:24:33Z",
        },
      ],
    });
    apiMock.markTrainedModelWeight.mockImplementation((modelId: string, payload: { deployment_name: string }) =>
      Promise.resolve({
        id: modelId,
        pipeline_id: "pipeline-1",
        training_job_id: "job-1",
        name: modelId === "model-best" ? "best.pt" : "last.pt",
        version: modelId === "model-best" ? "best.pt" : "last.pt",
        task: "detect",
        artifact_uri: `memory://models/trained/detect/${modelId === "model-best" ? "best.pt" : "last.pt"}`,
        metrics: { deployment_name: payload.deployment_name },
        status: "ready",
      }),
    );
    apiMock.createService.mockResolvedValue({ id: "service-1", status: "running" });
    apiMock.listResourcePools.mockResolvedValue({
      items: [
        {
          id: "pool-x86",
          name: "x86-nvidia-sm86",
          kind: "x86_nvidia",
          selector: {},
          compatibility_policy: { cuda_major: 12, tensorrt_major: 10, compute_capability: "8.6" },
          enabled: true,
        },
      ],
      total: 1,
    });
    apiMock.listNodes.mockResolvedValue({
      items: [
        {
          id: "node-a",
          name: "边缘节点 A",
          resource_pool_id: "pool-x86",
          status: "online",
          architecture: "x86_64",
          platform_kind: "x86_nvidia",
          capabilities: { gpu_uuids: ["GPU-a"] },
          resources: { gpu_name: "RTX 4090", gpu_memory_total_mb: 24576, gpu_memory_free_mb: 22000 },
          fingerprint: {},
          agent_version: "ssh",
        },
      ],
      total: 1,
    });
  });

  it("opens result files and routes the selected job to native training visualization", async () => {
    apiMock.listPipelines.mockResolvedValueOnce({
      items: [
        {
          id: "pipeline-1",
          name: "3413",
          task: "detect",
          scale: "n",
          status: "success",
          base_model_id: "base-1",
          dataset_id: "dataset-1",
          params_template: {},
          default_environment: { device: "cpu" },
          is_favorite: false,
          is_public: false,
          created_at: "2026-07-03T10:43:14Z",
        },
      ],
    });
    apiMock.listTrainingJobs.mockResolvedValueOnce({
      items: [
        {
          id: "job-1",
          pipeline_id: "pipeline-1",
          status: "success",
          metrics: {},
          created_at: "2026-07-03T10:43:14Z",
        },
      ],
    });
    apiMock.listTrainingJobArtifacts.mockResolvedValueOnce({
      items: [
        { name: "best.pt", kind: "weight", size_bytes: 30408704, download_url: "/training-jobs/job-1/artifacts/weight/best.pt" },
        { name: "last.pt", kind: "weight", size_bytes: 30408704, download_url: "/training-jobs/job-1/artifacts/weight/last.pt" },
        { name: "results.png", kind: "visualization", size_bytes: 153600, download_url: "/training-jobs/job-1/artifacts/visualization/results.png" },
      ],
    });

    const wrapper = mountView();
    await vi.waitFor(() => expect(apiMock.listTrainingJobs).toHaveBeenCalledTimes(1));
    await flushPromises();
    await wrapper.get('[data-testid="pipeline-card-pipeline-1"]').trigger("click");
    await wrapper.get('[data-testid="open-result-files"]').trigger("click");
    await flushPromises();

    expect(apiMock.listTrainingJobArtifacts).toHaveBeenCalledWith("job-1");
    expect(wrapper.text()).toContain("best.pt");
    expect(wrapper.text()).toContain("last.pt");
    expect(wrapper.text()).toContain("results.png");
    expect(wrapper.text()).toContain("29 MB");
    expect(wrapper.findAll('[data-testid^="download-result-"]')).toHaveLength(3);

    await wrapper.findAll("button").find((button) => button.text() === "可视化训练")?.trigger("click");
    expect(pushMock).toHaveBeenCalledWith({
      path: "/training-visualization",
      query: { job: "job-1" },
    });
    expect(wrapper.find("iframe").exists()).toBe(false);
    expect(modelSpaceViewSource).not.toMatch(/<iframe|127\.0\.0\.1:5001|127\.0\.0\.1:6006/);
  });

  it("renders pipeline cards and opens the create pipeline wizard inside model space", async () => {
    const wrapper = mountView();

    await vi.waitFor(() => expect(apiMock.listPipelines).toHaveBeenCalledTimes(1));
    await flushPromises();

    expect(wrapper.text()).toContain("模型空间");
    expect(wrapper.text()).toContain("3413");
    expect(wrapper.text()).toContain("目标检测");

    await wrapper.get('[data-testid="create-pipeline"]').trigger("click");

    expect(pushMock).not.toHaveBeenCalled();
    expect(wrapper.text()).toContain("零代码产线");
    expect(wrapper.text()).toContain("任务场景");

    await wrapper.get('[data-testid="confirm-create-pipeline"]').trigger("click");
    await flushPromises();

    expect(apiMock.createPipeline).toHaveBeenCalledWith({ name: "新建产线", task: "detect", scale: "n" });
    expect(wrapper.text()).toContain("选择产线");
    expect(wrapper.text()).toContain("数据准备");
    expect(wrapper.text()).toContain("参数准备");
    expect(wrapper.text()).toContain("提交训练");
  });

  it("reuses dataset processing visualization tabs in the training wizard", async () => {
    const wrapper = mountView();

    await vi.waitFor(() => expect(apiMock.listPipelines).toHaveBeenCalledTimes(1));
    await wrapper.get('[data-testid="create-pipeline"]').trigger("click");
    await wrapper.get('[data-testid="confirm-create-pipeline"]').trigger("click");
    await flushPromises();
    await wrapper.findAll("button").find((button) => button.text() === "下一步")?.trigger("click");
    await flushPromises();

    await vi.waitFor(() => expect(apiMock.analyzeDataset).toHaveBeenCalledWith("dataset-1"));
    expect(apiMock.listDatasetSamples).toHaveBeenCalledWith("dataset-1", { split: "train", limit: 60 });
    expect(apiMock.listDatasetSamples).toHaveBeenCalledWith("dataset-1", { split: "val", limit: 60 });
    expect(apiMock.listDatasetSamples).toHaveBeenCalledWith("dataset-1", { split: "test", limit: 60 });
    expect(wrapper.text()).toContain("训练集");
    expect(wrapper.text()).toContain("验证集");
    expect(wrapper.text()).toContain("测试集");
    expect(wrapper.text()).toContain("类别分布图");
    await wrapper.findAll("button").find((button) => button.text() === "类别分布图")?.trigger("click");
    expect(wrapper.text()).toContain("train");
    expect(wrapper.text()).toContain("val");
    expect(wrapper.text()).toContain("pepper");
  });

  it("opens an existing pipeline card in the four-step wizard", async () => {
    const wrapper = mountView();

    await vi.waitFor(() => expect(apiMock.listPipelines).toHaveBeenCalledTimes(1));
    await flushPromises();

    await wrapper.get('[data-testid="pipeline-card-pipeline-1"]').trigger("click");

    expect(wrapper.text()).toContain("返回产线列表");
    expect(wrapper.text()).toContain("选择产线");
    expect(wrapper.text()).toContain("数据准备");
    expect(wrapper.text()).toContain("参数准备");
    expect(wrapper.text()).toContain("提交训练");
    expect(wrapper.text()).toContain("3413");
    const stepButtons = wrapper.findAll(".wizard-steps button");
    expect(stepButtons).toHaveLength(4);

    await stepButtons[2].trigger("click");
    expect(wrapper.text()).toContain("Log Interval");

    await stepButtons[3].trigger("click");
    expect(wrapper.find(".submit-step").exists()).toBe(true);

    await stepButtons[0].trigger("click");
    await wrapper.findAll("button").find((button) => button.text() === "下一步")?.trigger("click");
    await flushPromises();
    await vi.waitFor(() => expect(apiMock.analyzeDataset).toHaveBeenCalledWith("dataset-1"));
  });

  it("shows saved pipeline evaluation history with ultralytics metrics", async () => {
    apiMock.listPipelines.mockResolvedValueOnce({
      items: [
        {
          id: "pipeline-1",
          name: "3413",
          task: "detect",
          scale: "n",
          status: "success",
          base_model_id: "base-1",
          dataset_id: "dataset-1",
          params_template: {},
          default_environment: { device: "cpu" },
          is_favorite: false,
          is_public: false,
          created_at: "2026-07-03T10:43:14Z",
        },
      ],
    });
    const wrapper = mountView();

    await vi.waitFor(() => expect(apiMock.listPipelines).toHaveBeenCalledTimes(1));
    await flushPromises();
    await wrapper.get('[data-testid="pipeline-card-pipeline-1"]').trigger("click");
    await wrapper.findAll("button").find((button) => button.text() === "评估")?.trigger("click");
    await wrapper.findAll("button").find((button) => button.text() === "评估历史")?.trigger("click");
    await flushPromises();

    expect(apiMock.listPipelineEvaluations).toHaveBeenCalledWith("pipeline-1", { limit: 50 });
    expect(wrapper.text()).toContain("请选择评估记录");
    expect(wrapper.text()).toContain("2026-07-09 18:24:33");
    expect(wrapper.text()).toContain("评估完成");
    expect(wrapper.text()).toContain("huajiao");
    expect(wrapper.text()).toContain("best.pt");
    expect(wrapper.text()).toContain("mAP50");
    expect(wrapper.text()).toContain("0.5341");
    expect(wrapper.text()).toContain("precision");
    expect(wrapper.text()).toContain("0.3603");
  });

  it("marks a trained weight with a deploy-only name and keeps true evaluation scores", async () => {
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
    apiMock.listPipelines.mockResolvedValueOnce({
      items: [
        {
          id: "pipeline-1",
          name: "3413",
          task: "detect",
          scale: "n",
          status: "success",
          base_model_id: "base-1",
          dataset_id: "dataset-1",
          params_template: {},
          default_environment: { device: "cpu" },
          is_favorite: false,
          is_public: false,
          created_at: "2026-07-03T10:43:14Z",
        },
      ],
    });
    apiMock.listTrainedModels.mockResolvedValueOnce({
      items: [
        {
          id: "model-best",
          pipeline_id: "pipeline-1",
          training_job_id: "job-1",
          name: "best.pt",
          version: "best.pt",
          task: "detect",
          artifact_uri: "memory://models/trained/detect/best.pt",
          metrics: { deployment_name: "best_model" },
          status: "ready",
        },
        {
          id: "model-last",
          pipeline_id: "pipeline-1",
          training_job_id: "job-1",
          name: "last.pt",
          version: "last.pt",
          task: "detect",
          artifact_uri: "memory://models/trained/detect/last.pt",
          metrics: {},
          status: "ready",
        },
      ],
    });
    apiMock.listTrainingJobs.mockResolvedValueOnce({
      items: [
        {
          id: "job-1",
          pipeline_id: "pipeline-1",
          status: "success",
          metrics: {},
          created_at: "2026-07-09T10:20:00Z",
        },
      ],
    });
    apiMock.listPipelineEvaluations.mockResolvedValueOnce({
      items: [
        {
          id: "eval-best",
          pipeline_id: "pipeline-1",
          dataset_id: "dataset-1",
          evaluation_set: "val",
          model_weight: "best.pt",
          environment: "cpu",
          status: "completed",
          score: 0.5341,
          metrics: { "metrics/mAP50(B)": 0.5341 },
          created_at: "2026-07-09T10:24:33Z",
        },
        {
          id: "eval-last",
          pipeline_id: "pipeline-1",
          dataset_id: "dataset-1",
          evaluation_set: "val",
          model_weight: "last.pt",
          environment: "cpu",
          status: "completed",
          score: 0.4205,
          metrics: { "metrics/mAP50(B)": 0.4205 },
          created_at: "2026-07-09T10:25:33Z",
        },
      ],
    });

    const wrapper = mountView();

    await vi.waitFor(() => expect(apiMock.listTrainedModels).toHaveBeenCalledTimes(1));
    await flushPromises();
    await wrapper.get('[data-testid="pipeline-card-pipeline-1"]').trigger("click");
    await flushPromises();
    await wrapper.get('[data-testid="detail-tab-evaluate"]').trigger("click");
    await vi.waitFor(() => expect(apiMock.listPipelineEvaluations).toHaveBeenCalledWith("pipeline-1", { limit: 50 }));
    await flushPromises();

    expect(wrapper.text()).toContain("0.5341");
    expect(wrapper.text()).toContain("0.4205");

    await wrapper.findAll("button").find((button) => button.text() === "标记权重")?.trigger("click");
    expect(wrapper.text()).toContain("请为模型权重命名，以便于在部署环节快速找到它");
    const input = wrapper.get('input[placeholder="请输入权重名称"]');
    expect((input.element as HTMLInputElement).value).toBe("best_model");
    await input.setValue("数据集A评估最佳");
    await wrapper.findAll("button").find((button) => button.text() === "确定")?.trigger("click");
    await flushPromises();

    expect(apiMock.markTrainedModelWeight).toHaveBeenCalledWith("model-best", {
      deployment_name: "数据集A评估最佳",
    });

    await wrapper.findAll("button").find((button) => button.text() === "部署")?.trigger("click");
    expect(wrapper.text()).toContain("数据集A评估最佳");

    expect(wrapper.text()).toContain("服务名称：");
    expect(wrapper.text()).toContain("选择环境：");
    await wrapper.findAll("button").find((button) => button.text() === "离线部署")?.trigger("click");
    await flushPromises();

    expect(wrapper.text()).not.toContain("服务名称：");
    expect(wrapper.text()).toContain("导出模型文件");
    await wrapper.findAll("button").find((button) => button.text() === "导出模型文件")?.trigger("click");
    expect(openSpy).toHaveBeenCalledWith(
      "/training-jobs/job-1/artifacts/weight/best.pt",
      "_blank",
      "noopener,noreferrer",
    );
  });

  it("loads real edge pools and nodes and submits automatic TensorRT optimization", async () => {
    mockDeployablePipeline();
    apiMock.listTrainedModels.mockResolvedValueOnce({
      items: [
        {
          id: "model-best",
          pipeline_id: "pipeline-1",
          training_job_id: "job-1",
          name: "best.pt",
          version: "best.pt",
          task: "detect",
          artifact_uri: "minio://models/best.pt",
          metrics: {
            checksum: "a".repeat(64),
            deployment_image_digest: `sha256:${"b".repeat(64)}`,
          },
          status: "ready",
        },
      ],
    });
    const wrapper = mountView();
    await flushPromises();
    await wrapper.get('[data-testid="pipeline-card-pipeline-1"]').trigger("click");
    await wrapper.get('[data-testid="detail-tab-deploy"]').trigger("click");
    await flushPromises();

    expect(apiMock.listResourcePools).toHaveBeenCalledTimes(1);
    expect(apiMock.listNodes).toHaveBeenCalledTimes(1);
    expect(wrapper.text()).toContain("x86-nvidia-sm86");
    expect(wrapper.text()).toContain("边缘节点 A");
    expect(wrapper.text()).toContain("RTX 4090");
    expect(wrapper.text()).toContain("自动优化");
    expect(wrapper.text()).toContain("TensorRT FP16");

    const startButton = wrapper.findAll("button").find((button) => button.text().includes("开始部署"));
    expect(startButton).toBeDefined();
    await startButton!.trigger("click");
    await flushPromises();

    expect(apiMock.createService).toHaveBeenCalledWith(
      expect.objectContaining({
        trained_model_id: "model-best",
        node_id: "node-a",
        image_digest: `sha256:${"b".repeat(64)}`,
        model_checksum: "a".repeat(64),
        format: "auto",
        precision: "auto",
        input_shape: [1, 3, 640, 640],
        gpu_uuids: ["GPU-a"],
      }),
    );
  });

  it("reveals manual optimization controls", async () => {
    mockDeployablePipeline();
    const wrapper = mountView();
    await flushPromises();
    await wrapper.get('[data-testid="pipeline-card-pipeline-1"]').trigger("click");
    await wrapper.get('[data-testid="detail-tab-deploy"]').trigger("click");
    await flushPromises();

    await wrapper.get('[data-testid="optimization-manual"]').trigger("click");
    expect(wrapper.text()).toContain("导出格式");
    expect(wrapper.text()).toContain("推理精度");
    expect(wrapper.text()).toContain("输入尺寸");
    expect(wrapper.text()).toContain("INT8 校准数据集");
  });
});
