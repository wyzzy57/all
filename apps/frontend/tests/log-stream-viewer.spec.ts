import { flushPromises, mount } from "@vue/test-utils";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LogStreamViewer from "@/components/logs/LogStreamViewer.vue";

const apiMock = vi.hoisted(() => ({
  getLogStream: vi.fn(),
  getLogChunks: vi.fn(),
  downloadLogStream: vi.fn(),
}));

vi.mock("@/api/client", () => ({ api: apiMock }));

describe("LogStreamViewer", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.getLogStream.mockResolvedValue({ id: "stream-1", status: "open" });
    apiMock.getLogChunks.mockResolvedValue({
      lines: [
        { timestamp: "2026-07-28T08:00:00Z", source: "stderr", level: "ERROR", message: "训练失败" },
        { timestamp: "2026-07-28T08:00:01Z", source: "stdout", level: "INFO", message: "重试中" },
      ],
      next_cursor: "0:2",
      has_more: false,
      bytes_read: 48,
    });
    apiMock.downloadLogStream.mockResolvedValue(new Blob(["log"]));
  });

  it("renders paged structured lines and highlights errors", async () => {
    const wrapper = mount(LogStreamViewer, {
      props: { streamId: "stream-1" },
      global: {
        stubs: {
          "el-input": true,
          "el-select": true,
          "el-option": true,
        },
      },
    });
    await flushPromises();

    expect(apiMock.getLogChunks).toHaveBeenCalledWith("stream-1", null);
    expect(wrapper.text()).toContain("训练失败");
    expect(wrapper.text()).toContain("重试中");
    expect(wrapper.find("li.danger").exists()).toBe(true);
    expect(wrapper.text()).toContain("实时更新中");
    wrapper.unmount();
  });
});
