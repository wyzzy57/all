# LLM Gradient Checkpoint Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Present Gradient Checkpoint as a compact full-width row below a balanced two-column training-stability grid.

**Architecture:** Keep the existing form state and responsive parameter grid. Add scoped layout classes for the two-column stability grid and compact full-row switch field.

**Tech Stack:** Vue 3, TypeScript, Element Plus, scoped CSS, Vitest, Vue Test Utils.

---

### Task 1: Gradient Checkpoint Layout

**Files:**
- Modify: `apps/frontend/src/features/pipeline-wizard/llm/LlmPipelineWizardSteps.vue`
- Test: `apps/frontend/tests/llm-pipeline-wizard-steps.spec.ts`

- [x] **Step 1: Write the failing component test**

Assert that the Gradient Checkpoint field uses dedicated compact and full-row classes, does not use `switch-field`, and is the last child of the stability grid.

- [x] **Step 2: Run the focused test and verify it fails**

Run: `npm test -- --run tests/llm-pipeline-wizard-steps.spec.ts`

Expected: FAIL because the control still uses the generic bordered switch panel.

- [x] **Step 3: Implement the balanced layout**

Use a two-column stability grid for the four standard controls. Move Gradient Checkpoint last and apply `grid-column: 1 / -1` with the compact field styling.

- [x] **Step 4: Run automated verification**

Run the focused test, full test suite, `npm run typecheck`, and `npm run build`.

- [x] **Step 5: Verify responsive layout**

Confirm desktop uses two balanced columns plus the full-width switch row, and narrow viewports collapse to one column without overflow.
