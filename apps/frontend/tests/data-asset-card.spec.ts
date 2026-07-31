import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import DataAssetCard from "@/components/data/DataAssetCard.vue";
import dataAssetCardSource from "@/components/data/DataAssetCard.vue?raw";
import { topLevelRuleDeclarations } from "./helpers/css-rules";

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
    const cardRule = topLevelRuleDeclarations(dataAssetCardSource, ".data-asset-card", "sfc");
    expect(cardRule?.get("background")).toEqual(["var(--visiox-card-surface)"]);
    expect(cardRule?.get("border")).toEqual(["1px solid var(--visiox-card-border)"]);
    expect(cardRule?.get("border-radius")).toEqual(["var(--visiox-card-radius)"]);
  });

  it("does not lift the shared card on hover", () => {
    const hoverRule = topLevelRuleDeclarations(dataAssetCardSource, ".data-asset-card:hover", "sfc");
    expect((hoverRule?.get("transform") ?? []).some((value) => /\btranslate(?:Y)?\s*\(/i.test(value))).toBe(false);
    expect((hoverRule?.get("box-shadow") ?? []).every((value) => value === "none")).toBe(true);
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
