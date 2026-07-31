import { describe, expect, it } from "vitest";

import { hasForbiddenHoverElevation, topLevelRuleDeclarations } from "./css-rules";

function declarations(css: string) {
  return topLevelRuleDeclarations(`.card:hover { ${css} }`, ".card:hover");
}

describe("CSS rule helpers", () => {
  it("ignores commented and nested rules and commented declarations", () => {
    const source = `
      /* .card { background: white; } */
      @media (max-width: 720px) { .card { background: white; } }
      @container panel (max-width: 900px) { .card { background: white; } }
      .card { /* background: white; */ color: black; }
    `;

    const rule = topLevelRuleDeclarations(source, ".card");
    expect(rule?.get("background")).toBeUndefined();
    expect(rule?.get("color")).toEqual(["black"]);
  });

  it("allows absent hover rules, no shadow, none, and small shadows", () => {
    expect(hasForbiddenHoverElevation(undefined)).toBe(false);
    expect(hasForbiddenHoverElevation(declarations("border-color: blue;"))).toBe(false);
    expect(hasForbiddenHoverElevation(declarations("box-shadow: none;"))).toBe(false);
    expect(hasForbiddenHoverElevation(declarations("box-shadow: 0 1px 2px rgb(0 0 0 / 8%);"))).toBe(false);
    expect(hasForbiddenHoverElevation(declarations("box-shadow: 0 2px 6px 1px #0001;"))).toBe(false);
  });

  it("rejects large or indeterminate shadows", () => {
    expect(hasForbiddenHoverElevation(declarations("box-shadow: 0 10px 24px rgb(0 0 0 / 8%);"))).toBe(true);
    expect(hasForbiddenHoverElevation(declarations("box-shadow: var(--card-shadow);"))).toBe(true);
  });

  it.each([
    "transform: translate(0, -1px);",
    "transform: translateY(-1px);",
    "transform: translate3d(0, -1px, 0);",
    "translate: 0 -1px;",
  ])("rejects negative Y lift from %s", (css) => {
    expect(hasForbiddenHoverElevation(declarations(css))).toBe(true);
  });

  it("allows horizontal and non-negative translation", () => {
    expect(hasForbiddenHoverElevation(declarations("transform: translate(-1px, 0);"))).toBe(false);
    expect(hasForbiddenHoverElevation(declarations("transform: translateY(1px);"))).toBe(false);
    expect(hasForbiddenHoverElevation(declarations("translate: -1px 0;"))).toBe(false);
  });
});
