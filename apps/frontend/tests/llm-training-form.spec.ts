import { describe, expect, it } from "vitest";

import {
  applyLlmConfigYaml,
  countLlmAdvancedChanges,
  createDefaultLlmTrainingForm,
  llmEffectiveBatchSize,
  resetLlmAdvancedGroup,
  stringifyLlmConfig,
  toLlamaFactoryConfig,
} from "@/features/pipeline-wizard/llm/llmTrainingForm";

describe("LLM training form", () => {
  it("defaults to the registry reachable from the local deployment", () => {
    const form = createDefaultLlmTrainingForm();

    expect(form.modelSource).toBe("modelscope");
    expect(form.requestedRevision).toBe("master");
  });

  it("builds a controlled QLoRA configuration", () => {
    const form = createDefaultLlmTrainingForm("LLM 产线");
    const config = toLlamaFactoryConfig(form);

    expect(config).toMatchObject({
      stage: "sft",
      finetuning_type: "lora",
      quantization_bit: 4,
      quantization_method: "bitsandbytes",
      cutoff_len: 1024,
      per_device_train_batch_size: 1,
      per_device_eval_batch_size: 1,
      gradient_accumulation_steps: 8,
      do_eval: true,
      eval_strategy: "steps",
    });
    expect(config).not.toHaveProperty("model_name_or_path");
    expect(config).not.toHaveProperty("output_dir");
    expect(llmEffectiveBatchSize(form)).toBe(8);
  });

  it("round trips editable parameters through YAML", () => {
    const form = createDefaultLlmTrainingForm();
    const yaml = stringifyLlmConfig({ ...form, epochs: 5, loraRank: 16, method: "lora" });
    const updated = applyLlmConfigYaml(form, yaml);

    expect(updated.epochs).toBe(5);
    expect(updated.loraRank).toBe(16);
    expect(updated.method).toBe("lora");
  });

  it("rejects system-managed fields in YAML", () => {
    const form = createDefaultLlmTrainingForm();
    expect(() => applyLlmConfigYaml(form, "model_name_or_path: C:/models/qwen\nstage: sft\n")).toThrow(
      "model_name_or_path 由 VisiOX 管理",
    );
  });

  it("counts and resets one advanced group without overwriting other settings", () => {
    const form = createDefaultLlmTrainingForm();
    form.loraRank = 32;
    form.loraDropout = 0.1;
    form.seed = 7;

    expect(countLlmAdvancedChanges(form, "lora")).toBe(2);
    expect(countLlmAdvancedChanges(form, "stability")).toBe(1);

    const reset = resetLlmAdvancedGroup(form, "lora");
    expect(reset.loraRank).toBe(8);
    expect(reset.loraDropout).toBe(0);
    expect(reset.seed).toBe(7);
  });
});
