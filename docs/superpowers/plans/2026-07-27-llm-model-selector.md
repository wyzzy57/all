# LLM Model Selector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a curated, searchable LLM model selector that still accepts arbitrary repository IDs.

**Architecture:** Keep source-specific recommendation metadata in a typed catalog module and render it through Element Plus's filterable `el-select` with `allow-create`. The existing resolver remains the authoritative availability check, and any model/source change invalidates its previous result.

**Tech Stack:** Vue 3, TypeScript, Element Plus, Vitest, Vue Test Utils.

---

### Task 1: Typed recommendation catalog

**Files:**
- Create: `apps/frontend/src/features/pipeline-wizard/llm/llmModelCatalog.ts`
- Test: `apps/frontend/tests/llm-model-catalog.spec.ts`

- [x] **Step 1: Write the failing catalog test**

```ts
expect(llmModelRecommendations("huggingface").map((item) => item.id)).toEqual([
  "Qwen/Qwen3-0.6B",
  "Qwen/Qwen3-1.7B",
  "Qwen/Qwen3-4B",
]);
```

- [x] **Step 2: Run the catalog test and verify missing-module failure**

Run: `npm test -- --run tests/llm-model-catalog.spec.ts`
Expected: FAIL because `llmModelCatalog.ts` does not exist.

- [x] **Step 3: Implement the typed catalog**

```ts
export type LlmModelRecommendation = {
  id: string;
  name: string;
  parameterScale: string;
  suitability: string;
  sources: readonly LlmModelSource[];
};

export function llmModelRecommendations(source: LlmModelSource) {
  return catalog.filter((item) => item.sources.includes(source));
}
```

- [x] **Step 4: Run the catalog test and verify it passes**

Run: `npm test -- --run tests/llm-model-catalog.spec.ts`
Expected: PASS.

### Task 2: Searchable custom-value selector

**Files:**
- Modify: `apps/frontend/src/features/pipeline-wizard/llm/LlmPipelineWizardSteps.vue`
- Modify: `apps/frontend/tests/llm-pipeline-wizard-steps.spec.ts`

- [x] **Step 1: Write a failing component test**

```ts
expect(wrapper.get('[data-testid="llm-model-id-selector"]').attributes()).toMatchObject({
  filterable: "true",
  "allow-create": "true",
});
expect(wrapper.text()).toContain("Qwen3 0.6B");
```

- [x] **Step 2: Run the component test and verify selector absence**

Run: `npm test -- --run tests/llm-pipeline-wizard-steps.spec.ts`
Expected: FAIL because the model ID is still an `el-input`.

- [x] **Step 3: Replace the input with an editable selector**

```vue
<el-select
  v-model="form.modelId"
  data-testid="llm-model-id-selector"
  filterable
  allow-create
  default-first-option
  @change="invalidateModelResolution"
>
  <el-option v-for="model in recommendedModels" :key="model.id" :value="model.id">
    <!-- name, ID, parameter scale, suitability -->
  </el-option>
</el-select>
```

- [x] **Step 4: Add source-aware recommendations and shared invalidation**

```ts
const recommendedModels = computed(() => llmModelRecommendations(form.value.modelSource));
function invalidateModelResolution() {
  modelReferenceChecked.value = false;
  modelResolution.value = null;
  modelResolutionError.value = "";
}
```

- [x] **Step 5: Run focused and full verification**

Run:
`npm test -- --run tests/llm-model-catalog.spec.ts tests/llm-pipeline-wizard-steps.spec.ts`
`npm test -- --run`
`npm run typecheck`
`npm run build`

Expected: all tests, type checking, and build pass.

- [x] **Step 6: Verify the live browser (blocked at pipeline interaction by current backend API load failures; component behavior covered by automated tests)**

Open the LLM model/data step and confirm the selector lists recommendations, filters by text, accepts a custom ID, and requires model resolution after a change.
