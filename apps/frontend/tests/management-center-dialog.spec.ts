import ElementPlus from "element-plus";
import { createPinia, setActivePinia } from "pinia";
import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import ManagementCenterDialog from "@/components/account/ManagementCenterDialog.vue";
import type { ManagementSection } from "@/components/account/ManagementCenterDialog.vue";
import { useAuthStore } from "@/stores/auth";

const user = {
  id: "user-1",
  username: "member",
  display_name: "普通用户",
  email: "member@example.com",
  role: "member" as const,
  status: "active",
  must_change_password: false,
};

async function mountDialog(role: "member" | "admin", initialSection: ManagementSection = "account") {
  const pinia = createPinia();
  setActivePinia(pinia);
  const auth = useAuthStore();
  auth.$patch({
    initialized: true,
    user: { ...user, role },
    accessToken: "token",
    expiresAt: Date.now() + 60_000,
  });
  const wrapper = mount(ManagementCenterDialog, {
    props: { modelValue: true, initialSection },
    global: {
      plugins: [pinia, ElementPlus],
      stubs: {
        teleport: true,
        AccountView: { template: '<div data-testid="account-panel">账户设置</div>' },
        AdminOverviewView: { template: '<div data-testid="overview-panel">平台总览</div>' },
        UserManagementView: { template: '<div data-testid="users-panel">用户管理</div>' },
        GroupManagementView: { template: '<div data-testid="groups-panel">用户分组</div>' },
        AuthorizationView: { template: '<div data-testid="authorization-panel">资源授权</div>' },
        AuditLogView: { template: '<div data-testid="audit-panel">审计日志</div>' },
        NodeManagementView: { template: '<div data-testid="resources-panel">节点与资源</div>' },
      },
    },
  });
  return wrapper;
}

describe("ManagementCenterDialog", () => {
  it("gives desktop management tables more room without widening the navigation rail", async () => {
    const wrapper = await mountDialog("admin", "users");

    expect(wrapper.get(".el-dialog").attributes("style")).toContain("width: min(1320px, 94vw)");
    expect(wrapper.get(".management-center-frame").classes()).toContain("management-center-frame");
  });

  it("opens directly on the requested administrator section", async () => {
    const wrapper = await mountDialog("admin", "users");

    expect(wrapper.get(".el-dialog").attributes("aria-label")).toBe("账户与平台管理");
    expect(wrapper.get("[data-testid='management-nav-users']").classes()).toContain("is-active");
    expect(wrapper.get("[data-testid='users-panel']").text()).toBe("用户管理");
  });

  it("switches sections inside the dedicated window", async () => {
    const wrapper = await mountDialog("admin", "account");

    await wrapper.get("[data-testid='management-nav-resources']").trigger("click");

    expect(wrapper.get("[data-testid='resources-panel']").text()).toBe("节点与资源");
  });

  it("keeps administrator sections hidden from normal users", async () => {
    const wrapper = await mountDialog("member", "users");

    expect(wrapper.find("[data-testid='management-nav-users']").exists()).toBe(false);
    expect(wrapper.get("[data-testid='management-nav-account']").classes()).toContain("is-active");
    expect(wrapper.get("[data-testid='account-panel']").text()).toBe("账户设置");
  });
});
