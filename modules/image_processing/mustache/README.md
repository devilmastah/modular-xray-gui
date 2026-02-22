# Mustache correction (image alteration module)

**Type:** Alteration  
**Pipeline slot:** 455 (after pincushion, before autocrop)  
**Purpose:** Corrects mustache (moustache) distortion: radial model with **k1·r² + k2·r⁴**. Using opposite signs for k1 and k2 gives the typical S-shaped (barrel + pincushion) correction. Center X/Y are saved; use **-1** for frame center.

---

## Integration

- **process_frame(frame, gui) → frame:** Radial remap with `r_src = r / (1 + k1*r_norm² + k2*r_norm⁴)`. Center from `mustache_center_x`, `mustache_center_y`; if either < 0, uses frame center.
- **State:** `gui.mustache_k1`, `gui.mustache_k2`, `gui.mustache_center_x`, `gui.mustache_center_y` set in **build_ui** and by the module callback. No gui.py changes.

---

## Settings

- **get_setting_keys():** Returns `["mustache_k1", "mustache_k2", "mustache_center_x", "mustache_center_y"]`.
- **MODULE_INFO:** `type`: `"alteration"`, `pipeline_slot`: 455, `default_enabled`: False.
- **get_settings_for_save(gui):** Returns k1, k2 and center X/Y from widgets or gui.

---

## UI

- **build_ui(gui, parent_tag):** Collapsing header “Mustache correction” with k1 and k2 sliders (-0.5 to 0.5), Center X and Center Y inputs. Hint: “k1*r^2 + k2*r^4. Opposite signs = mustache (S-shape). Center -1 = frame center.”

---

## Live preview

- When you change k1, k2, or Center X/Y, the callback calls **`gui._refresh_distortion_preview()`** (if present). The app re-runs mustache (and autocrop) on the last pre-distortion frame and repaints for immediate feedback. Requires live mode and at least one frame received.

---

## Dependencies

- **scipy** (for `scipy.ndimage.map_coordinates`). Already in app requirements.

---

## See also

- [MODULES_OVERVIEW.md](../MODULES_OVERVIEW.md) – Pipeline and alteration module contract.
- [pincushion](../pincushion/README.md) – Simpler radial correction (slot 450).
