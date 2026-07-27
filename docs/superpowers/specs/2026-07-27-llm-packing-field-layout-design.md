# LLM Packing Field Layout Design

## Goal

Make the LLM data-performance settings visually consistent, with Sample Packing presented as a normal compact field instead of an oversized bordered panel.

## Layout

- Keep the existing three-column parameter grid on desktop.
- Place Maximum Samples, Sample Packing, and Preprocessing Worker in the first row.
- Keep DataLoader Worker in the next available grid cell.
- Collapse the grid responsively using the existing parameter-grid breakpoints.

## Sample Packing Field

- Use the same field width, spacing, and vertical rhythm as numeric parameter fields.
- Put the label and switch on one horizontal line.
- Put the helper text below that line.
- Remove the standalone panel border and oversized minimum height.
- Preserve the existing `form.packing` behavior and Element Plus switch control.

## Verification

- Add a component test that distinguishes the compact packing field from the existing generic switch panel.
- Run the focused component test, full frontend tests, type checking, and production build.
- Verify the section visually at desktop and narrow viewport widths.
