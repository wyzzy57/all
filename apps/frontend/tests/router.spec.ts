import { describe, expect, it } from "vitest";

import router from "@/router";

describe("router", () => {
  it("exposes the MVP console sections", () => {
    const paths = router.getRoutes().map((route) => route.path);

    expect(paths).toEqual(
      expect.arrayContaining([
        "/workbench",
        "/model-space",
        "/data-preparation",
        "/services",
        "/tasks",
      ]),
    );
    expect(paths).not.toEqual(expect.arrayContaining(["/pipelines", "/devices", "/edge-apps", "/deployments"]));
  });
});
