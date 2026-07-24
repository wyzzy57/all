import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ServicesView from "@/views/services/ServicesView.vue";
import servicesViewSource from "@/views/services/ServicesView.vue?raw";

const pushMock = vi.hoisted(() => vi.fn());
const routeState = vi.hoisted(() => ({ params: {} as Record<string, string | undefined> }));
const apiMock = vi.hoisted(() => ({
  listServices: vi.fn(),
  getService: vi.fn(),
  updateService: vi.fn(),
  stopService: vi.fn(),
  rollbackService: vi.fn(),
  readServiceLog: vi.fn(),
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
        "el-alert": true,
        "el-empty": true,
        "el-icon": { template: "<span><slot /></span>" },
        "el-input": {
          props: ["modelValue"],
          emits: ["update:modelValue"],
          template: "<input :value=\"modelValue\" @input=\"$emit('update:modelValue', $event.target.value)\" />",
        },
        "el-option": true,
        "el-pagination": true,
        "el-select": true,
      },
    },
  });
}

describe("ServicesView", () => {
  it("keeps functional pagination anchored in list mode", () => {
    expect(servicesViewSource).toContain("v-for=\"service in pagedServices\"");
    expect(servicesViewSource).toContain("'list-mode': !selectedService");
    expect(servicesViewSource).toContain("v-model:current-page=\"currentPage\"");
    expect(servicesViewSource).toContain("margin-top: auto");
    expect(servicesViewSource).toContain("li.is-active");
  });

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
    apiMock.getService.mockImplementation((id: string) =>
      Promise.resolve(apiMock.listServices.mock.results[0]?.value?.items?.find?.((item: { id: string }) => item.id === id)),
    );
    const serviceResult = (status: string, phase: string) => ({
      id: "service-real",
      name: "真实服务",
      pipeline_id: "pipeline-1",
      model_name: "yolo26n.pt",
      model_weight: "best.pt",
      environment: "cpu",
      instance_count: 1,
      instance_name: "prod-01",
      resource_summary: "CPU 共享资源",
      status,
      endpoint: "/services/service-real/predict/image",
      calls: 0,
      config: { pipeline_name: "产线一" },
      health_status: status === "failed" ? "unhealthy" : "pending",
      phase,
      created_at: "2026-07-10T10:00:00Z",
      updated_at: "2026-07-10T10:00:00Z",
    });
    apiMock.stopService.mockResolvedValue(serviceResult("stopping", "queued"));
    apiMock.rollbackService.mockResolvedValue(serviceResult("rollback_queued", "queued"));
    apiMock.readServiceLog.mockResolvedValue("[INFO] 已完成模型预热\n[INFO] 服务健康检查通过");
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

  it("keeps polling running services for fresh health checks", async () => {
    vi.useFakeTimers();
    apiMock.getService.mockResolvedValue({
      ...(await apiMock.listServices()).items[0],
      health_status: "healthy",
      health_checked_at: "2026-07-22T08:30:00Z",
    });
    const wrapper = mountView();
    await flushPromises();

    await vi.advanceTimersByTimeAsync(2500);
    await flushPromises();

    expect(apiMock.getService).toHaveBeenCalledWith("service-real");
    expect(apiMock.getService).toHaveBeenCalledWith("service-pepper");
    wrapper.unmount();
    vi.useRealTimers();
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

  it("runs image inference through the existing service proxy", async () => {
    routeState.params = { serviceId: "service-real" };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue({
      ok: true,
      blob: () => Promise.resolve(new Blob(["image"], { type: "image/png" })),
    } as Response);
    const wrapper = mountView();
    await flushPromises();
    await wrapper.findAll("button").find((button) => button.text() === "在线体验")?.trigger("click");
    await wrapper.findAll("button").find((button) => button.text() === "运行")?.trigger("click");
    await flushPromises();

    expect(apiMock.predictServiceImage).toHaveBeenCalledWith("service-real", expect.any(File));
    expect(wrapper.text()).toContain("运行结果");
    fetchMock.mockRestore();
  });

  it("renders asynchronous edge phase, health, endpoint and redacted logs", async () => {
    routeState.params = { serviceId: "service-real" };
    apiMock.listServices.mockResolvedValueOnce({
      items: [
        {
          id: "service-real",
          name: "真实边缘服务",
          pipeline_id: "pipeline-1",
          model_name: "yolo26n.pt",
          model_weight: "best.pt",
          environment: "edge",
          instance_count: 1,
          instance_name: "prod-01",
          resource_summary: "边缘节点 A · RTX 4090",
          status: "warming_up",
          endpoint: "http://10.10.40.20:8080",
          calls: 0,
          config: { pipeline_name: "产线一" },
          node_id: "node-a",
          container_id: "container-1234567890",
          health_status: "starting",
          phase: "warming_up",
          log_uri: "https://storage.example/logs/redacted.log",
          created_at: "2026-07-10T10:00:00Z",
          updated_at: "2026-07-10T10:00:00Z",
        },
      ],
      total: 1,
      limit: 200,
      offset: 0,
    });
    const wrapper = mountView();
    await flushPromises();

    expect(wrapper.text()).toContain("预热中");
    expect(wrapper.text()).toContain("启动中");
    expect(wrapper.text()).toContain("10.10.40.20:8080");
    expect(wrapper.text()).toContain("container-1234");
    expect(wrapper.get('[data-testid="stop-service-service-real"]').attributes("disabled")).toBeUndefined();

    await wrapper.findAll("button").find((button) => button.text() === "日志")?.trigger("click");
    await flushPromises();
    expect(apiMock.readServiceLog).toHaveBeenCalledWith("https://storage.example/logs/redacted.log");
    expect(wrapper.text()).toContain("服务健康检查通过");
  });

  it("queues stop and rollback commands and preserves proxy inference", async () => {
    routeState.params = { serviceId: "service-real" };
    const wrapper = mountView();
    await flushPromises();

    await wrapper.get('[data-testid="stop-service-service-real"]').trigger("click");
    await flushPromises();
    expect(apiMock.stopService).toHaveBeenCalledWith("service-real");
    expect(wrapper.text()).toContain("停止排队中");

    wrapper.unmount();
    apiMock.listServices.mockResolvedValueOnce({
      items: [{
        id: "service-real", name: "真实服务", pipeline_id: "pipeline-1", model_name: "yolo26n.pt",
        model_weight: "best.pt", environment: "cpu", instance_count: 1, instance_name: "prod-01",
        resource_summary: "CPU 共享资源", status: "failed", endpoint: "", calls: 0,
        config: { pipeline_name: "产线一" }, health_status: "unhealthy", phase: "failed",
        created_at: "2026-07-10T10:00:00Z", updated_at: "2026-07-10T10:00:00Z",
      }],
      total: 1, limit: 200, offset: 0,
    });
    const rollbackWrapper = mountView();
    await flushPromises();
    await rollbackWrapper.get('[data-testid="rollback-service-service-real"]').trigger("click");
    await flushPromises();
    expect(apiMock.rollbackService).toHaveBeenCalledWith("service-real");

    await rollbackWrapper.findAll("button").find((button) => button.text() === "在线体验")?.trigger("click");
    await flushPromises();
    expect(rollbackWrapper.text()).toContain("选择测试图像");
    expect(rollbackWrapper.text()).toContain("运行结果");
  });
});
