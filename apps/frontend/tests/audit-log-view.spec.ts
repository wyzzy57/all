import ElementPlus from "element-plus";
import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick } from "vue";

import type { AuditLogListResponse } from "@/api/client";
import AsyncState from "@/components/common/AsyncState.vue";

const apiMock = vi.hoisted(() => ({
  listAuditLogs: vi.fn(),
}));

vi.mock("@/api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/client")>()),
  api: apiMock,
}));

import AuditLogView from "@/views/admin/AuditLogView.vue";
import auditLogSource from "@/views/admin/AuditLogView.vue?raw";

const response: AuditLogListResponse = {
  total: 21,
  next_cursor: "cursor-page-2",
  items: [
    {
      id: "log-1",
      actor_user_id: "user-1",
      action: "service.deploy",
      resource_type: "service",
      resource_id: "service-1",
      result: "failed",
      request_id: "request-1",
      metadata_json: {
        note: "This metadata is deliberately long so the table must keep it to a single visual line.",
        password: "old-password",
        private_key: "old-private-key",
        nested: { authorization: "Bearer stale-token", safe: "visible" },
        entries: [{ apiToken: "legacy-token" }],
      },
      created_at: "2026-07-29T12:30:00Z",
    },
  ],
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((nextResolve) => { resolve = nextResolve; });
  return { promise, resolve };
}

const wrappers: Array<ReturnType<typeof mount>> = [];
const dialogStub = {
  props: ["modelValue"],
  template: "<div v-if=\"modelValue\"><slot /></div>",
};

async function mountView() {
  const wrapper = mount(AuditLogView, {
    global: { plugins: [ElementPlus], stubs: { "el-dialog": dialogStub } },
  });
  wrappers.push(wrapper);
  await flushPromises();
  return wrapper;
}

describe("AuditLogView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiMock.listAuditLogs.mockResolvedValue(response);
  });

  afterEach(() => {
    wrappers.splice(0).forEach((wrapper) => wrapper.unmount());
  });

  it("uses the shared async state component for page-level states", () => {
    expect(auditLogSource).toContain('import AsyncState from "@/components/common/AsyncState.vue"');
    expect(auditLogSource).toContain("<AsyncState");
  });

  it("uses 44px hit areas for filter and detail actions", () => {
    expect(auditLogSource).toMatch(/\.filter-actions \.primary-button,\s*\.filter-actions \.secondary-button\s*\{[^}]*min-height:\s*44px/s);
    expect(auditLogSource).toMatch(/\.audit-detail-button::before\s*\{[^}]*width:\s*44px;[^}]*height:\s*44px/s);
    expect(auditLogSource).toMatch(/\.audit-pagination \.secondary-button\s*\{[^}]*min-height:\s*44px/s);
  });

  it("sends every filter to the server and pages audit records in groups of 20", async () => {
    const wrapper = await mountView();

    await wrapper.get("[data-testid='audit-filter-actor']").setValue("user-1");
    await wrapper.get("[data-testid='audit-filter-resource-type']").setValue("service");
    await wrapper.get("[data-testid='audit-filter-resource-id']").setValue("service-1");
    await wrapper.get("[data-testid='audit-filter-action']").setValue("service.deploy");
    await wrapper.get("[data-testid='audit-filter-result']").setValue("failed");
    await wrapper.get("[data-testid='audit-filter-from']").setValue("2026-07-01T00:00");
    await wrapper.get("[data-testid='audit-filter-to']").setValue("2026-07-31T23:59");
    await wrapper.get("form").trigger("submit");
    await flushPromises();

    expect(apiMock.listAuditLogs).toHaveBeenLastCalledWith({
      actor_user_id: "user-1",
      resource_type: "service",
      resource_id: "service-1",
      action: "service.deploy",
      result: "failed",
      created_from: new Date("2026-07-01T00:00").toISOString(),
      created_to: new Date(new Date("2026-07-31T23:59").getTime() + 60_000).toISOString(),
      cursor: undefined,
      limit: 20,
    });

    await wrapper.get("[data-testid='audit-page-next']").trigger("click");
    await flushPromises();
    expect(apiMock.listAuditLogs).toHaveBeenLastCalledWith(expect.objectContaining({
      cursor: response.next_cursor,
      limit: 20,
    }));

    await wrapper.get("[data-testid='audit-page-prev']").trigger("click");
    await flushPromises();
    expect(apiMock.listAuditLogs).toHaveBeenLastCalledWith(expect.objectContaining({
      cursor: undefined,
      limit: 20,
    }));

    await wrapper.get("[data-testid='audit-page-next']").trigger("click");
    await flushPromises();

    await wrapper.get("[data-testid='audit-filter-reset']").trigger("click");
    await flushPromises();
    expect(apiMock.listAuditLogs).toHaveBeenLastCalledWith(expect.objectContaining({
      cursor: undefined,
    }));
  });

  it("keeps only the newest request result when a filter request returns out of order", async () => {
    const first = deferred<AuditLogListResponse>();
    const second = deferred<AuditLogListResponse>();
    apiMock.listAuditLogs.mockReset().mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
    const wrapper = mount(AuditLogView, { global: { plugins: [ElementPlus], stubs: { "el-dialog": dialogStub } } });
    wrappers.push(wrapper);

    await wrapper.get("[data-testid='audit-filter-action']").setValue("new-action");
    await wrapper.get("form").trigger("submit");
    second.resolve({ ...response, items: [{ ...response.items[0], id: "new", action: "new-action" }] });
    await flushPromises();
    first.resolve({ ...response, items: [{ ...response.items[0], id: "old", action: "old-action" }] });
    await flushPromises();

    expect(wrapper.text()).toContain("new-action");
    expect(wrapper.text()).not.toContain("old-action");
  });

  it("keeps metadata to one line in the table and redacts sensitive values recursively in the detail dialog", async () => {
    const wrapper = await mountView();

    const metadata = wrapper.get("[data-testid='audit-metadata-log-1']");
    expect(metadata.classes()).toContain("audit-metadata-preview");
    expect(metadata.attributes("title")).toContain("[REDACTED]");
    expect(metadata.attributes("title")).not.toContain("old-password");

    await wrapper.get("[data-testid='audit-detail-log-1']").trigger("click");
    await flushPromises();
    const detail = wrapper.get("[data-testid='audit-detail-json']").text();
    expect(detail).toContain("[REDACTED]");
    expect(detail).toContain("visible");
    expect(detail).not.toContain("old-password");
    expect(detail).not.toContain("old-private-key");
    expect(detail).not.toContain("stale-token");
    expect(detail).not.toContain("legacy-token");
  });

  it("renders loading, a restrained failed state, error recovery, and empty state", async () => {
    const pending = deferred<AuditLogListResponse>();
    apiMock.listAuditLogs.mockReset().mockReturnValueOnce(pending.promise);
    const loading = mount(AuditLogView, { global: { plugins: [ElementPlus], stubs: { "el-dialog": dialogStub } } });
    wrappers.push(loading);
    await nextTick();
    const loadingState = loading.get("[data-testid='audit-loading']");
    expect(loadingState.text()).toContain("正在加载");
    expect(loadingState.attributes()).toMatchObject({ role: "status", "aria-live": "polite" });
    expect(loading.getComponent(AsyncState).props("state")).toBe("loading");
    pending.resolve(response);
    await flushPromises();
    expect(loading.get("[data-testid='audit-result-log-1']").classes()).toContain("is-failed");
    loading.unmount();
    wrappers.splice(wrappers.indexOf(loading), 1);

    apiMock.listAuditLogs.mockRejectedValueOnce(new Error("audit endpoint unavailable"));
    const failed = await mountView();
    expect(failed.get("[role='alert']").text()).toContain("audit endpoint unavailable");
    expect(failed.getComponent(AsyncState).props("state")).toBe("error");
    const callsBeforeRetry = apiMock.listAuditLogs.mock.calls.length;
    apiMock.listAuditLogs.mockResolvedValueOnce(response);
    await failed.get(".async-state__retry").trigger("click");
    await flushPromises();
    expect(apiMock.listAuditLogs).toHaveBeenCalledTimes(callsBeforeRetry + 1);
    expect(failed.find("[data-testid='audit-result-log-1']").exists()).toBe(true);
    failed.unmount();
    wrappers.splice(wrappers.indexOf(failed), 1);

    apiMock.listAuditLogs.mockResolvedValueOnce({
      items: [],
      total: 0,
      next_cursor: null,
    });
    const empty = await mountView();
    const emptyState = empty.get("[data-testid='audit-empty']");
    expect(emptyState.text()).toContain("暂无审计记录");
    expect(emptyState.attributes()).toMatchObject({ role: "status", "aria-live": "polite" });
    expect(empty.getComponent(AsyncState).props("state")).toBe("empty");
  });

  it("disables every filter path after access is denied", async () => {
    apiMock.listAuditLogs.mockRejectedValueOnce(Object.assign(new Error("forbidden"), { status: 403 }));
    const wrapper = await mountView();

    expect(wrapper.getComponent(AsyncState).props("state")).toBe("denied");
    expect(wrapper.get("[data-testid='audit-filter-fields']").attributes()).toHaveProperty("disabled");
    const callsAfterDenied = apiMock.listAuditLogs.mock.calls.length;

    await wrapper.get("form").trigger("submit");
    await wrapper.get("[data-testid='audit-filter-reset']").trigger("click");
    await flushPromises();

    expect(apiMock.listAuditLogs).toHaveBeenCalledTimes(callsAfterDenied);
  });
});
