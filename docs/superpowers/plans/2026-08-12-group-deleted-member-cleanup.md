# Group Deleted Member Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent stale soft-deleted user IDs from appearing in group responses or being resubmitted by the group editor.

**Architecture:** Filter group response membership by joining membership rows to non-deleted users. Keep the write endpoint's validation unchanged, and add a frontend active-user intersection immediately before the authoritative membership replacement call.

**Tech Stack:** FastAPI, SQLAlchemy, pytest, Vue 3, TypeScript, Vitest

---

### Task 1: Filter Deleted Members from Group Responses

**Files:**
- Modify: `tests/integration/test_admin_identity_api.py`
- Modify: `apps/api-service/src/visiox_api/routes/admin_groups.py:50-57`

- [ ] **Step 1: Write the failing API regression**

After the existing two-member replacement assertion, soft-delete the fixture member in the database and request the group list:

```python
    with session_factory.begin() as session:
        session.execute(
            update(User)
            .where(User.id == identity["member_id"])
            .values(status=STATUS_DELETED)
        )
    filtered_listing = client.get(
        "/admin/groups", headers=identity["admin_headers"]
    )
    assert filtered_listing.status_code == 200
    assert filtered_listing.json()["items"][0]["member_ids"] == [
        identity["admin_id"]
    ]
    assert filtered_listing.json()["items"][0]["member_count"] == 1
```

- [ ] **Step 2: Verify RED**

```powershell
& 'C:\Users\Administrator\Documents\visiox\.tools\Python312\python.exe' -m pytest tests/integration/test_admin_identity_api.py::test_group_crud_membership_replacement_and_org_validation -v
```

Expected: FAIL because `_response` still returns both membership rows.

- [ ] **Step 3: Filter response membership**

Change `_response` to join `User` and exclude `STATUS_DELETED`:

```python
            select(UserGroupMembership.user_id)
            .join(User, User.id == UserGroupMembership.user_id)
            .where(
                UserGroupMembership.group_id == group.id,
                User.status != STATUS_DELETED,
            )
```

- [ ] **Step 4: Verify GREEN**

Run the Step 2 command again and expect PASS.

### Task 2: Filter Stale IDs Before Frontend Submission

**Files:**
- Modify: `apps/frontend/tests/admin-identity-views.spec.ts`
- Modify: `apps/frontend/src/views/admin/GroupManagementView.vue`

- [ ] **Step 1: Write the failing frontend regression**

In the existing “loads all member selector pages and replaces group membership” test, change the group fixture to:

```ts
member_ids: ["deleted-user", "u1"], member_count: 2
```

Keep the final expectation:

```ts
expect(replaceMembers).toHaveBeenCalledWith("g1", []);
```

- [ ] **Step 2: Verify RED**

```powershell
npm test -- --run tests/admin-identity-views.spec.ts
```

Working directory: `apps/frontend`

Expected: FAIL because the current editor submits `deleted-user` after `u1` is unchecked.

- [ ] **Step 3: Intersect with active selector users**

Before `replaceUserGroupMembers`, derive and apply the current active IDs:

```ts
    const activeUserIds = new Set(users.value.map((user) => user.id));
    const memberIds = editor.memberIds.filter((userId) => activeUserIds.has(userId));
    await api.replaceUserGroupMembers(group.id, memberIds);
```

- [ ] **Step 4: Verify GREEN**

Run the Step 2 command again and expect all identity view tests to pass.

### Task 3: Regression, Runtime, and Data Cleanup Verification

- [ ] **Step 1: Run identity API integration tests**

```powershell
& 'C:\Users\Administrator\Documents\visiox\.tools\Python312\python.exe' -m pytest tests/integration/test_admin_identity_api.py -q
```

- [ ] **Step 2: Run full frontend tests and build**

From `apps/frontend`:

```powershell
npm test
npm run build
```

- [ ] **Step 3: Verify the live `一班` workflow**

Reload group management, edit `一班`, select `user1`, and save. Confirm no warning appears and the API response reports only the `user1` ID with `member_count: 1`. Confirm the stale database membership row is removed by the existing replacement transaction.

- [ ] **Step 4: Commit**

```powershell
git add -- tests/integration/test_admin_identity_api.py apps/api-service/src/visiox_api/routes/admin_groups.py apps/frontend/tests/admin-identity-views.spec.ts apps/frontend/src/views/admin/GroupManagementView.vue
git commit -m "fix: clean stale group memberships"
```
