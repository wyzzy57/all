import { describe, expect, it } from "vitest";

import router from "@/router";

describe("router", () => {
  it("exposes the MVP console sections", () => {
    const paths = router.getRoutes().map((route) => route.path);

    expect(paths).toEqual(
      expect.arrayContaining([
        "/model-space",
        "/data-preparation",
        "/pipelines",
        "/tasks",
        "/devices",
        "/edge-apps",
        "/deployments",
      ]),
    );
  });
});
