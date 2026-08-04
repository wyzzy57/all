# Workbench Card Surface Design

## Goal

Replace the bright white business cards in Data Preparation, Model Space, and Services with the restrained gray surfaces already used by the Workbench.

## Surface System

- Add reusable application surface tokens matching the Workbench:
  - standard card surface: `#f3f4f6`
  - raised entry surface: `#f6f7f8`
  - card border: `#e0e2e6`
  - card radius: `8px`
- Keep the application background distinct from card surfaces.
- Preserve colored import icons, primary buttons, status tags, owner markers, and selected-state accents.

## Scope

- Data Preparation import cards use the raised entry surface.
- Data Preparation asset cards, including the shared `DataAssetCard`, use the standard card surface.
- Model Space pipeline cards use the standard card surface.
- Services list cards use the standard card surface.
- Hover states may strengthen the border but must not lift the card or introduce a large shadow.
- Selected and focus-visible states remain clearly distinguishable and accessible.

## Exclusions

- Do not globally replace every white background.
- Do not alter dialogs, forms, inputs, dropdowns, pagination controls, training tools, inference views, or detail-page content panels.
- Do not change card dimensions, business data, routes, actions, or responsive grid behavior.

## Verification

- The four targeted card families use the shared surface tokens rather than hard-coded white backgrounds.
- Import cards remain visually distinct from repeated list cards.
- Hover does not translate cards or add a large drop shadow.
- Existing selected, keyboard-focus, tag, icon, and button states remain visible.
- Data Preparation, Model Space, and Services remain free of horizontal overflow at desktop and mobile widths.
- Focused tests, the complete frontend suite, type checking, and production build pass.
