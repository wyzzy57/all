# LLM Packing Field Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Present Sample Packing as a compact parameter field aligned with the surrounding LLM data-performance controls.

**Architecture:** Keep the existing parameter grid and form state. Add a dedicated compact packing-field class so the label, helper text, and switch align without inheriting the generic bordered switch-panel treatment.

**Tech Stack:** Vue 3, TypeScript, scoped CSS, Element Plus, Vitest, Vue Test Utils.

---

### Task 1: Compact Sample Packing Field

**Files:**
- Modify: `apps/frontend/src/features/pipeline-wizard/llm/LlmPipelineWizardSteps.vue:227-232`
- Modify: `apps/frontend/tests/llm-pipeline-wizard-steps.spec.ts`

- [x] **Step 1: Write the failing component test**

Add an assertion that the data-performance Packing control uses a dedicated `packing-field` class and exposes the existing helper text.

```ts
const packingField = wrapper.get(".packing-field");
expect(packingField.text()).toContain("样本 Packing");
expect(packingField.text()).toContain("拼接短样本提高吞吐");
expect(packingField.classes()).not.toContain("switch-field");
```

- [x] **Step 2: Run the focused test and verify it fails**

Run: `npm test -- --run tests/llm-pipeline-wizard-steps.spec.ts`

Expected: FAIL because `.packing-field` does not exist.

- [x] **Step 3: Implement the compact field**

Replace the generic panel class with a dedicated field structure.

```vue
<label class="packing-field">
  <span class="packing-field__header">
    <strong>样本 Packing</strong>
    <el-switch v-model="form.packing" />
  </span>
  <small>拼接短样本提高吞吐</small>
</label>
```

Add scoped CSS that uses the normal field height and removes the standalone border.

```css
.packing-field { display: flex; min-width: 0; flex-direction: column; gap: 8px; }
.packing-field__header { display: flex; min-height: 32px; align-items: center; justify-content: space-between; gap: 16px; }
.packing-field strong { color: #172033; font-size: 14px; }
.packing-field small { color: #667085; font-size: 12px; line-height: 1.5; }
```

- [x] **Step 4: Run verification**

Run:

```powershell
npm test -- --run tests/llm-pipeline-wizard-steps.spec.ts
npm test -- --run
npm run typecheck
npm run build
```

Expected: all tests, type checking, and production build pass.

- [x] **Step 5: Verify responsive layout**

Open the LLM parameter-preparation step and confirm:

- Desktop: Maximum Samples, Sample Packing, and Preprocessing Worker align as three equal columns.
- Narrow viewport: the grid collapses without text overlap or horizontal scrolling.

### Task 2: Dedicated Packing Row

**Files:**
- Modify: `apps/frontend/src/features/pipeline-wizard/llm/LlmPipelineWizardSteps.vue`
- Test: `apps/frontend/tests/llm-pipeline-wizard-steps.spec.ts`

- [x] **Step 1: Write the failing layout test**

Assert that `.packing-field` is the final child of the data-performance grid and has the `packing-field--full-row` class.

- [x] **Step 2: Run the focused test and verify it fails**

Run: `npm test -- --run tests/llm-pipeline-wizard-steps.spec.ts`

Expected: FAIL because Packing currently occupies the second grid cell and has no full-row class.

- [x] **Step 3: Reorder and span the Packing field**

Move Maximum Samples, Preprocessing Worker, and DataLoader Worker before Packing. Add `packing-field--full-row` with `grid-column: 1 / -1` so Packing owns the second row at every grid breakpoint.

- [x] **Step 4: Run verification**

Run:

```powershell
npm test -- --run tests/llm-pipeline-wizard-steps.spec.ts
npm run typecheck
npm run build
```

Expected: focused tests, type checking, and production build pass.

- [x] **Step 5: Verify desktop and narrow layouts**

Confirm the desktop grid has three numeric fields on row one and Packing on row two. Confirm narrow layouts remain single-column without overflow.
