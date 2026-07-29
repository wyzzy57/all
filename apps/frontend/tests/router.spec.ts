import { createPinia, disposePinia, setActivePinia, type Pinia } from "pinia";
import { createMemoryHistory, type RouteRecordRaw } from "vue-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { clearAccessToken } from "@/api/client";
import { createAppRouter, safeRedirectTarget } from "@/router";
import { useAuthStore } from "@/stores/auth";

const member = {
  id: "member-1",
  username: "member",
  display_name: "Member",
  email: "member@example.com",
  role: "member" as const,
  status: "active",
  must_change_password: false,
};

const admin = { ...member, id: "admin-1", username: "admin", role: "admin" as const };
const passwordChangeAdmin = { ...admin, must_change_password: true };
const guardTestRoutes: RouteRecordRaw[] = [
  { path: "/login", name: "login", component: { template: "<div>login</div>" } },
  {
    path: "/workbench",
    component: { template: "<div>workbench</div>" },
    meta: { requiresAuth: true },
  },
  { path: "/tasks", component: { template: "<div>tasks</div>" }, meta: { requiresAuth: true } },
  { path: "/services", component: { template: "<div>services</div>" }, meta: { requiresAuth: true } },
  { path: "/account", component: { template: "<div>account</div>" }, meta: { requiresAuth: true } },
];

function unauthenticatedResponse(): Promise<Response> {
  return Promise.resolve({
    ok: false,
    status: 401,
    text: () => Promise.resolve(JSON.stringify({ detail: "Not authenticated" })),
  } as Response);
}

function authenticatedResponse(): Promise<Response> {
  return Promise.resolve({
    ok: true,
    status: 200,
    json: () => Promise.resolve({
      access_token: "refreshed-token",
      token_type: "bearer",
      expires_in: 900,
      user: member,
    }),
  } as Response);
}

describe("router", () => {
  let pinia: Pinia;

  beforeEach(() => {
    pinia = createPinia();
    setActivePinia(pinia);
    clearAccessToken();
  });

  afterEach(() => {
    disposePinia(pinia);
    clearAccessToken();
    vi.restoreAllMocks();
  });

  it("exposes the MVP console sections", () => {
    const router = createAppRouter(createMemoryHistory());
    const paths = router.getRoutes().map((route) => route.path);

    expect(paths).toEqual(
      expect.arrayContaining([
        "/workbench",
        "/model-space",
        "/data-preparation",
        "/services",
        "/tasks",
        "/login",
        "/account",
        "/admin/users",
        "/admin/overview",
        "/admin/groups",
        "/admin/authorization",
        "/admin/audit-logs",
      ]),
    );
    expect(paths).not.toEqual(expect.arrayContaining(["/pipelines", "/devices", "/edge-apps", "/deployments"]));
  });

  it("initializes authentication once and preserves the original protected destination", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(unauthenticatedResponse);
    const router = createAppRouter(createMemoryHistory(), guardTestRoutes);

    await router.push("/tasks?status=FAILED");
    await router.isReady();

    expect(router.currentRoute.value.path).toBe("/login");
    expect(router.currentRoute.value.query.redirect).toBe("/tasks?status=FAILED");
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await router.push({ path: "/login", query: { redirect: "/services" } });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("restores an initialized expired session from the refresh cookie once", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(authenticatedResponse);
    const store = useAuthStore();
    store.$patch({
      initialized: true,
      user: member,
      accessToken: "expired-token",
      expiresAt: Date.now() - 1,
    });
    const router = createAppRouter(createMemoryHistory(), guardTestRoutes);

    await router.push("/tasks");
    await router.isReady();

    expect(router.currentRoute.value.path).toBe("/tasks");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith("/auth/refresh", expect.anything());

    await router.push("/services");
    expect(router.currentRoute.value.path).toBe("/services");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("redirects an initialized expired session when cookie refresh fails without looping", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(unauthenticatedResponse);
    const store = useAuthStore();
    store.$patch({ initialized: true, user: null, accessToken: null, expiresAt: null });
    const router = createAppRouter(createMemoryHistory(), guardTestRoutes);

    await router.push("/tasks");
    await router.isReady();

    expect(router.currentRoute.value.path).toBe("/login");
    expect(router.currentRoute.value.query.redirect).toBe("/tasks");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("moves authenticated users away from login using only safe internal redirects", async () => {
    const store = useAuthStore();
    store.$patch({
      initialized: true,
      user: member,
      accessToken: "member-token",
      expiresAt: Date.now() + 60_000,
    });
    const router = createAppRouter(createMemoryHistory(), guardTestRoutes);

    await router.push({ path: "/login", query: { redirect: "/tasks?status=SUCCESS" } });
    await router.isReady();
    expect(router.currentRoute.value.fullPath).toBe("/tasks?status=SUCCESS");

    await router.push({ path: "/login", query: { redirect: "//evil.example/steal" } });
    expect(router.currentRoute.value.path).toBe("/workbench");
  });

  it("treats trailing-slash login routes and redirects as login", async () => {
    const store = useAuthStore();
    store.$patch({
      initialized: true,
      user: member,
      accessToken: "member-token",
      expiresAt: Date.now() + 60_000,
    });
    const router = createAppRouter(createMemoryHistory(), guardTestRoutes);

    expect(safeRedirectTarget("/login/")).toBe("/workbench");
    await router.push({ path: "/login/", query: { redirect: "/login/" } });
    await router.isReady();

    expect(router.currentRoute.value.path).toBe("/workbench");
  });

  it("denies members and admits administrators on routes marked as admin-only", async () => {
    const router = createAppRouter(createMemoryHistory(), guardTestRoutes);
    router.addRoute({
      path: "/admin/test",
      component: { template: "<div>admin</div>" },
      meta: { requiresAuth: true, requiresAdmin: true },
    });
    const store = useAuthStore();
    store.$patch({
      initialized: true,
      user: member,
      accessToken: "member-token",
      expiresAt: Date.now() + 60_000,
    });

    await router.push("/admin/test");
    await router.isReady();
    expect(router.currentRoute.value.path).toBe("/workbench");

    store.$patch({ user: admin, accessToken: "admin-token", expiresAt: Date.now() + 60_000 });
    await router.push("/admin/test");
    expect(router.currentRoute.value.path).toBe("/admin/test");
  });

  it("marks audit logs as an administrator-only route", () => {
    const router = createAppRouter(createMemoryHistory());
    const auditRoute = router.resolve("/admin/audit-logs");

    expect(auditRoute.meta.requiresAuth).toBe(true);
    expect(auditRoute.meta.requiresAdmin).toBe(true);
  });

  it("requires initial-password accounts to visit account settings before other routes", async () => {
    const router = createAppRouter(createMemoryHistory(), guardTestRoutes);
    const store = useAuthStore();
    store.$patch({
      initialized: true,
      user: passwordChangeAdmin,
      accessToken: "bootstrap-admin-token",
      expiresAt: Date.now() + 60_000,
    });

    await router.push("/workbench");
    await router.isReady();
    expect(router.currentRoute.value.path).toBe("/account");

    await router.push("/account");
    expect(router.currentRoute.value.path).toBe("/account");
  });
});
