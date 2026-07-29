import { afterEach, describe, expect, it, vi } from "vitest";

import { api, clearAccessToken, setAccessToken } from "@/api/client";

function mockJsonResponse(payload: unknown = {}) {
  return Promise.resolve({
    ok: true,
    json: () => Promise.resolve(payload),
  } as Response);
}

function mockErrorResponse(payload: unknown, status = 503) {
  return Promise.resolve({
    ok: false,
    status,
    text: () => Promise.resolve(JSON.stringify(payload)),
  } as Response);
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

describe("api client", () => {
  afterEach(() => {
    clearAccessToken();
    vi.restoreAllMocks();
  });

  it("stores refreshed access tokens in memory and includes refresh credentials", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      if (input === "/auth/refresh") {
        return mockJsonResponse({
          access_token: "fresh-token",
          token_type: "bearer",
          expires_in: 900,
          user: authenticatedUser,
        });
      }
      return mockJsonResponse(authenticatedUser);
    });

    await api.refresh();
    await api.me();

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/auth/refresh",
      expect.objectContaining({ method: "POST", credentials: "include" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/auth/me",
      expect.objectContaining({
        credentials: "include",
        headers: expect.objectContaining({ Authorization: "Bearer fresh-token" }),
      }),
    );
  });

  it("uses one shared refresh for simultaneous 401 responses and replays each request once", async () => {
    setAccessToken("expired-token");
    let protectedCalls = 0;
    let refreshCalls = 0;
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      if (input === "/auth/refresh") {
        refreshCalls += 1;
        return mockJsonResponse({
          access_token: "rotated-token",
          token_type: "bearer",
          expires_in: 900,
          user: authenticatedUser,
        });
      }
      protectedCalls += 1;
      if (protectedCalls <= 2) return mockErrorResponse({ detail: "Access token expired" }, 401);
      expect(init?.headers).toEqual(expect.objectContaining({ Authorization: "Bearer rotated-token" }));
      return mockJsonResponse({ items: [], total: 0, limit: 100, offset: 0 });
    });

    await Promise.all([api.listTasks(), api.listTasks()]);

    expect(refreshCalls).toBe(1);
    expect(fetchMock).toHaveBeenCalledTimes(5);
    expect(protectedCalls).toBe(4);
  });

  it("replays a delayed stale 401 with the newer token without refreshing again", async () => {
    setAccessToken("expired-token");
    const delayedUnauthorized = deferred<Response>();
    let taskCalls = 0;
    let refreshCalls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      if (input === "/auth/refresh") {
        refreshCalls += 1;
        return mockJsonResponse({
          access_token: "rotated-token",
          token_type: "bearer",
          expires_in: 900,
          user: authenticatedUser,
        });
      }
      taskCalls += 1;
      if (taskCalls === 1) return mockErrorResponse({ detail: "Expired" }, 401);
      if (taskCalls === 2) return delayedUnauthorized.promise;
      expect(init?.headers).toEqual(expect.objectContaining({ Authorization: "Bearer rotated-token" }));
      return mockJsonResponse({ items: [], total: 0, limit: 100, offset: 0 });
    });

    const first = api.listTasks();
    const second = api.listTasks();
    await first;
    delayedUnauthorized.resolve(await mockErrorResponse({ detail: "Expired later" }, 401));
    await second;

    expect(refreshCalls).toBe(1);
    expect(taskCalls).toBe(4);
  });

  it("does not install or replay a refresh that finishes after logout", async () => {
    setAccessToken("expired-token");
    const pendingRefresh = deferred<Response>();
    let taskCalls = 0;
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      if (input === "/tasks") {
        taskCalls += 1;
        return mockErrorResponse({ detail: "Expired" }, 401);
      }
      if (input === "/auth/refresh") return pendingRefresh.promise;
      if (input === "/auth/logout") return Promise.resolve({ ok: true, status: 204 } as Response);
      expect(init?.headers).not.toEqual(expect.objectContaining({ Authorization: expect.any(String) }));
      return mockJsonResponse(authenticatedUser);
    });

    const protectedRequest = api.listTasks();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/auth/refresh", expect.anything()));
    await api.logout();
    pendingRefresh.resolve(await mockJsonResponse({
      access_token: "stale-token",
      token_type: "bearer",
      expires_in: 900,
      user: authenticatedUser,
    }));

    await expect(protectedRequest).rejects.toThrow("Authentication session changed");
    await api.me();
    expect(taskCalls).toBe(1);
  });

  it("does not let an older refresh overwrite a newer login", async () => {
    setAccessToken("expired-token");
    const pendingRefresh = deferred<Response>();
    const newerUser = { ...authenticatedUser, id: "user-2", username: "new-user" };
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      if (input === "/tasks") return mockErrorResponse({ detail: "Expired" }, 401);
      if (input === "/auth/refresh") return pendingRefresh.promise;
      if (input === "/auth/login") {
        return mockJsonResponse({
          access_token: "new-login-token",
          token_type: "bearer",
          expires_in: 900,
          user: newerUser,
        });
      }
      expect(init?.headers).toEqual(expect.objectContaining({ Authorization: "Bearer new-login-token" }));
      return mockJsonResponse(newerUser);
    });

    const protectedRequest = api.listTasks();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/auth/refresh", expect.anything()));
    await api.login({ username: "new-user", password: "new-password" });
    pendingRefresh.resolve(await mockJsonResponse({
      access_token: "stale-token",
      token_type: "bearer",
      expires_in: 900,
      user: authenticatedUser,
    }));

    await expect(protectedRequest).rejects.toThrow("Authentication session changed");
    await api.me();
  });

  it("never replays an old-session 401 after a newer login changes the auth epoch", async () => {
    setAccessToken("old-user-token");
    const delayedUnauthorized = deferred<Response>();
    let taskCalls = 0;
    let refreshCalls = 0;
    const newerUser = { ...authenticatedUser, id: "user-2", username: "new-user" };
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      if (input === "/tasks") {
        taskCalls += 1;
        return delayedUnauthorized.promise;
      }
      if (input === "/auth/refresh") {
        refreshCalls += 1;
        return mockJsonResponse({});
      }
      return mockJsonResponse({
        access_token: "new-user-token",
        token_type: "bearer",
        expires_in: 900,
        user: newerUser,
      });
    });

    const oldRequest = api.listTasks();
    await api.login({ username: "new-user", password: "new-password" });
    delayedUnauthorized.resolve(await mockErrorResponse({ detail: "Old session expired" }, 401));

    await expect(oldRequest).rejects.toThrow("Authentication session changed");
    expect(taskCalls).toBe(1);
    expect(refreshCalls).toBe(0);
  });

  it("replays an original request no more than once", async () => {
    setAccessToken("expired-token");
    let taskCalls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      if (input === "/auth/refresh") {
        return mockJsonResponse({
          access_token: "fresh-token",
          token_type: "bearer",
          expires_in: 900,
          user: authenticatedUser,
        });
      }
      taskCalls += 1;
      return mockErrorResponse({ detail: taskCalls === 1 ? "Expired" : "Still unauthorized" }, 401);
    });

    await expect(api.listTasks()).rejects.toThrow("Still unauthorized");
    expect(taskCalls).toBe(2);
  });

  it("does not recursively refresh auth requests", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      mockErrorResponse({ detail: "Invalid credentials" }, 401),
    );

    await expect(api.login({ username: "member", password: "wrong" })).rejects.toThrow("Invalid credentials");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("clears the in-memory session and reports the refresh error when refresh fails", async () => {
    setAccessToken("expired-token");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      if (input === "/auth/refresh") return mockErrorResponse({ detail: "Refresh session expired" }, 401);
      if (input === "/tasks") return mockErrorResponse({ detail: "Access token expired" }, 401);
      expect(init?.headers).not.toEqual(expect.objectContaining({ Authorization: expect.any(String) }));
      return mockJsonResponse(authenticatedUser);
    });

    await expect(api.listTasks()).rejects.toThrow("Refresh session expired");
    await api.me();

    expect(fetchMock).toHaveBeenLastCalledWith(
      "/auth/me",
      expect.objectContaining({
        headers: expect.not.objectContaining({ Authorization: expect.any(String) }),
      }),
    );
  });

  it("uses backend route contracts for task actions", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(() => mockJsonResponse({ items: [], total: 0 }));

    await api.createDataset({ name: "folder-a", task: "detect", class_schema: { names: ["ok", "defect"] } });
    await api.uploadDatasetSample("dataset-1", new File(["image"], "part.png", { type: "image/png" }));
    await api.uploadDatasetBatch("dataset-1", [new File(["label"], "part.txt", { type: "text/plain" })]);
    await api.analyzeDataset("dataset-1");
    await api.processDataset("dataset-1", { augment: { horizontalFlip: true }, clean: { blur: true } });
    await api.validateDataset("dataset-1");
    await api.createLabelProject("dataset-1", { external_project_id: "9001" });
    await api.syncLabelProjectSamples("label-project-1");
    await api.importLabelProjectAnnotations("label-project-1");
    await api.deleteDataset("dataset-1");
    await api.createTrainingJob("pipeline-1", {});

    expect(fetchMock).toHaveBeenNthCalledWith(
      1,
      "/datasets",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      2,
      "/datasets/dataset-1/samples:upload",
      expect.objectContaining({
        method: "POST",
        body: expect.any(FormData),
        headers: undefined,
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      "/datasets/dataset-1/samples:upload-batch",
      expect.objectContaining({
        method: "POST",
        body: expect.any(FormData),
        headers: undefined,
      }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      "/datasets/dataset-1/analyze",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      5,
      "/datasets/dataset-1/process",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      6,
      "/datasets/dataset-1/validate",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      7,
      "/datasets/dataset-1/label-projects",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      8,
      "/label-projects/label-project-1/sync-samples",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      9,
      "/label-projects/label-project-1/import-annotations",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      10,
      "/datasets/dataset-1",
      expect.objectContaining({ method: "DELETE" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      11,
      "/pipelines/pipeline-1/jobs",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("sends the audit log cursor when requesting a later page", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(() => mockJsonResponse({
      items: [],
      total: 0,
      next_cursor: null,
    }));

    await api.listAuditLogs({
      cursor: "opaque-page-cursor",
      limit: 20,
    });

    const requestedUrl = new URL(fetchMock.mock.calls[0][0] as string, "http://visiox.test");
    expect(requestedUrl.pathname).toBe("/admin/audit-logs");
    expect(requestedUrl.searchParams.get("cursor")).toBe("opaque-page-cursor");
    expect(requestedUrl.searchParams.get("limit")).toBe("20");
    expect(requestedUrl.searchParams.has("offset")).toBe(false);
  });

  it("surfaces backend detail messages from failed responses", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      mockErrorResponse({ detail: "Label Studio 服务不可用，请确认服务已启动并可访问" }),
    );

    await expect(api.createLabelProject("dataset-1")).rejects.toThrow("Label Studio 服务不可用");
  });

  it("uses real edge resource and deployment lifecycle routes", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(() => mockJsonResponse({ items: [], total: 0 }));

    await api.listResourcePools();
    await api.listNodes();
    await api.getService("service-1");
    await api.stopService("service-1");
    await api.rollbackService("service-1");

    expect(fetchMock).toHaveBeenNthCalledWith(1, "/resource-pools", expect.anything());
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/nodes", expect.anything());
    expect(fetchMock).toHaveBeenNthCalledWith(3, "/services/service-1", expect.anything());
    expect(fetchMock).toHaveBeenNthCalledWith(
      4,
      "/services/service-1/stop",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenNthCalledWith(
      5,
      "/services/service-1/rollback",
      expect.objectContaining({ method: "POST" }),
    );
  });
});

const authenticatedUser = {
  id: "user-1",
  username: "member",
  display_name: "Member One",
  email: "member@example.com",
  role: "member" as const,
  status: "active",
  must_change_password: false,
};
