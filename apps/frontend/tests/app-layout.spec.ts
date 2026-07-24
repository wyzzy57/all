import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import appSource from "@/App.vue?raw";

const stylesSource = readFileSync("src/styles.css", "utf8");

describe("global application header", () => {
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
    expect(appSource).toContain("sidebarCollapsed");
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
});
