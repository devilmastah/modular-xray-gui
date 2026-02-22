# Image Enhancement module (`microcontrast_dehaze`)

Combined enhancement module that applies:

1. **Deconvolution** (Richardson-Lucy, optional auto/manual)
2. **Clarity/Dehaze** (optional auto/manual)

Module type: **`alteration`**, slot **480** (before autocrop at 500, with room for intermediate modules).

---

## What it does

- Per-frame pipeline path (`process_frame`):
  - reads canonical module input via `gui.api.incoming_frame("microcontrast_dehaze", frame)`
  - caches incoming frame for this module
  - applies auto deconvolution if enabled
  - applies auto contrast enhancement if enabled
  - returns via `gui.api.outgoing_frame("microcontrast_dehaze", out)` to remaining steps

- Manual path (buttons):
  - uses cached module input frame (never current display fallback)
  - applies selected operation(s)
  - outputs via downstream pipeline helpers so only later modules run

This avoids cumulative/double-application behavior.

---

## Settings keys

- `microcontrast_clarity`
- `microcontrast_dehaze`
- `microcontrast_auto_workflow` (UI label: auto contrast enhancement)
- `microcontrast_auto_deconv_workflow` (UI label: auto deconvolution)
- `microcontrast_deconv_sigma`
- `microcontrast_deconv_iterations`
- `microcontrast_live_preview`

---

## Manual-safe pipeline API usage

The module uses API helpers introduced for manual operations on pipeline modules:

- `gui.api.incoming_frame("microcontrast_dehaze", frame, use_cached=True)`
- `gui.api.get_module_incoming_image("microcontrast_dehaze")`
- `gui.api.get_module_incoming_token("microcontrast_dehaze")`
- `gui.api.output_manual_from_module("microcontrast_dehaze", frame)`

Recommended pattern for similar modules:

1. Read incoming cached image for this module.
2. Apply manual processing from that cached source.
3. Output through `output_manual_from_module(...)` to run only downstream steps.

