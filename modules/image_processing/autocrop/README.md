# Autocrop (image alteration module)

**Type:** Alteration  
**Pipeline slot:** 500 (runs last, after pincushion and mustache)  
**Purpose:** Crops the image to a rectangle (x_start, x_end, y_start, y_end). Applied at the end of the pipeline so it only affects the final view.

---

## Integration

- **process_frame(frame, gui) → frame:** Crops to `frame[y_start:y_end, x_start:x_end]`. If `x_end <= x_start` or `y_end <= y_start` (e.g. default 0,0,0,0), returns the frame unchanged (no crop). Bounds are clamped to the frame size.
- **State:** `gui.crop_x_start`, `gui.crop_x_end`, `gui.crop_y_start`, `gui.crop_y_end` are set in **build_ui** from loaded settings and updated by the module’s callbacks. No gui.py changes required.

---

## Settings

- **get_setting_keys():** Returns `["crop_x_start", "crop_x_end", "crop_y_start", "crop_y_end"]`.
- **MODULE_INFO:** `type`: `"alteration"`, `pipeline_slot`: 500, `default_enabled`: False.
- **get_settings_for_save(gui):** Returns `crop_x_start`, `crop_x_end`, `crop_y_start`, `crop_y_end` from widgets or from gui (as plain int for JSON).

---

## UI

- **build_ui(gui, parent_tag):** Collapsing header “Autocrop” with four integer inputs (width 80): X start, X end, Y start, Y end. Hint: “0,0,0,0 = no crop. Applied at end of pipeline (view only).”

---

## Live preview

- When you change any crop value, the callback calls **`gui._refresh_distortion_preview()`** (if present). The app re-runs autocrop (and any earlier distortion steps) on the last pre-distortion frame and repaints, so the crop updates immediately. Requires live mode and at least one frame received.

---

## See also

- [MODULES_OVERVIEW.md](../MODULES_OVERVIEW.md) – Pipeline and alteration module contract.
