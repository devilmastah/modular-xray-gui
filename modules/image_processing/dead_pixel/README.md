# Dead pixel correction (image alteration module)

**Type:** Alteration  
**Pipeline slot:** 400 (after banding)  
**Purpose:** Correct dead vertical and horizontal lines by interpolating from neighboring pixels. State and settings stay on the main app (gui); this module provides process_frame and build_ui.

---

## Integration

- **process_frame(frame, gui):** When **gui.dead_lines_enabled** is true and at least one of **gui.dead_vertical_lines** / **gui.dead_horizontal_lines** is non-empty, calls the app’s **correct_dead_lines** and returns the corrected frame; otherwise returns the frame unchanged.
- **build_ui(gui, parent_tag):** Builds the “Dead Pixel Lines” collapsing header (Enable, vertical/horizontal line inputs). Callbacks live in the module (update gui state and call **gui._save_settings()**); no gui.py changes required.
- **get_settings_for_save(gui):** Returns **dead_lines_enabled**, **dead_vertical_lines** (string), **dead_horizontal_lines** (string) from DPG or from gui when the module’s UI is not built.

---

## Settings

- **get_setting_keys():** Returns `[]` (keys are in app **settings.DEFAULTS**).
- **MODULE_INFO:** type **"alteration"**, **pipeline_slot** 400, default_enabled True.

---

## Dependencies

- App’s **dead_pixel_correction** module (**correct_dead_lines**).

---

## See also

- [MODULES_OVERVIEW.md](../MODULES_OVERVIEW.md) – Pipeline and alteration modules.
- [banding](../banding/README.md) – Previous pipeline step (slot 300).
