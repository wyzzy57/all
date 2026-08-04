import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { api, type ComputeNodeRecord } from "@/api/client";
import NodeResourcePanel from "@/components/resources/NodeResourcePanel.vue";
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
  resources: { cpu_logical_cores: 8, gpu_count: 1, gpu_memory_total_mib: 24576 },
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

  it("shows unavailable metrics as missing instead of fake zero values", () => {
    const wrapper = mount(NodeResourcePanel, { props: { node } });

    expect(wrapper.text()).toContain("CPU8 核 · 暂无数据");
    expect(wrapper.text()).toContain("内存可用-");
    expect(wrapper.text()).toContain("磁盘可用-");
    expect(wrapper.text()).toContain("GPU1 张 · 暂无数据");
    expect(wrapper.text()).toContain("显存- / 24576 MiB");
    expect(wrapper.text()).toContain("温度 / 功耗暂无数据");
    expect(wrapper.text()).not.toContain("0.0%");
    expect(wrapper.text()).not.toContain("0°C");
  });

  it("renders real zero and non-zero resource metrics without replacing them", () => {
    const measuredNode: ComputeNodeRecord = {
      ...node,
      resources: {
        cpu_logical_cores: 8,
        cpu_utilization_percent: 17.4,
        memory_available_kib: 8 * 1024 * 1024,
        disk_available_bytes: 120 * 1024 ** 3,
        gpu_count: 1,
        gpu_utilization_percent: 0,
        gpu_memory_used_mib: 2048,
        gpu_memory_total_mib: 12288,
        gpu_temperature_celsius: 43,
        gpu_power_draw_watts: 38.6,
      },
    };
    const wrapper = mount(NodeResourcePanel, { props: { node: measuredNode } });

    expect(wrapper.text()).toContain("CPU8 核 · 17.4%");
    expect(wrapper.text()).toContain("内存可用8.0 GiB");
    expect(wrapper.text()).toContain("磁盘可用120.0 GiB");
    expect(wrapper.text()).toContain("GPU1 张 · 0.0%");
    expect(wrapper.text()).toContain("显存2048 / 12288 MiB");
    expect(wrapper.text()).toContain("温度 / 功耗43°C · 39 W");
  });
});
