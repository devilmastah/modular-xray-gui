# Detector and supply modules – overview

This document explains how **detector modules** and **supply (machine) modules** work and how they integrate with the main application. For the full **detector module contract** (API, threading, acquisition-only; dark/flat are in image processing modules), see **[README_DETECTOR_MODULES.md](README_DETECTOR_MODULES.md)**.

---

## 1. Module types

- **Detector module** – Provides the imaging source. Exactly one detector module is active at a time. It:
  - Reports frame size and builds the Connection UI.
  - Implements **acquisition only** (single, dual, continuous, capture N) and submits raw frames via **`gui.api.submit_frame(frame)`**. Dark and flat reference capture are **not** done by the detector; they are done by the **dark_correction** and **flat_correction** image processing modules.
- **Machine / supply module** – Optional hardware (Faxitron, HV supply, etc.). Multiple can be enabled. Some can optionally register as **`gui.beam_supply`** for **Auto On/Off** (turn on before acquisition, turn off when acquisition ends).
- **Workflow automation module** – Optional multi-step workflows (e.g. CT capture). Type **`"workflow_automation"`**. The app builds their UI in the control panel when enabled (after machine modules). They use **`gui.api.request_integration(num_frames)`** to trigger one capture (same as Start/Capture N) and get the processed frame; optional **`gui.workflow_keep_beam_on`** keeps the beam supply on between captures. See **§ 5a** below.
- **Image processing module** – Optional steps in the frame pipeline (e.g. dark subtract, flat divide). Each has a **`pipeline_slot`** (e.g. 100, 200); enabled image processing modules are run in slot order. See **§ 5** below.
- **Manual alteration module** – User-triggered image operations. Not in the per-frame pipeline; the user clicks a button to apply.
- **Hybrid image processing module** – A normal per-frame image processing module can also expose manual controls (e.g. Image Enhancement). For manual actions, use module incoming-frame cache + downstream-output API helpers to avoid double application.

The main app does **not** hardcode module names. It discovers packages under **`modules/`** via **`registry.discover_modules()`** and uses **`MODULE_INFO`** and **`get_setting_keys()`** from each package to build Settings checkboxes and load/save.

---

## 2. Discovery and settings (registry)

- **Discovery:** Registry discovers leaf packages under `modules/detector/`, `modules/machine/`, `modules/image_processing/`, `modules/workflow_automation/` (e.g. `modules.detector.hamamatsu_dc5`, `modules.machine.faxitron_mx20_dx50`). Each module dict has **`name`** and **`import_path`** for loading.
- **Per-module metadata:** `registry.get_module_info(import_path)` imports by `import_path` and returns:
  - **`MODULE_INFO`** (dict): `display_name`, `description`, `type` (`"detector"`, `"machine"`, `"image_processing"`, `"manual_alteration"`, or **`"workflow_automation"`**), `default_enabled`. For `type == "detector"` also **`camera_priority`**. For `type == "image_processing"` also **`pipeline_slot`** (int; order in the image pipeline).
  - **`get_setting_keys()`** (function): list of setting keys the module persists (e.g. port, voltage). Used for **`load_settings(extra_keys=...)`** and **`save_settings(..., extra_keys=...)`** without editing `settings.DEFAULTS`.
- **Settings UI:** The main app builds a “Load &lt;display_name&gt; module” checkbox per discovered module. Stored key: **`load_<name>_module`** (e.g. `load_hamamatsu_c7942_module`).
- **Apply / save:** On startup, **`_apply_loaded_settings`** sets integration time, banding, etc., and **`_module_enabled[name]`** from the loaded settings. Dark and flat **reference images** are then loaded from disk (per integration time / gain) for use by the dark_correction and flat_correction pipeline steps. When saving, the app calls **`get_settings_for_save(gui)`** for **every** discovered module and merges the returned dict into the settings.
- **Module settings API (recommended):** Use **`gui.api.get_setting(key, default)`** for default values when building UI (replaces **`gui.api.get_loaded_settings().get(key, default)`**). Use **`gui.api.get_module_settings_for_save(spec)`** for **`get_settings_for_save`**: pass a list of **`(key, tag, converter, default)`**; the API reads from DPG when the widget exists and from loaded settings otherwise, so saved values are never overwritten when the module’s UI is not built. Example: **`return gui.api.get_module_settings_for_save([("asi_gain", "asi_gain_slider", int, 50), ...])`**. **`all_extra_settings_keys(modules)`** gives the full set of extra keys for load/save.

**Adding a new module:** Put a package under `modules/<type>/<name>/` (e.g. `modules/detector/my_detector/`) with **`MODULE_INFO`** and (if needed) **`get_setting_keys()`**. No changes in **gui.py** or **settings.DEFAULTS** are required.

---

## 3. Detector modules (summary)

- **Selection:** The app picks the **enabled** detector module with the **highest** **`camera_priority`**. It calls **`get_frame_size()`** and **`build_ui(gui, parent_tag)`** for that module only.
- **Contract:** The module must:
  - Implement **`get_frame_size() -> (width, height)`** and **`build_ui(gui, parent_tag)`**.
  - Set **`gui.camera_module`** to an instance that implements: **`get_acquisition_modes()`**, **`is_connected()`**, **`start_acquisition(gui)`**, **`stop_acquisition(gui)`**, **`disconnect(gui)`**.
- **Acquisition:** When the user clicks Start, the app sets **`gui.acq_mode`**, **`gui.integration_time`**, **`gui.integration_n`**, and (when capturing dark/flat reference) **`gui._dark_stack_n`** / **`gui._flat_stack_n`**, then calls **`camera_module.start_acquisition(gui)`**. The **detector** starts a daemon thread that only acquires frames and calls **`gui.api.submit_frame(frame)`** for each. The detector does **not** perform dark/flat reference capture; when mode is dark or flat, the **dark_correction** or **flat_correction** image processing module runs the capture (collects frames via the app pipeline and saves the reference). The module sets **`gui.acq_thread`** and on finish **`gui.acq_mode = "idle"`**.
- **Stopping:** The app calls **`stop_acquisition(gui)`**; the module must call **`gui.api.signal_acquisition_stop()`** (or **`gui.acq_stop.set()`**) so the worker exits.

Full API, threading, and checklist: **[README_DETECTOR_MODULES.md](README_DETECTOR_MODULES.md)**.

---

## 4. Supply modules and the beam_supply contract

**Machine modules** (Faxitron, ESP HV supply, Example supply) add their own UI (e.g. voltage, Connect). Some can optionally act as the **beam supply** for acquisition:

- **Purpose:** Turn the beam (e.g. X-ray tube HV) **on** before acquisition and **off** when acquisition ends, so the user does not have to manually enable/disable the source.
- **Registration:** A module sets **`gui.beam_supply = <adapter>`** in its **`build_ui(gui, parent_tag)`**. Only one adapter should be set; typically the last-built supply module that supports Auto On/Off “wins”.

**Beam supply adapter contract** – The object assigned to **`gui.beam_supply`** must provide:

| Method | Description |
|--------|-------------|
| **`wants_auto_on_off() -> bool`** | Whether Auto On/Off is enabled (e.g. from a checkbox in the module UI). |
| **`is_connected() -> bool`** | Whether the device is connected. |
| **`turn_on_and_wait_ready(timeout_s: float) -> bool`** | Turn on and block until “ready” (e.g. HV stable). Return `True` on success, `False` on failure or timeout. The main app uses this **before** starting the camera acquisition when the user clicks Start (and only when mode is **not** `"dark"`). |
| **`turn_off() -> None`** | Turn off the beam. The main app calls this when acquisition transitions to **idle** (e.g. single shot finished, or user clicked Stop). |

**Main app behavior:**

- **Start (non-dark):** If **`beam_supply`** is set, **`wants_auto_on_off()`** and **`is_connected()`** are true, and mode ≠ `"dark"`: the app shows “Waiting for supply…” and calls **`turn_on_and_wait_ready(timeout_s=60)`** — **unless** **`gui.workflow_keep_beam_on`** is true (used by workflow modules to keep HV on between captures).
- **Dark capture:** The app **never** turns on the beam for dark; the supply is not called for `mode == "dark"`.
- **End of acquisition:** In **`_render_tick`**, when **`acq_mode`** transitions from non-idle to **`"idle"`**, if **`beam_supply`** is set and **`wants_auto_on_off()`** and **`is_connected()`**, the app calls **`beam_supply.turn_off()`** — **unless** **`gui.workflow_keep_beam_on`** is true. Workflow modules set this flag for the duration of a multi-capture run and clear it in a `finally` when done (and turn off the beam themselves if they turned it on).

Modules that do **not** implement this contract (e.g. Faxitron, which only does manual Expose) do not set **`gui.beam_supply`**; they remain compatible with any module that does (e.g. ESP HV supply or Example supply).

---

## 5a. Workflow automation modules

- **Discovery:** Same registry; **`type == "workflow_automation"`**. The app builds their UI in the control panel when enabled (after machine modules). Sort order in Settings: detector, image_processing, manual_alteration, machine, **workflow_automation**.
- **Contract:** Provide **`build_ui(gui, parent_tag)`** (and optionally **`get_setting_keys()`**, **`get_settings_for_save(gui)`**). No special pipeline; the workflow runs in its own thread and uses the main app’s capture flow.
- **Triggering a capture:** Call **`gui.api.request_integration(num_frames, timeout_seconds=300.0)`** from your workflow thread. This starts the same acquisition as Start/Capture N (uses current UI: acq mode, integration time, N from Integration section), waits for idle, then returns the **processed** frame (after dark/flat, corrections, integration) or **`None`** on timeout/stop/failure. On **`None`**, check **`gui.api.get_last_integration_fail_reason()`** (`"timeout"`, `"stopped"`, `"no_frame"`, `"not_connected"`, `"not_idle"`).
- **Keep beam on:** Set **`gui.workflow_keep_beam_on = True`** before the first **`request_integration`** and clear it in a **`finally`** when your workflow ends. The main app will **not** turn the beam on before each capture or off after each capture while this is true. Turn the beam on once at the start (e.g. **`beam_supply.turn_on_and_wait_ready()`**) and off once in **`finally`** (e.g. **`beam_supply.turn_off()`**) if you used it.
- **Example:** **ct_capture** – multi-projection CT: for each angle, (placeholder) rotate, wait settle time, **`api.request_integration(stack_n)`**, save TIFF to **`captures/<datetime>/i.tif`**. See **ct_capture/README.md**.

---

## 5. Image processing modules (summary)

- **Discovery:** Same registry; **`type == "image_processing"`** and **`pipeline_slot`** (int). The app builds **`_alteration_pipeline`** as a list of **`(pipeline_slot, module_name, process_frame)`** from enabled image processing modules sorted by slot.
- **Contract:** Each image processing module provides **`process_frame(frame, gui) -> frame`** (and optionally **`build_ui(gui, parent_tag)`**). Canonical module I/O pattern:
  - `api = gui.api`
  - `frame = api.incoming_frame(MODULE_NAME, frame)`
  - process
  - `return api.outgoing_frame(MODULE_NAME, frame_out)`
  In **`_push_frame`**, the app runs each step in slot order; the frame immediately before the first step with **slot ≥ 450** is stored as **`_frame_before_distortion`** for live preview.
- **Current slots:** Dark = 100, Flat = 200, Banding = 300, Dead pixel = 400, Pincushion = 450, Mustache = 455, Image Enhancement = 480, Autocrop = 500, Background separator = 600.
- **Live preview (distortion/crop/final post-steps):** Modules with **slot ≥ 450** (pincushion, mustache, autocrop, background separator) can call **`gui._refresh_distortion_preview()`** from their UI callbacks. The app re-runs only those steps on **`_frame_before_distortion`** and repaints the texture, so adjusting sliders updates the image immediately without waiting for the next frame. Preview is only available when in live mode and after at least one frame has been received.
- **Apply / Revert (reusable API):** Alteration modules that support “Apply automatically” plus manual **Apply** and **Revert** should use the shared API so behaviour and UI are consistent:
  - **`gui.api.build_alteration_apply_revert_ui(gui, module_name, apply_callback, auto_apply_attr="...", revert_snapshot_attr="...", default_auto_apply=True)`** – Adds an “Apply automatically” checkbox and **Apply** / **Revert** buttons to the current DPG container (and a separator under the buttons). Call this **first** in your module’s **build_ui** so the block is the top of the section. **apply_callback** is a callable that receives **gui** and should: get incoming image via **get_module_incoming_image(module_name)**, optionally store a snapshot for Revert, run your step, then call **output_manual_from_module(module_name, out)**.
  - **`gui.api.alteration_auto_apply(gui, auto_apply_attr, default=True)`** – Use in **process_frame** to decide whether to run the step: if it returns **False**, return the frame unchanged (e.g. **`return api.outgoing_frame(MODULE_NAME, frame)`**).
  - Persist the auto-apply value by including **auto_apply_attr** in **get_setting_keys()** and **get_settings_for_save()** (the API does not manage the full settings dict).
- **Manual Apply / Revert behaviour:**  
  - **Revert** at a module: takes the frame **before** that module (incoming cache or snapshot), runs the **rest of the pipeline** (downstream only, that module skipped) and paints the result. The pipeline **module cache** is updated for every downstream step run, so **get_module_incoming_image** for later modules reflects this run (e.g. after reverting dead_pixel, applying pincushion uses the reverted frame, not the old cached one).  
  - **Apply** at a module: takes the current **get_module_incoming_image(module_name)** (which may be from a previous Revert or live run), runs your step, then **output_manual_from_module(module_name, out)** which continues the pipeline and updates the cache for downstream modules.  
  So Revert and Apply never “re-apply” upstream modules incorrectly; the cache is always updated when the continuation runs.
- **Manual-safe pipeline helpers (direct use):** If you need custom UI instead of the shared block, use:
  - **`gui.api.get_module_incoming_image(module_name)`** – Cached incoming frame for that module (updated by live pipeline and by **output_manual_from_module** when continuation runs).
  - **`gui.api.get_module_incoming_token(module_name)`**
  - **`gui.api.output_manual_from_module(module_name, frame)`** – Runs pipeline from the next module onward and paints; also updates **\_pipeline_module_cache** for each step so downstream modules see the correct incoming frames.

---

## 6. List of modules and docs

| Module | Type | Doc |
|--------|------|-----|
| **dark_correction** | Image processing (slot 100) | [image_processing/dark_correction/README.md](image_processing/dark_correction/README.md) |
| **flat_correction** | Image processing (slot 200) | [image_processing/flat_correction/README.md](image_processing/flat_correction/README.md) |
| **bad_pixel_map** | Image processing (slot 250) | [image_processing/bad_pixel_map/README.md](image_processing/bad_pixel_map/README.md) |
| **banding** | Image processing (slot 300) | [image_processing/banding/README.md](image_processing/banding/README.md) |
| **dead_pixel** | Image processing (slot 400) | [image_processing/dead_pixel/README.md](image_processing/dead_pixel/README.md) |
| **pincushion** | Image processing (slot 450) | [image_processing/pincushion/README.md](image_processing/pincushion/README.md) |
| **mustache** | Image processing (slot 455) | [image_processing/mustache/README.md](image_processing/mustache/README.md) |
| **autocrop** | Image processing (slot 500) | [image_processing/autocrop/README.md](image_processing/autocrop/README.md) |
| **background_separator** | Image processing (slot 600, manual + auto) | [image_processing/background_separator/README.md](image_processing/background_separator/README.md) |
| **asi_camera** | Detector | [detector/asi_camera/README.md](detector/asi_camera/README.md) |
| **hamamatsu_c7942** | Detector | [detector/hamamatsu_c7942/README.md](detector/hamamatsu_c7942/README.md) |
| **hamamatsu_dc5** (C9730DK-11 / C9732) | Detector | [detector/hamamatsu_dc5/README.md](detector/hamamatsu_dc5/README.md) |
| **open_image** | Detector | [detector/open_image/README.md](detector/open_image/README.md) |
| **faxitron** | Machine | [machine/faxitron/README.md](machine/faxitron/README.md) |
| **faxitron_mx20_dx50** | Machine (optional beam_supply) | [machine/faxitron_mx20_dx50/README.md](machine/faxitron_mx20_dx50/README.md) |
| **esp_hv_supply** | Machine (optional beam_supply) | [machine/esp_hv_supply/README.md](machine/esp_hv_supply/README.md) |
| **example_supply** | Machine (optional beam_supply) | [machine/example_supply/README.md](machine/example_supply/README.md) |
| **example_arduino_powersupply** | Machine (optional beam_supply) | [machine/example_arduino_powersupply/README.md](machine/example_arduino_powersupply/README.md) |
| **ct_capture** | Workflow automation | [workflow_automation/ct_capture/README.md](workflow_automation/ct_capture/README.md) |
| **microcontrast_dehaze** (Image Enhancement) | Image processing (slot 480, with manual controls) | [image_processing/microcontrast_dehaze/README.md](image_processing/microcontrast_dehaze/README.md) |

---

## 7. Summary diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Main app (XrayGUI)                                                       │
│  - Frame pipeline: dark → flat → banding → dead pixel → pincushion →     │
│    mustache → image enhancement → autocrop → background separator           │
│    (slot 100–600); live preview                                             │
│    for slot ≥ 450                                                         │
│  - Acquisition: Start → (optional beam_supply.turn_on_and_wait_ready)   │
│    → clear buffer → camera_module.start_acquisition(gui)                 │
│  - End: acq_mode → idle → (optional beam_supply.turn_off())               │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
        Registry discovers modules/<type>/* and builds Settings checkboxes
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
┌───────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│ Detector module   │     │ Supply (e.g.     │     │ Supply (e.g.     │
│ (one active);     │     │ esp_hv_supply)   │     │ faxitron)       │
│ acquisition only, │     │ Optional:        │     │ No beam_supply   │
│ api.submit_frame  │     │ gui.beam_supply  │     │ Manual Expose   │
└───────────────────┘     └─────────────────┘     └─────────────────┘
```

For full detector API and threading, see **[README_DETECTOR_MODULES.md](README_DETECTOR_MODULES.md)**. For a short **code reference** (entry points, module types, main APIs), see **[../docs/CODE_REFERENCE.md](../docs/CODE_REFERENCE.md)**.
