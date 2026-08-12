import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ModelSpaceView from "@/views/model-space/ModelSpaceView.vue";
import modelSpaceViewSource from "@/views/model-space/ModelSpaceView.vue?raw";
import { hasForbiddenHoverElevation, topLevelRuleDeclarations } from "./helpers/css-rules";

const pushMock = vi.hoisted(() => vi.fn());
const replaceMock = vi.hoisted(() => vi.fn());
const routeMock = vi.hoisted(() => ({ query: {} as Record<string, string> }));
const apiMock = vi.hoisted(() => ({
  listBaseModels: vi.fn(),
  uploadBaseModel: vi.fn(),
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
  clonePipeline: vi.fn(),
  deletePipeline: vi.fn(),
  predictPipelineImage: vi.fn(),
  evaluatePipeline: vi.fn(),
  listPipelineEvaluations: vi.fn(),
  markTrainedModelWeight: vi.fn(),
  createService: vi.fn(),
  listResourcePools: vi.fn(),
  listNodes: vi.fn(),
  getFrameworkCapabilities: vi.fn(),
}));

vi.mock("vue-router", () => ({
  useRoute: () => routeMock,
  useRouter: () => ({ push: pushMock, replace: replaceMock }),
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
      info: vi.fn(),
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
        "el-cascader": {
          props: ["modelValue", "options"],
          emits: ["update:modelValue"],
          template: `
            <div data-testid="framework-model-cascader">
              <template v-for="group in options" :key="group.value">
                <button
                  v-for="model in group.children"
                  :key="model.value"
                  type="button"
                  :data-testid="'model-' + model.value"
                  @click="$emit('update:modelValue', [group.value, model.value])"
                >{{ model.label }}</button>
              </template>
            </div>
          `,
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
  it("uses the standard shared pipeline card surface", () => {
    const cardRule = topLevelRuleDeclarations(modelSpaceViewSource, ".pipeline-card", "sfc");
    expect(cardRule?.get("background")).toEqual(["var(--visiox-card-surface)"]);
    expect(cardRule?.get("border")).toEqual(["1px solid var(--visiox-card-border)"]);
    expect(cardRule?.get("border-radius")).toEqual(["var(--visiox-card-radius)"]);
  });

  it("limits restrained hover feedback to non-skeleton pipeline cards", () => {
    expect(topLevelRuleDeclarations(modelSpaceViewSource, ".pipeline-card:hover", "sfc")).toBeUndefined();
    const hoverRule = topLevelRuleDeclarations(
      modelSpaceViewSource,
      ".pipeline-card:not(.skeleton-card):hover",
      "sfc",
    );
    expect(hoverRule?.get("border-color")).toEqual(["#aeb7c3"]);
    expect(hasForbiddenHoverElevation(hoverRule)).toBe(false);
  });

  it("keeps the narrowest pipeline and scenario grids shrinkable", () => {
    const narrowContainer = modelSpaceViewSource.match(
      /@container model-space \(max-width: 480px\) \{([\s\S]*?)\n\}/,
    )?.[1];

    expect(narrowContainer).toMatch(/\.pipeline-grid,\s*\.scenario-grid/);
    expect(narrowContainer).toContain("grid-template-columns: minmax(0, 1fr)");
  });

  it("uses the real shared resource dialog instead of hard-coded public scopes", () => {
    expect(modelSpaceViewSource).toContain("ResourceSharingDialog");
    expect(modelSpaceViewSource).not.toContain('const scopeOptions = [');
    expect(modelSpaceViewSource).not.toContain("confirmPublicConfig");
  });

  it("anchors the list pagination and styles its selected page", () => {
    expect(modelSpaceViewSource).toContain("'list-mode': viewMode === 'list'");
    expect(modelSpaceViewSource).toContain("position: sticky");
    expect(modelSpaceViewSource).toContain("margin-top: auto");
    expect(modelSpaceViewSource).toContain("li.is-active");
  });

  beforeEach(() => {
    vi.clearAllMocks();
    routeMock.query = {};
    replaceMock.mockImplementation(async (location: { query?: Record<string, string> }) => {
      routeMock.query = location.query ?? {};
    });
    apiMock.listBaseModels.mockResolvedValue({
      items: [
        {
          id: "base-1",
          family: "YOLO26",
          filename: "yolo26n.pt",
          task: "detect",
          scale: "n",
          status: "ready",
          local_uri: "minio://models/base/yolo26-detect-n/yolo26n.pt",
          checksum: "c".repeat(64),
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
    apiMock.uploadBaseModel.mockResolvedValue({
      id: "custom-model-1",
      family: "custom-custom-model-1",
      filename: "custom.pt",
      task: "detect",
      scale: "n",
      status: "ready",
      local_uri: "memory://models/custom/custom-model-1/custom.pt",
      checksum: "d".repeat(64),
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
    apiMock.clonePipeline.mockResolvedValue({
      id: "pipeline-clone",
      name: "3413 copy",
      task: "detect",
      scale: "n",
      status: "draft",
      framework: "ultralytics",
      task_kind: "object_detection",
      recipe: { model: { key: "yolo26-n" } },
      params_template: {},
      default_environment: {},
    });
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
    apiMock.getFrameworkCapabilities.mockImplementation(async (taskKind: string) => ({
      task_kind: taskKind,
      adapters: taskKind === "llm_sft" ? [
        {
          framework: "llamafactory",
          adapter_key: "llamafactory.llm_sft.v1",
          adapter_version: "1.0.0",
          display_name: "LLaMA-Factory",
          available: true,
          tasks: [
            {
              task_type: "llm_sft",
              models: [
                {
                  model_key: "qwen3-0.6b",
                  display_name: "Qwen3-0.6B",
                  family: "qwen3",
                  variant: "0.6b",
                },
              ],
              accepted_dataset_formats: ["sharegpt", "alpaca"],
              convertible_dataset_formats: [],
              resources: {
                resource_kinds: ["cuda"],
                cpu_cores_min: 4,
                memory_mb_min: 16384,
                gpu_count_min: 1,
                gpu_memory_mb_min: 12288,
              },
              parameters: [],
            },
          ],
        },
      ] : [
        {
          framework: "ultralytics",
          adapter_key: "ultralytics.object_detection.v1",
          adapter_version: "1.0.0",
          display_name: "Ultralytics",
          available: true,
          tasks: [
            {
              task_type: "object_detection",
              models: [
                {
                  model_key: "yolo26-n",
                  display_name: "YOLO26-N",
                  family: "yolo26",
                  variant: "n",
                },
              ],
              accepted_dataset_formats: ["yolo"],
              convertible_dataset_formats: ["coco"],
              resources: {
                resource_kinds: ["cpu", "cuda"],
                cpu_cores_min: 2,
                memory_mb_min: 4096,
                gpu_count_min: 0,
                gpu_memory_mb_min: 0,
              },
              parameters: [],
            },
          ],
        },
        {
          framework: "paddlex",
          adapter_key: "paddlex.object_detection.v1",
          adapter_version: "1.0.0",
          display_name: "PaddleX",
          available: true,
          tasks: [
            {
              task_type: "object_detection",
              models: [
                {
                  model_key: "pp-yoloe-s",
                  display_name: "PP-YOLOE-S",
                  family: "PP-YOLOE",
                  variant: "S",
                },
              ],
              accepted_dataset_formats: ["coco"],
              convertible_dataset_formats: ["yolo"],
              resources: {
                resource_kinds: ["cuda"],
                cpu_cores_min: 4,
                memory_mb_min: 8192,
                gpu_count_min: 1,
                gpu_memory_mb_min: 8192,
              },
              parameters: [{ name: "epochs", value_type: "integer", required: false, default: 100 }],
            },
          ],
        },
      ],
    }));
  });

  it("loads the object detection framework catalog before creating a pipeline", async () => {
    const wrapper = mountView();
    await vi.waitFor(() => expect(apiMock.listPipelines).toHaveBeenCalledTimes(1));

    await wrapper.get('[data-testid="create-pipeline"]').trigger("click");

    await vi.waitFor(() => expect(apiMock.getFrameworkCapabilities).toHaveBeenCalledWith("object_detection"));
    expect(wrapper.find(".create-dialog .framework-model-selector").exists()).toBe(false);
  });

  it("selects the framework first, selects its model with the dataset, and prepares parameters last", async () => {
    const wrapper = mountView();
    await vi.waitFor(() => expect(apiMock.listPipelines).toHaveBeenCalledTimes(1));

    await wrapper.get('[data-testid="create-pipeline"]').trigger("click");
    await vi.waitFor(() => expect(apiMock.getFrameworkCapabilities).toHaveBeenCalledWith("object_detection"));
    expect(wrapper.find('[data-testid="framework-paddlex"]').exists()).toBe(false);
    await wrapper.get('[data-testid="confirm-create-pipeline"]').trigger("click");
    await flushPromises();

    await vi.waitFor(() => expect(wrapper.find('[data-testid="framework-paddlex"]').exists()).toBe(true));
    expect(wrapper.find('[data-testid="model-yolo26-n"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="model-pp-yoloe-s"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="framework-parameter-epochs"]').exists()).toBe(false);
    await wrapper.get('[data-testid="framework-paddlex"]').trigger("click");
    await flushPromises();

    expect(apiMock.createPipeline).toHaveBeenCalledWith(expect.objectContaining({
      engine: "yolo26",
      task: "detect",
      scale: "n",
      task_kind: "object_detection",
      framework: "ultralytics",
      adapter_key: "ultralytics.object_detection.v1",
      model_family: "yolo26",
      recipe: { model: { key: "yolo26-n" } },
    }));
    await vi.waitFor(() => expect(apiMock.updatePipeline).toHaveBeenCalledWith(
      "pipeline-new",
      expect.objectContaining({
        engine: "paddlex",
        framework: "paddlex",
        adapter_key: "paddlex.object_detection.v1",
        recipe: { model: { key: "pp-yoloe-s" } },
      }),
    ));

    await wrapper.findAll("button").find((button) => button.text() === "下一步")?.trigger("click");
    await flushPromises();
    expect(wrapper.find('[data-testid="framework-paddlex"]').exists()).toBe(false);
    expect(wrapper.get('[data-testid="model-pp-yoloe-s"]').text()).toContain("PP-YOLOE-S");
    await wrapper.get('[data-testid="model-pp-yoloe-s"]').trigger("click");
    await flushPromises();

    await wrapper.findAll("button").find((button) => button.text() === "下一步")?.trigger("click");
    await flushPromises();
    const parameterStep = wrapper.findAll(".wizard-steps button").find((button) => button.text().includes("参数准备"));
    expect(parameterStep?.classes()).toContain("active");
    expect(wrapper.find('[data-testid="framework-paddlex"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="framework-parameter-epochs"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="advanced-yaml"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="object-detection-training-config"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("表单配置");
    expect(wrapper.text()).toContain("YAML 配置");
    expect(wrapper.text()).not.toContain("轮次(Epochs)");
    expect(wrapper.text()).not.toContain("修改配置文件");

    await wrapper.get('[data-testid="yaml-tab"]').trigger("click");
    const configEditor = wrapper.get<HTMLTextAreaElement>('[data-testid="framework-yaml-editor"]');
    expect(configEditor.element.value).toContain("epochs: 100");
    expect(configEditor.element.value).not.toContain("Ultralytics");
    await configEditor.setValue("epochs: fast");
    expect(wrapper.get('[data-testid="yaml-errors"]').text()).toContain("epochs 必须是整数");

    await wrapper.findAll("button").find((button) => button.text() === "下一步")?.trigger("click");
    await flushPromises();
    expect(parameterStep?.classes()).toContain("active");
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

    expect(apiMock.createPipeline).toHaveBeenCalledWith(expect.objectContaining({
      name: "新建产线",
      engine: "yolo26",
      task: "detect",
      scale: "n",
      task_kind: "object_detection",
      framework: "ultralytics",
      model_family: "yolo26",
      recipe: { model: { key: "yolo26-n" } },
    }));
    expect(wrapper.text()).toContain("选择产线");
    expect(wrapper.text()).toContain("数据准备");
    expect(wrapper.text()).toContain("参数准备");
    expect(wrapper.text()).toContain("提交训练");
  });

  it("uploads a local pt model and binds it to the draft pipeline", async () => {
    const wrapper = mountView();
    await vi.waitFor(() => expect(apiMock.listPipelines).toHaveBeenCalledTimes(1));
    await wrapper.get('[data-testid="create-pipeline"]').trigger("click");
    await wrapper.findAll("button").find((button) => button.text() === "本地模型")?.trigger("click");
    const file = new File([new Uint8Array(2048)], "custom.pt", { type: "application/octet-stream" });
    const input = wrapper.get('[data-testid="local-model-file"]');
    Object.defineProperty(input.element, "files", { value: [file], configurable: true });
    await input.trigger("change");
    await wrapper.get('[data-testid="confirm-create-pipeline"]').trigger("click");
    await flushPromises();

    expect(apiMock.uploadBaseModel).toHaveBeenCalledWith(file, { task: "detect", scale: "n" });
    expect(apiMock.createPipeline).toHaveBeenCalledWith({
      name: "新建产线",
      engine: "yolo26",
      task: "detect",
      scale: "n",
      base_model_id: "custom-model-1",
    });
    expect(wrapper.text()).toContain("选择产线");
  });

  it("uses the dedicated LLM wizard instead of YOLO fields", async () => {
    apiMock.listDatasets.mockResolvedValueOnce({
      items: [
        {
          id: "llm-dataset-1",
          name: "equipment-sft",
          task: "llm",
          status: "validated",
          sample_count: 120,
          annotation_count: 120,
          class_schema: { format: "sharegpt" },
        },
      ],
    });
    apiMock.createPipeline.mockResolvedValueOnce({
      id: "pipeline-llm",
      name: "新建产线",
      task: "llm",
      scale: "llm",
      status: "draft",
      params_template: {},
      default_environment: {},
    });
    apiMock.updatePipeline.mockResolvedValueOnce({
      id: "pipeline-llm",
      name: "新建产线",
      task: "llm",
      scale: "llm",
      status: "draft",
      params_template: { engine: "llamafactory" },
      default_environment: { device: "remote" },
    });

    const wrapper = mountView();
    await vi.waitFor(() => expect(apiMock.listPipelines).toHaveBeenCalledTimes(1));
    await wrapper.get('[data-testid="create-pipeline"]').trigger("click");
    await wrapper.findAll("button").find((button) => button.text().includes("大模型训练"))?.trigger("click");
    await wrapper.get('[data-testid="confirm-create-pipeline"]').trigger("click");
    await flushPromises();

    expect(apiMock.createPipeline).toHaveBeenCalledWith(expect.objectContaining({
      name: "新建产线",
      engine: "llamafactory",
      task: "llm",
      scale: "0.6b",
      task_kind: "llm_sft",
      framework: "llamafactory",
      model_family: "qwen3",
      recipe: { model: { key: "qwen3-0.6b" } },
    }));
    expect(wrapper.get('[data-testid="llm-pipeline-wizard"]').text()).toContain("监督微调 SFT");
    expect(wrapper.text()).not.toContain("直接部署");

    await wrapper.findAll("button").find((button) => button.text() === "下一步")?.trigger("click");
    await flushPromises();

    const draftPatch = apiMock.updatePipeline.mock.calls[apiMock.updatePipeline.mock.calls.length - 1]?.[1];
    expect(draftPatch?.params_template).toHaveProperty("model_id", "qwen3-0.6b");
    expect(draftPatch?.default_environment).not.toHaveProperty("resource_pool_id");
    expect(draftPatch?.default_environment).not.toHaveProperty("node_id");
    expect(wrapper.text()).toContain("模型与数据");
    expect(wrapper.text()).toContain("Hugging Face");
    expect(wrapper.text()).toContain("ModelScope");
    expect(wrapper.text()).not.toContain("类别数量(Class Num)");
    expect(wrapper.text()).not.toContain("数据切分");
    expect(replaceMock).toHaveBeenLastCalledWith({
      path: "/model-space",
      query: { pipeline: "pipeline-llm", step: "data" },
    });
  });

  it("restores an LLM draft and its step from the URL", async () => {
    routeMock.query = { pipeline: "pipeline-llm-existing", step: "data" };
    apiMock.listPipelines.mockResolvedValueOnce({
      items: [
        {
          id: "pipeline-llm-existing",
          name: "LLM draft",
          engine: "llamafactory",
          task: "llm",
          scale: "llm",
          status: "draft",
          params_template: {},
          default_environment: {},
        },
      ],
    });

    const wrapper = mountView();
    await vi.waitFor(() => expect(apiMock.listPipelines).toHaveBeenCalledTimes(1));
    await flushPromises();

    expect(wrapper.text()).toContain("模型与数据");
    expect(wrapper.find('[data-testid="llm-pipeline-wizard"]').exists()).toBe(true);
  });

  it("starts the LLM wizard with LLaMA-Factory even when its runtime probe is unavailable", async () => {
    apiMock.getFrameworkCapabilities.mockImplementationOnce(async () => ({ task_kind: "object_detection", adapters: [] }));
    apiMock.getFrameworkCapabilities.mockImplementationOnce(async () => ({
      task_kind: "llm_sft",
      adapters: [{
        framework: "llamafactory",
        adapter_key: "llamafactory.llm_sft.v1",
        adapter_version: "1.0.0",
        display_name: "LLaMA-Factory",
        available: false,
        unavailable_reason: "Runtime probe unavailable",
        tasks: [{
          task_type: "llm_sft",
          models: [{ model_key: "qwen3-0.6b", display_name: "Qwen3-0.6B", family: "qwen3", variant: "0.6b" }],
          accepted_dataset_formats: ["sharegpt"],
          convertible_dataset_formats: [],
          resources: { resource_kinds: ["cuda"], cpu_cores_min: 4, memory_mb_min: 16384, gpu_count_min: 1, gpu_memory_mb_min: 12288 },
          parameters: [],
        }],
      }],
    }));
    apiMock.createPipeline.mockResolvedValueOnce({
      id: "pipeline-llm-unavailable-runtime",
      name: "新建产线",
      engine: "llamafactory",
      task: "llm",
      scale: "0.6b",
      task_kind: "llm_sft",
      framework: "llamafactory",
      adapter_key: "llamafactory.llm_sft.v1",
      adapter_version: "1.0.0",
      model_family: "qwen3",
      recipe: { model: { key: "qwen3-0.6b" } },
      status: "draft",
      params_template: {},
      default_environment: {},
    });

    const wrapper = mountView();
    await vi.waitFor(() => expect(apiMock.listPipelines).toHaveBeenCalledTimes(1));
    await wrapper.get('[data-testid="create-pipeline"]').trigger("click");
    await wrapper.findAll("button").find((button) => button.text().includes("大模型训练"))?.trigger("click");
    await wrapper.get('[data-testid="confirm-create-pipeline"]').trigger("click");
    await flushPromises();

    expect(apiMock.createPipeline).toHaveBeenCalledWith(expect.objectContaining({
      engine: "llamafactory",
      task: "llm",
      framework: "llamafactory",
      adapter_key: "llamafactory.llm_sft.v1",
    }));
    expect(wrapper.find('[data-testid="llm-pipeline-wizard"]').exists()).toBe(true);
  });

  it("creates an unsupported scenario as a pending capability draft without requiring a framework model", async () => {
    apiMock.createPipeline.mockResolvedValueOnce({
      id: "pipeline-document",
      name: "新建产线",
      engine: "pending",
      task: "document",
      scale: "pending",
      task_kind: "document",
      framework: "pending",
      adapter_key: "pending.document.v1",
      adapter_version: "1.0.0",
      model_family: "pending",
      recipe: { capability_status: "pending" },
      status: "draft",
      params_template: {},
      default_environment: {},
    });

    const wrapper = mountView();
    await vi.waitFor(() => expect(apiMock.listPipelines).toHaveBeenCalledTimes(1));
    await wrapper.get('[data-testid="create-pipeline"]').trigger("click");
    await wrapper.findAll("button").find((button) => button.text().includes("文档图像信息抽取"))?.trigger("click");
    await wrapper.get('[data-testid="confirm-create-pipeline"]').trigger("click");
    await flushPromises();

    expect(apiMock.createPipeline).toHaveBeenCalledWith({
      name: "新建产线",
      task: "document",
      task_kind: "document",
      framework: "pending",
      adapter_key: "pending.document.v1",
      adapter_version: "1.0.0",
      model_family: "pending",
      recipe: { capability_status: "pending" },
    });
    expect(wrapper.get('[data-testid="pending-capability"]').text()).toContain("训练能力待接入");
    expect(wrapper.find('[data-testid="framework-paddlex"]').exists()).toBe(false);
    expect(wrapper.find('[data-testid="wizard-next"]').exists()).toBe(false);
  });

  it("offers cloning from the detail page after framework identity is locked", async () => {
    apiMock.listPipelines.mockResolvedValueOnce({
      items: [
        {
          id: "pipeline-locked",
          name: "Locked PaddleX",
          engine: "paddlex",
          task: "detect",
          scale: "S",
          status: "success",
          task_kind: "object_detection",
          framework: "paddlex",
          adapter_key: "paddlex.object_detection.v1",
          adapter_version: "1.0.0",
          model_family: "PP-YOLOE",
          recipe: { model: { key: "pp-yoloe-s" } },
          framework_locked_at: "2026-08-07T01:00:00Z",
          first_submitted_job_id: "job-locked",
          params_template: {},
          default_environment: {},
        },
      ],
    });

    const wrapper = mountView();
    await vi.waitFor(() => expect(apiMock.listPipelines).toHaveBeenCalledTimes(1));
    await flushPromises();
    await wrapper.get('[data-testid="pipeline-card-pipeline-locked"]').trigger("click");
    await wrapper.get('[data-testid="clone-locked-pipeline"]').trigger("click");
    await flushPromises();

    expect(apiMock.clonePipeline).toHaveBeenCalledWith("pipeline-locked", { name: "Locked PaddleX copy" });
    expect(replaceMock).toHaveBeenLastCalledWith({
      path: "/model-space",
      query: { pipeline: "pipeline-clone", step: "overview" },
    });
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

  it("enforces forward step gates from the top navigation while allowing backward navigation", async () => {
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

    await stepButtons[3].trigger("click");
    await flushPromises();
    expect(stepButtons[3].classes()).toContain("active");
    expect(wrapper.find(".submit-step").exists()).toBe(true);

    await stepButtons[1].trigger("click");
    await flushPromises();
    expect(stepButtons[1].classes()).toContain("active");

    await stepButtons[2].trigger("click");
    await flushPromises();
    expect(stepButtons[2].classes()).toContain("active");
    expect(wrapper.find('[data-testid="object-detection-training-config"]').exists()).toBe(true);
    expect(wrapper.text()).toContain("表单配置");
    expect(wrapper.text()).toContain("YAML 配置");
    expect(wrapper.text()).not.toContain("Log Interval");

    await wrapper.get('[data-testid="yaml-tab"]').trigger("click");
    const editor = wrapper.get<HTMLTextAreaElement>('[data-testid="framework-yaml-editor"]');
    await editor.setValue("unknown_parameter: 1");
    expect(wrapper.get('[data-testid="yaml-errors"]').exists()).toBe(true);

    await stepButtons[3].trigger("click");
    await flushPromises();
    expect(stepButtons[2].classes()).toContain("active");
    expect(wrapper.find(".submit-step").exists()).toBe(false);

    await editor.setValue("{}");
    expect(wrapper.find('[data-testid="yaml-errors"]').exists()).toBe(false);
    await stepButtons[3].trigger("click");
    await flushPromises();
    expect(stepButtons[3].classes()).toContain("active");
    expect(wrapper.find(".submit-step").exists()).toBe(true);

    await stepButtons[1].trigger("click");
    await flushPromises();
    expect(stepButtons[1].classes()).toContain("active");
  });

  it("selects a real online GPU node and submits the distributed training contract", async () => {
    apiMock.listNodes.mockResolvedValueOnce({
      items: [
        {
          id: "node-rtx3060",
          name: "edge-10-10-13-20",
          resource_pool_id: "pool-x86",
          status: "online",
          architecture: "x86_64",
          platform_kind: "x86_nvidia",
          capabilities: { nvidia_gpu: true, gpu_models: ["NVIDIA GeForce RTX 3060"] },
          resources: { gpu_count: 1, gpu_memory_total_mib: 12288 },
          fingerprint: {
            inventory_snapshot: {
              gpus: [
                {
                  name: "NVIDIA GeForce RTX 3060",
                  uuid: "GPU-rtx3060",
                  memory_total_mib: 12288,
                },
              ],
            },
          },
          agent_version: "ssh-bootstrap",
        },
      ],
      total: 1,
    });
    const wrapper = mountView();
    await vi.waitFor(() => expect(apiMock.listPipelines).toHaveBeenCalledTimes(1));
    await flushPromises();

    await wrapper.get('[data-testid="pipeline-card-pipeline-1"]').trigger("click");
    for (const index of [1, 2, 3]) {
      await wrapper.findAll(".wizard-steps button")[index].trigger("click");
      await flushPromises();
    }
    await vi.waitFor(() => expect(apiMock.listNodes).toHaveBeenCalledTimes(1));
    await flushPromises();

    expect(wrapper.text()).toContain("远程 GPU");
    expect(wrapper.text()).toContain("edge-10-10-13-20");
    expect(wrapper.text()).toContain("NVIDIA GeForce RTX 3060");
    expect(wrapper.text()).toContain("12288 MiB");
    expect(wrapper.get('[data-testid="training-node-node-rtx3060"]').classes()).toContain("selected");

    await wrapper.get('[data-testid="training-image-digest"]').setValue(
      `registry.local/visiox/yolo26-training@sha256:${"a".repeat(64)}`,
    );
    await wrapper.findAll("button").find((button) => button.text() === "提交训练")?.trigger("click");
    await flushPromises();

    expect(apiMock.updatePipeline).toHaveBeenCalledWith(
      "pipeline-1",
      expect.objectContaining({ default_environment: { device: "0", workers: 2 } }),
    );
    expect(apiMock.createTrainingJob).toHaveBeenCalledWith("pipeline-1", {
      environment: { device: "0", workers: 2 },
      distributed: {
        resource_pool_id: "pool-x86",
        requested_gpus: 1,
        node_ids: ["node-rtx3060"],
        training_image_digest: `registry.local/visiox/yolo26-training@sha256:${"a".repeat(64)}`,
      },
    });
  });

  it("keeps local CPU training free of distributed node settings", async () => {
    const wrapper = mountView();
    await vi.waitFor(() => expect(apiMock.listPipelines).toHaveBeenCalledTimes(1));
    await flushPromises();

    await wrapper.get('[data-testid="pipeline-card-pipeline-1"]').trigger("click");
    for (const index of [1, 2, 3]) {
      await wrapper.findAll(".wizard-steps button")[index].trigger("click");
      await flushPromises();
    }
    await wrapper.get('[data-testid="training-target-local"]').trigger("click");
    await wrapper.findAll("button").find((button) => button.text() === "提交训练")?.trigger("click");
    await flushPromises();

    expect(apiMock.updatePipeline).toHaveBeenCalledWith(
      "pipeline-1",
      expect.objectContaining({ default_environment: { device: "cpu", workers: 2 } }),
    );
    expect(apiMock.createTrainingJob).toHaveBeenCalledWith("pipeline-1", {});
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
        format: "auto",
        precision: "auto",
        input_shape: [1, 3, 640, 640],
        gpu_uuids: ["GPU-a"],
      }),
    );
    const request = apiMock.createService.mock.calls[0][0];
    expect(request).not.toHaveProperty("image_digest");
    expect(request).not.toHaveProperty("model_checksum");
  });

  it("selects an official pretrained weight without user-supplied digests", async () => {
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
          metrics: { checksum: "a".repeat(64) },
          status: "ready",
        },
      ],
    });
    const wrapper = mountView();
    await flushPromises();
    await wrapper.get('[data-testid="pipeline-card-pipeline-1"]').trigger("click");
    await wrapper.get('[data-testid="detail-tab-deploy"]').trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("本产线模型权重");
    expect(wrapper.text()).toContain("官方预训练权重");
    expect(wrapper.text()).not.toContain("镜像摘要：");
    expect(wrapper.text()).not.toContain("模型校验和：");

    await wrapper.get('[data-testid="deployment-weight-official"]').trigger("click");
    await wrapper.findAll("button").find((button) => button.text().includes("开始部署"))!.trigger("click");
    await flushPromises();

    expect(apiMock.createService).toHaveBeenCalledWith(
      expect.objectContaining({
        base_model_id: "base-1",
        model_name: "yolo26n.pt",
        model_weight: "yolo26n.pt",
      }),
    );
    expect(apiMock.createService.mock.calls[0][0]).not.toHaveProperty("trained_model_id");
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
