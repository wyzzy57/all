import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DataPreparationView from "@/views/data-preparation/DataPreparationView.vue";
import dataPreparationViewSource from "@/views/data-preparation/DataPreparationView.vue?raw";

function ruleBody(source: string, selector: string, declaration: string) {
  const escapedSelector = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const rules = [...source.matchAll(new RegExp(`^\\s*${escapedSelector}\\s*\\{([^{}]*)\\}`, "gm"))]
    .filter((match) => new RegExp(`(?:^|;)\\s*${declaration}\\s*:`).test(match[1]));
  expect(rules, `expected one exact ${selector} rule`).toHaveLength(1);
  return rules[0]![1];
}

const apiMock = vi.hoisted(() => ({
  createLabelProject: vi.fn(),
  datasetExportUrl: vi.fn(),
  deleteDataset: vi.fn(),
  assignDatasetSplitRatio: vi.fn(),
  analyzeDataset: vi.fn(),
  datasetSampleContentUrl: vi.fn(),
  listDatasets: vi.fn(),
  listLabelProjects: vi.fn(),
  listDatasetSamples: vi.fn(),
  launchLabelProject: vi.fn(),
  processDataset: vi.fn(),
  importLabelProjectAnnotations: vi.fn(),
  getTask: vi.fn(),
  promoteDataset: vi.fn(),
  validateDataset: vi.fn(),
}));

const messageBoxMock = vi.hoisted(() => ({
  confirm: vi.fn(),
}));

vi.mock("@/api/client", () => ({
  api: apiMock,
}));

vi.mock("element-plus", () => ({
  ElMessage: {
    error: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
  },
  ElMessageBox: messageBoxMock,
}));

function mountView() {
  return mount(DataPreparationView, {
    global: {
      directives: {
        loading: () => undefined,
      },
      stubs: {
        "el-alert": true,
        "el-button": { template: "<button v-bind=\"$attrs\" @click=\"$emit('click')\"><slot /></button>" },
        "el-dialog": { template: "<section v-if=\"modelValue\" class=\"dialog\"><slot /></section>", props: ["modelValue"] },
        "el-drawer": { template: "<section v-if=\"modelValue\"><slot /></section>", props: ["modelValue"] },
        "el-empty": true,
        "el-form": { template: "<form><slot /></form>" },
        "el-form-item": { template: "<label><slot /></label>" },
        "el-input": true,
        "el-option": true,
        "el-progress": true,
        "el-segmented": true,
        "el-select": true,
        "el-table": true,
        "el-table-column": true,
        "el-tag": true,
      },
    },
  });
}

describe("DataPreparationView", () => {
  it("uses the raised shared surface for import cards", () => {
    const importCardRule = ruleBody(dataPreparationViewSource, ".import-card", "background");
    expect(importCardRule).toMatch(/background:\s*var\(--visiox-card-surface-raised\)\s*;/);
    expect(importCardRule).toMatch(/border:\s*1px solid var\(--visiox-card-border\)\s*;/);
    expect(importCardRule).toMatch(/border-radius:\s*var\(--visiox-card-radius\)\s*;/);
  });

  it("uses the standard shared surface for dataset cards", () => {
    const datasetCardRule = ruleBody(dataPreparationViewSource, ".dataset-card", "background");
    expect(datasetCardRule).toMatch(/background:\s*var\(--visiox-card-surface\)\s*;/);
    expect(datasetCardRule).toMatch(/border:\s*1px solid var\(--visiox-card-border\)\s*;/);
    expect(datasetCardRule).toMatch(/border-radius:\s*var\(--visiox-card-radius\)\s*;/);
  });

  it("does not lift dataset cards on hover", () => {
    const hoverRule = ruleBody(dataPreparationViewSource, ".dataset-card:hover", "box-shadow");
    expect([...hoverRule.matchAll(/box-shadow\s*:\s*([^;{}]+)/g)].map((match) => match[1].trim())).toEqual(["none"]);
    expect([...hoverRule.matchAll(/transform\s*:\s*([^;{}]+)/g)].map((match) => match[1].trim())).toEqual(["none"]);
    expect(hoverRule).not.toMatch(/translateY\s*\(/);
  });

  it("uses the shared resource dialog for dataset visibility", () => {
    expect(dataPreparationViewSource).toContain("ResourceSharingDialog");
    expect(dataPreparationViewSource).toContain("openDatasetSharing(dataset)");
  });

  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.listDatasets.mockResolvedValue({
      items: [
        {
          id: "dataset-1",
          name: "huajiao",
          task: "detect",
          status: "preparing",
          sample_count: 135,
          annotation_count: 135,
          created_at: "2026-07-06T00:00:00Z",
          updated_at: "2026-07-06T00:00:00Z",
        },
      ],
      total: 1,
      limit: 50,
      offset: 0,
    });
    apiMock.listLabelProjects.mockResolvedValue({ items: [], total: 0 });
    apiMock.launchLabelProject.mockResolvedValue({
      launch_url: "http://127.0.0.1:8080/visiox-auth?launch_token=managed-token",
    });
    apiMock.deleteDataset.mockResolvedValue({});
    apiMock.assignDatasetSplitRatio.mockResolvedValue({ items: [], total: 0, limit: 0, offset: 0 });
    apiMock.analyzeDataset.mockResolvedValue({
      id: "task-analysis",
      status: "SUCCESS",
      payload: {
        result: {
          sample_count: 135,
          annotation_count: 135,
          class_distribution: { defect: 100, ok: 35 },
          split_distribution: { train: 108, val: 27, test: 0 },
        },
      },
    });
    apiMock.datasetSampleContentUrl.mockImplementation((datasetId: string, sampleId: string) => `/datasets/${datasetId}/samples/${sampleId}/content`);
    apiMock.datasetExportUrl.mockImplementation((datasetId: string) => `/datasets/${datasetId}/export`);
    apiMock.listDatasetSamples.mockResolvedValue({ items: [], total: 0, limit: 60, offset: 0 });
    apiMock.processDataset.mockResolvedValue({
      id: "task-process",
      status: "SUCCESS",
      payload: { result: { augmented_count: 1, cleaning_issue_count: 0 } },
    });
    apiMock.validateDataset.mockResolvedValue({
      id: "task-1",
      status: "SUCCESS",
      payload: { result: { valid: true } },
    });
    apiMock.importLabelProjectAnnotations.mockResolvedValue({ id: "import-1", status: "QUEUED" });
    apiMock.getTask.mockResolvedValue({ id: "import-1", status: "SUCCESS" });
    apiMock.promoteDataset.mockResolvedValue({
      id: "dataset-derived",
      name: "huajiao-数据集",
      task: "detect",
      status: "created",
      sample_count: 135,
      annotation_count: 135,
    });
    messageBoxMock.confirm.mockResolvedValue(undefined);
  });

  it("keeps preparation card metadata in a stable four-row layout", async () => {
    const wrapper = mountView();
    await vi.waitFor(() => expect(apiMock.listDatasets).toHaveBeenCalledTimes(1));
    await flushPromises();

    const layout = wrapper.get(".prepare-card-layout");
    expect(layout.get(".prepare-card-head").text()).toContain("huajiao");
    expect(layout.get(".chip-row").text()).toContain("目标检测");
    expect(layout.get(".dataset-meta").text()).toContain("Label Studio");
    expect(layout.get(".dataset-stats").text()).toContain("样本 135");
  });

  it("shows a more action on dataset cards and deletes after confirmation", async () => {
    const wrapper = mountView();
    await vi.waitFor(() => expect(apiMock.listDatasets).toHaveBeenCalledTimes(1));
    await flushPromises();

    await wrapper.get('[data-testid="dataset-more-dataset-1"]').trigger("click");
    await wrapper.get('[data-testid="delete-dataset-dataset-1"]').trigger("click");

    expect(messageBoxMock.confirm).toHaveBeenCalled();
    await vi.waitFor(() => expect(apiMock.deleteDataset).toHaveBeenCalledWith("dataset-1"));
    expect(apiMock.listDatasets).toHaveBeenCalledTimes(2);
  });

  it("keeps legacy imported records visible in data preparation", async () => {
    apiMock.listDatasets.mockResolvedValue({
      items: [
        {
          id: "legacy-dataset",
          name: "历史花椒数据",
          task: "detect",
          status: "validated",
          source: "upload",
          sample_count: 135,
          annotation_count: 135,
        },
        {
          id: "promoted-dataset",
          name: "已转换数据集",
          task: "detect",
          status: "created",
          source: "label_studio",
          storage_uri: "preparation://source-dataset",
          sample_count: 120,
          annotation_count: 120,
        },
      ],
      total: 2,
      limit: 50,
      offset: 0,
    });
    const wrapper = mountView();
    await vi.waitFor(() => expect(apiMock.listDatasets).toHaveBeenCalledTimes(1));
    await flushPromises();

    expect(wrapper.text()).toContain("历史花椒数据");
    expect(wrapper.text()).not.toContain("已转换数据集");

    await wrapper.get('[data-testid="dataset-tab"]').trigger("click");
    expect(wrapper.text()).toContain("历史花椒数据");
    expect(wrapper.text()).toContain("已转换数据集");
  });

  it("validates a prepared dataset from the dataset card", async () => {
    apiMock.listDatasets.mockResolvedValue({
      items: [{ id: "dataset-1", name: "huajiao", task: "detect", status: "created", sample_count: 135, annotation_count: 135 }],
      total: 1,
      limit: 50,
      offset: 0,
    });
    const wrapper = mountView();
    await vi.waitFor(() => expect(apiMock.listDatasets).toHaveBeenCalledTimes(1));
    await flushPromises();

    await wrapper.get('[data-testid="dataset-tab"]').trigger("click");
    await wrapper.get('[data-testid="validate-dataset-dataset-1"]').trigger("click");

    expect(apiMock.validateDataset).toHaveBeenCalledWith("dataset-1");
    await vi.waitFor(() => expect(apiMock.listDatasets).toHaveBeenCalledTimes(2));
  });

  it("opens Label Studio through a managed launch token when clicking a preparation card", async () => {
    apiMock.createLabelProject.mockResolvedValue({
      id: "project-1",
      dataset_id: "dataset-1",
      provider: "label_studio",
      project_url: "http://127.0.0.1:8080/projects/1/data",
      sync_status: "created",
      created_at: "2026-07-06T00:00:00Z",
      updated_at: "2026-07-06T00:00:00Z",
    });
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
    const wrapper = mountView();
    await vi.waitFor(() => expect(apiMock.listDatasets).toHaveBeenCalledTimes(1));
    await flushPromises();

    await wrapper.get(".dataset-card").trigger("click");
    await flushPromises();

    expect(apiMock.createLabelProject).toHaveBeenCalledWith("dataset-1");
    expect(apiMock.launchLabelProject).toHaveBeenCalledWith("project-1");
    expect(openSpy).toHaveBeenCalledWith(
      "http://127.0.0.1:8080/visiox-auth?launch_token=managed-token",
      "_blank",
      "noopener,noreferrer",
    );
    expect(apiMock.analyzeDataset).not.toHaveBeenCalled();
    openSpy.mockRestore();
  });

  it("opens the shared import dialog from each import entry", async () => {
    const wrapper = mountView();
    await vi.waitFor(() => expect(apiMock.listDatasets).toHaveBeenCalledTimes(1));
    await flushPromises();

    await wrapper.get('[data-testid="import-video"]').trigger("click");

    expect(wrapper.text()).toContain("新增对应的数据集");
    expect(wrapper.text()).toContain("视频文件导入");
    expect(wrapper.text()).toContain("切帧频率");
  });

  it("keeps data rules minimal and mirrors the video advanced options", async () => {
    const wrapper = mountView();
    await vi.waitFor(() => expect(apiMock.listDatasets).toHaveBeenCalledTimes(1));
    await flushPromises();

    await wrapper.get('[data-testid="import-video"]').trigger("click");
    const text = wrapper.text();

    expect(text).toContain("数据集名称");
    expect(text).toContain("标签");
    expect(text).not.toContain("上传方式");
    expect(text).not.toContain("任务类型");
    for (const option of ["全选", "水平翻转", "垂直翻转", "旋转", "平移", "缩放", "噪声", "模糊"]) {
      expect(text).toContain(option);
    }
    for (const option of ["完全重复", "接近重复", "低信息密度", "过暗", "过亮", "长宽比异常", "大小异常", "灰色"]) {
      expect(text).toContain(option);
    }

    const clusters = wrapper.findAll(".checkbox-cluster");
    const augmentInputs = clusters[0].findAll('input[type="checkbox"]');
    const cleanInputs = clusters[1].findAll('input[type="checkbox"]');

    await augmentInputs[0].setValue(true);
    expect(augmentInputs.slice(1).every((input) => (input.element as HTMLInputElement).checked)).toBe(true);

    await cleanInputs[0].setValue(true);
    expect(cleanInputs.slice(1).every((input) => (input.element as HTMLInputElement).checked)).toBe(true);
  });

  it("shows dataset detail with a downloadable export link from the dataset tab", async () => {
    apiMock.listDatasets.mockResolvedValue({
      items: [{ id: "dataset-1", name: "huajiao", task: "detect", status: "created", sample_count: 135, annotation_count: 135 }],
      total: 1,
      limit: 50,
      offset: 0,
    });
    const wrapper = mountView();
    await vi.waitFor(() => expect(apiMock.listDatasets).toHaveBeenCalledTimes(1));
    await flushPromises();

    await wrapper.get('[data-testid="dataset-tab"]').trigger("click");
    await wrapper.get(".dataset-card").trigger("click");

    expect(wrapper.text()).toContain("返回数据集列表");
    expect(wrapper.text()).toContain("添加数据集介绍");
    expect(wrapper.get('[data-testid="download-dataset-dataset-1"]').attributes("href")).toBe("/datasets/dataset-1/export");
  });

  it("applies split ratios from the processing panel", async () => {
    apiMock.listDatasets.mockResolvedValue({
      items: [{ id: "dataset-1", name: "huajiao", task: "detect", status: "created", sample_count: 135, annotation_count: 135 }],
      total: 1,
      limit: 50,
      offset: 0,
    });
    const wrapper = mountView();
    await vi.waitFor(() => expect(apiMock.listDatasets).toHaveBeenCalledTimes(1));
    await flushPromises();

    await wrapper.get('[data-testid="dataset-tab"]').trigger("click");
    await wrapper.get('[data-testid="process-dataset-dataset-1"]').trigger("click");
    await flushPromises();
    await wrapper.get(".split-panel button").trigger("click");

    expect(apiMock.assignDatasetSplitRatio).toHaveBeenCalledWith("dataset-1", {
      train_ratio: 80,
      val_ratio: 20,
      test_ratio: 0,
    });
  });

  it("keeps a revalidate action visible for validated datasets", async () => {
    apiMock.listDatasets.mockResolvedValue({
      items: [
        {
          id: "dataset-1",
          name: "huajiao",
          task: "detect",
          status: "validated",
          sample_count: 135,
          annotation_count: 135,
          created_at: "2026-07-06T00:00:00Z",
          updated_at: "2026-07-06T00:00:00Z",
        },
      ],
      total: 1,
      limit: 50,
      offset: 0,
    });

    const wrapper = mountView();
    await vi.waitFor(() => expect(apiMock.listDatasets).toHaveBeenCalledTimes(1));
    await flushPromises();

    await wrapper.get('[data-testid="dataset-tab"]').trigger("click");
    expect(wrapper.get('[data-testid="validate-dataset-dataset-1"]').text()).toContain("重新检验");
  });

  it("converts only Label Studio annotations into a dataset", async () => {
    apiMock.createLabelProject.mockResolvedValue({
      id: "project-1",
      dataset_id: "dataset-1",
      provider: "label_studio",
      project_url: "http://127.0.0.1:8080/projects/1/data",
      sync_status: "synced",
    });
    const wrapper = mountView();
    await vi.waitFor(() => expect(apiMock.listDatasets).toHaveBeenCalledTimes(1));
    await flushPromises();

    await wrapper.get('[data-testid="dataset-more-dataset-1"]').trigger("click");
    await wrapper.findAll(".dataset-action-menu button").find((button) => button.text().includes("转为数据集"))!.trigger("click");
    await vi.waitFor(() => expect(apiMock.promoteDataset).toHaveBeenCalledWith("dataset-1"));

    expect(apiMock.importLabelProjectAnnotations).toHaveBeenCalledWith("project-1");
    expect(apiMock.getTask).toHaveBeenCalledWith("import-1");
  });

  it("does not render the old warning triangle on preparation cards", async () => {
    const wrapper = mountView();
    await vi.waitFor(() => expect(apiMock.listDatasets).toHaveBeenCalledTimes(1));
    await flushPromises();

    expect(wrapper.text()).not.toContain("△");
  });
});
