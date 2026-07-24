import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("frontend development environment", () => {
  it("configures the TensorBoard external tool", () => {
    const source = readFileSync(resolve(process.cwd(), ".env.development"), "utf8");

    expect(source).toContain("VITE_TENSORBOARD_URL=http://127.0.0.1:6006");
    expect(source).toContain(
      "VITE_REMOTE_TRAINING_IMAGE_DIGEST=10.10.40.2:5000/visiox/yolo26-training@sha256:fdd6b2d8075de434cd2cd6e49ad472cd2bef869ce46840037f11d46de75e5a2c",
    );
  });

  it("proxies edge resource inventory APIs in local development", () => {
    const source = readFileSync(resolve(process.cwd(), "vite.config.ts"), "utf8");

    expect(source).toContain('"/nodes": apiProxy');
    expect(source).toContain('"/resource-pools": apiProxy');
  });
});
