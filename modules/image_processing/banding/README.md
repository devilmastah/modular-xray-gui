# Banding correction (image alteration module)

**Type:** Alteration  
**Pipeline slot:** 300 (after dark and flat)  
**Purpose:** Horizontal and/or vertical banding removal for sensors that exhibit row/column banding. Uses reference stripes (right columns for horizontal, bottom rows for vertical) and optional auto-optimization of the smooth window.

---

## Integration

- **process_frame(frame, gui) → frame:** Reads banding state from **`gui.api`** (e.g. `get_banding_enabled()`, `get_vertical_banding_first()`, `get_banding_optimized_win()`, `get_vertical_banding_optimized_win()`) and applies horizontal and/or vertical banding correction using the app’s **banding_correction** library. Caches optimized windows via **`api.set_banding_optimized_win()`** / **`api.set_vertical_banding_optimized_win()`** when auto-optimize is enabled.
- **State:** Banding parameters and caches live on the main app; the module uses the Application API for get/set. Load/save uses the same settings keys as before; the banding module builds the UI and runs the pipeline step.
- **build_ui(gui, parent_tag):** Builds the “Banding Correction” collapsing header with Enable, Auto-optimize, stripe width, smooth window, vertical options, and order. Callbacks are **`gui._cb_banding_*`** and **`gui._cb_vertical_banding_*`** so the main app keeps handling updates and `_save_settings()`.

---

## Settings

- **get_setting_keys():** Returns `[]`; banding keys are still read/written by the main app’s `_save_settings` / `_apply_loaded_settings` when the banding UI exists.
- **MODULE_INFO:** `type`: `"alteration"`, `pipeline_slot`: 300, `default_enabled`: True.

---

## Dependencies

- App’s **banding_correction** module (correct_banding, correct_vertical_banding, optimize_smooth_window, optimize_smooth_window_vertical). The banding alteration module adds the app directory to `sys.path` so the library can be imported.

---

## See also

- [MODULES_OVERVIEW.md](../MODULES_OVERVIEW.md) – Pipeline and alteration modules.
- [dark_correction](../dark_correction/README.md), [flat_correction](../flat_correction/README.md) – Earlier pipeline steps.
