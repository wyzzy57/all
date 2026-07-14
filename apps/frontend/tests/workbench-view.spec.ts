import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import WorkbenchView from "@/views/workbench/WorkbenchView.vue";

const pushMock = vi.hoisted(() => vi.fn());
const apiMock = vi.hoisted(() => ({
  listDatasets: vi.fn(),
  listPipelines: vi.fn(),
  listServices: vi.fn(),
}));

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: pushMock }),
}));

vi.mock("@/api/client", () => ({ api: apiMock }));

vi.mock("element-plus", async () => {
  const actual = await vi.importActual<typeof import("element-plus")>("element-plus");
  return { ...actual, ElMessage: { error: vi.fn() } };
});

describe("WorkbenchView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.listDatasets.mockResolvedValue({
      items: [
        {
          id: "dataset-1",
          name: "真实花椒数据集",
          task: "detect",
          status: "validated",
          source: "label_studio",
          sample_count: 12,
          annotation_count: 12,
          created_at: "2026-07-10T08:00:00Z",
        },
        {
          id: "dataset-2",
          name: "未标注数据",
          task: "classify",
          status: "created",
          source: "upload",
          sample_count: 8,
          annotation_count: 0,
          created_at: "2026-07-10T07:00:00Z",
        },
      ],
    });
    apiMock.listPipelines.mockResolvedValue({
      items: [
        { id: "pipeline-1", name: "真实检测产线", task: "detect", scale: "n", status: "success" },
        { id: "pipeline-2", name: "配置产线", task: "classify", scale: "n", status: "ready" },
        { id: "pipeline-3", name: "中止产线", task: "detect", scale: "n", status: "canceled" },
      ],
    });
    apiMock.listServices.mockResolvedValue({
      items: [
        {
          id: "service-1",
          name: "真实边缘服务",
          pipeline_id: "pipeline-1",
          model_name: "yolo26n.pt",
          model_weight: "best.pt",
          environment: "cpu",
          instance_count: 1,
          instance_name: "edge-prod-01",
          resource_summary: "CPU",
          status: "running",
          endpoint: "/services/service-1/predict/image",
          calls: 0,
          config: {},
          created_at: "2026-07-10T09:00:00Z",
          updated_at: "2026-07-10T09:00:00Z",
        },
      ],
    });
  });

  it("renders dashboard panels and quick access links", async () => {
    const wrapper = mount(WorkbenchView);
    await flushPromises();

    expect(wrapper.text()).toContain("工作台");
    expect(wrapper.text()).toContain("数据准备分析");
    expect(wrapper.text()).toContain("模型空间分析");
    expect(wrapper.text()).toContain("服务列表分析");
    expect(wrapper.text()).toContain("真实花椒数据集");
    expect(wrapper.text()).toContain("真实边缘服务");
    expect(wrapper.text()).toContain("真实检测产线");
    expect(wrapper.text()).not.toContain("千问3");
    const stoppedStatus = wrapper.findAll(".status-card").find((card) => card.text().includes("运行中止"));
    expect(stoppedStatus?.text()).toContain("1");

    const quickLinks = wrapper.findAll(".quick-link");
    expect(quickLinks).toHaveLength(3);

    await quickLinks[0].trigger("click");
    await quickLinks[1].trigger("click");
    await quickLinks[2].trigger("click");

    expect(pushMock).toHaveBeenNthCalledWith(1, "/data-preparation");
    expect(pushMock).toHaveBeenNthCalledWith(2, "/data-preparation");
    expect(pushMock).toHaveBeenNthCalledWith(3, "/services");
  });
});
