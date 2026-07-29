import { readFileSync } from "node:fs";

import ElementPlus from "element-plus";
import { createPinia } from "pinia";
import { mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "@/App.vue";
import appSource from "@/App.vue?raw";
import UserAccountMenu from "@/components/account/UserAccountMenu.vue";
import adminOverviewSource from "@/views/admin/AdminOverviewView.vue?raw";
import auditLogSource from "@/views/admin/AuditLogView.vue?raw";
import workbenchSource from "@/views/workbench/WorkbenchView.vue?raw";

const stylesSource = readFileSync("src/styles.css", "utf8");

describe("global application header", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it("renders login outside the authenticated shell and pins the account menu to the sidebar bottom", () => {
    expect(appSource).toContain("isLoginRoute");
    expect(appSource).toContain('class="auth-route"');
    expect(appSource).toContain('class="sidebar-account"');
    expect(appSource).toContain(':collapsed="effectiveCollapsed"');
  });

  it("keeps the header shell and deployment status without repeated page copy", () => {
    expect(appSource).toContain('class="app-header"');
    expect(appSource).toContain("内网部署");
    expect(appSource).not.toContain('class="page-title"');
    expect(appSource).not.toContain('class="page-subtitle"');
    expect(appSource).not.toContain("私有化视觉模型平台");
  });

  it("uses a viewport-owned shell and collapses navigation before content becomes cramped", () => {
    expect(appSource).toContain('class="app-content-shell"');
    expect(appSource).toContain('class="app-main"');
    expect(appSource).toContain(':aria-label="item.label"');
    expect(appSource).toContain("effectiveCollapsed");
    expect(appSource).toContain("visiox.sidebar.collapsed");
    expect(appSource).toContain("收起侧边栏");
  });

  it("places data preparation before model space in the primary navigation", () => {
    expect(appSource.indexOf('path: "/data-preparation"')).toBeLessThan(
      appSource.indexOf('path: "/model-space"'),
    );
  });

  it("uses one continuous branded header above the sidebar and content", () => {
    expect(appSource).toContain('class="brand-word">Visio');
    expect(appSource).toContain('class="brand-x">X');
    expect(appSource.indexOf('class="app-header"')).toBeLessThan(
      appSource.indexOf('class="app-body"'),
    );
    expect(appSource.indexOf('class="app-header"')).toBeLessThan(
      appSource.indexOf('class="app-sidebar"'),
    );
  });

  it("keeps the page canvas white and reveals the sidebar handle on interaction", () => {
    expect(stylesSource).toContain("--visiox-canvas: #ffffff");
    expect(stylesSource).toContain("background: #f5f6f8");
    expect(stylesSource).toContain(".nav-group");
    expect(stylesSource).toContain(".app-sidebar:hover .sidebar-toggle");
    expect(stylesSource).toContain(".app-sidebar:focus-within .sidebar-toggle");
  });

  it("visually collapses the sidebar on narrow screens and keeps an explicit account focus ring", () => {
    expect(stylesSource).toContain("@media (max-width: 720px)");
    expect(stylesSource).toContain(".app-sidebar.mobile-expanded");
    expect(stylesSource).toContain("flex-basis: 64px");
    expect(stylesSource).toContain("outline: 2px solid");
    expect(stylesSource).toContain("outline-offset:");
  });

  it("starts truly collapsed on mobile and immediately expands as an overlay", async () => {
    const listeners = new Set<(event: MediaQueryListEvent) => void>();
    vi.stubGlobal("matchMedia", vi.fn(() => ({
      matches: true,
      media: "(max-width: 720px)",
      onchange: null,
      addEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => listeners.add(listener),
      removeEventListener: (_type: string, listener: (event: MediaQueryListEvent) => void) => listeners.delete(listener),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })));
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/workbench", component: { template: "<div>workbench</div>" } }],
    });
    await router.push("/workbench");
    await router.isReady();
    const wrapper = mount(App, { global: { plugins: [createPinia(), router, ElementPlus] } });

    expect(wrapper.get(".app-sidebar").classes()).toContain("collapsed");
    expect(wrapper.getComponent(UserAccountMenu).props("collapsed")).toBe(true);
    expect(wrapper.get(".sidebar-toggle").attributes("aria-label")).toBe("展开侧边栏");
    expect(wrapper.findAllComponents({ name: "ElMenu" }).map((menu) => menu.props("collapse"))).toEqual([true, true]);

    await wrapper.get(".sidebar-toggle").trigger("click");
    expect(wrapper.get(".app-sidebar").classes()).not.toContain("collapsed");
    expect(wrapper.get(".app-sidebar").classes()).toContain("mobile-expanded");
    expect(wrapper.getComponent(UserAccountMenu).props("collapsed")).toBe(false);
    expect(wrapper.get(".sidebar-toggle").attributes("aria-label")).toBe("收起侧边栏");

    for (const listener of listeners) listener({ matches: true } as MediaQueryListEvent);
    await wrapper.vm.$nextTick();
    expect(wrapper.get(".app-sidebar").classes()).toContain("collapsed");
  });

  it("defines shell containment and page overflow contracts for browser QA", () => {
    expect(appSource.indexOf('class="app-header"')).toBeLessThan(appSource.indexOf('class="app-body"'));
    expect(appSource).toContain('class="app-content-shell"');
    expect(stylesSource).toMatch(/\.app-content-shell\s*\{[^}]*min-width:\s*0;[^}]*overflow:\s*hidden/s);
    expect(stylesSource).toMatch(/\.app-main\s*\{[^}]*min-width:\s*0;[^}]*overflow:\s*auto/s);
    expect(workbenchSource).toContain("@container workbench (max-width: 1080px)");
    expect(adminOverviewSource).toContain("@container admin-overview (max-width: 1120px)");
    expect(adminOverviewSource).toContain(".failure-table-wrap { overflow-x: auto;");
    expect(auditLogSource).toContain(".audit-table-wrap { overflow-x: auto;");
    expect(auditLogSource).toContain("min-width: 980px");
    expect(auditLogSource).toContain('width="min(760px, calc(100vw - 32px))"');
    expect(stylesSource).toContain("width: min(var(--el-dialog-width, 50%), calc(100vw - 32px));");
  });

  it.each([
    ["src/components/common/AsyncState.vue", "正在加载"],
    ["src/views/workbench/WorkbenchView.vue", "工作台"],
    ["src/views/admin/AdminOverviewView.vue", "平台总览"],
    ["src/views/admin/AuditLogView.vue", "审计日志"],
  ])("keeps %s as valid UTF-8 with intact Chinese text", (file, marker) => {
    const source = new TextDecoder("utf-8", { fatal: true }).decode(readFileSync(file));
    expect(source).toContain(marker);
    expect(source).not.toContain(String.fromCharCode(0xfffd));
  });
});
