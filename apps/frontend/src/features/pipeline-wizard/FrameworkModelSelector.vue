<template>
  <section class="framework-model-selector" aria-label="Framework and model selection">
    <p v-if="locked" class="lock-reason" role="status">{{ lockReason || "This pipeline is locked after its first training submission." }}</p>
    <div class="framework-grid">
      <button
        v-for="adapter in frameworks"
        :key="adapter.framework"
        class="framework-option"
        :class="{ selected: selection.framework === adapter.framework }"
        type="button"
        :disabled="locked || !adapter.available"
        :data-testid="`framework-${adapter.framework}`"
        @click="selectFramework(adapter.framework)"
      >
        <strong>{{ adapter.display_name || adapter.framework }}</strong>
        <small>{{ adapter.available ? "Runtime available" : adapter.unavailable_reason || "Runtime unavailable" }}</small>
      </button>
    </div>

    <div class="model-grid">
      <button
        v-for="model in models"
        :key="model.model_key"
        class="model-option"
        :class="{ selected: selection.modelKey === model.model_key }"
        type="button"
        :disabled="locked"
        :data-testid="`model-${model.model_key}`"
        @click="selectModel(model.model_key)"
      >
        <strong>{{ model.display_name }}</strong>
        <span>{{ selection.framework }}</span>
        <small>{{ datasetFormats }}</small>
        <small>{{ resourceRequirements }}</small>
      </button>
    </div>

    <div v-if="parameters.length" class="parameter-grid">
      <label v-for="parameter in parameters" :key="parameter.name">
        <span>{{ parameter.name }}</span>
        <input
          v-if="parameter.value_type !== 'boolean' && !(parameter.choices?.length)"
          :value="String(parameterValues[parameter.name] ?? parameter.default ?? '')"
          :type="parameter.value_type === 'string' ? 'text' : 'number'"
          :min="parameter.minimum ?? undefined"
          :max="parameter.maximum ?? undefined"
          :disabled="locked"
          @input="updateParameter(parameter.name, ($event.target as HTMLInputElement).value, parameter.value_type)"
        />
        <select
          v-else-if="parameter.choices?.length"
          :value="String(parameterValues[parameter.name] ?? parameter.default ?? '')"
          :disabled="locked"
          @change="updateParameter(parameter.name, ($event.target as HTMLSelectElement).value, parameter.value_type)"
        >
          <option v-for="choice in parameter.choices" :key="String(choice)" :value="String(choice)">{{ choice }}</option>
        </select>
        <input
          v-else
          :checked="Boolean(parameterValues[parameter.name] ?? parameter.default)"
          :disabled="locked"
          type="checkbox"
          @change="updateParameter(parameter.name, ($event.target as HTMLInputElement).checked, 'boolean')"
        />
      </label>
    </div>

    <details class="advanced-yaml">
      <summary>高级 YAML 配置</summary>
      <textarea
        :value="advancedYaml"
        :disabled="locked"
        spellcheck="false"
        data-testid="advanced-yaml"
        @input="$emit('update:advancedYaml', ($event.target as HTMLTextAreaElement).value)"
      />
    </details>
    <button v-if="locked" type="button" class="clone-button" @click="$emit('clone')">克隆并更换框架</button>
  </section>
</template>

<script setup lang="ts">
import { computed } from "vue";

import type { FrameworkCapabilityCatalogResponse, FrameworkParameterCapabilityRecord } from "@/api/client";
import { compatibleFrameworks, compatibleModels, taskForFramework, type FrameworkModelSelection } from "./frameworkCatalog";

const props = withDefaults(defineProps<{
  catalog: FrameworkCapabilityCatalogResponse | null;
  selection: FrameworkModelSelection;
  parameterValues?: Record<string, unknown>;
  advancedYaml?: string;
  locked?: boolean;
  lockReason?: string;
}>(), { parameterValues: () => ({}), advancedYaml: "", locked: false, lockReason: "" });

const emit = defineEmits<{
  "update:selection": [value: FrameworkModelSelection];
  "update:parameterValues": [value: Record<string, unknown>];
  "update:advancedYaml": [value: string];
  clone: [];
}>();

const frameworks = computed(() => compatibleFrameworks(props.catalog, props.selection.taskKind));
const models = computed(() => compatibleModels(props.catalog, props.selection.taskKind, props.selection.framework));
const selectedTask = computed(() => frameworks.value.find((adapter) => adapter.framework === props.selection.framework));
const parameters = computed(() => taskForFramework(selectedTask.value, props.selection.taskKind)?.parameters ?? []);
const datasetFormats = computed(() => taskForFramework(selectedTask.value, props.selection.taskKind)?.accepted_dataset_formats.join(", ") || "未声明数据格式");
const resourceRequirements = computed(() => {
  const resource = taskForFramework(selectedTask.value, props.selection.taskKind)?.resources;
  return resource ? `${resource.cpu_cores_min} CPU · ${resource.memory_mb_min} MiB 内存 · ${resource.gpu_count_min} GPU · ${resource.gpu_memory_mb_min} MiB 显存` : "未声明资源要求";
});

function selectFramework(framework: string) {
  const adapter = frameworks.value.find((item) => item.framework === framework);
  const model = adapter && taskForFramework(adapter, props.selection.taskKind)?.models[0];
  if (!adapter?.available || !model) return;
  emit("update:selection", { taskKind: props.selection.taskKind, framework, adapterKey: adapter.adapter_key, adapterVersion: adapter.adapter_version, modelKey: model.model_key });
}

function selectModel(modelKey: string) {
  emit("update:selection", { ...props.selection, modelKey });
}

function updateParameter(name: string, raw: string | boolean, valueType: FrameworkParameterCapabilityRecord["value_type"]) {
  const parameter = parameters.value.find((item) => item.name === name);
  if (raw === "" && (valueType !== "string" || parameter?.required)) return;
  const value = valueType === "boolean" ? raw : valueType === "integer" || valueType === "number" ? Number(raw) : raw;
  if (typeof value === "number" && !Number.isFinite(value)) return;
  if (valueType === "integer" && typeof value === "number" && !Number.isInteger(value)) return;
  if (typeof value === "number" && parameter?.minimum != null && value < parameter.minimum) return;
  if (typeof value === "number" && parameter?.maximum != null && value > parameter.maximum) return;
  emit("update:parameterValues", { ...props.parameterValues, [name]: value });
}
</script>

<style scoped>
.framework-model-selector { display: grid; gap: 16px; min-width: 0; }
.framework-grid, .model-grid, .parameter-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; }
.framework-option, .model-option { display: grid; min-height: 92px; align-content: start; gap: 6px; padding: 12px; border: 1px solid #d0d5dd; border-radius: 6px; background: #fff; color: #1d2939; text-align: left; cursor: pointer; }
.framework-option.selected, .model-option.selected { border-color: #1763ff; box-shadow: inset 0 0 0 1px #1763ff; }
.framework-option:disabled, .model-option:disabled { cursor: not-allowed; opacity: .65; }
.framework-option small, .model-option small, .model-option span { color: #667085; }
.parameter-grid label { display: grid; gap: 6px; min-width: 0; font-size: 13px; }
.parameter-grid input, .parameter-grid select, .advanced-yaml textarea { box-sizing: border-box; width: 100%; min-height: 34px; border: 1px solid #d0d5dd; border-radius: 4px; padding: 6px 8px; }
.advanced-yaml textarea { min-height: 132px; margin-top: 8px; font-family: ui-monospace, monospace; resize: vertical; }
.lock-reason { margin: 0; color: #b42318; }
.clone-button { justify-self: start; border: 0; background: transparent; color: #1763ff; cursor: pointer; }
@media (max-width: 560px) { .framework-grid, .model-grid, .parameter-grid { grid-template-columns: minmax(0, 1fr); } }
</style>
