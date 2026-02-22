# CT capture (workflow automation)

Multi-projection CT workflow: for each angle, (placeholder) rotate command, settle delay, then one integration (same as Start/Capture N), save TIFF. Uses the **Integration** section’s N frames; respects current ROI, dark/flat, and acq mode.

## Features

- **Number of projections** and **total rotation (degrees)**; step angle is computed.
- **Settle delay (ms)** after each (placeholder) rotate before capture.
- **Keep HV on** — beam supply stays on between projections (no per-capture on/off). Ignored when no beam supply module is loaded (acquisition runs normally).
- **Start CT scan** → **Abort CT scan** while running; abort requests stop at next projection.
- Saves to **`app/captures/<YYYY-MM-DD_HH-MM-SS>/0.tif`, `1.tif`, …** as **16-bit normalized TIFF** (full per-projection data range mapped to 0..65535).

## Requirements

- A **camera module** connected (e.g. ASI camera).
- **tifffile** (in `requirements.txt`).
- Motor/rotate is a **placeholder** until hardware is wired; replace **`_send_rotate_placeholder()`** in **`ct_capture/__init__.py`** with your controller call.

## Settings (persisted)

- `ct_num_projections`, `ct_total_rotation_deg`, `ct_settle_delay_ms`, `ct_keep_hv_on`

## Doc references

- Workflow automation contract: **modules/MODULES_OVERVIEW.md** § 5a.
- **request_integration** and **workflow_keep_beam_on**: **../docs/README_GUI.md** § 6.
