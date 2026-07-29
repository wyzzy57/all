import ElementPlus from "element-plus";
import { createPinia, setActivePinia } from "pinia";
import { mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import UserAccountMenu from "@/components/account/UserAccountMenu.vue";
import { useAuthStore } from "@/stores/auth";
import AccountView from "@/views/account/AccountView.vue";

const member = {
  id: "member-1",
  username: "member",
  display_name: "普通用户",
  email: "member@example.com",
  role: "member" as const,
  status: "active",
  must_change_password: false,
};

async function mountMenu(role: "member" | "admin", collapsed = false) {
  const pinia = createPinia();
  setActivePinia(pinia);
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", component: { template: "<div />" } },
      { path: "/login", component: { template: "<div />" } },
      { path: "/account", component: { template: "<div />" } },
      { path: "/admin/overview", component: { template: "<div />" } },
      { path: "/admin/audit-logs", component: { template: "<div />" } },
      { path: "/admin/users", component: { template: "<div />" } },
      { path: "/admin/groups", component: { template: "<div />" } },
      { path: "/admin/authorization", component: { template: "<div />" } },
    ],
  });
  await router.push("/");
  await router.isReady();
  const auth = useAuthStore();
  auth.$patch({
    initialized: true,
    user: { ...member, role },
    accessToken: "token",
    expiresAt: Date.now() + 60_000,
  });
  const wrapper = mount(UserAccountMenu, {
    props: { collapsed },
    global: { plugins: [pinia, router, ElementPlus] },
  });
  return { auth, router, wrapper };
}

describe("UserAccountMenu", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("shows the member identity and only member actions while expanded", async () => {
    const { wrapper } = await mountMenu("member");

    expect(wrapper.get("[data-testid='account-name']").text()).toBe("普通用户");
    expect(wrapper.get("[data-testid='account-role']").text()).toContain("普通用户");
    expect(wrapper.find("[data-testid='admin-user-entry']").exists()).toBe(false);
    expect(wrapper.get("[data-testid='account-trigger']").attributes("aria-label")).toContain("账户菜单");
  });

  it("shows only the avatar in collapsed mode", async () => {
    const { wrapper } = await mountMenu("member", true);

    expect(wrapper.get("[data-testid='account-avatar']")).toBeTruthy();
    expect(wrapper.find("[data-testid='account-name']").exists()).toBe(false);
    expect(wrapper.find("[data-testid='account-role']").exists()).toBe(false);
  });

  it("exposes user, group and authorization entries to administrators", async () => {
    const { wrapper } = await mountMenu("admin");

    expect(wrapper.find("[data-testid='admin-user-entry']").exists()).toBe(true);
    expect(wrapper.find("[data-testid='admin-overview-entry']").exists()).toBe(true);
    expect(wrapper.find("[data-testid='admin-audit-entry']").exists()).toBe(true);
    expect(wrapper.find("[data-testid='admin-group-entry']").exists()).toBe(true);
    expect(wrapper.find("[data-testid='admin-authorization-entry']").exists()).toBe(true);
  });

  it("logs out and returns to login", async () => {
    const { auth, router, wrapper } = await mountMenu("member");
    vi.spyOn(auth, "logout").mockResolvedValue();

    await wrapper.get("[data-testid='logout-entry']").trigger("click");

    expect(auth.logout).toHaveBeenCalledOnce();
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe("/login"));
  });

  it("returns to login even when the server logout request fails", async () => {
    const { auth, router, wrapper } = await mountMenu("member");
    vi.spyOn(auth, "logout").mockRejectedValue(new Error("network unavailable"));

    await wrapper.get("[data-testid='logout-entry']").trigger("click");

    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe("/login"));
  });
});

describe("AccountView", () => {
  it("shows the forced password warning and saves profile changes", async () => {
    const { auth, wrapper } = await mountAccount(true);
    vi.spyOn(auth, "updateProfile").mockResolvedValue({ ...member, display_name: "New Name" });

    expect(wrapper.text()).toContain("临时密码");
    await wrapper.get("[data-testid='profile-display-name']").setValue("New Name");
    await wrapper.get("[data-testid='profile-email']").setValue("new@example.com");
    await wrapper.get("[data-testid='profile-form']").trigger("submit");

    expect(auth.updateProfile).toHaveBeenCalledWith({ display_name: "New Name", email: "new@example.com" });
  });

  it("changes the password and returns to login", async () => {
    const { auth, router, wrapper } = await mountAccount(false);
    vi.spyOn(auth, "changePassword").mockResolvedValue();

    await wrapper.get("[data-testid='current-password']").setValue("old-password");
    await wrapper.get("[data-testid='new-password']").setValue("new-password-123");
    await wrapper.get("[data-testid='confirm-password']").setValue("new-password-123");
    await wrapper.get("[data-testid='password-form']").trigger("submit");

    expect(auth.changePassword).toHaveBeenCalledWith("old-password", "new-password-123");
    await vi.waitFor(() => expect(router.currentRoute.value.path).toBe("/login"));
  });
});

async function mountAccount(mustChangePassword: boolean) {
  const pinia = createPinia();
  setActivePinia(pinia);
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/account", component: AccountView },
      { path: "/login", component: { template: "<div>login</div>" } },
    ],
  });
  await router.push("/account");
  await router.isReady();
  const auth = useAuthStore();
  auth.$patch({
    initialized: true,
    user: { ...member, must_change_password: mustChangePassword },
    accessToken: "token",
    expiresAt: Date.now() + 60_000,
  });
  const wrapper = mount(AccountView, { global: { plugins: [pinia, router, ElementPlus] } });
  return { auth, router, wrapper };
}
