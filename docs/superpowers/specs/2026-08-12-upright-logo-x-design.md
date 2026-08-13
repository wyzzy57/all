# Upright Logo X Design

## Goal

Make the blue `X` in the sidebar `VisioX` wordmark visually upright instead of leaning to the right.

## Scope

- Remove the italic font style from `.brand-x`.
- Remove the `skewX(-9deg)` transform from `.brand-x`.
- Preserve the existing font family, font size, font weight, blue color, one-pixel spacing, alignment, link behavior, and collapsed-sidebar sizing.
- Do not replace the text wordmark with an image or SVG and do not alter the login-page brand.

## Implementation

Update the global `.brand-x` rule in `apps/frontend/src/styles.css` so the glyph uses `font-style: normal` and no transform. The existing collapsed-sidebar override continues to control only spacing and font size.

## Verification

- Add a style-source regression assertion proving `.brand-x` is not italic or skewed.
- Run the focused layout test, the full frontend test suite, and the production build.
- Inspect the running workbench in the browser and confirm that the blue `X` is upright in both expanded and collapsed sidebar states.
