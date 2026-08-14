import { readFileSync, readdirSync } from "node:fs";

import ElementPlus from "element-plus";
import { createPinia } from "pinia";
import { mount } from "@vue/test-utils";
import { createMemoryHistory, createRouter } from "vue-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "@/App.vue";
import appSource from "@/App.vue?raw";
import UserAccountMenu from "@/components/account/UserAccountMenu.vue";
import stylesRawSource from "@/styles.css?raw";
import adminOverviewSource from "@/views/admin/AdminOverviewView.vue?raw";
import auditLogSource from "@/views/admin/AuditLogView.vue?raw";
import workbenchSource from "@/views/workbench/WorkbenchView.vue?raw";
import { topLevelRuleDeclarations } from "./helpers/css-rules";

const stylesSource = stylesRawSource || readFileSync("src/styles.css", "utf8");

function sourceFiles(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = `${directory}/${entry.name}`;
    if (entry.isDirectory()) return sourceFiles(path);
    return /\.(?:css|vue)$/.test(entry.name) ? [path] : [];
  });
}

describe("global application header", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it("renders login outside the authenticated shell and pins the account menu to the sidebar bottom", () => {
    expect(appSource).toContain("isLoginRoute");
    expect(appSource).toContain('class="auth-route"');
    expect(appSource).toContain('class="sidebar-account"');
    expect(appSource).toContain(':collapsed="userCollapsed"');
  });

  it("moves the brand into the sidebar and removes the standalone header", () => {
    expect(appSource).not.toContain('class="app-header"');
    expect(appSource).toContain('class="sidebar-brand"');
    expect(appSource).not.toContain('class="page-title"');
    expect(appSource).not.toContain('class="page-subtitle"');
  });

  it("uses a viewport-owned shell and keeps manual navigation collapse", () => {
    expect(appSource).toContain('class="app-content-shell"');
    expect(appSource).toContain('class="app-main"');
    expect(appSource).toContain(':aria-label="item.label"');
    expect(appSource).toContain("userCollapsed");
    expect(appSource).toContain("visiox.sidebar.collapsed");
    expect(appSource).toContain("收起侧边栏");
  });

  it("places data preparation before model space in the primary navigation", () => {
    expect(appSource.indexOf('path: "/data-preparation"')).toBeLessThan(
      appSource.indexOf('path: "/model-space"'),
    );
  });

  it("keeps the VisiOX wordmark inside the navigation rail", () => {
    expect(appSource).toContain('class="brand-word">Visio');
    expect(appSource).toContain('class="brand-x">X');
    expect(appSource.indexOf('class="sidebar-brand"')).toBeGreaterThan(
      appSource.indexOf('class="app-sidebar"'),
    );
    expect(appSource.indexOf('class="sidebar-brand"')).toBeLessThan(
      appSource.indexOf('class="sidebar-nav"'),
    );
  });

  it("renders the blue wordmark X upright", () => {
    expect(stylesSource).toMatch(
      /\.brand-x\s*\{[^}]*font-style:\s*normal;[^}]*transform:\s*none;/s,
    );
  });

  it("uses a white application canvas and neutral selected navigation", () => {
    expect(stylesSource).toContain("--visiox-canvas: #ffffff");
    expect(stylesSource).toContain("--visiox-sidebar: #ffffff");
    expect(stylesSource).toMatch(/\.nav-menu \.el-menu-item\.is-active\s*\{[^}]*background:\s*#e7e7e7;[^}]*color:\s*#171717/s);
    expect(stylesSource).not.toMatch(/\.nav-menu \.el-menu-item\.is-active::before/);
    expect(stylesSource).toContain(".nav-group");
    expect(stylesSource).toContain(".app-sidebar:hover .sidebar-toggle");
    expect(stylesSource).toContain(".app-sidebar:focus-within .sidebar-toggle");
  });

  it("defines the shared business card surface tokens on the root", () => {
    const rootRule = topLevelRuleDeclarations(stylesSource, ":root");
    expect(rootRule?.get("--visiox-surface")).toEqual(["#ffffff"]);
    expect(rootRule?.get("--visiox-card-surface")).toEqual(["#ffffff"]);
    expect(rootRule?.get("--visiox-card-surface-raised")).toEqual(["#ffffff"]);
    expect(rootRule?.get("--visiox-card-border")).toEqual(["#e0e2e6"]);
    expect(rootRule?.get("--visiox-card-radius")).toEqual(["8px"]);
  });

  it("keeps the workbench surfaces white while preserving bordered sections", () => {
    expect(workbenchSource).toContain("--workbench-surface: #ffffff");
    expect(workbenchSource).toContain("--workbench-surface-raised: #ffffff");
    expect(workbenchSource).toContain("border: 1px solid var(--workbench-border)");
  });

  it("keeps manual sidebar collapse and an explicit account focus ring", () => {
    expect(stylesSource).not.toContain(".app-sidebar.mobile-expanded");
    expect(stylesSource).toContain("flex-basis: 64px");
    expect(stylesSource).toContain("outline: 2px solid");
    expect(stylesSource).toContain("outline-offset:");
  });

  it("keeps sidebar state independent from viewport width", async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: "/workbench", component: { template: "<div>workbench</div>" } }],
    });
    await router.push("/workbench");
    await router.isReady();
    const wrapper = mount(App, { global: { plugins: [createPinia(), router, ElementPlus] } });

    expect(wrapper.get(".app-sidebar").classes()).not.toContain("collapsed");
    expect(wrapper.getComponent(UserAccountMenu).props("collapsed")).toBe(false);
    expect(wrapper.get(".sidebar-toggle").attributes("aria-label")).toBe("收起侧边栏");
    expect(wrapper.findAllComponents({ name: "ElMenu" }).map((menu) => menu.props("collapse"))).toEqual([false, false]);

    await wrapper.get(".sidebar-toggle").trigger("click");
    expect(wrapper.get(".app-sidebar").classes()).toContain("collapsed");
    expect(wrapper.getComponent(UserAccountMenu).props("collapsed")).toBe(true);
    expect(wrapper.get(".sidebar-toggle").attributes("aria-label")).toBe("展开侧边栏");
  });

  it("defines shell containment and page overflow contracts for browser QA", () => {
    const workbenchRootRules = [...workbenchSource.matchAll(/\.workbench-view\s*\{([^{}]*)\}/g)]
      .map((match) => match[1]);

    expect(appSource).not.toContain('class="app-header"');
    expect(appSource).toContain('class="app-content-shell"');
    expect(stylesSource).toMatch(/\.app-content-shell\s*\{[^}]*min-width:\s*0;[^}]*overflow:\s*auto/s);
    expect(stylesSource).toMatch(/\.app-main\s*\{[^}]*min-width:\s*var\(--visiox-desktop-canvas-min-width\);[^}]*overflow:\s*visible/s);
    expect(workbenchSource).toContain("@container workbench (max-width: 760px)");
    expect(workbenchSource).not.toMatch(/@media \(max-width:\s*1350px\)/);
    for (const ruleBody of workbenchRootRules) {
      expect(ruleBody).not.toMatch(/transform\s*:[^;{}]*scale\s*\(/);
    }
    expect(adminOverviewSource).toContain("@container admin-overview (max-width: 1120px)");
    expect(adminOverviewSource).toContain(".failure-table-wrap { overflow-x: auto;");
    expect(auditLogSource).toContain(".audit-table-wrap { overflow-x: auto;");
    expect(auditLogSource).toContain("min-width: 980px");
    expect(auditLogSource).toContain('width="min(760px, calc(100vw - 32px))"');
    expect(stylesSource).toContain("width: min(var(--el-dialog-width, 50%), calc(100vw - 32px));");
  });

  it("keeps a stable desktop canvas when browser zoom reduces the CSS viewport", () => {
    const rootRule = topLevelRuleDeclarations(stylesSource, ":root");

    expect(rootRule?.get("--visiox-desktop-canvas-min-width")).toEqual(["1480px"]);
    expect(appSource).not.toContain("matchMedia");
    expect(appSource).not.toContain("isMobile");
    expect(appSource).not.toContain("sidebar-scrim");
    expect(stylesSource).toMatch(
      /\.app-content-shell\s*\{[^}]*overflow:\s*auto/s,
    );
    expect(stylesSource).toMatch(
      /\.app-main\s*\{[^}]*flex:\s*0 0 var\(--visiox-desktop-canvas-min-width\);[^}]*width:\s*var\(--visiox-desktop-canvas-min-width\);[^}]*min-width:\s*var\(--visiox-desktop-canvas-min-width\)/s,
    );
    expect(stylesSource).not.toContain("@media (max-width: 720px)");
    expect(stylesSource).not.toContain("@media (max-width: 1180px)");

    const viewportBreakpoints = sourceFiles("src").flatMap((file) => {
      const source = readFileSync(file, "utf8");
      return [...source.matchAll(/@media\s*\(\s*max-width\s*:\s*(?!0px)[^)]+\)/g)]
        .map((match) => `${file}: ${match[0]}`);
    });
    expect(viewportBreakpoints).toEqual([]);
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
