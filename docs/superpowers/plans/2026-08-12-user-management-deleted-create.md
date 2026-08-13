# User Management Deleted Records and Create Action Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hide soft-deleted users from the default admin list and restore the create-user action inside the management center without removing audit access.

**Architecture:** Make the API's absent-status behavior explicitly mean “non-deleted,” while keeping every explicit status filter authoritative. Preserve the existing user view and editor; adjust only the management center's embedding CSS so it hides duplicate heading copy but leaves header action buttons available.

**Tech Stack:** FastAPI, SQLAlchemy, pytest, Vue 3, Element Plus, TypeScript, Vitest

---

## File Structure

- Modify `tests/integration/test_admin_identity_api.py`: lock the default and explicit-deleted list contracts.
- Modify `apps/api-service/src/visiox_api/routes/admin_users.py`: exclude deleted users only when no status filter is supplied.
- Modify `apps/frontend/tests/management-center-dialog.spec.ts`: lock the embedded-header CSS contract.
- Modify `apps/frontend/src/components/account/ManagementCenterDialog.vue`: preserve identity header actions in the embedded layout.

### Task 1: Exclude Soft-Deleted Users by Default

**Files:**
- Modify: `tests/integration/test_admin_identity_api.py`
- Modify: `apps/api-service/src/visiox_api/routes/admin_users.py:175-187`

- [ ] **Step 1: Write the failing API regression test**

In `test_user_update_reset_and_soft_delete_revoke_sessions`, after the successful delete, add:

```python
    default_listing = client.get(
        "/admin/users", headers=identity["admin_headers"]
    )
    deleted_listing = client.get(
        "/admin/users?status=deleted", headers=identity["admin_headers"]
    )
    assert default_listing.status_code == 200
    assert identity["member_id"] not in {
        item["id"] for item in default_listing.json()["items"]
    }
    assert deleted_listing.status_code == 200
    assert [item["id"] for item in deleted_listing.json()["items"]] == [
        identity["member_id"]
    ]
```

- [ ] **Step 2: Run the focused API test and verify RED**

Run from the repository root:

```powershell
& 'C:\Users\Administrator\Documents\visiox\.tools\Python312\python.exe' -m pytest tests/integration/test_admin_identity_api.py::test_user_update_reset_and_soft_delete_revoke_sessions -v
```

Expected: FAIL because the default listing still includes the soft-deleted member.

- [ ] **Step 3: Implement the absent-status default**

Change the status filter block in `list_users` to:

```python
    if status_filter:
        statement = statement.where(User.status == status_filter)
    else:
        statement = statement.where(User.status != STATUS_DELETED)
```

- [ ] **Step 4: Run the focused API test and verify GREEN**

Run the Step 2 command again.

Expected: PASS, proving the default list omits the record while `status=deleted` returns it.

### Task 2: Preserve the Embedded Create-User Action

**Files:**
- Modify: `apps/frontend/tests/management-center-dialog.spec.ts`
- Modify: `apps/frontend/src/components/account/ManagementCenterDialog.vue:358-360`

- [ ] **Step 1: Write the failing CSS contract test**

Import the component source:

```ts
import managementCenterSource from "@/components/account/ManagementCenterDialog.vue?raw";
```

Add this test:

```ts
it("keeps embedded identity actions visible while hiding duplicate heading copy", () => {
  expect(managementCenterSource).toContain(".identity-page-header > div");
  expect(managementCenterSource).toContain("justify-content: flex-end");
  expect(managementCenterSource).not.toMatch(
    /:deep\(\.identity-page-header\)\s*\{\s*display:\s*none;/,
  );
});
```

- [ ] **Step 2: Run the focused frontend test and verify RED**

Run from `apps/frontend`:

```powershell
npm test -- --run tests/management-center-dialog.spec.ts
```

Expected: FAIL because the source currently hides `.identity-page-header` entirely.

- [ ] **Step 3: Implement the minimal embedded header CSS**

Replace the existing embedding override with:

```css
.management-center-content-body :deep(.identity-page-header) {
  justify-content: flex-end;
}

.management-center-content-body :deep(.identity-page-header > div) {
  display: none;
}
```

This leaves the existing view's create button rendered and aligned right while removing its duplicate heading copy.

- [ ] **Step 4: Run the focused frontend test and verify GREEN**

Run the Step 2 command again.

Expected: all management center tests pass.

### Task 3: Regression and Live Verification

**Files:**
- Verify only; no planned production edits.

- [ ] **Step 1: Run API identity integration tests**

```powershell
& 'C:\Users\Administrator\Documents\visiox\.tools\Python312\python.exe' -m pytest tests/integration/test_admin_identity_api.py -q
```

Expected: all identity API integration tests pass.

- [ ] **Step 2: Run frontend tests and build**

From `apps/frontend`:

```powershell
npm test
npm run build
```

Expected: all frontend tests pass and the production build exits successfully.

- [ ] **Step 3: Verify live behavior**

On the worktree development server:

1. Open management center → user management.
2. Confirm the default list does not contain the deleted user.
3. Select status `deleted` and confirm the deleted record appears.
4. Confirm `创建用户` appears in the section action area.
5. Open the editor, submit a unique test user, and confirm it appears in the default list.

- [ ] **Step 4: Commit the implementation**

```powershell
git add -- tests/integration/test_admin_identity_api.py apps/api-service/src/visiox_api/routes/admin_users.py apps/frontend/tests/management-center-dialog.spec.ts apps/frontend/src/components/account/ManagementCenterDialog.vue
git commit -m "fix: restore user management workflows"
```
