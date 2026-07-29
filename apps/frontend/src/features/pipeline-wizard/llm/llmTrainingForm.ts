import { parse, stringify } from "yaml";

export type LlmModelSource = "huggingface" | "modelscope";
export type LlmFineTuningMethod = "lora" | "qlora";
export type LlmConfigMode = "form" | "yaml";
export type LlmAdvancedGroup = "lora" | "stability" | "logging" | "data";

export type LlmTrainingForm = {
  name: string;
  modelSource: LlmModelSource;
  modelId: string;
  requestedRevision: string;
  resolvedRevision: string;
  template: string;
  trustRemoteCode: boolean;
  datasetId: string;
  method: LlmFineTuningMethod;
  learningRate: number;
  epochs: number;
  cutoffLen: number;
  batchSize: number;
  gradientAccumulationSteps: number;
  valSize: number;
  scheduler: string;
  warmupRatio: number;
  precision: "auto" | "bf16" | "fp16";
  loraRank: number;
  loraAlpha: number;
  loraDropout: number;
  loraTarget: string;
  maxGradNorm: number;
  seed: number;
  gradientCheckpointing: boolean;
  flashAttention: "auto" | "disabled";
  ropeScaling: "none" | "linear" | "dynamic";
  loggingSteps: number;
  evalSteps: number;
  saveSteps: number;
  saveTotalLimit: number;
  maxSamples: number | null;
  packing: boolean;
  preprocessingWorkers: number;
  dataloaderWorkers: number;
  poolId: string;
  nodeId: string;
  autoOptimize: boolean;
};

export const managedLlmFields = [
  "model_name_or_path",
  "dataset",
  "dataset_dir",
  "output_dir",
  "logging_dir",
  "report_to",
  "resume_from_checkpoint",
] as const;

export function createDefaultLlmTrainingForm(name = "新建产线"): LlmTrainingForm {
  return {
    name,
    modelSource: "modelscope",
    modelId: "",
    requestedRevision: "master",
    resolvedRevision: "",
    template: "auto",
    trustRemoteCode: false,
    datasetId: "",
    method: "qlora",
    learningRate: 0.0001,
    epochs: 3,
    cutoffLen: 1024,
    batchSize: 1,
    gradientAccumulationSteps: 8,
    valSize: 0.1,
    scheduler: "cosine",
    warmupRatio: 0.1,
    precision: "auto",
    loraRank: 8,
    loraAlpha: 16,
    loraDropout: 0,
    loraTarget: "all",
    maxGradNorm: 1,
    seed: 42,
    gradientCheckpointing: true,
    flashAttention: "auto",
    ropeScaling: "none",
    loggingSteps: 5,
    evalSteps: 100,
    saveSteps: 100,
    saveTotalLimit: 2,
    maxSamples: null,
    packing: false,
    preprocessingWorkers: 4,
    dataloaderWorkers: 0,
    poolId: "",
    nodeId: "",
    autoOptimize: true,
  };
}

export function toLlamaFactoryConfig(form: LlmTrainingForm): Record<string, unknown> {
  return {
    stage: "sft",
    do_train: true,
    finetuning_type: "lora",
    quantization_bit: form.method === "qlora" ? 4 : undefined,
    quantization_method: form.method === "qlora" ? "bitsandbytes" : undefined,
    template: form.template === "auto" ? undefined : form.template,
    trust_remote_code: form.trustRemoteCode || undefined,
    learning_rate: form.learningRate,
    num_train_epochs: form.epochs,
    cutoff_len: form.cutoffLen,
    per_device_train_batch_size: form.batchSize,
    gradient_accumulation_steps: form.gradientAccumulationSteps,
    val_size: form.valSize,
    do_eval: form.valSize > 0,
    eval_strategy: form.valSize > 0 ? "steps" : "no",
    per_device_eval_batch_size: form.batchSize,
    lr_scheduler_type: form.scheduler,
    warmup_ratio: form.warmupRatio,
    bf16: form.precision === "bf16" || undefined,
    fp16: form.precision === "fp16" || undefined,
    lora_rank: form.loraRank,
    lora_alpha: form.loraAlpha,
    lora_dropout: form.loraDropout,
    lora_target: form.loraTarget,
    max_grad_norm: form.maxGradNorm,
    seed: form.seed,
    gradient_checkpointing: form.gradientCheckpointing,
    flash_attn: form.flashAttention === "auto" ? "auto" : "disabled",
    rope_scaling: form.ropeScaling === "none" ? undefined : form.ropeScaling,
    logging_steps: form.loggingSteps,
    eval_steps: form.evalSteps,
    save_steps: form.saveSteps,
    save_total_limit: form.saveTotalLimit,
    max_samples: form.maxSamples ?? undefined,
    packing: form.packing,
    preprocessing_num_workers: form.preprocessingWorkers,
    dataloader_num_workers: form.dataloaderWorkers,
    plot_loss: true,
  };
}

export function stringifyLlmConfig(form: LlmTrainingForm) {
  const config = Object.fromEntries(
    Object.entries(toLlamaFactoryConfig(form)).filter(([, value]) => value !== undefined),
  );
  return stringify(config, { lineWidth: 0, sortMapEntries: false });
}

export function applyLlmConfigYaml(form: LlmTrainingForm, source: string): LlmTrainingForm {
  const parsed = parse(source);
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("配置文件必须是 YAML 对象");
  }
  const config = parsed as Record<string, unknown>;
  for (const field of managedLlmFields) {
    if (Object.prototype.hasOwnProperty.call(config, field)) {
      throw new Error(`${field} 由 VisiOX 管理，不能在配置文件中修改`);
    }
  }
  const numberValue = (key: string, fallback: number) => {
    if (config[key] === undefined) return fallback;
    const value = Number(config[key]);
    if (!Number.isFinite(value)) throw new Error(`${key} 必须是有效数字`);
    return value;
  };
  const stringValue = (key: string, fallback: string) =>
    config[key] === undefined ? fallback : String(config[key]);

  const quantizationBit = config.quantization_bit === undefined ? undefined : Number(config.quantization_bit);
  return {
    ...form,
    method: quantizationBit === 4 ? "qlora" : "lora",
    template: stringValue("template", form.template),
    trustRemoteCode: Boolean(config.trust_remote_code ?? form.trustRemoteCode),
    learningRate: numberValue("learning_rate", form.learningRate),
    epochs: numberValue("num_train_epochs", form.epochs),
    cutoffLen: numberValue("cutoff_len", form.cutoffLen),
    batchSize: numberValue("per_device_train_batch_size", form.batchSize),
    gradientAccumulationSteps: numberValue("gradient_accumulation_steps", form.gradientAccumulationSteps),
    valSize: numberValue("val_size", form.valSize),
    scheduler: stringValue("lr_scheduler_type", form.scheduler),
    warmupRatio: numberValue("warmup_ratio", form.warmupRatio),
    precision: config.bf16 ? "bf16" : config.fp16 ? "fp16" : "auto",
    loraRank: numberValue("lora_rank", form.loraRank),
    loraAlpha: numberValue("lora_alpha", form.loraAlpha),
    loraDropout: numberValue("lora_dropout", form.loraDropout),
    loraTarget: stringValue("lora_target", form.loraTarget),
    maxGradNorm: numberValue("max_grad_norm", form.maxGradNorm),
    seed: numberValue("seed", form.seed),
    gradientCheckpointing: Boolean(config.gradient_checkpointing ?? form.gradientCheckpointing),
    flashAttention: stringValue("flash_attn", form.flashAttention) === "disabled" ? "disabled" : "auto",
    ropeScaling: stringValue("rope_scaling", form.ropeScaling) as LlmTrainingForm["ropeScaling"],
    loggingSteps: numberValue("logging_steps", form.loggingSteps),
    evalSteps: numberValue("eval_steps", form.evalSteps),
    saveSteps: numberValue("save_steps", form.saveSteps),
    saveTotalLimit: numberValue("save_total_limit", form.saveTotalLimit),
    maxSamples: config.max_samples === undefined ? form.maxSamples : numberValue("max_samples", 0),
    packing: Boolean(config.packing ?? form.packing),
    preprocessingWorkers: numberValue("preprocessing_num_workers", form.preprocessingWorkers),
    dataloaderWorkers: numberValue("dataloader_num_workers", form.dataloaderWorkers),
  };
}

export function llmEffectiveBatchSize(form: LlmTrainingForm) {
  return form.batchSize * form.gradientAccumulationSteps;
}

const advancedGroupFields = {
  lora: ["loraRank", "loraAlpha", "loraDropout", "loraTarget"],
  stability: ["maxGradNorm", "seed", "gradientCheckpointing", "flashAttention", "ropeScaling"],
  logging: ["loggingSteps", "evalSteps", "saveSteps", "saveTotalLimit"],
  data: ["maxSamples", "packing", "preprocessingWorkers", "dataloaderWorkers"],
} as const satisfies Record<LlmAdvancedGroup, readonly (keyof LlmTrainingForm)[]>;

export function countLlmAdvancedChanges(form: LlmTrainingForm, group: LlmAdvancedGroup) {
  const recommended = createDefaultLlmTrainingForm(form.name);
  return advancedGroupFields[group].filter((field) => !Object.is(form[field], recommended[field])).length;
}

export function resetLlmAdvancedGroup(form: LlmTrainingForm, group: LlmAdvancedGroup): LlmTrainingForm {
  const recommended = createDefaultLlmTrainingForm(form.name);
  const reset = { ...form };
  for (const field of advancedGroupFields[group]) {
    (reset as Record<keyof LlmTrainingForm, LlmTrainingForm[keyof LlmTrainingForm]>)[field] = recommended[field];
  }
  return reset;
}
