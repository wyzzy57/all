import { flushPromises, mount } from "@vue/test-utils";
import { describe, expect, it, vi } from "vitest";

import LlmPipelineWizardSteps from "@/features/pipeline-wizard/llm/LlmPipelineWizardSteps.vue";
import { createDefaultLlmTrainingForm } from "@/features/pipeline-wizard/llm/llmTrainingForm";

const apiMock = vi.hoisted(() => ({
  previewLlmDataset: vi.fn(),
  resolveLlmModel: vi.fn(),
  uploadLlmDataset: vi.fn(),
}));

vi.mock("@/api/client", () => ({ api: apiMock }));

describe("LlmPipelineWizardSteps", () => {
  it("renders Sample Packing as a compact parameter field", () => {
    const wrapper = mount(LlmPipelineWizardSteps, {
      props: {
        activeStep: 2,
        modelValue: createDefaultLlmTrainingForm("LLM draft"),
        datasets: [],
        resourcePools: [],
        computeNodes: [],
      },
      global: {
        stubs: {
          "el-icon": { template: "<span><slot /></span>" },
          "el-input": true,
          "el-input-number": true,
          "el-select": { template: "<div><slot /></div>" },
          "el-option": true,
          "el-switch": true,
        },
      },
    });

    const packingField = wrapper.get(".packing-field");
    expect(packingField.text()).toContain("样本 Packing");
    expect(packingField.text()).toContain("拼接短样本提高吞吐");
    expect(packingField.classes()).not.toContain("switch-field");
    expect(packingField.classes()).toContain("packing-field--full-row");
    expect(packingField.element.parentElement?.lastElementChild).toBe(packingField.element);
  });

  it("renders Gradient Checkpoint as a compact full-width row", () => {
    const wrapper = mount(LlmPipelineWizardSteps, {
      props: {
        activeStep: 2,
        modelValue: createDefaultLlmTrainingForm("LLM draft"),
        datasets: [],
        resourcePools: [],
        computeNodes: [],
      },
      global: {
        stubs: {
          "el-icon": { template: "<span><slot /></span>" },
          "el-input": true,
          "el-input-number": true,
          "el-select": { template: "<div><slot /></div>" },
          "el-option": true,
          "el-switch": true,
        },
      },
    });

    const checkpointField = wrapper.get(".gradient-checkpoint-field");
    expect(checkpointField.text()).toContain("梯度检查点");
    expect(checkpointField.text()).toContain("降低显存占用");
    expect(checkpointField.classes()).not.toContain("switch-field");
    expect(checkpointField.classes()).toContain("gradient-checkpoint-field--full-row");
    expect(checkpointField.element.parentElement?.classList.contains("stability-grid")).toBe(true);
    expect(checkpointField.element.parentElement?.lastElementChild).toBe(checkpointField.element);
  });

  it("offers curated models, accepts custom IDs, and invalidates an earlier resolution", async () => {
    const form = createDefaultLlmTrainingForm("LLM draft");
    form.modelId = "Qwen/Qwen3-0.6B";
    form.datasetId = "dataset-1";

    const wrapper = mount(LlmPipelineWizardSteps, {
      props: {
        activeStep: 1,
        modelValue: form,
        datasets: [{ id: "dataset-1", name: "equipment-sft", task: "llm", status: "validated", sample_count: 3, annotation_count: 3 }],
        resourcePools: [],
        computeNodes: [],
      },
      global: {
        stubs: {
          "el-icon": { template: "<span><slot /></span>" },
          "el-input": true,
          "el-input-number": true,
          "el-select": {
            name: "ElSelect",
            props: {
              modelValue: { type: String, default: "" },
              filterable: { type: Boolean, default: false },
              allowCreate: { type: Boolean, default: false },
              defaultFirstOption: { type: Boolean, default: false },
            },
            emits: ["update:modelValue", "change"],
            template: '<div :data-filterable="String(filterable)" :data-allow-create="String(allowCreate)" :data-default-first-option="String(defaultFirstOption)"><slot /></div>',
          },
          "el-option": {
            props: ["label", "value"],
            template: "<div>{{ label }}<slot /></div>",
          },
          "el-switch": true,
        },
      },
    });

    const selector = wrapper.get('[data-testid="llm-model-id-selector"]');
    expect(selector.attributes("data-filterable")).toBe("true");
    expect(selector.attributes("data-allow-create")).toBe("true");
    expect(selector.text()).toContain("Qwen3 0.6B");
    expect(selector.text()).toContain("Qwen3 4B");
    expect((wrapper.vm as unknown as { validateStep: (step: number) => { valid: boolean } }).validateStep(1).valid).toBe(true);

    const huggingFaceButton = wrapper.findAll("button").find((button) => button.text() === "Hugging Face");
    expect(huggingFaceButton).toBeDefined();
    await huggingFaceButton?.trigger("click");
    expect(form.modelSource).toBe("huggingface");
    expect(form.requestedRevision).toBe("main");

    const selectorComponent = wrapper.findAllComponents({ name: "ElSelect" }).find((component) => component.attributes("data-testid") === "llm-model-id-selector");
    selectorComponent?.vm.$emit("change", "custom/example-model");
    await wrapper.vm.$nextTick();
    expect((wrapper.vm as unknown as { validateStep: (step: number) => { valid: boolean; message: string } }).validateStep(1)).toEqual({
      valid: false,
      message: "请先解析基础模型",
    });
  });

  it("shows token distribution, truncation risk, and a dataset detail dialog", async () => {
    apiMock.previewLlmDataset.mockResolvedValue({
      dataset_id: "dataset-1",
      format: "sharegpt",
      manifest_checksum: "abc",
      samples: [
        { index: 1, character_count: 100, token_estimate: 120, messages: [{ role: "user", content: "question" }, { role: "assistant", content: "answer" }] },
        { index: 2, character_count: 500, token_estimate: 900, messages: [{ role: "user", content: "long question" }, { role: "assistant", content: "long answer" }] },
        { index: 3, character_count: 900, token_estimate: 1400, messages: [{ role: "user", content: "very long question" }, { role: "assistant", content: "very long answer" }] },
      ],
      token_analysis: { method: "format_aware_estimate", exact: false, sample_count: 3, minimum: 120, maximum: 1400, average: 806.67 },
    });
    const form = createDefaultLlmTrainingForm("LLM draft");
    form.modelId = "Qwen/Qwen3-0.6B";
    form.datasetId = "dataset-1";
    form.cutoffLen = 1024;

    const wrapper = mount(LlmPipelineWizardSteps, {
      props: {
        activeStep: 1,
        modelValue: form,
        datasets: [{ id: "dataset-1", name: "equipment-sft", task: "llm", status: "validated", format: "sharegpt", sample_count: 3, annotation_count: 3, schema_config: { messages: "conversations" } }],
        resourcePools: [],
        computeNodes: [],
      },
      global: {
        stubs: {
          "el-icon": { template: "<span><slot /></span>" },
          "el-input": true,
          "el-input-number": true,
          "el-select": { template: "<div><slot /></div>" },
          "el-option": true,
          "el-switch": true,
        },
      },
    });
    await flushPromises();

    expect(apiMock.previewLlmDataset).toHaveBeenCalledWith("dataset-1", 20);
    expect(wrapper.text()).toContain("Token 长度分布");
    expect(wrapper.text()).toContain("P50 900");
    expect(wrapper.text()).toContain("预计 33.3% 样本会被截断");

    await wrapper.get('[data-testid="llm-dataset-details"]').trigger("click");
    expect(wrapper.get('[role="dialog"]').text()).toContain("字段映射");
    expect(wrapper.get('[role="dialog"]').text()).toContain("conversations");
  });

  it("blocks submission when the selected online GPU has insufficient total memory", async () => {
    const form = createDefaultLlmTrainingForm("LLM draft");
    form.modelId = "Qwen/Qwen3-0.6B";
    form.datasetId = "dataset-1";
    form.method = "lora";
    form.poolId = "pool-1";
    form.nodeId = "node-1";

    const wrapper = mount(LlmPipelineWizardSteps, {
      props: {
        activeStep: 3,
        modelValue: form,
        datasets: [{ id: "dataset-1", name: "equipment-sft", task: "llm", status: "validated", sample_count: 3, annotation_count: 3 }],
        resourcePools: [{ id: "pool-1", name: "GPU pool", kind: "x86_nvidia", selector: {}, compatibility_policy: {}, enabled: true }],
        computeNodes: [{
          id: "node-1",
          name: "edge-gpu",
          resource_pool_id: "pool-1",
          status: "online",
          architecture: "x86_64",
          platform_kind: "x86_nvidia",
          capabilities: { nvidia_gpu: true, gpu_models: ["NVIDIA RTX"] },
          resources: { gpu_count: 1, gpu_memory_total_mib: 8192 },
          fingerprint: {},
        agent_version: "ssh-bootstrap",
        enabled: true,
        labels: {},
        connection_method: "ssh",
        inventory_refreshed_at: null,
        resource_revision: 1,
        }],
      },
      global: {
        stubs: {
          "el-icon": { template: "<span><slot /></span>" },
          "el-input": true,
          "el-input-number": true,
          "el-select": { template: "<div><slot /></div>" },
          "el-option": true,
          "el-switch": true,
        },
      },
    });

    expect(wrapper.text()).toContain("8 GB，不足");
    expect((wrapper.vm as unknown as { validateStep: (step: number) => { valid: boolean; message: string } }).validateStep(3)).toEqual({
      valid: false,
      message: "所选节点总显存不足，请改用 QLoRA 或选择更大显存节点",
    });
  });
});
