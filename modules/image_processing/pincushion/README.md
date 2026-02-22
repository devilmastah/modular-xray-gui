# Pincushion correction (image alteration module)

**Type:** Alteration  
**Pipeline slot:** 450 (after dead pixel, before autocrop)  
**Purpose:** Corrects pincushion (radial) distortion. Center of distortion is set by **Center X** and **Center Y** (pixels); use **-1** for frame center. No dependency on other modules.

---

## Integration

- **process_frame(frame, gui) → frame:** Applies radial remap: for each output pixel at radius r from center, samples from radius `r_src = r / (1 + k * r_norm^2)`. Center (cx, cy) = `gui.pincushion_center_x`, `gui.pincushion_center_y`; if either &lt; 0, uses frame center `(width/2, height/2)`.
- **State:** `gui.pincushion_strength`, `gui.pincushion_center_x`, `gui.pincushion_center_y` set in **build_ui** and by the module callback. No gui.py changes required.

---

## Settings

- **get_setting_keys():** Returns `["pincushion_strength", "pincushion_center_x", "pincushion_center_y"]`.
- **MODULE_INFO:** `type`: `"alteration"`, `pipeline_slot`: 450, `default_enabled`: False.
- **get_settings_for_save(gui):** Returns strength and center X/Y from widgets or gui.

---

## UI

- **build_ui(gui, parent_tag):** Collapsing header “Pincushion correction” with Strength slider, Center X and Center Y (float inputs). Hint: “Center X/Y in pixels. Use -1 for frame center. Positive strength = pincushion correction.”

---

## Live preview

- When you change Strength or Center X/Y, the callback calls **`gui._refresh_distortion_preview()`** (if present). The app re-runs pincushion, mustache, and autocrop on the last pre-distortion frame and repaints, so you see the effect immediately. Requires live mode and at least one frame received.

---

## Dependencies

- **scipy** (for `scipy.ndimage.map_coordinates`). Already in app requirements.

---

## See also

- [MODULES_OVERVIEW.md](../MODULES_OVERVIEW.md) – Pipeline and alteration module contract.
