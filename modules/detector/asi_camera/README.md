# ASI camera capture module

**Type:** Camera  
**Purpose:** Imaging source for ZWO ASI cameras via **zwoasi**. Exposes **gain** (0–600) and **exposure** (10 ms–5 s via the app’s integration-time dropdown). Supports **single**, **dual**, **continuous**, and **capture N** only (no dark/flat; dark and flat capture live in the dark_correction and flat_correction modules).

---

## Integration

- **Frame size:** `get_frame_size()` returns **`(1920, 1080)`** before connect; after Connect the actual size is the current **ROI** (region of interest). The UI provides **X start, X end, Y start, Y end** to set the ROI; the SDK constrains width to a multiple of 8 and height to a multiple of 2.
- **Integration choices:** The module implements **`get_integration_choices()`** so the main app’s integration-time combo shows: **0.01 s, 0.05 s, 0.1 s, 0.25 s, 0.5 s, 1 s, 2 s, 5 s** (10 ms–5000 ms). Exposure is applied to the ASI SDK using **`api.get_integration_time_seconds()`**.
- **Registration:** **`build_ui(gui, parent_tag)`** registers the module with **`gui.api.register_camera_module(mod)`**.
- **Acquisition:** Worker uses **zwoasi**: sets exposure and gain, captures frames, submits raw float32 via **`api.submit_frame(frame)`**. Single, dual, continuous, and capture_n only; dark/flat reference capture is done by the dark_correction and flat_correction modules via the app API.

---

## UI

- **Connection (ASI camera capture):** Connect, Disconnect, status text (e.g. **Connected (1920×1080) (full 3840×2160)**), **Gain** slider (0–600), **ROI** inputs (**X start**, **X end**, **Y start**, **Y end**) when connected. Integration time is in the main app’s Acquisition section (dropdown filled from **`get_integration_choices()`**).

---

## Settings

- **`get_setting_keys()`:** Returns **`["asi_gain", "asi_x_start", "asi_x_end", "asi_y_start", "asi_y_end"]`**.
- **`get_settings_for_save(gui)`:** Returns **`asi_gain`** and the four ROI values from the widgets (or from **`gui`** when the module UI is not built).
- **MODULE_INFO:** `display_name`: "ASI camera capture", `type`: "camera", `default_enabled`: False, **`camera_priority`**: 8.

---

## Acquisition modes

| Mode ID    | Label        | Behavior |
|-----------|--------------|----------|
| `single`  | Single Shot  | One frame at current exposure/gain, submit via **`api.submit_frame(frame)`**. |
| `dual`    | Dual Shot    | One frame (same as single). |
| `continuous` | Continuous | Loop capture and submit until **`api.acquisition_should_stop()`**. |
| `capture_n` | Capture N  | **`api.get_integration_frame_count()`** frames, each submitted. |

Dark and flat reference capture are handled by the **dark_correction** and **flat_correction** modules (not the camera).

---

## Dependencies

- **zwoasi** – Python bindings for the ZWO ASI SDK. Install with: `pip install zwoasi`.
- **ZWO ASI SDK** – Native library; set **`ASI_SDK_PATH`** to the SDK directory if the library is not on the system path.

---

## See also

- [README_DETECTOR_MODULES.md](../README_DETECTOR_MODULES.md) – Full detector module contract.
- [MODULES_OVERVIEW.md](../MODULES_OVERVIEW.md) – Camera vs supply modules and registry.
