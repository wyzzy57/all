# Upright Logo X Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the blue `X` in the sidebar `VisioX` wordmark upright while preserving every other wordmark property.

**Architecture:** Keep the existing text-based wordmark and change only its global `.brand-x` CSS rule. A source-level style regression test will guard both properties that currently create the lean, while browser inspection will verify the rendered expanded and collapsed states.

**Tech Stack:** Vue 3, CSS, Vitest, Vite

---

### Task 1: Guard and Correct the X Posture

**Files:**
- Modify: `apps/frontend/tests/app-layout.spec.ts`
- Modify: `apps/frontend/src/styles.css`

- [ ] **Step 1: Write the failing regression test**

Add this test beside the existing wordmark test in `apps/frontend/tests/app-layout.spec.ts`:

```ts
it("renders the blue wordmark X upright", () => {
  expect(stylesSource).toMatch(
    /\.brand-x\s*\{[^}]*font-style:\s*normal;[^}]*transform:\s*none;/s,
  );
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Run from `apps/frontend`:

```powershell
npm test -- --run tests/app-layout.spec.ts -t "renders the blue wordmark X upright"
```

Expected: FAIL because `.brand-x` currently contains `font-style: italic` and `transform: skewX(-9deg)`.

- [ ] **Step 3: Implement the minimal CSS change**

Update the existing `.brand-x` rule in `apps/frontend/src/styles.css` to:

```css
.brand-x {
  margin-left: 1px;
  color: #3269eb;
  font-style: normal;
  transform: none;
}
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```powershell
npm test -- --run tests/app-layout.spec.ts -t "renders the blue wordmark X upright"
```

Expected: one matching test passes.

- [ ] **Step 5: Run frontend regression checks**

Run from `apps/frontend`:

```powershell
npm test
npm run build
```

Expected: all Vitest tests pass and the Vite production build succeeds.

- [ ] **Step 6: Verify the rendered wordmark**

Open `http://127.0.0.1:5175/workbench`, inspect `.brand-x`, and confirm:

```text
font-style: normal
transform: none
```

Visually confirm the blue `X` is upright with the sidebar expanded, then collapse the sidebar and confirm the standalone blue `X` remains upright and centered.

- [ ] **Step 7: Commit**

```powershell
git add apps/frontend/tests/app-layout.spec.ts apps/frontend/src/styles.css
git commit -m "fix: straighten wordmark x"
```
