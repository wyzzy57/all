# Group Deleted Member Cleanup Design

## Goal

Allow administrators to edit and save groups that contain stale membership rows for soft-deleted users, without exposing or resubmitting those deleted users.

## Approved Behavior

- Group list and mutation responses include only non-deleted users in `member_ids` and `member_count`.
- The group editor displays only active users as selectable members, as it does today.
- Before replacing membership, the frontend intersects selected IDs with the currently loaded active-user IDs.
- A successful membership replacement remains authoritative and removes stale deleted-user membership rows through the existing delete-and-reinsert transaction.
- Deleted user records and audit history remain intact.

## Implementation

The API response query will join `UserGroupMembership` to `User` and filter `User.status != deleted`. The membership write endpoint retains its validation, so direct callers still cannot add deleted or cross-organization users.

The frontend will derive an active-user ID set from the loaded selector data and submit only IDs present in that set. This protects against stale group responses and users deleted between group loading and editing.

## Verification

- API integration test inserts a stale deleted-user membership and proves group responses omit it while reporting the correct member count.
- Frontend test opens a group whose `member_ids` contains an invisible stale ID and proves only active selected IDs are submitted.
- Run identity API integration tests, frontend identity tests, the full frontend suite, and production build.
- Live verification saves the existing `一班` group with `user1`, confirms the warning disappears, and confirms its response contains only `user1`.

## Non-Goals

- Hard-deleting users or group history.
- Automatically restoring deleted users.
- Changing group authorization or adding inactive users to the selector.
