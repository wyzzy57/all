<template>
  <section class="framework-model-selector" aria-label="Framework and model selection">
    <p v-if="(mode === 'selection' || mode === 'framework') && locked" class="lock-reason" role="status">{{ lockReason || "This pipeline is locked after its first training submission." }}</p>
    <div v-if="mode === 'selection' || mode === 'framework'" class="framework-grid">
      <button
        v-for="adapter in frameworks"
        :key="adapter.framework"
        class="framework-option"
        :class="{ selected: selection.framework === adapter.framework }"
        type="button"
        :disabled="locked"
        :data-testid="`framework-${adapter.framework}`"
        @click="selectFramework(adapter.framework)"
      >
        <span class="framework-option__header">
          <span class="framework-option__identity">
            <span class="framework-mark" aria-hidden="true">{{ frameworkInitial(adapter.display_name || adapter.framework) }}</span>
            <span>
              <strong>{{ adapter.display_name || adapter.framework }}</strong>
              <small>{{ frameworkDescription(adapter.framework) }}</small>
            </span>
          </span>
          <span v-if="selection.framework === adapter.framework" class="selected-check" aria-label="已选择">✓</span>
        </span>
        <span class="framework-option__footer">
          <span class="runtime-status" :class="adapter.available ? 'available' : 'pending'">
            <i aria-hidden="true" />
            {{ adapter.available ? "运行环境可用" : "运行环境待配置" }}
          </span>
          <span
            v-if="!adapter.available"
            class="runtime-info"
            :data-testid="`framework-${adapter.framework}-info`"
            :title="runtimeConfigurationHint(adapter.framework)"
            aria-label="查看运行环境配置说明"
          >?</span>
        </span>
      </button>
    </div>

    <div v-if="mode === 'selection' || mode === 'model'" class="model-grid">
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

    <div v-if="mode === 'parameters' && parameters.length" class="parameter-grid">
      <label v-for="parameter in parameters" :key="parameter.name">
        <span>{{ parameter.name }}</span>
        <input
          v-if="parameter.value_type !== 'boolean' && !(parameter.choices?.length)"
          :value="String(parameterValues[parameter.name] ?? parameter.default ?? '')"
          :type="parameter.value_type === 'string' ? 'text' : 'number'"
          :min="parameter.minimum ?? undefined"
          :max="parameter.maximum ?? undefined"
          :disabled="locked"
          :data-testid="`framework-parameter-${parameter.name}`"
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

    <details v-if="mode === 'parameters'" class="advanced-yaml">
      <summary>高级 YAML 配置</summary>
      <textarea
        :value="advancedYaml"
        :disabled="locked"
        spellcheck="false"
        data-testid="advanced-yaml"
        @input="$emit('update:advancedYaml', ($event.target as HTMLTextAreaElement).value)"
      />
    </details>
    <button v-if="(mode === 'selection' || mode === 'framework') && locked" type="button" class="clone-button" @click="$emit('clone')">克隆并更换框架</button>
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
  mode?: "selection" | "framework" | "model" | "parameters";
}>(), { parameterValues: () => ({}), advancedYaml: "", locked: false, lockReason: "", mode: "selection" });

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
  if (!adapter || !model) return;
  emit("update:selection", { taskKind: props.selection.taskKind, framework, adapterKey: adapter.adapter_key, adapterVersion: adapter.adapter_version, modelKey: model.model_key });
}

function frameworkInitial(displayName: string) {
  return displayName.trim().slice(0, 1).toUpperCase();
}

function frameworkDescription(framework: string) {
  if (framework === "paddlex") return "飞桨视觉训练框架";
  if (framework === "ultralytics") return "YOLO 视觉训练框架";
  if (framework === "llamafactory") return "大模型微调框架";
  return "训练框架";
}

function runtimeConfigurationHint(framework: string) {
  if (framework === "ultralytics") return "尚未配置 Ultralytics 训练镜像。配置完成后即可提交训练。";
  if (framework === "paddlex") return "尚未配置 PaddleX 训练镜像。配置完成后即可提交训练。";
  if (framework === "llamafactory") return "尚未配置 LLaMA-Factory 训练镜像。配置完成后即可提交训练。";
  return "该框架的训练运行环境尚未配置。";
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
.framework-model-selector { display: grid; gap: 16px; width: 100%; min-width: 0; }
.framework-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 14px; }
.model-grid, .parameter-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 10px; }
.framework-option { display: grid; min-height: 136px; align-content: space-between; gap: 18px; padding: 18px; border: 1px solid #e1e5eb; border-radius: 8px; background: #fff; color: #111827; text-align: left; cursor: pointer; transition: transform 160ms ease, border-color 160ms ease, background-color 160ms ease, box-shadow 160ms ease; }
.framework-option:hover { border-color: #aeb7c3; background: #fafafa; }
.framework-option:not(:disabled):hover { transform: translateY(-1px); }
.framework-option.selected { border-color: #475467; background: #f4f4f4; box-shadow: inset 0 0 0 1px #475467; }
.framework-option:focus-visible { outline: 2px solid #409eff; outline-offset: 2px; }
.framework-option:disabled { cursor: not-allowed; opacity: .55; }
.framework-option__header, .framework-option__footer, .framework-option__identity { display: flex; align-items: center; }
.framework-option__header, .framework-option__footer { justify-content: space-between; gap: 12px; }
.framework-option__identity { min-width: 0; gap: 12px; }
.framework-option__identity > span:last-child { display: grid; min-width: 0; gap: 4px; }
.framework-option__identity strong { font-size: 16px; line-height: 1.25; }
.framework-option__identity small { color: #667085; font-size: 13px; font-weight: 400; }
.framework-mark { display: grid; flex: 0 0 40px; width: 40px; height: 40px; place-items: center; border-radius: 7px; background: #202123; color: #fff; font-size: 16px; font-weight: 700; }
.selected-check { display: grid; flex: 0 0 24px; width: 24px; height: 24px; place-items: center; border-radius: 50%; background: #202123; color: #fff; font-size: 14px; }
.runtime-status { display: inline-flex; align-items: center; gap: 7px; color: #475467; font-size: 13px; }
.runtime-status i { width: 8px; height: 8px; border-radius: 50%; background: #98a2b3; }
.runtime-status.available { color: #067647; }
.runtime-status.available i { background: #12b76a; }
.runtime-status.pending { color: #b54708; }
.runtime-status.pending i { background: #f79009; }
.runtime-info { display: grid; width: 22px; height: 22px; place-items: center; border: 1px solid #cfd4dc; border-radius: 50%; color: #667085; font-size: 12px; font-weight: 600; }
.model-option { display: grid; min-height: 92px; align-content: start; gap: 6px; padding: 12px; border: 1px solid #d0d5dd; border-radius: 6px; background: #fff; color: #1d2939; text-align: left; cursor: pointer; }
.model-option.selected { border-color: #1763ff; box-shadow: inset 0 0 0 1px #1763ff; }
.model-option:disabled { cursor: not-allowed; opacity: .65; }
.model-option small, .model-option span { color: #667085; }
.parameter-grid label { display: grid; gap: 6px; min-width: 0; font-size: 13px; }
.parameter-grid input, .parameter-grid select, .advanced-yaml textarea { box-sizing: border-box; width: 100%; min-height: 34px; border: 1px solid #d0d5dd; border-radius: 4px; padding: 6px 8px; }
.advanced-yaml textarea { min-height: 132px; margin-top: 8px; font-family: ui-monospace, monospace; resize: vertical; }
.lock-reason { margin: 0; color: #b42318; }
.clone-button { justify-self: start; border: 0; background: transparent; color: #1763ff; cursor: pointer; }
@media not all { .framework-grid, .model-grid, .parameter-grid { grid-template-columns: minmax(0, 1fr); } }
</style>
