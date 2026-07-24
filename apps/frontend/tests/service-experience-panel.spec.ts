import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, describe, expect, it, vi } from "vitest";

import ServiceExperiencePanel from "@/components/ServiceExperiencePanel.vue";

describe("ServiceExperiencePanel", () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("opens the annotated result in a large preview", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue({
      blob: () => Promise.resolve(new Blob(["image"], { type: "image/png" })),
    } as Response);
    const runInference = vi.fn().mockResolvedValue({
      predictions: [{ class_id: 0, label: "pepper", confidence: 0.91 }],
      result_image: "data:image/png;base64,cG5n",
    });
    const wrapper = mount(ServiceExperiencePanel, {
      props: { runInference },
      global: {
        stubs: {
          "el-dialog": {
            props: ["modelValue", "title"],
            template: '<div v-if="modelValue" role="dialog"><slot /></div>',
          },
          "el-icon": { template: "<span><slot /></span>" },
          "el-tooltip": { template: "<span><slot /></span>" },
        },
      },
    });

    await wrapper.findAll("button").find((button) => button.text() === "运行")?.trigger("click");
    await flushPromises();

    const previewButton = wrapper.get('[data-testid="service-result-preview-button"]');
    expect(previewButton.attributes("aria-label")).toBe("放大查看运行结果");
    await previewButton.trigger("click");

    expect(wrapper.get('[data-testid="service-result-preview-dialog-image"]').attributes("src"))
      .toBe("data:image/png;base64,cG5n");
  });
});
