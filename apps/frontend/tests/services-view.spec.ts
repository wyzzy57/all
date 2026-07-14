import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ServicesView from "@/views/services/ServicesView.vue";

const pushMock = vi.hoisted(() => vi.fn());
const routeState = vi.hoisted(() => ({ params: {} as Record<string, string | undefined> }));
const apiMock = vi.hoisted(() => ({
  listServices: vi.fn(),
  updateService: vi.fn(),
  deleteService: vi.fn(),
  predictServiceImage: vi.fn(),
}));

vi.mock("vue-router", () => ({
  useRoute: () => routeState,
  useRouter: () => ({ push: pushMock }),
}));

vi.mock("@/api/client", () => ({ api: apiMock }));

vi.mock("element-plus", async () => {
  const actual = await vi.importActual<typeof import("element-plus")>("element-plus");
  return {
    ...actual,
    ElMessage: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
  };
});

function mountView() {
  return mount(ServicesView, {
    global: {
      stubs: {
        "el-empty": true,
        "el-icon": { template: "<span><slot /></span>" },
        "el-input": {
          props: ["modelValue"],
          emits: ["update:modelValue"],
          template: "<input :value=\"modelValue\" @input=\"$emit('update:modelValue', $event.target.value)\" />",
        },
        "el-option": true,
        "el-select": true,
      },
    },
  });
}

describe("ServicesView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    routeState.params = {};
    apiMock.listServices.mockResolvedValue({
      items: [
        {
          id: "service-real",
          name: "真实服务",
          pipeline_id: "pipeline-1",
          model_name: "yolo26n.pt",
          model_weight: "best.pt",
          environment: "cpu",
          instance_count: 1,
          instance_name: "prod-01",
          resource_summary: "CPU 共享资源",
          status: "running",
          endpoint: "/services/service-real/predict/image",
          calls: 0,
          config: { pipeline_name: "产线一" },
          created_at: "2026-07-10T10:00:00Z",
          updated_at: "2026-07-10T10:00:00Z",
        },
        {
          id: "service-pepper",
          name: "花椒检测在线预测模型",
          pipeline_id: "pipeline-2",
          model_name: "yolo26n.pt",
          model_weight: "best.pt",
          environment: "gpu-node-1",
          instance_count: 1,
          instance_name: "pepper-prod-01",
          resource_summary: "gpu节点_1 显卡1",
          status: "running",
          endpoint: "/services/service-pepper/predict/image",
          calls: 2,
          config: { pipeline_name: "花椒检测1" },
          created_at: "2026-07-10T11:00:00Z",
          updated_at: "2026-07-10T11:00:00Z",
        },
      ],
      total: 2,
      limit: 200,
      offset: 0,
    });
    apiMock.updateService.mockImplementation((id: string, payload: { status: string }) => Promise.resolve({ id, ...payload }));
    apiMock.deleteService.mockResolvedValue(undefined);
    apiMock.predictServiceImage.mockResolvedValue({
      pipeline_id: "pipeline-1",
      model_weight: "best.pt",
      environment: "cpu",
      predictions: [],
      result_image: "data:image/png;base64,test",
    });
  });

  it("renders service cards and opens service detail", async () => {
    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.text()).toContain("服务列表");
    expect(wrapper.text()).toContain("真实服务");
    expect(wrapper.text()).toContain("查看日志");
    expect(wrapper.findAll('[data-testid^="delete-service-"]')).toHaveLength(2);

    await wrapper.get('[data-testid="delete-service-service-real"]').trigger("click");
    await flushPromises();

    expect(apiMock.deleteService).toHaveBeenCalledWith("service-real");
    expect(wrapper.findAll('[data-testid^="delete-service-"]')).toHaveLength(1);
    expect(pushMock).not.toHaveBeenCalled();

    await wrapper.get('[data-testid="service-card-service-pepper"]').trigger("click");

    expect(pushMock).toHaveBeenCalledWith("/services/service-pepper");
  });

  it("opens service detail from a card", async () => {
    const wrapper = mountView();
    await flushPromises();

    await wrapper.get('[data-testid="service-card-service-real"]').trigger("click");

    expect(pushMock).toHaveBeenCalledWith("/services/service-real");
  });

  it("renders detail tabs and online experience", async () => {
    routeState.params = { serviceId: "service-pepper" };
    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.text()).toContain("返回产线列表");
    expect(wrapper.text()).toContain("花椒检测在线预测模型");
    expect(wrapper.text()).toContain("基础信息");

    await wrapper.findAll("button").find((button) => button.text() === "在线体验")?.trigger("click");
    await flushPromises();

    expect(wrapper.text()).toContain("选择测试图像");
    expect(wrapper.text()).toContain("运行结果");
    expect(wrapper.text()).toContain("图片");
    expect(wrapper.text()).toContain("JSON");
  });
});
