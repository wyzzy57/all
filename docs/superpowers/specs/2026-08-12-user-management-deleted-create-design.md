# User Management Deleted Records and Create Action Design

## Goal

Make the normal user-management workflow show only current accounts and restore the create-user action inside the management center.

## Approved Behavior

- `GET /admin/users` excludes soft-deleted users when no `status` query is supplied.
- `GET /admin/users?status=deleted` continues to return deleted records for audit and investigation.
- The management center continues to render its own section title and description.
- Embedded identity views hide only their duplicate heading copy; header action buttons remain visible and right-aligned.
- The standalone `/admin/users` view retains its existing full header and create button.

## Implementation

The API list query will apply `User.status != deleted` only when `status_filter` is absent. An explicit status filter remains authoritative.

The management center's scoped embedding CSS will stop hiding `.identity-page-header` wholesale. It will hide the header's descriptive `div`, preserve the action button, and align the remaining header content to the right. This fixes the create-user entry without adding component-specific events or duplicating form logic.

## Verification

- API integration test: delete a user, verify the default list omits it, and verify the explicit deleted filter returns it.
- Frontend regression test: verify embedded identity headers preserve their action area rather than being entirely hidden.
- Run focused API/frontend tests, the complete frontend suite, and the production build.
- Browser check: deleted user absent by default; selecting the deleted filter reveals it; create-user button opens the existing editor and successfully creates a user.

## Non-Goals

- Hard-deleting users or audit history.
- Changing account uniqueness or allowing deleted accounts to be restored.
- Rebuilding the user editor or changing authorization rules.
