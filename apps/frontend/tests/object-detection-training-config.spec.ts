import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import ObjectDetectionTrainingConfig from "@/features/pipeline-wizard/ObjectDetectionTrainingConfig.vue";
import {
  buildFrameworkTrainingConfig,
  parseFrameworkTrainingYaml,
} from "@/features/pipeline-wizard/frameworkTrainingConfig";
import type {
  FrameworkModelCapabilityRecord,
  FrameworkParameterCapabilityRecord,
} from "@/api/client";

const parameters: FrameworkParameterCapabilityRecord[] = [
  { name: "epochs", value_type: "integer", required: false, default: 100, minimum: 1, maximum: 1000, advanced_group: "training" },
  { name: "batch", value_type: "integer", required: false, default: 16, minimum: -1, advanced_group: "resources" },
  { name: "optimizer", value_type: "string", required: false, default: "auto", choices: ["auto", "SGD", "AdamW"], advanced_group: "optimizer" },
  { name: "amp", value_type: "boolean", required: false, default: true, advanced_group: "resources" },
];

const ultralyticsModel: FrameworkModelCapabilityRecord = {
  model_key: "yolo26n",
  display_name: "YOLO26-N",
  config_format: "yaml",
  config_template: "epochs: 100\nbatch: 16\noptimizer: auto\namp: true\n",
  basic_parameter_names: ["epochs", "batch"],
  managed_parameter_names: ["model", "data", "project", "name", "device"],
};

const paddlexModel: FrameworkModelCapabilityRecord = {
  model_key: "pp-yoloe-s",
  display_name: "PP-YOLOE-S",
  config_format: "yaml",
  config_template: "epochs: 50\nbatch_size: 8\nlearning_rate: 0.001\namp: true\n",
  basic_parameter_names: ["epochs", "batch_size", "learning_rate"],
  managed_parameter_names: ["model", "dataset_dir", "output_dir", "device"],
};

describe("frameworkTrainingConfig", () => {
  it("builds the selected model template without leaking previous framework fields", () => {
    expect(buildFrameworkTrainingConfig(ultralyticsModel, parameters, { epochs: 12, batch_size: 99 })).toEqual({
      epochs: 12,
      batch: 16,
      optimizer: "auto",
      amp: true,
    });

    expect(buildFrameworkTrainingConfig(paddlexModel, [], { batch: 32 })).toEqual({
      epochs: 50,
      batch_size: 8,
      learning_rate: 0.001,
      amp: true,
    });
  });

  it("validates syntax, unknown, managed, type, range and choice errors", () => {
    expect(parseFrameworkTrainingYaml("epochs: [", ultralyticsModel, parameters).errors[0]).toContain("YAML");
    expect(parseFrameworkTrainingYaml("epochs: 10\nmystery: 1", ultralyticsModel, parameters).errors).toContain("未知参数：mystery");
    expect(parseFrameworkTrainingYaml("epochs: 10\ndevice: 0", ultralyticsModel, parameters).errors).toContain("系统托管参数不可修改：device");
    expect(parseFrameworkTrainingYaml("epochs: fast", ultralyticsModel, parameters).errors).toContain("epochs 必须是整数");
    expect(parseFrameworkTrainingYaml("epochs: 0", ultralyticsModel, parameters).errors).toContain("epochs 不能小于 1");
    expect(parseFrameworkTrainingYaml("epochs: 10\noptimizer: Lion", ultralyticsModel, parameters).errors).toContain("optimizer 必须是以下选项之一：auto、SGD、AdamW");
  });
});

describe("ObjectDetectionTrainingConfig", () => {
  it("renders basic and advanced controls by metadata and emits synchronized params", async () => {
    const wrapper = mount(ObjectDetectionTrainingConfig, {
      props: { model: ultralyticsModel, parameters, modelValue: {} },
      global: {
        stubs: {
          "el-input-number": { props: ["modelValue"], emits: ["update:modelValue"], template: '<button class="number-control" @click="$emit(\'update:modelValue\', 25)">{{ modelValue }}</button>' },
          "el-select": { template: '<div class="select-control"><slot /></div>' },
          "el-option": true,
          "el-switch": true,
        },
      },
    });

    expect(wrapper.text()).toContain("表单配置");
    expect(wrapper.text()).toContain("YAML 配置");
    expect(wrapper.get('[data-parameter="epochs"]').classes()).toContain("basic-parameter");
    expect(wrapper.get('[data-group="optimizer"]').text()).toContain("optimizer");

    await wrapper.get('[data-parameter="epochs"] .number-control').trigger("click");
    const modelEvents = wrapper.emitted("update:modelValue") ?? [];
    const validEvents = wrapper.emitted("update:valid") ?? [];
    expect(modelEvents[modelEvents.length - 1]?.[0]).toMatchObject({ epochs: 25 });
    expect(validEvents[validEvents.length - 1]?.[0]).toBe(true);
  });

  it("switches to YAML, reports inline errors, and resets when the model changes", async () => {
    const wrapper = mount(ObjectDetectionTrainingConfig, {
      props: { model: ultralyticsModel, parameters, modelValue: { epochs: 20 } },
      global: { stubs: { "el-input-number": true, "el-select": true, "el-option": true, "el-switch": true } },
    });

    await wrapper.get('[data-testid="yaml-tab"]').trigger("click");
    const editor = wrapper.get<HTMLTextAreaElement>('[data-testid="framework-yaml-editor"]');
    expect(editor.element.value).toContain("epochs: 20");
    await editor.setValue("epochs: 0\ndevice: 0");
    expect(wrapper.get('[data-testid="yaml-errors"]').text()).toContain("epochs 不能小于 1");
    expect(wrapper.get('[data-testid="yaml-errors"]').text()).toContain("系统托管参数不可修改：device");
    const validEvents = wrapper.emitted("update:valid") ?? [];
    expect(validEvents[validEvents.length - 1]?.[0]).toBe(false);

    await wrapper.setProps({ model: paddlexModel, parameters: [], modelValue: {} });
    expect(wrapper.get<HTMLTextAreaElement>('[data-testid="framework-yaml-editor"]').element.value).toContain("batch_size: 8");
    expect(wrapper.get<HTMLTextAreaElement>('[data-testid="framework-yaml-editor"]').element.value).not.toContain("batch: 16");
  });
});
