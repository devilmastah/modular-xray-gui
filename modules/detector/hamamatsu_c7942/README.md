# Hamamatsu C7942 camera module

**Type:** Camera  
**Purpose:** Imaging source for the C7942 sensor via Teensy. Provides Connection UI and acquisition (**single**, **dual**, **continuous**, **capture N** only; dark and flat capture live in the dark_correction and flat_correction modules).

---

## Integration

- **Frame size:** `get_frame_size()` returns **`(2400, 2400)`** (matches `HamamatsuTeensy.FRAME_WIDTH/HEIGHT`).
- **Registration:** **`build_ui(gui, parent_tag)`** registers the module with **`gui.api.register_camera_module(mod)`**. On Connect the module sets **`gui.teensy`** to the Teensy driver so other modules (e.g. Faxitron) can use the same device.
- **Acquisition:** Worker thread triggers and reads frames via the Teensy, submits raw frames with **`api.submit_frame(frame)`**. Single, dual, continuous, and capture_n only; dark/flat reference capture is done by the dark_correction and flat_correction modules via the app API.

---

## UI

- **Connection (C7942):** Connect button and status text (`c7942_conn_status`). Connect instantiates **`HamamatsuTeensy()`**, pings, and assigns **`gui.teensy`**; Disconnect (on app exit) is handled via **`disconnect(gui)`**.

---

## Settings

- **`get_setting_keys()`:** Returns **`[]`** (no extra keys persisted).
- **MODULE_INFO:** `display_name`: "Hamamatsu C7942", `type`: "camera", `default_enabled`: True, **`camera_priority`**: 5 (so Open Image wins when both are enabled).

---

## Acquisition modes

| Mode ID    | Label        | Behavior |
|-----------|--------------|----------|
| `single`  | Single Shot  | One frame (clear, integrate, read, submit via **`api.submit_frame(frame)`**). |
| `dual`    | Dual Shot    | One integrated frame (same as single). |
| `continuous` | Continuous | Loop until **`api.acquisition_should_stop()`**. |
| `capture_n` | Capture N  | **`api.get_integration_frame_count()`** frames, then stop. |

Dark and flat reference capture are handled by the **dark_correction** and **flat_correction** modules (not the camera).

---

## Dependencies

- **`app.HamamatsuTeensy`** – Driver in the app package (sibling of `modules`).
- **`dead_pixel_correction.correct_dead_lines`** – Used when dead-line correction is enabled for dark/flat averaging.

---

## See also

- [README_DETECTOR_MODULES.md](../README_DETECTOR_MODULES.md) – Full detector module contract.
- [MODULES_OVERVIEW.md](../MODULES_OVERVIEW.md) – Camera vs supply modules and registry.
