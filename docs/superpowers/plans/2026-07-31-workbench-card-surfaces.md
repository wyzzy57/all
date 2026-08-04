# Workbench Card Surfaces Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Apply the Workbench gray surface language to the four targeted business card families without changing forms, dialogs, business behavior, or responsive grids.

**Architecture:** Define semantic card-surface tokens in the global theme, then consume them only in Data Preparation import/asset cards, Model Space pipeline cards, and Services list cards. Keep all interaction and data contracts unchanged; tests enforce token use and prohibit elevated hover motion.

**Tech Stack:** Vue 3, scoped CSS, CSS custom properties, Vitest, Vue Test Utils, in-app browser QA.

---

### Task 1: Add failing surface-contract tests

**Files:**
- Modify: `apps/frontend/tests/data-preparation-view.spec.ts`
- Modify: `apps/frontend/tests/data-asset-card.spec.ts`
- Modify: `apps/frontend/tests/model-space-view.spec.ts`
- Modify: `apps/frontend/tests/services-view.spec.ts`
- Modify: `apps/frontend/tests/app-layout.spec.ts`

- [ ] **Step 1: Import the required raw sources**

Add raw imports where absent:

```ts
import stylesSource from "@/styles.css?raw";
import dataAssetCardSource from "@/components/data/DataAssetCard.vue?raw";
```

- [ ] **Step 2: Assert the shared semantic tokens**

In `app-layout.spec.ts`, require:

```ts
expect(stylesSource).toContain("--visiox-card-surface: #f3f4f6");
expect(stylesSource).toContain("--visiox-card-surface-raised: #f6f7f8");
expect(stylesSource).toContain("--visiox-card-border: #e0e2e6");
expect(stylesSource).toContain("--visiox-card-radius: 8px");
```

- [ ] **Step 3: Assert each targeted card consumes the tokens**

Use exact rule extraction or bounded regular expressions so unrelated white panels cannot satisfy the tests:

```ts
expect(importCardRule).toContain("background: var(--visiox-card-surface-raised)");
expect(importCardRule).toContain("border: 1px solid var(--visiox-card-border)");
expect(datasetCardRule).toContain("background: var(--visiox-card-surface)");
expect(dataAssetCardRule).toContain("background: var(--visiox-card-surface)");
expect(pipelineCardRule).toContain("background: var(--visiox-card-surface)");
expect(serviceCardRule).toContain("background: var(--visiox-card-surface)");
```

Require each targeted card to use `var(--visiox-card-radius)` and require pipeline/service/data hover rules not to contain `translateY` or elevated box shadows.

- [ ] **Step 4: Verify RED**

```powershell
npm test -- --run tests/app-layout.spec.ts tests/data-preparation-view.spec.ts tests/data-asset-card.spec.ts tests/model-space-view.spec.ts tests/services-view.spec.ts
```

Expected: FAIL because shared tokens do not exist and targeted cards still use hard-coded white surfaces.

- [ ] **Step 5: Commit the RED contract**

```powershell
git add apps/frontend/tests
git commit -m "test: define shared business card surfaces"
```

### Task 2: Implement shared business card surfaces

**Files:**
- Modify: `apps/frontend/src/styles.css`
- Modify: `apps/frontend/src/views/data-preparation/DataPreparationView.vue`
- Modify: `apps/frontend/src/components/data/DataAssetCard.vue`
- Modify: `apps/frontend/src/views/model-space/ModelSpaceView.vue`
- Modify: `apps/frontend/src/views/services/ServicesView.vue`

- [ ] **Step 1: Add global semantic tokens**

Add to `:root` without changing the existing form surface token:

```css
--visiox-card-surface: #f3f4f6;
--visiox-card-surface-raised: #f6f7f8;
--visiox-card-border: #e0e2e6;
--visiox-card-radius: 8px;
```

- [ ] **Step 2: Update Data Preparation cards**

Use the raised token for `.import-card` and the standard token for `.dataset-card`:

```css
.import-card {
  background: var(--visiox-card-surface-raised);
  border: 1px solid var(--visiox-card-border);
  border-radius: var(--visiox-card-radius);
  box-shadow: none;
}

.dataset-card {
  background: var(--visiox-card-surface);
  border: 1px solid var(--visiox-card-border);
  border-radius: var(--visiox-card-radius);
  transition: border-color 0.15s ease;
}

.dataset-card:hover {
  border-color: #aeb7c3;
  box-shadow: none;
  transform: none;
}
```

- [ ] **Step 3: Update the shared DataAssetCard**

Apply the same standard surface, border, radius, and restrained hover behavior to `.data-asset-card`. Preserve its selected/focus states and dimensions.

- [ ] **Step 4: Update Model Space and Services cards**

Apply the standard surface token and restrained hover behavior to `.pipeline-card` and `.service-card`. Preserve `.pipeline-card.selected`, card action visibility, service status tags, and all button/icon colors.

- [ ] **Step 5: Run focused tests and type checking**

```powershell
npm test -- --run tests/app-layout.spec.ts tests/data-preparation-view.spec.ts tests/data-asset-card.spec.ts tests/model-space-view.spec.ts tests/services-view.spec.ts
npm run typecheck
```

Expected: all focused tests pass and type checking exits with code 0.

- [ ] **Step 6: Commit implementation**

```powershell
git add apps/frontend/src/styles.css apps/frontend/src/views/data-preparation/DataPreparationView.vue apps/frontend/src/components/data/DataAssetCard.vue apps/frontend/src/views/model-space/ModelSpaceView.vue apps/frontend/src/views/services/ServicesView.vue
git commit -m "style: unify business card surfaces"
```

### Task 3: Complete regression and visual verification

**Files:**
- Verify: `apps/frontend/src/styles.css`
- Verify: the five targeted Vue files

- [ ] **Step 1: Run full verification**

```powershell
npm test -- --run
npm run build
git diff --check
```

Expected: all tests pass, `vue-tsc` and Vite build succeed, and the worktree remains clean.

- [ ] **Step 2: Verify desktop pages**

At 1366x768 inspect `/data-preparation`, `/model-space`, and `/services`:

- import cards use the raised gray surface;
- asset, pipeline, and service cards use the standard gray surface;
- colored icons, tags, buttons, selection, and actions remain visible;
- cards do not lift or gain a large shadow on hover.

- [ ] **Step 3: Verify responsive pages**

At 1024x768 and 375x812 confirm no horizontal overflow, clipped text, or broken grid behavior on the three pages.

- [ ] **Step 4: Record completion**

Report commits, test totals, build result, visual QA results, and the preview URL.
