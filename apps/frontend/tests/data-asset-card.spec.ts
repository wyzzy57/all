import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import DataAssetCard from "@/components/data/DataAssetCard.vue";
import dataAssetCardSource from "@/components/data/DataAssetCard.vue?raw";

function topLevelRuleBody(source: string, selector: string, declaration: string) {
  const styleTagStart = source.indexOf("<style");
  const cssStart = styleTagStart === -1 ? 0 : source.indexOf(">", styleTagStart) + 1;
  const closingStyle = source.indexOf("</style>", cssStart);
  const cssEnd = closingStyle === -1 ? source.length : closingStyle;
  const cssSource = source.slice(cssStart, cssEnd);
  const escapedSelector = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const rules = [...cssSource.matchAll(new RegExp(`^\\s*${escapedSelector}\\s*\\{([^{}]*)\\}`, "gm"))]
    .filter((match) => {
      const prefix = cssSource.slice(0, match.index);
      return [...prefix].reduce(
        (depth, character) => depth + (character === "{" ? 1 : character === "}" ? -1 : 0),
        0,
      ) === 0;
    })
    .filter((match) => new RegExp(`(?:^|;)\\s*${declaration}\\s*:`).test(match[1]));
  expect(rules, `expected one top-level ${selector} rule`).toHaveLength(1);
  return rules[0]![1];
}

const asset = {
  id: "dataset-1",
  name: "llm-sft-demo",
  task: "llm",
  status: "validated",
  source: "llm_upload",
  sample_count: 8,
  annotation_count: 6,
  created_at: "2026-07-27T08:00:00Z",
  visibility: "private",
  asset_role: "working",
};


describe("DataAssetCard", () => {
  it("uses the standard shared card surface", () => {
    for (const nestedCardRule of [
      `<style>@media (max-width: 720px) {
      .data-asset-card { background: var(--visiox-card-surface); }
      }</style>`,
      `<style>@container data-card (max-width: 900px) {
      .data-asset-card { background: var(--visiox-card-surface); }
      }</style>`,
    ]) {
      expect(() => topLevelRuleBody(nestedCardRule, ".data-asset-card", "background"))
        .toThrow(/expected one top-level \.data-asset-card rule/);
    }

    const cardRule = topLevelRuleBody(dataAssetCardSource, ".data-asset-card", "background");
    expect(cardRule).toMatch(/background:\s*var\(--visiox-card-surface\)\s*;/);
    expect(cardRule).toMatch(/border:\s*1px solid var\(--visiox-card-border\)\s*;/);
    expect(cardRule).toMatch(/border-radius:\s*var\(--visiox-card-radius\)\s*;/);
  });

  it("does not lift the shared card on hover", () => {
    const hoverRule = topLevelRuleBody(dataAssetCardSource, ".data-asset-card:hover", "box-shadow");
    expect([...hoverRule.matchAll(/box-shadow\s*:\s*([^;{}]+)/g)].map((match) => match[1].trim())).toEqual(["none"]);
    expect([...hoverRule.matchAll(/transform\s*:\s*([^;{}]+)/g)].map((match) => match[1].trim())).toEqual(["none"]);
    expect(hoverRule).not.toMatch(/translateY\s*\(/);
  });

  it("renders preparation metadata in stable rows and emits workflow actions", async () => {
    const wrapper = mount(DataAssetCard, {
      props: {
        asset,
        mode: "prepare",
        statusText: "已校验",
        sourceText: "大模型导入",
        taskText: "大模型训练",
        formattedTime: "2026/7/27 16:00:00",
        labelStudioAvailable: true,
        menuOpen: true,
      },
    });

    expect(wrapper.get(".data-asset-card__title").text()).toContain("llm-sft-demo");
    expect(wrapper.get(".data-asset-card__chips").text()).toContain("大模型训练");
    expect(wrapper.get(".data-asset-card__metadata").text()).toContain("Label Studio");
    expect(wrapper.get(".data-asset-card__counts").text()).toContain("样本 8");
    expect(wrapper.get(".data-asset-card__counts").text()).toContain("标注 6");

    await wrapper.get("[data-testid='convert-dataset-dataset-1']").trigger("click");
    await wrapper.get("[data-testid='label-studio-dataset-1']").trigger("click");
    expect(wrapper.emitted("convert")).toHaveLength(1);
    expect(wrapper.emitted("label-studio")).toHaveLength(1);
  });

  it("renders published actions without changing the shared card shell", async () => {
    const wrapper = mount(DataAssetCard, {
      props: {
        asset: { ...asset, asset_role: "published" },
        mode: "datasets",
        statusText: "已发布",
        sourceText: "Label Studio 导入",
        taskText: "大模型训练",
        formattedTime: "2026/7/27 16:00:00",
        canValidate: true,
        menuOpen: true,
      },
    });

    expect(wrapper.classes()).toContain("dataset-card");
    await wrapper.get("[data-testid='validate-dataset-dataset-1']").trigger("click");
    await wrapper.get("[data-testid='process-dataset-dataset-1']").trigger("click");
    expect(wrapper.emitted("validate")).toHaveLength(1);
    expect(wrapper.emitted("process")).toHaveLength(1);
  });
});
