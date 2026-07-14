import { describe, expect, it } from "vitest";

import appSource from "@/App.vue?raw";

describe("global application header", () => {
  it("keeps the header shell and deployment status without repeated page copy", () => {
    expect(appSource).toContain('class="app-header"');
    expect(appSource).toContain("内网部署");
    expect(appSource).not.toContain('class="page-title"');
    expect(appSource).not.toContain('class="page-subtitle"');
    expect(appSource).not.toContain("私有化视觉模型平台");
  });
});
