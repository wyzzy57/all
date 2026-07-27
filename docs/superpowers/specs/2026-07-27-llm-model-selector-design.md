# LLM Model Selector Design

## Goal

Upgrade the large-model pipeline's model ID field from a plain text input to a searchable curated selector without removing manual repository ID entry.

## Interaction

- Use one filterable selector with custom-value creation enabled.
- Show a source-specific curated list for Hugging Face or ModelScope.
- Each recommendation shows a display name, parameter scale, and a short single-GPU suitability note.
- Store and submit only the canonical `namespace/repository` ID.
- Preserve arbitrary manual IDs that match the backend repository-ID contract.
- Changing the source or model ID invalidates the previous resolution result and requires the user to resolve the model again.
- Keep revision input and the existing resolve action unchanged.

## Model Catalog

- Keep catalog metadata in a focused typed frontend module, separate from the wizard component.
- The first catalog contains small Qwen models suitable for the current single-GPU LLM workflow.
- Catalog entries explicitly declare supported source and repository ID so the two source views can diverge later.
- The catalog is advisory; successful backend resolution remains the source of truth.

## Error Handling

- An empty selector cannot be resolved.
- A manually entered value that does not use `namespace/repository` is rejected by the existing inline validation.
- A recommendation that becomes unavailable is handled by the existing resolver error and can still be replaced by a manual ID.

## Testing

- Verify source-specific recommendations are returned by the catalog helper.
- Verify the selector enables filtering and custom values.
- Verify selecting or typing a model invalidates the previous resolution state.
- Run focused wizard tests, full frontend tests, type checking, and production build.

## Scope

This change does not add live Hugging Face or ModelScope search, model downloads, new backend endpoints, or automatic model resolution.
