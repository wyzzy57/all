# LLM Gradient Checkpoint Layout Design

## Goal

Make the training-stability controls easier to scan by removing the oversized Gradient Checkpoint panel and giving the switch a dedicated compact row.

## Layout

- Display Maximum Gradient Norm, Random Seed, Flash Attention, and RoPE Scaling in a two-column grid on desktop.
- Place Gradient Checkpoint after those four controls on a row spanning the full grid width.
- Preserve the existing responsive collapse to one column on narrow viewports.

## Gradient Checkpoint Field

- Put the label and switch on one horizontal line.
- Put the helper text below the label row.
- Remove the generic switch-panel border and oversized minimum height.
- Preserve the existing `form.gradientCheckpointing` binding.

## Verification

- Add a component test for field order, full-row placement, and compact styling.
- Run focused and full frontend tests, type checking, and production build.
- Verify desktop and narrow layouts in the browser.
