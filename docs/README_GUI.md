# Main GUI (gui.py)

The main application is a **generic X-ray acquisition shell**. It provides the viewport, acquisition controls, dark/flat correction, banding and dead-pixel correction, and export. It does **not** talk to hardware directly; imaging and machine hardware are provided by **loadable modules** under `modules/`.

---

## 1. Structure

- **`XrayGUI`** – Single main class. Created in `app.py` and runs the DPG event loop.
- **Startup:** `__init__` discovers modules, loads settings (including integration time), then loads dark/flat for the **restored** integration time so they are correct from the first frame.
- **UI build:** `_build_ui()` creates the main window, texture, control panel, and calls into the **selected detector module** (frame size, Connection UI) and each **enabled machine module** (Faxitron, HV supply, etc.). Settings checkboxes for modules come from the **registry** (no hardcoding in gui.py).

---

## 2. Frame pipeline

All frames from the camera go through **`gui.api.submit_frame(frame)`** (which calls **`submit_raw_frame`** → **`_push_frame(frame)`**):

1. **Alteration pipeline** – For each enabled **image alteration** module (sorted by **`pipeline_slot`**), the app calls **`process_frame(frame, gui)`**. Slots: **Dark correction** (100), **Flat correction** (200), **Banding** (300), **Dead pixel** (400), **Pincushion** (450), **Mustache** (455), **Image Enhancement** (480), **Autocrop** (500), **Background separator** (600). The pipeline is stored as `(slot, module_name, process_frame)` tuples; the frame after the last step with slot &lt; 450 is saved as **`_frame_before_distortion`** for live preview (see below).
2. **Buffer** – Append result to `frame_buffer`, trim to `integration_n`, set **`display_frame = mean(buffer)`**.
3. **Display** – **`_render_tick`** uses **`new_frame_ready`**; when set, **`_update_display()`** paints **`display_frame`** (or raw/deconvolved snapshot) to the texture with windowing / histogram equalization.

**Live preview (distortion/crop/post-steps):** When modules with sliders call **`_refresh_distortion_preview()`** (e.g. pincushion, mustache, autocrop, background separator), the app re-runs only steps with **slot ≥ 450** on **`_frame_before_distortion`** and repaints, so you see the effect immediately without waiting for the next frame. Works only in live mode and after at least one frame has been received.

**Apply / Revert (alteration modules):** Many alteration modules expose “Apply automatically”, **Apply**, and **Revert**. **Revert** shows the frame before that module and runs the rest of the pipeline (skipping that module); **Apply** runs that module on the current incoming frame and then the rest of the pipeline. When either runs, the app updates the **pipeline module cache** for each downstream step, so **get_module_incoming_image** for later modules reflects the last manual or live run (e.g. after reverting dead_pixel, applying pincushion uses the reverted frame). So modules do not incorrectly re-apply each other.

**Important:** At **start of acquisition** the frame buffer is **cleared** so the display uses only frames from the current run (with current dark/flat). When opening a new TIFF in the Open Image module, the buffer is also cleared before submitting so one image is shown with dark/flat applied.

---

## 3. Acquisition flow

- User selects mode (combo) and clicks **Start**. `_cb_start` maps the combo label to a **mode_id** (`single`, `dual`, `continuous`, `capture_n`), sets `integration_time` and `integration_n` from UI, then calls **`_start_acquisition(mode_id)`**. (Dark and flat reference capture are triggered by Capture Dark / Capture Flat and run in the **dark_correction** and **flat_correction** modules, not as acquisition modes.)
- **`_start_acquisition(mode)`**:
  - If a **beam supply** is registered and wants Auto On/Off and is connected, and the current capture has not requested beam off (e.g. dark reference): set progress to “Waiting for supply…”, call **`beam_supply.turn_on_and_wait_ready(timeout_s=60)`**; on failure or timeout/abort, show status and return without starting.
  - Switch display to live, clear frame buffer and display for this run.
  - Set `acq_mode = mode`, clear `acq_stop`, then call **`camera_module.start_acquisition(self)`**.
- The **detector module** runs a worker thread that submits each frame via **`api.submit_frame(frame)`**. When the worker finishes it calls **`api.set_acquisition_idle()`**.
- **`_render_tick`** runs every frame: when transition from non-idle → idle is detected, it copies **`display_frame`** → **`last_captured_frame`** (for workflow modules that use **`request_integration`**); if beam supply wants Auto On/Off and **`workflow_keep_beam_on`** is false, it calls **`beam_supply.turn_off()`**. Progress bar, status line, and module tick callbacks are updated.

---

## 4. Dark and flat

- **Per integration time (gain and resolution-aware):** Master dark and flat are stored under `app/darks/<camera_name>/` and `app/flats/<camera_name>/` using names like **`dark_<time>_<gain>_<width>x<height>.npy`** and **`flat_<time>_<gain>_<width>x<height>.npy`** (legacy names without resolution are still supported). The app loads the nearest match for current integration time, gain, and frame size.
- **Load:** `_load_dark_field()` / `_load_flat_field()` use **`self.integration_time`** (and gain from the camera). They are called after **`_apply_loaded_settings`** at startup and when the user changes the integration time combo (**`_cb_integ_time_changed`**).
- **Capture:** When the user clicks **Capture Dark** or **Capture Flat**, the **dark_correction** or **flat_correction** module runs **`capture_dark(gui)`** / **`capture_flat(gui)`** (in a background thread). Those functions use **`api.request_n_frames_processed_up_to_slot()`** to get N frames run through the pipeline up to their slot, average them, set the app’s dark/flat, and save via the app’s save helpers.

---

## 5. Settings

- **Persistence:** `settings.json` in the app directory. **`load_settings(extra_keys=...)`** and **`save_settings(s, extra_keys=...)`** support module-specific keys from the registry.
- **Apply on startup:** **`_apply_loaded_settings(s)`** sets integration time, windowing, banding, module enable flags (`_module_enabled`), etc. Dark/flat are then loaded for the restored integration time.
- **Save:** **`_save_settings()`** builds a dict from UI (and from each enabled module’s **`get_settings_for_save()`**), merges module enable state from **`_module_enabled`**, and calls **`save_settings(s, extra_keys=self._extra_settings_keys)`**. The Settings window is closed with **`on_close`** so closing it triggers a save.

---

## 6. Key methods (reference)

| Method | Purpose |
|--------|--------|
| **`clear_frame_buffer()`** | Clear buffer and display; used at start of acquisition and when Open Image loads a new file. |
| **`submit_raw_frame(frame)`** | Entry point for each frame from the camera (modules call **`gui.api.submit_frame(frame)`**, which calls this). Runs `_push_frame` (full pipeline, buffer, display). |
| **`request_integration(num_frames, timeout_seconds=300.0)`** | For workflow modules (e.g. CT). Trigger one capture (same as Start/Capture N with current UI settings); block until idle; return processed frame (float32) or `None`. On `None`, see **`_last_integration_fail_reason`** (`"timeout"`, `"stopped"`, `"no_frame"`, etc.). Call from the workflow thread, not the main thread. |
| **`_push_frame(frame)`** | Run alteration pipeline (dark, flat, banding, dead pixel, pincushion, mustache, image enhancement, autocrop, background separator); store pre-distortion frame for slot ≥ 450; update buffer and `display_frame`. |
| **`_refresh_distortion_preview()`** | Re-run only steps with slot ≥ 450 on `_frame_before_distortion` and repaint; used by slider-driven post/distortion modules for live preview. |
| **`_start_acquisition(mode)`** | Optional beam supply turn-on + wait (unless **`workflow_keep_beam_on`**); clear buffer; set acq_mode and start detector module. |
| **`_render_tick()`** | Update display, progress, status; on acquisition end copy `display_frame` → `last_captured_frame`, then call beam_supply.turn_off() if Auto On/Off and not **`workflow_keep_beam_on`**; run module tick callbacks. |
| **`_load_dark_field()`** / **`_load_flat_field()`** | Load master dark/flat for current **`self.integration_time`** (and camera gain); nearest file by (time, gain). |
| **`_save_dark_field()`** / **`_save_flat_field()`** | Persist current dark/flat to `darks/<camera_name>/` and `flats/<camera_name>/` (called by dark_correction / flat_correction after capture). |

**Workflow hooks:** **`gui.workflow_keep_beam_on`** — when true, the app does not turn the beam on before each capture or off after each capture (used by CT “Keep HV on”). Set true at start of a multi-capture workflow and clear in `finally`; turn beam on/off yourself if needed. **`gui.last_captured_frame`** — set by the main thread when acquisition goes idle (processed frame); **`request_integration`** returns a copy of this after waiting for it.

---

## 7. Related documentation

- **Architecture (data flow, roles):** [ARCHITECTURE.md](ARCHITECTURE.md)
- **Code reference (entry points, module types, APIs):** [CODE_REFERENCE.md](CODE_REFERENCE.md) — start here if you are new to the codebase.
- **Camera and supply modules:** [modules/MODULES_OVERVIEW.md](modules/MODULES_OVERVIEW.md)
- **Camera module contract (detailed):** [modules/README_DETECTOR_MODULES.md](modules/README_DETECTOR_MODULES.md)
- **Per-module docs:** [modules/&lt;name&gt;/README.md](modules/) (e.g. ct_capture, asi_camera, esp_hv_supply).
