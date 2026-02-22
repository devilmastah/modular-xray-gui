# Code reference – where to start

Short reference for developers: entry points, module types, and the **Application API** that all modules use to talk to the app.

**→ Intended architecture and data flow:** [ARCHITECTURE.md](ARCHITECTURE.md) — single responsibility, camera vs acquisition vs pipeline vs display, integration (N frames through full workflow), and exceptions (manual module actions + power supply).

---

## 1. Entry points and main files


| File / folder                     | Purpose                                                                                                                                            |
| --------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `**app.py`**                      | Application entry: creates **XrayGUI**, runs the Dear PyGui loop. Start the app from here.                                                         |
| `**gui.py`**                      | Main window and logic: **XrayGUI** class. Frame pipeline, acquisition, dark/flat, Settings, profiles. Creates `**gui.api`** (AppAPI).              |
| `**app_api.py**`                  | **Application API**: single facade for modules. Use `**gui.api.xxx()`** for all operations (frames, acquisition, settings, status, etc.).          |
| `**settings.py**`                 | Load/save **settings.json** and **profiles/**; used by the app and via `**gui.api.save_settings()`** / `**gui.api.get_loaded_settings()**`.        |
| `**modules/**`            | All loadable modules (cameras, supplies, corrections, workflows). The app **discovers** packages here; no need to edit **gui.py** to add a module. |
| `**modules/registry.py`** | **discover_modules()**, **get_module_info(name)**. Used by the app to build Settings checkboxes and load/save.                                     |


---

## 2. Module types (what you can add)

Each module is a **Python package** under `**modules/<type>/<name>/`** (e.g. `modules/camera/hamamatsu_dc5/`) with an `**__init__.py**` that defines at least `**MODULE_INFO**`.


| Type                    | `MODULE_INFO["type"]`   | Purpose                                                                                                                                                                                                | Docs                                                                 |
| ----------------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------- |
| **Detector**             | `"detector"`            | Imaging source (one active). Connect, acquire frames, submit via `**gui.api.submit_frame(frame)`**, register via `**gui.api.register_camera_module(mod)**`.                                            | [README_DETECTOR_MODULES.md](modules/README_DETECTOR_MODULES.md) |
| **Machine / supply**    | `"machine"`             | Optional hardware (HV, Faxitron, etc.). Can register `**gui.api.register_beam_supply(adapter)`** for Auto On/Off.                                                                                      | [MODULES_OVERVIEW.md](modules/MODULES_OVERVIEW.md) § 4       |
| **Workflow automation** | `"workflow_automation"` | Multi-step workflows (e.g. CT). Use `**gui.api.request_integration(num_frames)`** to run one capture and get the processed frame.                                                                      | [MODULES_OVERVIEW.md](modules/MODULES_OVERVIEW.md) § 5a      |
| **Image processing**    | `"image_processing"`     | Per-frame pipeline step (dark, flat, banding, dead pixel, pincushion, mustache, Image Enhancement, autocrop, background separator). `**pipeline_slot`** defines order. Use `**api.incoming_frame(module_name, frame)**` at entry and `**api.outgoing_frame(module_name, frame)**` at return for uniform module I/O. | [MODULES_OVERVIEW.md](modules/MODULES_OVERVIEW.md) § 5       |
| **Manual alteration**   | `"manual_alteration"`   | User-triggered actions on a static frame. Prefer module-cache pipeline helpers when available (see 4.9).                                                                                               | [MODULES_OVERVIEW.md](modules/MODULES_OVERVIEW.md) § 1       |


---

## 3. What every module can define


| Item                             | Required?                         | Description                                                                                                                                                                                                                                                                               |
| -------------------------------- | --------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `**MODULE_INFO`**                | Yes                               | Dict: `**display_name**`, `**description**`, `**type**` (see above), `**default_enabled**` (bool). Detector: `**camera_priority**`. Alteration: `**pipeline_slot**`.                                                                                                                        |
| `**get_setting_keys()**`         | No                                | Function returning list of setting keys to persist (e.g. `["ct_num_projections", "ct_keep_hv_on"]`).                                                                                                                                                                                      |
| `**get_settings_for_save(gui)**` | No                                | Return dict of current values. Prefer `**gui.api.get_module_settings_for_save(spec)**` with a list of `**(key, tag, converter, default)**` so the API auto-reads from DPG or loaded settings.                                                                                             |
| `**build_ui(gui, parent_tag)**`  | Yes for detector, machine, workflow | Add your UI under **parent_tag** (e.g. `"control_panel"`). Detector: call `**gui.api.register_camera_module(mod)`**. Beam supply: `**gui.api.register_beam_supply(adapter)**`. Use `**gui.api.get_setting(key, default)**` for widget defaults, `**gui.api.save_settings()**` in callbacks. |


---

## 4. Application API (`gui.api`)

All modules should use `**gui.api**` (defined in `**app_api.py**`) for every operation. The table below is a quick index; see `**app_api.py**` for full docstrings and signatures.

### 4.1 Frames


| Method                                                       | Use case                                                                                                                   |
| ------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------- |
| `**submit_frame(frame)**`                                    | Camera: submit one raw frame (float32, H×W). App runs pipeline, buffer, display. Call from acquisition thread.             |
| `**clear_frame_buffer()**`                                   | Clear buffer and display so the next submitted frame(s) are the only content.                                              |
| `**request_integration(num_frames, timeout_seconds=300.0)**` | Workflow: run one capture (same as Start/Capture N). Blocks; returns processed frame or `None`. Call from workflow thread. |
| `**get_last_integration_fail_reason()**`                     | After **request_integration** returns `None`: `"timeout"`, `"stopped"`, `"no_frame"`, `"not_connected"`, `"not_idle"`, `"supply_not_connected"`. |


### 4.2 Acquisition (camera worker)


| Method                                                                      | Use case                                                                               |
| --------------------------------------------------------------------------- | -------------------------------------------------------------------------------------- |
| `**acquisition_should_stop()`**                                             | `True` if user requested stop. Check in your acquisition loop.                         |
| `**get_acquisition_mode()**`                                                | Current mode: `"single"`, `"dual"`, `"continuous"`, `"capture_n"`, `"dark"`, `"flat"`. |
| `**get_integration_time_seconds()**`                                        | Exposure/integration time (seconds).                                                   |
| `**get_integration_frame_count()**`                                         | Number of frames to stack (Capture N).                                                 |
| `**get_dark_capture_stack_count()**` / `**get_flat_capture_stack_count()**` | Frames to average for dark/flat capture.                                               |
| `**get_camera_uses_dual_shot_for_capture_n()**`                             | `True` if the active camera does 2 exposures per frame in capture_n (e.g. C7942). Dark/flat modules use this to double the capture timeout. |
| `**set_acquisition_idle()**`                                                | Call when your acquisition worker has finished (sets mode idle, clears progress).      |
| `**set_acquisition_thread(thread)**`                                        | Set the current acquisition thread (app joins on exit).                                |
| `**clear_acquisition_stop_flag()**`                                         | Clear stop event before starting acquisition.                                          |
| `**signal_acquisition_stop()**`                                             | Request the acquisition worker to stop.                                                |


### 4.3 Progress and status


| Method                               | Use case                                          |
| ------------------------------------ | ------------------------------------------------- |
| `**set_progress(value, text=None)**` | Progress bar (0.0–1.0) and optional overlay text. |
| `**set_status_message(msg)**`        | Set the main status bar message.                  |


### 4.4 Dark / flat


| Method                                                | Use case                                                                                                |
| ----------------------------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `**get_dark_field()**` / `**get_flat_field()**`       | Current dark/flat reference (or `None`).                                                                |
| `**set_dark_field(arr)**` / `**set_flat_field(arr)**` | Set reference (e.g. after dark/flat capture). Use from worker; API uses frame_lock.                     |
| `**save_dark_field()**` / `**save_flat_field()**`     | Persist current dark/flat to disk (after set_*).                                                        |
| `**trigger_dark_flat_reload()*`*                      | Reload dark/flat nearest match (e.g. after ROI or gain change).                                         |
| `**get_frame_size()**`                                | `(width, height)` of current frame.                                                                     |
| `**set_frame_size(width, height)**`                   | Camera calls on connect or ROI change.                                                                  |
| `**get_frame_lock()**`                                | Lock to hold when reading/writing dark/flat from a worker (if not using set_dark_field/set_flat_field). |


### 4.5 Dead pixel


| Method                                | Use case                                             |
| ------------------------------------- | ---------------------------------------------------- |
| `**dead_pixel_correction_enabled()**` | Whether dead-line correction is enabled.             |
| `**get_dead_pixel_lines()**`          | `(vertical_lines, horizontal_lines)` for correction. |


### 4.6 Settings


| Method                                   | Use case                                                                                                                                           |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| `**get_loaded_settings()**`              | Loaded settings dict (for default values in **build_ui**).                                                                                         |
| `**get_setting(key, default)`**          | Single key from loaded settings (shorthand for **build_ui** default_value).                                                                        |
| `**get_module_settings_for_save(spec)`** | Build settings dict from DPG or loaded settings. **spec** = list of `**(key, tag, converter, default)`**; auto fallback when widget doesn’t exist. |
| `**save_settings()**`                    | Persist current UI state to **settings.json** (call when user changes something).                                                                  |


### 4.7 Workflow and registration


| Method                                 | Use case                                                                       |
| -------------------------------------- | ------------------------------------------------------------------------------ |
| `**set_workflow_keep_beam_on(value)`** | When `True`, app does not turn beam on/off per capture (e.g. CT “Keep HV on”). |
| `**get_beam_supply()**`                | Optional beam supply adapter (turn_on_and_wait_ready, turn_off, etc.).         |
| `**get_camera_module()**`              | Current camera module (or `None`).                                             |
| `**is_camera_connected()**`            | `True` if a camera module is loaded and connected.                             |
| `**register_camera_module(module)**`   | Camera module calls this in **build_ui** after creating its UI.                |
| `**register_beam_supply(adapter)`**    | Beam supply module calls this to enable Auto On/Off.                           |


### 4.8 Module load state


| Method                                                                                | Use case                                                                                                                  |
| ------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------- |
| `**is_module_loaded(module_name)**`                                                   | `True` if the module is enabled in Settings (e.g. `"pincushion"`, `"banding"`).                                           |
| `**warn_if_option_used_but_module_not_loaded(module_name, option_description=None)**` | Call before using an option that depends on a module. Returns `False` if not loaded (and sets a one-time status warning). |
| `**warn_about_unloaded_options_with_saved_values()**`                                 | Called by the app once after building the pipeline; warns if saved values exist but the module is disabled.               |


### 4.9 Display + Manual Pipeline Helpers


| Method                                                             | Use case                                                                               |
| ------------------------------------------------------------------ | -------------------------------------------------------------------------------------- |
| `**get_current_display_frame()**`                                  | Current frame shown in the display (for “Apply to frame”).                             |
| `**paint_frame_to_display(frame)**`                                | Paint a frame to the texture (e.g. after deconvolution).                               |
| `**show_preview_in_main_view(frame, use_histogram=True)**`         | Show a preview in the main window. **use_histogram=True**: apply windowing/histogram/hist eq. **False**: raw (scale to fit, frame min/max); use for masks to avoid washed-out look. New frames do not overwrite until **clear_main_view_preview()**. |
| `**clear_main_view_preview()**`                                    | Leave preview mode and restore the normal display (live/raw/deconvolved).             |
| `**refresh_display()**`                                            | Force display refresh.                                                                 |
| `**set_display_mode(mode)**`                                       | `'live'`, `'raw'`, `'deconvolved'`, etc.                                               |
| `**get_deconv_sigma()**` / `**get_deconv_iterations()**`           | Deconvolution parameters.                                                              |
| `**set_deconv_sigma(value)**` / `**set_deconv_iterations(value)**` | Set deconvolution parameters.                                                          |
| `**get_module_incoming_image(module_name)**`                       | Cached incoming image for that module (updated by live pipeline and by **output_manual_from_module** when continuation runs, so Apply/Revert see correct upstream state). |
| `**get_module_incoming_token(module_name)**`                       | Token for cached incoming image; changes each new frame.                               |
| `**incoming_frame(module_name, frame, use_cached=False)**`         | Canonical module input. `use_cached=True` prefers cached incoming image when available. |
| `**outgoing_frame(module_name, frame)**`                           | Canonical module output. Return through this from `process_frame(...)`.                |
| `**continue_pipeline_from_slot(frame, start_slot_exclusive)**`     | Run remaining alteration steps after a slot; updates **\_pipeline_module_cache** for each step so **get_module_incoming_image** stays correct. |
| `**continue_pipeline_from_module(module_name, frame)**`            | Run remaining alteration steps after a module (uses **continue_pipeline_from_slot**). |
| `**output_manual_from_module(module_name, frame)**`                | Run pipeline from the next module onward and paint result; updates cache for downstream modules (so later Apply/Revert use correct incoming frames). |
| `**build_alteration_apply_revert_ui(gui, module_name, apply_callback, auto_apply_attr=..., revert_snapshot_attr=None, default_auto_apply=True)**` | Add “Apply automatically” checkbox and Apply/Revert buttons (and separator). Call **first** in **build_ui**. **apply_callback(gui)** should get incoming image, store snapshot if needed, run your step, then **output_manual_from_module**. |
| `**alteration_auto_apply(gui, auto_apply_attr, default=True)**`     | Whether to run the alteration in **process_frame** (guard: if **False**, return frame unchanged). |


### 4.10 Pipeline state (alteration modules)

`**process_frame(frame, gui)**` receives **gui**; use `**api = gui.api`** and then:

- `**frame = api.incoming_frame(MODULE_NAME, frame)**` on entry.
- If using the shared Apply/Revert UI, guard with `**if not api.alteration_auto_apply(gui, "your_auto_apply_attr", default=True): return api.outgoing_frame(MODULE_NAME, frame)**` so the step is skipped when “Apply automatically” is off.
- Process your algorithm.
- `**return api.outgoing_frame(MODULE_NAME, frame_out)**` on exit.
- If module manual actions need fixed source caching, use `**api.get_module_incoming_image(MODULE_NAME)**` (and **output_manual_from_module**; the continuation updates the cache so downstream modules see the correct incoming frames).


| Method                                                                                                                                                         | Use case                                           |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------- |
| `**get_banding_enabled()**`, `**get_vertical_banding_enabled()**`, `**get_vertical_banding_first()**`                                                          | Banding on/off and order.                          |
| `**get_vertical_stripe_h()**`, `**get_vertical_smooth_win()**`, `**get_banding_smooth_win()**`, `**get_banding_black_w()**`                                    | Banding parameters.                                |
| `**get_banding_auto_optimize()**`, `**get_vertical_banding_auto_optimize()**`                                                                                  | Auto-optimize flags.                               |
| `**get_banding_optimized_win()**`, `**set_banding_optimized_win(v)**`, `**get_vertical_banding_optimized_win()**`, `**set_vertical_banding_optimized_win(v)**` | Banding pipeline cache (optimized smooth windows). |
| `**get_crop_region()**`                                                                                                                                        | `(x_start, y_start, x_end, y_end)` for autocrop.   |
| `**get_pincushion_params()**`                                                                                                                                  | `(strength, center_x, center_y)`.                  |
| `**get_mustache_params()**`                                                                                                                                    | `(k1, k2, center_x, center_y)`.                    |


### 4.11 Internal ref (callbacks that must be gui methods)


| Property      | Use case                                                                                                                                                       |
| ------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `**api.gui**` | Raw **gui** reference. Use when a DPG callback must be bound to a **gui** method (e.g. `**api.gui._cb_banding_enabled`**). Prefer API methods everywhere else. |


---

## 5. Adding a new module (minimal checklist)

1. Create `**modules/<name>/__init__.py**`.
2. Set `**MODULE_INFO = {"display_name": "...", "description": "...", "type": "...", "default_enabled": False}**`.
3. If you need persisted settings: implement `**get_setting_keys()**` and `**get_settings_for_save(gui)**`. Prefer `**return gui.api.get_module_settings_for_save([(key, tag, converter, default), ...])**` so the app auto-handles “widget exists vs. loaded settings”.
4. Implement `**build_ui(gui, parent_tag)**`. For camera: also `**get_frame_size()**` and an object with **start_acquisition(gui)**, **stop_acquisition(gui)**, **is_connected()**, **disconnect(gui)**, **get_acquisition_modes()**; then call `**gui.api.register_camera_module(mod)`**. For beam supply: call `**gui.api.register_beam_supply(adapter)**`. Use `**gui.api.get_setting(key, default)**` for widget default values and `**gui.api.save_settings()**` in callbacks.
5. Enable the module in **Settings → Load **** module** and restart.

No edits to **gui.py** or **settings.DEFAULTS** are required; the registry picks up the new package.

---

## 6. Where to read more

- **Intended design (architecture, data flow, single responsibility):** [ARCHITECTURE.md](ARCHITECTURE.md)
- **Full API implementation and docstrings:** `**app_api.py`**
- **Module types and discovery:** [modules/MODULES_OVERVIEW.md](modules/MODULES_OVERVIEW.md)
- **Detector contract (threading, modes; dark/flat in image processing modules):** [modules/README_DETECTOR_MODULES.md](modules/README_DETECTOR_MODULES.md)
- **Main GUI (pipeline, acquisition, methods):** [README_GUI.md](README_GUI.md)
- **Per-module:** `modules/<name>/README.md` (e.g. **ct_capture**, **asi_camera**, **esp_hv_supply**).

