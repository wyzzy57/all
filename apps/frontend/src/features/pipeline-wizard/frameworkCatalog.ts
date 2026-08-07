import type {
  FrameworkCapabilityCatalogResponse,
  FrameworkCapabilityRecord,
  FrameworkModelCapabilityRecord,
  FrameworkTaskCapabilityRecord,
} from "@/api/client";

export type FrameworkModelSelection = {
  taskKind: string;
  framework: string;
  adapterKey: string;
  adapterVersion: string;
  modelKey: string;
};

export function taskKindForPipelineTask(task: string): string {
  if (task === "detect") return "object_detection";
  if (task === "llm") return "llm_sft";
  return task;
}

export function pipelineTaskForTaskKind(taskKind: string): string {
  if (taskKind === "object_detection") return "detect";
  if (taskKind === "llm_sft") return "llm";
  return taskKind;
}

export function taskForFramework(
  adapter: FrameworkCapabilityRecord | null | undefined,
  taskKind: string,
): FrameworkTaskCapabilityRecord | undefined {
  return adapter?.tasks?.find((task) => task.task_type === taskKind);
}

export function compatibleFrameworks(
  catalog: FrameworkCapabilityCatalogResponse | null,
  taskKind: string,
): FrameworkCapabilityRecord[] {
  if (!catalog) return [];
  return (catalog.adapters ?? []).filter((adapter) => taskForFramework(adapter, taskKind));
}

export function legacyEngineForFramework(framework: string): "yolo26" | "paddlex" | "llamafactory" {
  if (framework === "paddlex") return "paddlex";
  if (framework === "llamafactory") return "llamafactory";
  return "yolo26";
}

export function compatibleModels(
  catalog: FrameworkCapabilityCatalogResponse | null,
  taskKind: string,
  framework: string,
): FrameworkModelCapabilityRecord[] {
  const adapter = compatibleFrameworks(catalog, taskKind).find((item) => item.framework === framework);
  return adapter ? taskForFramework(adapter, taskKind)?.models ?? [] : [];
}

export function defaultFrameworkModelSelection(
  catalog: FrameworkCapabilityCatalogResponse | null,
  taskKind: string,
): FrameworkModelSelection | null {
  const adapter = compatibleFrameworks(catalog, taskKind).find((item) => item.available);
  const model = adapter && taskForFramework(adapter, taskKind)?.models[0];
  if (!adapter || !model) return null;
  return {
    taskKind,
    framework: adapter.framework,
    adapterKey: adapter.adapter_key,
    adapterVersion: adapter.adapter_version,
    modelKey: model.model_key,
  };
}
