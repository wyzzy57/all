# Workbench Header and Resource Density Design

## Goal

Improve the visual connection between the Workbench heading and the dashboard panels while keeping the complete expanded-sidebar desktop view inside a 1366x768 viewport.

## Layout

- Reduce the desktop heading row from about 65px to 48-52px.
- Reduce the gap between the heading and KPI summary from 10px to 6px.
- Keep the title, supporting sentence, and generated timestamp; do not remove information.
- Preserve the existing 58px KPI strip, 310px command area, and 240px lower overview row unless browser verification proves a small adjustment is required.
- Keep the 1350px viewport breakpoint: 1366px remains compact, while narrower layouts use natural vertical flow.

## Resource Usage

- Render CPU, memory, and disk as three separate full-width rows in that order.
- Keep GPU rows below the three system rows when GPU telemetry exists.
- Each row retains its label, reading, availability message, and progress track.
- The resource panel may scroll internally only when additional GPU rows cannot fit; CPU, memory, and disk must remain visible without scrolling at 1366x768.

## Scope

- Limit changes to Workbench composition styles, resource-panel presentation overrides, and focused regression tests.
- Do not change dashboard APIs, polling, metric derivation, routes, or component data contracts.

## Verification

- At 1366x768 with the sidebar expanded, the document has no horizontal or vertical scrollbar and no Workbench content is clipped.
- The heading visually connects to the KPI strip with a 6px inter-row gap.
- CPU, memory, and disk each occupy one resource row.
- At 1340px, 1024px, and 375px, the page uses natural vertical scrolling without overlap or horizontal overflow.
- Focused Workbench tests, the full frontend test suite, type checking, and the production build pass.
