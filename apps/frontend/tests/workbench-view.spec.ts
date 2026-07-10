import { mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import WorkbenchView from "@/views/workbench/WorkbenchView.vue";

const pushMock = vi.hoisted(() => vi.fn());

vi.mock("vue-router", () => ({
  useRouter: () => ({ push: pushMock }),
}));

describe("WorkbenchView", () => {
  it("renders dashboard panels and quick access links", async () => {
    const wrapper = mount(WorkbenchView);

    expect(wrapper.text()).toContain("工作台");
    expect(wrapper.text()).toContain("数据准备分析");
    expect(wrapper.text()).toContain("模型空间分析");
    expect(wrapper.text()).toContain("服务列表分析");

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
