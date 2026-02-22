# Flat correction (image alteration module)

**Type:** Alteration  
**Pipeline slot:** 200 (runs after dark)  
**Purpose:** Divides each frame by the loaded flat field (normalized) to correct vignetting and sensitivity. Flat is captured after dark correction, so normalization uses flat as stored (no extra dark subtraction in this step).

---

## Integration

- **process_frame(frame, gui) → frame:** If **`api.get_flat_field()`** is set, divides frame by normalized flat (with divide-by-zero protection and safe clipping). Otherwise returns frame unchanged.
- Flat **data** and **load/save** stay in the main app (via API). **Capture Flat** is implemented in this module (**`capture_flat(gui)`**); the main GUI triggers it. This module performs the pipeline division and owns flat reference capture.

---

## Settings

- **get_setting_keys():** Returns `[]`.  
- **MODULE_INFO:** `type`: `"alteration"`, `pipeline_slot`: 200, `default_enabled`: True.

---

## See also

- [MODULES_OVERVIEW.md](../MODULES_OVERVIEW.md) – Pipeline and alteration module contract.
- [dark_correction](../dark_correction/README.md) – Dark subtraction (slot 100).
