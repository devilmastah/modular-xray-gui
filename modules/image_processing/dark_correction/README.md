# Dark correction (image alteration module)

**Type:** Alteration  
**Pipeline slot:** 100 (runs first, before flat)  
**Purpose:** Subtracts the loaded dark field from each frame. Optional scale-matching when frame and dark have different value ranges (e.g. 16‑bit TIFF vs 12‑bit dark).

---

## Integration

- **process_frame(frame, gui) → frame:** If **`api.get_dark_field()`** is set and shape matches the frame, subtracts dark (with optional scaling so same-content images subtract to ~0). Otherwise returns the frame unchanged.
- Dark **data** and **load/save** stay in the main app (via API: `get_dark_field()`, etc.). **Capture Dark** is implemented in this module (**`capture_dark(gui)`**); the main GUI triggers it. This module performs the pipeline subtraction and owns dark reference capture.

---

## Settings

- **get_setting_keys():** Returns `[]`.  
- **MODULE_INFO:** `type`: `"alteration"`, `pipeline_slot`: 100, `default_enabled`: True.

---

## See also

- [MODULES_OVERVIEW.md](../MODULES_OVERVIEW.md) – Pipeline and alteration module contract.
- [flat_correction](../flat_correction/README.md) – Flat division (slot 200).
