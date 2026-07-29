import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/api/client";
import ResourceSharingDialog from "@/components/sharing/ResourceSharingDialog.vue";


describe("ResourceSharingDialog", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "listSharingPrincipals").mockResolvedValue({
      organization: { id: "org-1", name: "默认组织" },
      users: [{ id: "user-1", username: "operator", display_name: "操作员" }],
      groups: [{ id: "group-1", name: "视觉组" }],
    });
    vi.spyOn(api, "getResourceSharing").mockResolvedValue({
      resource_type: "pipeline",
      resource_id: "pipeline-1",
      visibility: "organization",
      grants: [{
        id: "grant-1",
        resource_type: "pipeline",
        resource_id: "pipeline-1",
        principal_type: "organization",
        principal_id: "org-1",
        permissions: ["view", "use"],
        expires_at: null,
      }],
    });
  });

  it("loads real sharing state and saves a complete replacement", async () => {
    const replace = vi.spyOn(api, "replaceResourceSharing").mockResolvedValue({
      resource_type: "pipeline",
      resource_id: "pipeline-1",
      visibility: "private",
      grants: [],
    });
    const wrapper = mount(ResourceSharingDialog, {
      props: {
        modelValue: true,
        resourceType: "pipeline",
        resourceId: "pipeline-1",
        resourceName: "检测产线",
      },
      global: { plugins: [ElementPlus] },
    });
    await flushPromises();

    expect(wrapper.text()).toContain("访问与共享");
    expect(wrapper.text()).toContain("全平台成员");
    await wrapper.get("[data-testid='sharing-specific']").trigger("click");
    expect(wrapper.text()).toContain("操作员");
    await wrapper.get("[data-testid='sharing-private']").trigger("click");
    await wrapper.get("[data-testid='save-sharing']").trigger("click");
    await flushPromises();

    expect(replace).toHaveBeenCalledWith("pipeline", "pipeline-1", { grants: [] });
  });

  it("does not offer organization publication for nodes", async () => {
    vi.spyOn(api, "getResourceSharing").mockResolvedValue({
      resource_type: "node",
      resource_id: "node-1",
      visibility: "private",
      grants: [],
    });
    const wrapper = mount(ResourceSharingDialog, {
      props: {
        modelValue: true,
        resourceType: "node",
        resourceId: "node-1",
        resourceName: "GPU 节点",
      },
      global: { plugins: [ElementPlus] },
    });
    await flushPromises();

    expect(wrapper.text()).not.toContain("全平台成员");
    expect(wrapper.text()).toContain("指定成员或分组");
  });
});
