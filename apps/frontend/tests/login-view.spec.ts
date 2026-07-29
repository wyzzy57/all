import ElementPlus from "element-plus";
import { mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import LoginView from "@/views/auth/LoginView.vue";

const authMock = vi.hoisted(() => ({
  loading: false,
  error: null as string | null,
  login: vi.fn(),
}));

vi.mock("@/stores/auth", () => ({
  useAuthStore: () => authMock,
}));

async function mountLogin(redirect?: string) {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/login", component: LoginView },
      { path: "/workbench", component: { template: "<div>workbench</div>" } },
      { path: "/tasks", component: { template: "<div>tasks</div>" } },
    ],
  });
  await router.push({ path: "/login", query: redirect ? { redirect } : undefined });
  await router.isReady();
  const wrapper = mount(LoginView, {
    global: { plugins: [router, ElementPlus] },
  });
  return { router, wrapper };
}

describe("LoginView", () => {
  beforeEach(() => {
    authMock.loading = false;
    authMock.error = null;
    authMock.login.mockReset();
  });

  it("submits the username and password then returns to the original route", async () => {
    authMock.login.mockResolvedValue(undefined);
    const { router, wrapper } = await mountLogin("/tasks?status=FAILED");

    await wrapper.get('[data-testid="login-username"]').setValue("admin");
    await wrapper.get('[data-testid="login-password"]').setValue("correct-password");
    await wrapper.get("form").trigger("submit");

    await vi.waitFor(() => expect(authMock.login).toHaveBeenCalledWith("admin", "correct-password"));
    await vi.waitFor(() => expect(router.currentRoute.value.fullPath).toBe("/tasks?status=FAILED"));
  });

  it("shows a server error and keeps the user on the login page", async () => {
    authMock.login.mockImplementation(async () => {
      authMock.error = "用户名或密码错误";
      throw new Error("Invalid credentials");
    });
    const { router, wrapper } = await mountLogin();

    await wrapper.get('[data-testid="login-username"]').setValue("admin");
    await wrapper.get('[data-testid="login-password"]').setValue("wrong-password");
    await wrapper.get("form").trigger("submit");

    await vi.waitFor(() => expect(wrapper.text()).toContain("用户名或密码错误"));
    expect(router.currentRoute.value.path).toBe("/login");
  });

  it("disables submission while credentials are incomplete and rejects external redirects", async () => {
    authMock.login.mockResolvedValue(undefined);
    const { router, wrapper } = await mountLogin("https://evil.example/steal");
    const submit = wrapper.get('[data-testid="login-submit"]');

    expect(submit.attributes("disabled")).toBeDefined();
    await wrapper.get('[data-testid="login-username"]').setValue("admin");
    expect(submit.attributes("disabled")).toBeDefined();
    await wrapper.get('[data-testid="login-password"]').setValue("correct-password");
    await wrapper.get("form").trigger("submit");

    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe("/workbench"));
  });
});
