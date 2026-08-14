import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import FrameworkModelSelector from "@/features/pipeline-wizard/FrameworkModelSelector.vue";
import type { FrameworkCapabilityCatalogResponse } from "@/api/client";

const catalog: FrameworkCapabilityCatalogResponse = {
  task_kind: "object_detection",
  adapters: [
    {
      framework: "ultralytics", adapter_key: "ultralytics", adapter_version: "1.0.0", display_name: "Ultralytics", available: true,
      tasks: [{ task_type: "object_detection", models: [{ model_key: "yolo26-n", display_name: "YOLO26-N" }], accepted_dataset_formats: ["yolo"], convertible_dataset_formats: [], resources: { resource_kinds: ["cpu", "cuda"], cpu_cores_min: 2, memory_mb_min: 4096, gpu_count_min: 1, gpu_memory_mb_min: 4096 }, parameters: [] }],
    },
    {
      framework: "paddlex", adapter_key: "paddlex", adapter_version: "1.0.0", display_name: "PaddleX", available: true,
      tasks: [{ task_type: "object_detection", models: [{ model_key: "pp-yoloe-s", display_name: "PP-YOLOE-S" }, { model_key: "rt-detr-l", display_name: "RT-DETR-L" }], accepted_dataset_formats: ["coco"], convertible_dataset_formats: ["yolo"], resources: { resource_kinds: ["cuda"], cpu_cores_min: 4, memory_mb_min: 8192, gpu_count_min: 1, gpu_memory_mb_min: 8192 }, parameters: [{ name: "epochs", value_type: "integer", required: false, default: 100 }] }],
    },
    {
      framework: "llamafactory", adapter_key: "llamafactory", adapter_version: "1.0.0", display_name: "LLaMA-Factory", available: true,
      tasks: [{ task_type: "llm_sft", models: [{ model_key: "qwen3", display_name: "Qwen3" }], accepted_dataset_formats: ["sharegpt"], convertible_dataset_formats: [], resources: { resource_kinds: ["cuda"], cpu_cores_min: 4, memory_mb_min: 16384, gpu_count_min: 1, gpu_memory_mb_min: 12288 }, parameters: [] }],
    },
  ],
};

describe("FrameworkModelSelector", () => {
  it("separates framework selection from parameter preparation", () => {
    const selection = { taskKind: "object_detection" as const, framework: "paddlex", adapterKey: "paddlex", adapterVersion: "1.0.0", modelKey: "pp-yoloe-s" };
    const selectionView = mount(FrameworkModelSelector, {
      props: { catalog, selection, mode: "selection" },
    });
    const parameterView = mount(FrameworkModelSelector, {
      props: { catalog, selection, mode: "parameters" },
    });

    expect(selectionView.find('[data-testid="framework-paddlex"]').exists()).toBe(true);
    expect(selectionView.find('[data-testid="framework-parameter-epochs"]').exists()).toBe(false);
    expect(selectionView.find('[data-testid="advanced-yaml"]').exists()).toBe(false);
    expect(parameterView.find('[data-testid="framework-paddlex"]').exists()).toBe(false);
    expect(parameterView.find('[data-testid="framework-parameter-epochs"]').exists()).toBe(true);
    expect(parameterView.find('[data-testid="advanced-yaml"]').exists()).toBe(true);
  });

  it("can render framework-only and model-only stages", () => {
    const selection = { taskKind: "object_detection" as const, framework: "paddlex", adapterKey: "paddlex", adapterVersion: "1.0.0", modelKey: "pp-yoloe-s" };
    const frameworkView = mount(FrameworkModelSelector, {
      props: { catalog, selection, mode: "framework" },
    });
    const modelView = mount(FrameworkModelSelector, {
      props: { catalog, selection, mode: "model" },
    });

    expect(frameworkView.find('[data-testid="framework-paddlex"]').exists()).toBe(true);
    expect(frameworkView.find('[data-testid="model-pp-yoloe-s"]').exists()).toBe(false);
    expect(modelView.find('[data-testid="framework-paddlex"]').exists()).toBe(false);
    expect(modelView.find('[data-testid="model-pp-yoloe-s"]').exists()).toBe(true);
  });

  it("presents runtime readiness as compact status instead of exposing backend diagnostics", () => {
    const unavailableCatalog: FrameworkCapabilityCatalogResponse = {
      ...catalog,
      adapters: catalog.adapters.map((adapter) => adapter.framework === "ultralytics"
        ? {
            ...adapter,
            available: false,
            unavailable_reason: "No runtime implementation is registered for this adapter operation; runtime unavailable: Configure VISIOX_ULTRALYTICS_TRAINING_IMAGE_DIGEST with an immutable image digest",
          }
        : adapter),
    };
    const wrapper = mount(FrameworkModelSelector, {
      props: {
        catalog: unavailableCatalog,
        selection: { taskKind: "object_detection", framework: "paddlex", adapterKey: "paddlex", adapterVersion: "1.0.0", modelKey: "pp-yoloe-s" },
        mode: "framework",
      },
    });

    expect(wrapper.get('[data-testid="framework-paddlex"]').text()).toContain("运行环境可用");
    expect(wrapper.get('[data-testid="framework-ultralytics"]').text()).toContain("运行环境待配置");
    expect(wrapper.text()).not.toContain("No runtime implementation");
    expect(wrapper.get('[data-testid="framework-ultralytics-info"]').attributes("title")).toContain("训练镜像");
  });

  it("shows only compatible object detection frameworks and PaddleX models", async () => {
    const wrapper = mount(FrameworkModelSelector, {
      props: {
        catalog,
        selection: { taskKind: "object_detection", framework: "ultralytics", adapterKey: "ultralytics", adapterVersion: "1.0.0", modelKey: "yolo26-n" },
      },
    });

    expect(wrapper.text()).toContain("Ultralytics");
    expect(wrapper.text()).toContain("PaddleX");
    expect(wrapper.text()).not.toContain("LLaMA-Factory");

    await wrapper.get('[data-testid="framework-paddlex"]').trigger("click");
    expect(wrapper.emitted("update:selection")?.[0]).toEqual([{ taskKind: "object_detection", framework: "paddlex", adapterKey: "paddlex", adapterVersion: "1.0.0", modelKey: "pp-yoloe-s" }]);
    await wrapper.setProps({ selection: { taskKind: "object_detection", framework: "paddlex", adapterKey: "paddlex", adapterVersion: "1.0.0", modelKey: "pp-yoloe-s" } });
    expect(wrapper.text()).toContain("PP-YOLOE-S");
    expect(wrapper.text()).toContain("RT-DETR-L");
    expect(wrapper.text()).not.toContain("YOLO26-N");
  });

  it("shows only LLaMA-Factory for LLM SFT", () => {
    const wrapper = mount(FrameworkModelSelector, {
      props: {
        catalog,
        selection: { taskKind: "llm_sft", framework: "llamafactory", adapterKey: "llamafactory", adapterVersion: "1.0.0", modelKey: "qwen3" },
      },
    });

    expect(wrapper.text()).toContain("LLaMA-Factory");
    expect(wrapper.text()).not.toContain("PaddleX");
    expect(wrapper.text()).not.toContain("Ultralytics");
  });

  it("locks framework and model controls after submission and offers cloning", async () => {
    const wrapper = mount(FrameworkModelSelector, {
      props: {
        catalog,
        selection: { taskKind: "object_detection", framework: "paddlex", adapterKey: "paddlex", adapterVersion: "1.0.0", modelKey: "pp-yoloe-s" },
        locked: true,
        lockReason: "首次提交训练后不可更换框架",
      },
    });

    expect(wrapper.text()).toContain("首次提交训练后不可更换框架");
    expect(wrapper.get('[data-testid="framework-paddlex"]').attributes("disabled")).toBeDefined();
    expect(wrapper.get('[data-testid="model-pp-yoloe-s"]').attributes("disabled")).toBeDefined();
    await wrapper.get(".clone-button").trigger("click");
    expect(wrapper.emitted("clone")).toHaveLength(1);
  });
});
