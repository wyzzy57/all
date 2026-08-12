# Management Center Content Width Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Widen the management center's right-hand desktop content area while keeping its existing 220px navigation rail and mobile layout unchanged.

**Architecture:** Keep the current two-column grid and responsive breakpoint. Change only the Element Plus dialog width contract so the extra desktop width flows into the existing `minmax(0, 1fr)` content column, with the current overflow behavior retained as a narrow-viewport fallback.

**Tech Stack:** Vue 3, Element Plus, TypeScript, Vitest, Vue Test Utils, Vite

---

## File Structure

- Modify `apps/frontend/tests/management-center-dialog.spec.ts`: assert the approved dialog width and unchanged left-rail grid contract.
- Modify `apps/frontend/src/components/account/ManagementCenterDialog.vue`: replace the desktop dialog width value only.

### Task 1: Widen the Right-Hand Management Content Area

**Files:**
- Modify: `apps/frontend/tests/management-center-dialog.spec.ts`
- Modify: `apps/frontend/src/components/account/ManagementCenterDialog.vue:5`

- [ ] **Step 1: Write the failing width contract test**

Add this test inside `describe("ManagementCenterDialog", ...)`:

```ts
it("gives desktop management tables more room without widening the navigation rail", async () => {
  const wrapper = await mountDialog("admin", "users");

  expect(wrapper.get(".el-dialog").attributes("style")).toContain("width: min(1320px, 94vw)");
  expect(wrapper.get(".management-center-frame").classes()).toContain("management-center-frame");
});
```

The first assertion captures the approved desktop dialog contract. The frame assertion keeps the test anchored to the existing component structure; the fixed `220px` rail remains in the component's scoped CSS and is also checked during visual verification.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
npm test -- --run tests/management-center-dialog.spec.ts
```

Working directory: `apps/frontend`

Expected: FAIL because the rendered dialog style still contains `width: min(1080px, calc(100vw - 40px))` instead of `width: min(1320px, 94vw)`.

- [ ] **Step 3: Apply the minimal production change**

In `apps/frontend/src/components/account/ManagementCenterDialog.vue`, change the dialog property to:

```vue
width="min(1320px, 94vw)"
```

Do not change `grid-template-columns: 220px minmax(0, 1fr)` or the existing mobile media query.

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```powershell
npm test -- --run tests/management-center-dialog.spec.ts
```

Working directory: `apps/frontend`

Expected: all tests in `management-center-dialog.spec.ts` pass.

- [ ] **Step 5: Run frontend regression verification**

Run:

```powershell
npm test
npm run build
```

Working directory: `apps/frontend`

Expected: the complete Vitest suite passes and the Vite production build exits successfully.

- [ ] **Step 6: Verify the live desktop layout**

At the supplied `1386x912` desktop viewport, open `/workbench`, open the management center on user management, and verify:

1. The dialog is approximately `1303px` wide (`94vw`) and leaves visible outer margins.
2. The left navigation rail remains `220px` wide.
3. The right content area receives the added width.
4. The complete user management table is visible without routine horizontal dragging.
5. At `720px` viewport width or below, the existing stacked mobile layout remains active.

- [ ] **Step 7: Commit the implementation**

```powershell
git add -- apps/frontend/tests/management-center-dialog.spec.ts apps/frontend/src/components/account/ManagementCenterDialog.vue
git commit -m "fix: widen management center content"
```
