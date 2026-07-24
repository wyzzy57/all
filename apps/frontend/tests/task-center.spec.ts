import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useTaskCenterStore } from "@/stores/taskCenter";
import taskViewSource from "@/views/tasks/TasksView.vue?raw";

function task(id: string, status: string) {
  return {
    id,
    task_type: "training",
    status,
    progress: 20,
    retryable: false,
    created_at: "2026-07-05T00:00:00Z",
    updated_at: "2026-07-05T00:00:00Z",
  };
}

function mockJsonResponse(payload: unknown = {}) {
  return Promise.resolve({
    ok: true,
    json: () => Promise.resolve(payload),
  } as Response);
}

describe("task center store", () => {
  beforeEach(() => {
    setActivePinia(createPinia());
    vi.restoreAllMocks();
  });

  it("loads task metrics from the API", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      mockJsonResponse({
        items: [task("task-1", "RUNNING"), task("task-2", "FAILED")],
        total: 2,
        limit: 100,
        offset: 0,
      }),
    );

    const store = useTaskCenterStore();
    await store.refresh();

    expect(store.items).toHaveLength(2);
    expect(store.runningCount).toBe(1);
    expect(store.failedCount).toBe(1);
    expect(store.error).toBeNull();
  });

  it("cancels a task and refreshes the list", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementationOnce(() => mockJsonResponse(task("task-1", "CANCELLED")))
      .mockImplementationOnce(() =>
        mockJsonResponse({ items: [task("task-1", "CANCELLED")], total: 1, limit: 100, offset: 0 }),
      );

    const store = useTaskCenterStore();
    await store.cancel("task-1");

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/tasks/task-1/cancel",
      expect.objectContaining({ method: "POST" }),
    );
    expect(store.items[0].status).toBe("CANCELLED");
  });

  it("keeps long task errors on one line and exposes details in a tooltip", () => {
    expect(taskViewSource).toContain('class="task-error"');
    expect(taskViewSource).toContain(":content=\"taskError(row)\"");
    expect(taskViewSource).toContain("text-overflow: ellipsis");
    expect(taskViewSource).toContain("white-space: nowrap");
    expect(taskViewSource).toContain('class="task-table-scroll"');
    expect(taskViewSource).toContain("min-width: 1210px");
  });

  it("deletes a terminal training task and refreshes the list", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementationOnce(() => mockJsonResponse())
      .mockImplementationOnce(() =>
        mockJsonResponse({ items: [], total: 0, limit: 100, offset: 0 }),
      );

    const store = useTaskCenterStore();
    await store.remove("task-1");

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/tasks/task-1",
      expect.objectContaining({ method: "DELETE" }),
    );
    expect(store.items).toEqual([]);
  });
});
