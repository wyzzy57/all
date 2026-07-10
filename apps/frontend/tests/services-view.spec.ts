import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ServicesView from "@/views/services/ServicesView.vue";

const pushMock = vi.hoisted(() => vi.fn());
const routeState = vi.hoisted(() => ({ params: {} as Record<string, string | undefined> }));

vi.mock("vue-router", () => ({
  useRoute: () => routeState,
  useRouter: () => ({ push: pushMock }),
}));

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
  });

  it("renders service cards and opens service detail", async () => {
    const wrapper = mountView();

    expect(wrapper.text()).toContain("服务列表");
    expect(wrapper.text()).toContain("千问3");
    expect(wrapper.text()).toContain("查看日志");
    expect(wrapper.findAll('[data-testid^="delete-service-"]')).toHaveLength(8);

    await wrapper.get('[data-testid="delete-service-service-qwen3"]').trigger("click");

    expect(wrapper.findAll('[data-testid^="delete-service-"]')).toHaveLength(7);
    expect(pushMock).not.toHaveBeenCalled();

    await wrapper.get('[data-testid="service-card-service-vllm-1"]').trigger("click");

    expect(pushMock).toHaveBeenCalledWith("/services/service-vllm-1");
  });

  it("opens service detail from a card", async () => {
    const wrapper = mountView();

    await wrapper.get('[data-testid="service-card-service-qwen3"]').trigger("click");

    expect(pushMock).toHaveBeenCalledWith("/services/service-qwen3");
  });

  it("renders detail tabs and online experience", async () => {
    routeState.params = { serviceId: "service-pepper" };
    const wrapper = mountView();

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
