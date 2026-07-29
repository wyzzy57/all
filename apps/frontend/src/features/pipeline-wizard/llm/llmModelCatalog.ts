import type { LlmModelSource } from "./llmTrainingForm";

export type LlmModelRecommendation = {
  id: string;
  name: string;
  parameterScale: string;
  suitability: string;
  sources: readonly LlmModelSource[];
};

const supportedSources = ["huggingface", "modelscope"] as const;

const catalog: readonly LlmModelRecommendation[] = [
  {
    id: "Qwen/Qwen3-0.6B",
    name: "Qwen3 0.6B",
    parameterScale: "0.6B",
    suitability: "轻量验证，单卡训练压力较低",
    sources: supportedSources,
  },
  {
    id: "Qwen/Qwen3-1.7B",
    name: "Qwen3 1.7B",
    parameterScale: "1.7B",
    suitability: "效果与资源占用较均衡",
    sources: supportedSources,
  },
  {
    id: "Qwen/Qwen3-4B",
    name: "Qwen3 4B",
    parameterScale: "4B",
    suitability: "12 GB 显存建议使用 QLoRA",
    sources: supportedSources,
  },
];

export function llmModelRecommendations(source: LlmModelSource): LlmModelRecommendation[] {
  return catalog
    .filter((model) => model.sources.includes(source))
    .map((model) => ({ ...model, sources: [...model.sources] }));
}
