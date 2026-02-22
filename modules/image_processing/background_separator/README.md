# Background separator module (`background_separator`)

Alteration module that flattens bright uncovered sensor background to a stable white level.

- **Type:** `alteration`
- **Slot:** `600` (after autocrop, with room for additional post-crop modules)
- **Modes:** automatic (all frames) and manual (apply/revert)

## Core behavior

1. Estimate a robust white reference from high-value pixels.
2. Reject the extreme bright tail to reduce hot-pixel influence.
3. Compute threshold = `white_reference - offset`.
4. Clip values above threshold to the white reference.

This suppresses noisy bright background regions while preserving object contrast.

## Settings keys

- `background_separator_offset`
- `background_separator_auto_workflow`
- `background_separator_live_preview`

## API pattern

- Per-frame pipeline uses:
  - `api.incoming_frame("background_separator", frame)`
  - `api.outgoing_frame("background_separator", out)`
- Manual apply/revert uses:
  - `api.get_module_incoming_image("background_separator")`
  - `api.output_manual_from_module("background_separator", frame)`
