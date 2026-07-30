# VisiOX ChatGPT Shell and Management Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the authenticated application shell with a ChatGPT-style gray sidebar and show account/admin tools inside a dedicated management-center dialog.

**Architecture:** `App.vue` owns shell and dialog state, `UserAccountMenu.vue` emits semantic management-section selections, and a new `ManagementCenterDialog.vue` lazily renders the existing account/admin views. Existing direct routes remain unchanged for deep links and authorization guards.

**Tech Stack:** Vue 3, TypeScript, Pinia, Vue Router, Element Plus, Vitest, Vue Test Utils.

## Global Constraints

- Do not change backend APIs, resource ownership, authentication, or route authorization.
- Reuse the existing Element Plus icon set and existing account/admin view components.
- Active navigation is neutral light gray, never light blue.
- The VIsiOX brand moves into the sidebar; the standalone top header is removed.
- The global authenticated canvas uses neutral ChatGPT-like gray tokens.
- Preserve mobile collapse behavior and keyboard accessibility.

---

### Task 1: Lock the shell and menu contracts in tests

**Files:**
- Modify: `apps/frontend/tests/app-layout.spec.ts`
- Modify: `apps/frontend/tests/account-menu.spec.ts`
- Create: `apps/frontend/tests/management-center-dialog.spec.ts`

**Interfaces:**
- `UserAccountMenu` emits `open-management` with one of `account`, `overview`, `users`, `groups`, `authorization`, `audit`, `resources`.
- `ManagementCenterDialog` consumes `modelValue: boolean` and `initialSection: ManagementSection`.

- [ ] Replace header assertions with sidebar-brand and neutral-active-state assertions.
- [ ] Assert account commands emit a management section without changing the current route.
- [ ] Assert the management dialog renders the requested section and hides admin sections from members.
- [ ] Run `npm test -- --run tests/app-layout.spec.ts tests/account-menu.spec.ts tests/management-center-dialog.spec.ts` and confirm the new assertions fail before implementation.

### Task 2: Build the management center

**Files:**
- Create: `apps/frontend/src/components/account/ManagementCenterDialog.vue`
- Modify: `apps/frontend/src/components/account/UserAccountMenu.vue`

**Interfaces:**
- Export `ManagementSection` from `ManagementCenterDialog.vue`.
- Emit `update:modelValue` when the window closes.
- Emit `open-management` from `UserAccountMenu` for all non-logout account commands.

- [ ] Implement the dialog with a left navigation rail, asynchronous existing views, admin visibility filtering, Esc close behavior, and an accessible title.
- [ ] Restyle the account dropdown with a profile summary, 16px modal-like radius, neutral hover states, and a rotating chevron.
- [ ] Run the three focused test files and confirm they pass.

### Task 3: Rebuild the global shell

**Files:**
- Modify: `apps/frontend/src/App.vue`
- Modify: `apps/frontend/src/styles.css`

**Interfaces:**
- `App.vue` handles `@open-management="openManagement"` and passes `v-model` plus `initial-section` into `ManagementCenterDialog`.
- Keep `effectiveCollapsed`, local-storage persistence, and existing mobile media-query behavior.

- [ ] Remove the standalone `app-header` and add `sidebar-brand` at the top of `app-sidebar`.
- [ ] Add a mobile sidebar scrim and close it when selected.
- [ ] Replace white navigation groups and blue active states with an unframed neutral navigation rail.
- [ ] Add global background, surface, border, shadow, radius, and motion tokens for the new shell.
- [ ] Add responsive dialog/sidebar rules and reduced-motion handling.
- [ ] Run the focused shell tests and confirm they pass.

### Task 4: Verify regression safety and visual behavior

**Files:**
- Modify only if verification exposes a defect in files from Tasks 1-3.

- [ ] Run `npm test -- --run` and expect all frontend tests to pass.
- [ ] Run `npm run typecheck` and expect exit code 0.
- [ ] Run `npm run build` and expect a successful Vite production build.
- [ ] Open `http://127.0.0.1:5174/` and verify desktop and narrow-width views: sidebar, selected state, account popover, management-section switching, close behavior, and no overlapping text.

