# Management Center Content Width Design

## Goal

Increase the usable width of the management center's right-hand content area so desktop data tables, especially user management, can show their columns and actions without routine horizontal scrolling.

## Approved Layout

- Keep the existing left navigation rail at `220px`.
- Increase the dialog width from `min(1080px, calc(100vw - 40px))` to `min(1320px, 94vw)`.
- Give all added width to the right-hand content area through the existing `220px minmax(0, 1fr)` grid.
- Preserve the current dialog height, spacing, visual treatment, navigation behavior, and mobile breakpoint behavior.
- Retain horizontal overflow as a fallback for viewports where the table still cannot fit; do not hide or truncate actions merely to remove the scrollbar.

## Scope

The production change is limited to the management center dialog width. No table columns, table actions, API behavior, role behavior, navigation items, or mobile navigation patterns change.

## Responsive Behavior

- Desktop: the dialog uses up to `1320px` and no more than `94vw`, leaving visible page margins.
- At the existing `720px` breakpoint and below: keep the current explicit mobile width and stacked navigation/content layout.
- Intermediate widths continue to use the current overflow fallback when the complete user table cannot fit safely.

## Verification

- Add a focused component regression assertion for the approved desktop width contract before changing production code.
- Run the management center component tests.
- Run the complete frontend test suite and production build.
- Inspect the live management center at the supplied desktop viewport and confirm the left rail remains unchanged while the right content area is wider and the user table no longer needs routine horizontal dragging.

## Non-Goals

- Widening the left navigation rail.
- Redesigning the user management table.
- Converting the dialog into a full-screen page.
- Changing unrelated management views.
