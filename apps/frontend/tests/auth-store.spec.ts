import { createPinia, disposePinia, type Pinia, setActivePinia } from "pinia";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";

import { api, clearAccessToken } from "@/api/client";
import { useAuthStore } from "@/stores/auth";

const member = {
  id: "user-1",
  username: "member",
  display_name: "Member One",
  email: "member@example.com",
  role: "member" as const,
  status: "active",
  must_change_password: false,
};

const admin = { ...member, id: "admin-1", username: "admin", role: "admin" as const };
type TestUser = Omit<typeof member, "role"> & { role: "admin" | "member" };

function jsonResponse(payload: unknown = {}, status = 200): Promise<Response> {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(payload),
    text: () => Promise.resolve(JSON.stringify(payload)),
  } as Response);
}

function noContentResponse(): Promise<Response> {
  return Promise.resolve({ ok: true, status: 204 } as Response);
}

function loginResponse(user: TestUser = member, accessToken = "access-token") {
  return {
    access_token: accessToken,
    token_type: "bearer",
    expires_in: 900,
    user,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((resolvePromise) => {
    resolve = resolvePromise;
  });
  return { promise, resolve };
}

describe("auth store", () => {
  let pinia: Pinia;

  beforeEach(() => {
    pinia = createPinia();
    setActivePinia(pinia);
    clearAccessToken();
  });

  afterEach(() => {
    disposePinia(pinia);
    clearAccessToken();
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("sets authenticated login state without persisting the access token", async () => {
    const storageSpy = vi.spyOn(Storage.prototype, "setItem");
    vi.spyOn(globalThis, "fetch").mockImplementation(() => jsonResponse(loginResponse(admin)));
    const store = useAuthStore();

    await store.login("admin", "correct-password");

    expect(store.user).toEqual(admin);
    expect(store.accessToken).toBe("access-token");
    expect(store.expiresAt).toBeGreaterThan(Date.now());
    expect(store.isAuthenticated).toBe(true);
    expect(store.isAdmin).toBe(true);
    expect(storageSpy).not.toHaveBeenCalled();
  });

  it("initializes the current user from the refresh cookie once", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      jsonResponse(loginResponse(member, "bootstrap-token")),
    );
    const store = useAuthStore();

    await Promise.all([store.initialize(), store.initialize()]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      "/auth/refresh",
      expect.objectContaining({ credentials: "include" }),
    );
    expect(store.user).toEqual(member);
    expect(store.accessToken).toBe("bootstrap-token");
    expect(store.initialized).toBe(true);
  });

  it("treats an unauthenticated bootstrap as a normal signed-out state", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      jsonResponse({ detail: "Invalid refresh token" }, 401),
    );
    const store = useAuthStore();

    await expect(store.initialize()).resolves.toBeUndefined();

    expect(store.initialized).toBe(true);
    expect(store.loading).toBe(false);
    expect(store.error).toBeNull();
    expect(store.isAuthenticated).toBe(false);
  });

  it("keeps background bootstrap failures out of the login error surface", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      jsonResponse({ detail: "Not Found" }, 404),
    );
    const store = useAuthStore();

    await expect(store.initialize()).resolves.toBeUndefined();

    expect(store.initialized).toBe(true);
    expect(store.error).toBeNull();
    expect(store.isAuthenticated).toBe(false);
  });

  it("updates state after an explicit refresh", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      jsonResponse(loginResponse(member, "refreshed-token")),
    );
    const store = useAuthStore();

    await store.refresh();

    expect(store.accessToken).toBe("refreshed-token");
    expect(store.user).toEqual(member);
    expect(store.isAuthenticated).toBe(true);
  });

  it("applies an automatic protected-request refresh to the Pinia session", async () => {
    let taskCalls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      if (input === "/auth/login") return jsonResponse(loginResponse(member, "expired-token"));
      if (input === "/auth/refresh") return jsonResponse(loginResponse(admin, "automatic-token"));
      taskCalls += 1;
      if (taskCalls === 1) return jsonResponse({ detail: "Expired" }, 401);
      return jsonResponse({ items: [], total: 0, limit: 100, offset: 0 });
    });
    const store = useAuthStore();
    await store.login("member", "correct-password");

    const { api } = await import("@/api/client");
    await api.listTasks();

    expect(store.user).toEqual(admin);
    expect(store.accessToken).toBe("automatic-token");
    expect(store.isAdmin).toBe(true);
  });

  it("clears Pinia state when automatic refresh terminally fails", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      if (input === "/auth/login") return jsonResponse(loginResponse());
      if (input === "/tasks") return jsonResponse({ detail: "Expired" }, 401);
      return jsonResponse({ detail: "Refresh expired" }, 401);
    });
    const store = useAuthStore();
    await store.login("member", "correct-password");

    const { api } = await import("@/api/client");
    await expect(api.listTasks()).rejects.toThrow("Refresh expired");

    expect(store.user).toBeNull();
    expect(store.accessToken).toBeNull();
    expect(store.expiresAt).toBeNull();
  });

  it("clears the reactive session when its access token expires", async () => {
    vi.useFakeTimers();
    vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      jsonResponse({ ...loginResponse(), expires_in: 2 }),
    );
    const store = useAuthStore();
    await store.login("member", "correct-password");

    await vi.advanceTimersByTimeAsync(2_000);

    expect(store.user).toBeNull();
    expect(store.accessToken).toBeNull();
    expect(store.isAuthenticated).toBe(false);
  });

  it("initializes separate Pinia instances from one shared refresh", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(() =>
      jsonResponse(loginResponse(member, "shared-token")),
    );
    const firstPinia = pinia;
    const firstStore = useAuthStore(firstPinia);
    const secondPinia = createPinia();
    const secondStore = useAuthStore(secondPinia);

    await Promise.all([firstStore.initialize(), secondStore.initialize()]);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(firstStore.accessToken).toBe("shared-token");
    expect(secondStore.accessToken).toBe("shared-token");
    expect(firstStore.initialized).toBe(true);
    expect(secondStore.initialized).toBe(true);
    disposePinia(secondPinia);
  });

  it("does not let an old initialize completion overwrite a newer login", async () => {
    const pendingInitialize = deferred<Response>();
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      if (input === "/auth/refresh") return pendingInitialize.promise;
      return jsonResponse(loginResponse(admin, "new-login-token"));
    });
    const store = useAuthStore();

    const initialization = store.initialize();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/auth/refresh", expect.anything()));
    await store.login("admin", "new-password");
    pendingInitialize.resolve(await jsonResponse(loginResponse(member, "old-bootstrap-token")));
    await initialization;

    expect(store.user).toEqual(admin);
    expect(store.accessToken).toBe("new-login-token");
    expect(store.initialized).toBe(true);
    expect(store.loading).toBe(false);
    expect(store.error).toBeNull();
  });

  it("does not let an old refresh completion alter a newer login", async () => {
    const pendingRefresh = deferred<Response>();
    let loginCalls = 0;
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      if (input === "/auth/login") {
        loginCalls += 1;
        return jsonResponse(loginCalls === 1
          ? loginResponse(member, "old-token")
          : loginResponse(admin, "new-token"));
      }
      return pendingRefresh.promise;
    });
    const store = useAuthStore();
    await store.login("member", "old-password");

    const oldRefresh = store.refresh();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/auth/refresh", expect.anything()));
    await store.login("admin", "new-password");
    pendingRefresh.resolve(await jsonResponse(loginResponse(member, "stale-refresh-token")));
    await expect(oldRefresh).rejects.toThrow("Authentication session changed");

    expect(store.user).toEqual(admin);
    expect(store.accessToken).toBe("new-token");
    expect(store.loading).toBe(false);
    expect(store.error).toBeNull();
  });

  it("does not let an old logout completion clear a newer login", async () => {
    const pendingLogout = deferred<Response>();
    const pendingNewLogin = deferred<Response>();
    let loginCalls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      if (input === "/auth/logout") return pendingLogout.promise;
      loginCalls += 1;
      if (loginCalls === 1) return jsonResponse(loginResponse(member, "old-token"));
      return pendingNewLogin.promise;
    });
    const store = useAuthStore();
    await store.login("member", "old-password");

    const oldLogout = store.logout();
    const newLogin = store.login("admin", "new-password");
    pendingLogout.resolve(await jsonResponse({ detail: "Logout failed" }, 503));
    await expect(oldLogout).rejects.toThrow("Logout failed");

    expect(store.loading).toBe(true);
    expect(store.error).toBeNull();

    pendingNewLogin.resolve(await jsonResponse(loginResponse(admin, "new-token")));
    await newLogin;

    expect(store.user).toEqual(admin);
    expect(store.accessToken).toBe("new-token");
    expect(store.loading).toBe(false);
    expect(store.error).toBeNull();
  });

  it("does not let an old password-change completion clear a newer login", async () => {
    const pendingPasswordChange = deferred<Response>();
    let loginCalls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      if (input === "/account/change-password") return pendingPasswordChange.promise;
      loginCalls += 1;
      return jsonResponse(loginCalls === 1
        ? loginResponse(member, "old-token")
        : loginResponse(admin, "new-token"));
    });
    const store = useAuthStore();
    await store.login("member", "old-password");

    const oldPasswordChange = store.changePassword("old-password", "changed-password-123");
    await store.login("admin", "new-password");
    pendingPasswordChange.resolve(await noContentResponse());
    await oldPasswordChange;

    expect(store.user).toEqual(admin);
    expect(store.accessToken).toBe("new-token");
    expect(store.loading).toBe(false);
    expect(store.error).toBeNull();
  });

  it("discards a delayed profile response after the session identity changes", async () => {
    const pendingProfile = deferred<Response>();
    let loginCalls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation((input) => {
      if (input === "/account/profile") return pendingProfile.promise;
      loginCalls += 1;
      return jsonResponse(loginCalls === 1
        ? loginResponse(member, "old-token")
        : loginResponse(admin, "new-token"));
    });
    const store = useAuthStore();
    await store.login("member", "old-password");

    const oldProfileUpdate = store.updateProfile({ display_name: "Old User Updated" });
    await store.login("admin", "new-password");
    pendingProfile.resolve(await jsonResponse({ ...member, display_name: "Old User Updated" }));
    await oldProfileUpdate;

    expect(store.user).toEqual(admin);
    expect(store.accessToken).toBe("new-token");
    expect(store.loading).toBe(false);
    expect(store.error).toBeNull();
  });

  it("preserves a delayed profile update across an unowned same-user automatic refresh", async () => {
    const pendingRefresh = deferred<Response>();
    const delayedProfileUnauthorized = deferred<Response>();
    const updatedMember = { ...member, display_name: "Updated After Refresh" };
    let taskCalls = 0;
    let profileCalls = 0;
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation((input, init) => {
      if (input === "/auth/login") return jsonResponse(loginResponse(member, "expired-token"));
      if (input === "/auth/refresh") return pendingRefresh.promise;
      if (input === "/tasks") {
        taskCalls += 1;
        if (taskCalls === 1) return jsonResponse({ detail: "Expired" }, 401);
        expect(init?.headers).toEqual(expect.objectContaining({ Authorization: "Bearer refreshed-token" }));
        return jsonResponse({ items: [], total: 0, limit: 100, offset: 0 });
      }
      profileCalls += 1;
      if (profileCalls === 1) return delayedProfileUnauthorized.promise;
      expect(init?.headers).toEqual(expect.objectContaining({ Authorization: "Bearer refreshed-token" }));
      return jsonResponse(updatedMember);
    });
    const store = useAuthStore();
    await store.login("member", "password");

    const backgroundRequest = api.listTasks();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledWith("/auth/refresh", expect.anything()));
    const profileUpdate = store.updateProfile({ display_name: updatedMember.display_name });
    pendingRefresh.resolve(await jsonResponse(loginResponse(member, "refreshed-token")));
    await backgroundRequest;
    expect(store.loading).toBe(true);

    delayedProfileUnauthorized.resolve(await jsonResponse({ detail: "Expired" }, 401));
    await profileUpdate;

    expect(profileCalls).toBe(2);
    expect(store.user).toEqual(updatedMember);
    expect(store.accessToken).toBe("refreshed-token");
    expect(store.loading).toBe(false);
    expect(store.error).toBeNull();
  });

  it("always clears local state when logout fails", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("network unavailable"));
    const store = useAuthStore();
    store.$patch({ user: member, accessToken: "access-token", expiresAt: Date.now() + 60_000 });

    await expect(store.logout()).rejects.toThrow("network unavailable");

    expect(store.user).toBeNull();
    expect(store.accessToken).toBeNull();
    expect(store.expiresAt).toBeNull();
    expect(store.isAuthenticated).toBe(false);
  });

  it("updates the account profile in local state", async () => {
    const updated = { ...member, display_name: "Updated Name" };
    vi.spyOn(globalThis, "fetch").mockImplementation(() => jsonResponse(updated));
    const store = useAuthStore();
    store.$patch({ user: member, accessToken: "access-token", expiresAt: Date.now() + 60_000 });

    await store.updateProfile({ display_name: "Updated Name" });

    expect(store.user).toEqual(updated);
  });

  it("clears the session after changing the password", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(() => noContentResponse());
    const store = useAuthStore();
    store.$patch({ user: member, accessToken: "access-token", expiresAt: Date.now() + 60_000 });

    await store.changePassword("old-password", "new-password-123");

    expect(store.user).toBeNull();
    expect(store.accessToken).toBeNull();
    expect(store.expiresAt).toBeNull();
  });
});
