# Open image camera module

**Type:** Camera  
**Purpose:** Load a TIFF (or image) file and submit it as a frame. No hardware; useful to test the camera module contract and run the display pipeline (dark/flat, banding, dead lines, export) on file-based images.

---

## Integration

- **Frame size:** **`get_frame_size()`** returns **`(2400, 2400)`** (same as C7942 for texture consistency). Loaded images are **resized** to this size if needed.
- **Registration:** **`build_ui(gui, parent_tag)`** sets **`gui.camera_module`** to an **`OpenImageModule`** instance.
- **“Acquisition”:** Single Shot loads the current file (or prompts for one), passes it through the pipeline once. The module calls **`gui.clear_frame_buffer()`** before submitting so the display shows only that image (with dark/flat applied).

---

## UI

- **Open image:** Collapsing header with “Open image…” button and optional path/label. File dialog selects a TIFF (or image); the file is loaded as float32 **(H, W)**, resized to **FRAME_H × FRAME_W** if necessary, and stored as the “current image.” Connection status reflects whether an image is loaded.
- **Single Shot:** When Start → Single Shot is used, the module submits the current image once (after clearing the frame buffer). If no image is loaded, it can prompt to open one.

---

## Settings

- **`get_setting_keys()`:** Returns **`[]`**.
- **MODULE_INFO:** `display_name`: "Open image", `type`: "camera", `default_enabled`: False, **`camera_priority`**: 10 (higher than C7942; when both are enabled, Open Image is selected as the active camera).

---

## Acquisition modes

Only **Single Shot** is exposed. Other modes (dual, continuous, capture N) are not implemented for this test module.

---

## Special behavior

- **Buffer clear:** When a new image is loaded (or when Single Shot runs), the module calls **`gui.clear_frame_buffer()`** so the display shows only the submitted frame(s) with current dark/flat and corrections.
- **Resize:** Images with different dimensions are resized to 2400×2400 (e.g. via `skimage.transform.resize` or `scipy.ndimage.zoom`). Load supports TIFF (tifffile) or PIL fallback.

---

## See also

- [README_DETECTOR_MODULES.md](../README_DETECTOR_MODULES.md) – Detector module contract.
- [MODULES_OVERVIEW.md](../MODULES_OVERVIEW.md) – Module discovery and camera vs supply.
