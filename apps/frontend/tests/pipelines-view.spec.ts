import { flushPromises, mount } from "@vue/test-utils";
import ElementPlus from "element-plus";
import { beforeEach, describe, expect, it, vi } from "vitest";

import PipelinesView from "@/views/pipelines/PipelinesView.vue";

const apiMock = vi.hoisted(() => ({
  createPipeline: vi.fn(),
  createTrainingJob: vi.fn(),
  listBaseModels: vi.fn(),
  listDatasets: vi.fn(),
  listPipelines: vi.fn(),
  listTrainingJobs: vi.fn(),
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
    },
  };
});

function mountView() {
  return mount(PipelinesView, {
    global: {
      plugins: [ElementPlus],
      stubs: {
        "el-alert": true,
        "el-card": { template: "<section><slot name=\"header\" /><slot /></section>" },
        "el-col": { template: "<div><slot /></div>" },
        "el-form": { template: "<form><slot /></form>" },
        "el-form-item": { template: "<label><slot /></label>" },
        "el-input": true,
        "el-input-number": true,
        "el-option": { props: ["label"], template: "<option>{{ label }}</option>" },
        "el-row": { template: "<div><slot /></div>" },
        "el-select": { template: "<select><slot /></select>" },
        "el-step": true,
        "el-steps": { template: "<div><slot /></div>" },
        "el-switch": true,
      },
    },
  });
}

describe("PipelinesView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.listBaseModels.mockResolvedValue({
      items: [
        {
          id: "base-remote",
          name: "YOLO26n",
          version: "0.1",
          task: "detect",
          scale: "n",
          status: "ready",
        },
      ],
    });
    apiMock.listDatasets.mockResolvedValue({ items: [] });
    apiMock.listPipelines.mockResolvedValue({ items: [] });
    apiMock.listTrainingJobs.mockResolvedValue({ items: [] });
  });

  it("shows prepared base models without manual download actions", async () => {
    const wrapper = mountView();

    await vi.waitFor(() => expect(apiMock.listBaseModels).toHaveBeenCalledTimes(1));
    await flushPromises();

    expect(wrapper.text()).toContain("YOLO26n");
    expect(wrapper.text()).toContain("可用");
    expect(wrapper.find('[data-testid="download-base-model-base-remote"]').exists()).toBe(false);
  });

  it("shows prepared datasets even before validation", async () => {
    apiMock.listDatasets.mockResolvedValue({
      items: [
        {
          id: "dataset-1",
          name: "huajiao",
          task: "detect",
          status: "created",
          sample_count: 135,
          annotation_count: 135,
        },
      ],
    });

    const wrapper = mountView();

    await vi.waitFor(() => expect(apiMock.listDatasets).toHaveBeenCalledTimes(1));
    await flushPromises();

    expect(wrapper.text()).toContain("huajiao");
    expect(wrapper.text()).toContain("待校验");
  });
});
