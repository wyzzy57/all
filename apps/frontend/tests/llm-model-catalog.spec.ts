import { describe, expect, it } from "vitest";

import { llmModelRecommendations } from "@/features/pipeline-wizard/llm/llmModelCatalog";

describe("LLM model catalog", () => {
  it("returns the curated Qwen models for each supported source", () => {
    const expectedIds = [
      "Qwen/Qwen3-0.6B",
      "Qwen/Qwen3-1.7B",
      "Qwen/Qwen3-4B",
    ];

    expect(llmModelRecommendations("huggingface").map((item) => item.id)).toEqual(expectedIds);
    expect(llmModelRecommendations("modelscope").map((item) => item.id)).toEqual(expectedIds);
  });

  it("returns new objects so callers cannot mutate the shared catalog", () => {
    const first = llmModelRecommendations("huggingface");
    const second = llmModelRecommendations("huggingface");

    expect(first).not.toBe(second);
    expect(first[0]).not.toBe(second[0]);
  });
});
