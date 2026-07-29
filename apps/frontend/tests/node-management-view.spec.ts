import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type ComputeNodeRecord } from "@/api/client";
import NodeManagementView from "@/views/resources/NodeManagementView.vue";

const node: ComputeNodeRecord = {
  id: "node-1",
  name: "gpu-server-01",
  resource_pool_id: "pool-1",
  status: "online",
  enabled: true,
  architecture: "x86_64",
  platform_kind: "x86_nvidia",
  labels: {},
  connection_method: "ssh",
  capabilities: { docker: { version: "27.1.1" } },
  resources: { gpu_count: 1, gpu_memory_total_mib: 24576 },
  fingerprint: { cuda_runtime_version: "12.4", tensorrt_version: "10.2" },
  resource_revision: 3,
  inventory_refreshed_at: "2026-07-28T08:00:00Z",
  agent_version: "ssh-bootstrap",
};

describe("node management view", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, "listNodes").mockResolvedValue({ items: [node], total: 1 });
    vi.spyOn(api, "listResourcePools").mockResolvedValue({
      items: [{ id: "pool-1", name: "x86 GPU", kind: "x86_nvidia", selector: {}, compatibility_policy: {}, enabled: true }],
      total: 1,
    });
  });

  it("renders unified node resources and refreshes the selected node", async () => {
    const refresh = vi.spyOn(api, "refreshNode").mockResolvedValue({
      status: "ok", node_id: node.id, supported: true, unsupported_reasons: [],
      compatibility_key: "x86", resource_pool_id: "pool-1", inventory: {},
    });
    const wrapper = mount(NodeManagementView, { global: { plugins: [ElementPlus] } });
    await flushPromises();

    expect(wrapper.text()).toContain("gpu-server-01");
    expect(wrapper.text()).toContain("TensorRT 10.2");
    await wrapper.get("[data-testid='refresh-node-node-1']").trigger("click");
    await flushPromises();
    expect(refresh).toHaveBeenCalledWith("node-1");
  });
});
