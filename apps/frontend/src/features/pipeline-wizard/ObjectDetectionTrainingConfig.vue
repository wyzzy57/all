<template>
  <section class="training-config" data-testid="object-detection-training-config">
    <header class="config-header">
      <div>
        <h3>训练参数</h3>
        <p>{{ model.display_name }} · {{ model.config_format?.toUpperCase() || "YAML" }}</p>
      </div>
      <div class="mode-tabs" role="tablist" aria-label="参数配置模式">
        <button type="button" :class="{ active: mode === 'form' }" data-testid="form-tab" @click="mode = 'form'">表单配置</button>
        <button type="button" :class="{ active: mode === 'yaml' }" data-testid="yaml-tab" @click="mode = 'yaml'">YAML 配置</button>
      </div>
    </header>

    <template v-if="mode === 'form'">
      <section v-if="basicParameters.length" class="parameter-section">
        <div class="section-heading"><strong>基础参数</strong><span>常用训练设置</span></div>
        <div class="parameter-grid">
          <ParameterField
            v-for="parameter in basicParameters"
            :key="parameter.name"
            :parameter="parameter"
            :value="parameterValue(parameter.name)"
            class="basic-parameter"
            @change="setParameter(parameter.name, $event)"
          />
        </div>
      </section>

      <section v-for="group in advancedGroups" :key="group.name" class="advanced-section" :data-group="group.name">
        <details open>
          <summary><span>{{ groupLabel(group.name) }}</span><small>{{ group.parameters.length }} 项</small></summary>
          <div class="parameter-grid">
            <ParameterField
              v-for="parameter in group.parameters"
              :key="parameter.name"
              :parameter="parameter"
              :value="parameterValue(parameter.name)"
              @change="setParameter(parameter.name, $event)"
            />
          </div>
        </details>
      </section>
    </template>

    <section v-else class="yaml-layout">
      <div class="yaml-editor-panel">
        <textarea
          :value="yamlText"
          data-testid="framework-yaml-editor"
          aria-label="训练 YAML 配置"
          spellcheck="false"
          @input="updateYaml(($event.target as HTMLTextAreaElement).value)"
        />
        <div v-if="errors.length" class="yaml-errors" data-testid="yaml-errors" role="alert">
          <strong>配置存在 {{ errors.length }} 个问题</strong>
          <span v-for="error in errors" :key="error">{{ error }}</span>
        </div>
        <p v-else class="yaml-valid">YAML 配置有效，已同步到表单参数。</p>
      </div>
      <aside class="managed-parameters">
        <strong>系统托管参数</strong>
        <p>模型、数据、输出目录和运行设备由 Visiox 在提交时生成。</p>
        <code v-for="name in model.managed_parameter_names || []" :key="name">{{ name }}</code>
      </aside>
    </section>
  </section>
</template>

<script lang="ts">
import { defineComponent, h, resolveComponent, type PropType } from "vue";

import type { FrameworkParameterCapabilityRecord as FieldParameterCapabilityRecord } from "@/api/client";

const ParameterField = defineComponent({
  name: "ParameterField",
  props: {
    parameter: { type: Object as PropType<FieldParameterCapabilityRecord>, required: true },
    value: { type: [String, Number, Boolean] as PropType<string | number | boolean | null>, default: null },
  },
  emits: ["change"],
  setup(props, { emit }) {
    return () => {
      const parameter = props.parameter;
      const common = { modelValue: props.value, "onUpdate:modelValue": (value: unknown) => emit("change", value) };
      let control;
      if (parameter.value_type === "boolean") {
        control = h(resolveComponent("el-switch"), common);
      } else if (parameter.choices?.length) {
        control = h(resolveComponent("el-select"), common, () => parameter.choices?.map((choice) => h(resolveComponent("el-option"), { label: String(choice), value: choice })));
      } else if (parameter.value_type === "integer" || parameter.value_type === "number") {
        control = h(resolveComponent("el-input-number"), { ...common, min: parameter.minimum ?? undefined, max: parameter.maximum ?? undefined });
      } else {
        control = h(resolveComponent("el-input"), common);
      }
      return h("label", { class: "parameter-field", "data-parameter": parameter.name }, [
        h("span", [h("strong", parameter.name), parameter.required ? h("em", "必填") : null]),
        control,
        parameter.help_text || parameter.description ? h("small", parameter.help_text || parameter.description || "") : null,
      ]);
    };
  },
});

export default defineComponent({ name: "ObjectDetectionTrainingConfig", components: { ParameterField } });
</script>

<script setup lang="ts">
import { computed, ref, watch } from "vue";

import type {
  FrameworkModelCapabilityRecord,
  FrameworkParameterCapabilityRecord,
} from "@/api/client";
import {
  buildFrameworkTrainingConfig,
  parseFrameworkTrainingYaml,
  stringifyFrameworkTrainingConfig,
  validateFrameworkTrainingParams,
  type FrameworkTrainingParams,
} from "@/features/pipeline-wizard/frameworkTrainingConfig";

const props = defineProps<{
  model: FrameworkModelCapabilityRecord;
  parameters: FrameworkParameterCapabilityRecord[];
  modelValue: FrameworkTrainingParams;
}>();
const emit = defineEmits<{
  "update:modelValue": [value: FrameworkTrainingParams];
  "update:valid": [value: boolean];
}>();

const mode = ref<"form" | "yaml">("form");
const params = ref<FrameworkTrainingParams>({});
const yamlText = ref("");
const errors = ref<string[]>([]);
let syncing = false;

const basicNames = computed(() => new Set(props.model.basic_parameter_names ?? []));
const basicParameters = computed(() => props.parameters.filter((parameter) => basicNames.value.has(parameter.name)));
const advancedGroups = computed(() => {
  const groups = new Map<string, FrameworkParameterCapabilityRecord[]>();
  for (const parameter of props.parameters) {
    if (basicNames.value.has(parameter.name)) continue;
    const name = parameter.advanced_group || "other";
    groups.set(name, [...(groups.get(name) ?? []), parameter]);
  }
  return [...groups].map(([name, groupParameters]) => ({ name, parameters: groupParameters }));
});

function publish(next: FrameworkTrainingParams, nextErrors: string[]) {
  params.value = next;
  errors.value = nextErrors;
  emit("update:valid", nextErrors.length === 0);
  if (!nextErrors.length) emit("update:modelValue", { ...next });
}

function resetForModel() {
  const next = buildFrameworkTrainingConfig(props.model, props.parameters, props.modelValue);
  yamlText.value = stringifyFrameworkTrainingConfig(next);
  publish(next, validateFrameworkTrainingParams(next, props.model, props.parameters));
}

function setParameter(name: string, value: unknown) {
  const next = { ...params.value, [name]: value };
  const nextErrors = validateFrameworkTrainingParams(next, props.model, props.parameters);
  yamlText.value = stringifyFrameworkTrainingConfig(next);
  publish(next, nextErrors);
}

function parameterValue(name: string): string | number | boolean | null {
  const value = params.value[name];
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean" ? value : null;
}

function updateYaml(source: string) {
  yamlText.value = source;
  const result = parseFrameworkTrainingYaml(source, props.model, props.parameters);
  publish(result.params, result.errors);
}

function groupLabel(name: string) {
  return ({ training: "训练策略", resources: "资源与性能", optimizer: "优化器", data: "数据处理", checkpointing: "检查点", augmentation: "数据增强", other: "其他参数" } as Record<string, string>)[name] || name;
}

watch(() => props.model.model_key, resetForModel, { immediate: true });
watch(
  () => props.modelValue,
  (value) => {
    if (syncing) return;
    const next = buildFrameworkTrainingConfig(props.model, props.parameters, value);
    if (JSON.stringify(next) === JSON.stringify(params.value)) return;
    syncing = true;
    yamlText.value = stringifyFrameworkTrainingConfig(next);
    publish(next, validateFrameworkTrainingParams(next, props.model, props.parameters));
    syncing = false;
  },
  { deep: true },
);
</script>

<style scoped>
.training-config { border: 1px solid #dfe3e8; border-radius: 6px; background: #fff; }
.config-header { display: flex; align-items: center; justify-content: space-between; padding: 18px 20px; border-bottom: 1px solid #e5e7eb; }
.config-header h3, .config-header p { margin: 0; }
.config-header p { margin-top: 4px; color: #6b7280; font-size: 13px; }
.mode-tabs { display: flex; padding: 3px; border-radius: 6px; background: #f3f4f6; }
.mode-tabs button { min-width: 96px; padding: 8px 14px; border: 0; border-radius: 4px; background: transparent; cursor: pointer; }
.mode-tabs button.active { background: #fff; color: #1677ff; box-shadow: 0 1px 3px rgb(0 0 0 / 10%); }
.parameter-section, .advanced-section { padding: 18px 20px; border-bottom: 1px solid #eef0f2; }
.section-heading { display: flex; gap: 12px; align-items: baseline; margin-bottom: 14px; }
.section-heading span, summary small { color: #8a94a6; font-size: 12px; }
.parameter-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 16px; }
.parameter-field { display: grid; gap: 7px; min-width: 0; }
.parameter-field > span { display: flex; gap: 8px; align-items: center; }
.parameter-field em { color: #ef4444; font-size: 11px; font-style: normal; }
.parameter-field small { color: #8a94a6; line-height: 1.45; }
details summary { display: flex; justify-content: space-between; cursor: pointer; font-weight: 600; margin-bottom: 14px; }
.yaml-layout { display: grid; grid-template-columns: minmax(0, 1fr) 230px; gap: 18px; padding: 20px; }
.yaml-editor-panel textarea { width: 100%; min-height: 360px; resize: vertical; box-sizing: border-box; border: 1px solid #cfd6df; border-radius: 4px; padding: 14px; font: 13px/1.65 Consolas, monospace; }
.yaml-errors { display: grid; gap: 5px; margin-top: 10px; padding: 12px; border: 1px solid #fecaca; background: #fff7f7; color: #b42318; }
.yaml-valid { color: #16803c; }
.managed-parameters { align-self: start; padding: 15px; background: #f7f8fa; border-radius: 4px; }
.managed-parameters p { color: #6b7280; font-size: 12px; line-height: 1.5; }
.managed-parameters code { display: block; margin-top: 6px; color: #4b5563; }
@media (max-width: 900px) { .parameter-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); } .yaml-layout { grid-template-columns: 1fr; } }
</style>
