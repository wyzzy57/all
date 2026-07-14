import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("frontend development environment", () => {
  it("configures the TensorBoard external tool", () => {
    const source = readFileSync(resolve(process.cwd(), ".env.development"), "utf8");

    expect(source).toContain("VITE_TENSORBOARD_URL=http://127.0.0.1:6006");
  });
});
