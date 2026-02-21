# Camera and supply modules – overview

This document explains how **camera modules** and **supply (machine) modules** work and how they integrate with the main application. For the full **camera module contract** (API, threading, acquisition-only; dark/flat are in alteration modules), see **[README_CAMERA_MODULES.md](README_CAMERA_MODULES.md)**.

---

## 1. Module types

- **Camera module** – Provides the imaging source. Exactly one camera module is active at a time. It:
  - Reports frame size and builds the Connection UI.
  - Implements **acquisition only** (single, dual, continuous, capture N) and submits raw frames via **`gui.api.submit_frame(frame)`**. Dark and flat reference capture are **not** done by the camera; they are done by the **dark_correction** and **flat_correction** alteration modules.
- **Machine / supply module** – Optional hardware (Faxitron, HV supply, etc.). Multiple can be enabled. Some can optionally register as **`gui.beam_supply`** for **Auto On/Off** (turn on before acquisition, turn off when acquisition ends).
- **Workflow automation module** – Optional multi-step workflows (e.g. CT capture). Type **`"workflow_automation"`**. The app builds their UI in the control panel when enabled (after machine modules). They use **`gui.api.request_integration(num_frames)`** to trigger one capture (same as Start/Capture N) and get the processed frame; optional **`gui.workflow_keep_beam_on`** keeps the beam supply on between captures. See **§ 5a** below.
- **Image alteration module** – Optional steps in the frame pipeline (e.g. dark subtract, flat divide). Each has a **`pipeline_slot`** (e.g. 100, 200); enabled alteration modules are run in slot order. See **§ 5** below.
- **Manual alteration module** – User-triggered image operations. Not in the per-frame pipeline; the user clicks a button to apply.
- **Hybrid alteration module** – A normal per-frame alteration module can also expose manual controls (e.g. Image Enhancement). For manual actions, use module incoming-frame cache + downstream-output API helpers to avoid double application.

The main app does **not** hardcode module names. It discovers packages under **`machine_modules/`** via **`registry.discover_modules()`** and uses **`MODULE_INFO`** and **`get_setting_keys()`** from each package to build Settings checkboxes and load/save.

---

## 2. Discovery and settings (registry)

- **Discovery:** `registry._discover_package_names()` lists subpackages of `machine_modules` (e.g. `faxitron`, `hamamatsu_c7942`, `open_image`, `esp_hv_supply`, `example_supply`). Private names and `registry` itself are skipped.
- **Per-module metadata:** `registry.get_module_info(name)` imports `machine_modules.<name>` and returns:
  - **`MODULE_INFO`** (dict): `display_name`, `description`, `type` (`"camera"`, `"machine"`, `"alteration"`, `"manual_alteration"`, or **`"workflow_automation"`**), `default_enabled`. For `type == "camera"` also **`camera_priority`**. For `type == "alteration"` also **`pipeline_slot`** (int; order in the image pipeline).
  - **`get_setting_keys()`** (function): list of setting keys the module persists (e.g. port, voltage). Used for **`load_settings(extra_keys=...)`** and **`save_settings(..., extra_keys=...)`** without editing `settings.DEFAULTS`.
- **Settings UI:** The main app builds a “Load &lt;display_name&gt; module” checkbox per discovered module. Stored key: **`load_<name>_module`** (e.g. `load_hamamatsu_c7942_module`).
- **Apply / save:** On startup, **`_apply_loaded_settings`** sets integration time, banding, etc., and **`_module_enabled[name]`** from the loaded settings. Dark and flat **reference images** are then loaded from disk (per integration time / gain) for use by the dark_correction and flat_correction pipeline steps. When saving, the app calls **`get_settings_for_save(gui)`** for **every** discovered module and merges the returned dict into the settings.
- **Module settings API (recommended):** Use **`gui.api.get_setting(key, default)`** for default values when building UI (replaces **`gui.api.get_loaded_settings().get(key, default)`**). Use **`gui.api.get_module_settings_for_save(spec)`** for **`get_settings_for_save`**: pass a list of **`(key, tag, converter, default)`**; the API reads from DPG when the widget exists and from loaded settings otherwise, so saved values are never overwritten when the module’s UI is not built. Example: **`return gui.api.get_module_settings_for_save([("asi_gain", "asi_gain_slider", int, 50), ...])`**. **`all_extra_settings_keys(modules)`** gives the full set of extra keys for load/save.

**Adding a new module:** Put a package under `machine_modules/<name>/` with **`MODULE_INFO`** and (if needed) **`get_setting_keys()`**. No changes in **gui.py** or **settings.DEFAULTS** are required.

---

## 3. Camera modules (summary)

- **Selection:** The app picks the **enabled** camera module with the **highest** **`camera_priority`**. It calls **`get_frame_size()`** and **`build_ui(gui, parent_tag)`** for that module only.
- **Contract:** The module must:
  - Implement **`get_frame_size() -> (width, height)`** and **`build_ui(gui, parent_tag)`**.
  - Set **`gui.camera_module`** to an instance that implements: **`get_acquisition_modes()`**, **`is_connected()`**, **`start_acquisition(gui)`**, **`stop_acquisition(gui)`**, **`disconnect(gui)`**.
- **Acquisition:** When the user clicks Start, the app sets **`gui.acq_mode`**, **`gui.integration_time`**, **`gui.integration_n`**, and (when capturing dark/flat reference) **`gui._dark_stack_n`** / **`gui._flat_stack_n`**, then calls **`camera_module.start_acquisition(gui)`**. The **camera** starts a daemon thread that only acquires frames and calls **`gui.api.submit_frame(frame)`** for each. The camera does **not** perform dark/flat reference capture; when mode is dark or flat, the **dark_correction** or **flat_correction** alteration module runs the capture (collects frames via the app pipeline and saves the reference). The module sets **`gui.acq_thread`** and on finish **`gui.acq_mode = "idle"`**.
- **Stopping:** The app calls **`stop_acquisition(gui)`**; the module must call **`gui.api.signal_acquisition_stop()`** (or **`gui.acq_stop.set()`**) so the worker exits.

Full API, threading, and checklist: **[README_CAMERA_MODULES.md](README_CAMERA_MODULES.md)**.

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

- **Discovery:** Same registry; **`type == "workflow_automation"`**. The app builds their UI in the control panel when enabled (after machine modules). Sort order in Settings: camera, alteration, manual_alteration, machine, **workflow_automation**.
- **Contract:** Provide **`build_ui(gui, parent_tag)`** (and optionally **`get_setting_keys()`**, **`get_settings_for_save(gui)`**). No special pipeline; the workflow runs in its own thread and uses the main app’s capture flow.
- **Triggering a capture:** Call **`gui.api.request_integration(num_frames, timeout_seconds=300.0)`** from your workflow thread. This starts the same acquisition as Start/Capture N (uses current UI: acq mode, integration time, N from Integration section), waits for idle, then returns the **processed** frame (after dark/flat, corrections, integration) or **`None`** on timeout/stop/failure. On **`None`**, check **`gui.api.get_last_integration_fail_reason()`** (`"timeout"`, `"stopped"`, `"no_frame"`, `"not_connected"`, `"not_idle"`).
- **Keep beam on:** Set **`gui.workflow_keep_beam_on = True`** before the first **`request_integration`** and clear it in a **`finally`** when your workflow ends. The main app will **not** turn the beam on before each capture or off after each capture while this is true. Turn the beam on once at the start (e.g. **`beam_supply.turn_on_and_wait_ready()`**) and off once in **`finally`** (e.g. **`beam_supply.turn_off()`**) if you used it.
- **Example:** **ct_capture** – multi-projection CT: for each angle, (placeholder) rotate, wait settle time, **`api.request_integration(stack_n)`**, save TIFF to **`captures/<datetime>/i.tif`**. See **ct_capture/README.md**.

---

## 5. Image alteration modules (summary)

- **Discovery:** Same registry; **`type == "alteration"`** and **`pipeline_slot`** (int). The app builds **`_alteration_pipeline`** as a list of **`(pipeline_slot, module_name, process_frame)`** from enabled alteration modules sorted by slot.
- **Contract:** Each alteration module provides **`process_frame(frame, gui) -> frame`** (and optionally **`build_ui(gui, parent_tag)`**). Canonical module I/O pattern:
  - `api = gui.api`
  - `frame = api.incoming_frame(MODULE_NAME, frame)`
  - process
  - `return api.outgoing_frame(MODULE_NAME, frame_out)`
  In **`_push_frame`**, the app runs each step in slot order; the frame immediately before the first step with **slot ≥ 450** is stored as **`_frame_before_distortion`** for live preview.
- **Current slots:** Dark = 100, Flat = 200, Banding = 300, Dead pixel = 400, Pincushion = 450, Mustache = 455, Image Enhancement = 480, Autocrop = 500, Background separator = 600.
- **Live preview (distortion/crop/final post-steps):** Modules with **slot ≥ 450** (pincushion, mustache, autocrop, background separator) can call **`gui._refresh_distortion_preview()`** from their UI callbacks. The app re-runs only those steps on **`_frame_before_distortion`** and repaints the texture, so adjusting sliders updates the image immediately without waiting for the next frame. Preview is only available when in live mode and after at least one frame has been received.
- **Manual-safe pipeline helpers:** For manual apply/revert from a pipeline module, use:
  - **`gui.api.incoming_frame(module_name, frame, use_cached=True)`** (or `get_module_incoming_image(...)` if you need direct cache access)
  - **`gui.api.get_module_incoming_image(module_name)`**
  - **`gui.api.get_module_incoming_token(module_name)`**
  - **`gui.api.output_manual_from_module(module_name, frame)`**
  
  This ensures manual edits start from module input cache and continue only downstream modules.

---

## 6. List of modules and docs

| Module | Type | Doc |
|--------|------|-----|
| **dark_correction** | Alteration (slot 100) | [dark_correction/README.md](dark_correction/README.md) |
| **flat_correction** | Alteration (slot 200) | [flat_correction/README.md](flat_correction/README.md) |
| **bad_pixel_map** | Alteration (slot 250) | [bad_pixel_map/README.md](bad_pixel_map/README.md) |
| **banding** | Alteration (slot 300) | [banding/README.md](banding/README.md) |
| **dead_pixel** | Alteration (slot 400) | [dead_pixel/README.md](dead_pixel/README.md) |
| **pincushion** | Alteration (slot 450) | [pincushion/README.md](pincushion/README.md) |
| **mustache** | Alteration (slot 455) | [mustache/README.md](mustache/README.md) |
| **autocrop** | Alteration (slot 500) | [autocrop/README.md](autocrop/README.md) |
| **background_separator** | Alteration (slot 600, manual + auto) | [background_separator/README.md](background_separator/README.md) |
| **asi_camera** | Camera | [asi_camera/README.md](asi_camera/README.md) |
| **hamamatsu_c7942** | Camera | [hamamatsu_c7942/README.md](hamamatsu_c7942/README.md) |
| **open_image** | Camera | [open_image/README.md](open_image/README.md) |
| **faxitron** | Machine | [faxitron/README.md](faxitron/README.md) |
| **esp_hv_supply** | Machine (optional beam_supply) | [esp_hv_supply/README.md](esp_hv_supply/README.md) |
| **example_supply** | Machine (optional beam_supply) | [example_supply/README.md](example_supply/README.md) |
| **example_arduino_powersupply** | Machine (optional beam_supply) | [example_arduino_powersupply/README.md](example_arduino_powersupply/README.md) |
| **ct_capture** | Workflow automation | [ct_capture/README.md](ct_capture/README.md) |
| **microcontrast_dehaze** (Image Enhancement) | Alteration (slot 480, with manual controls) | [microcontrast_dehaze/README.md](microcontrast_dehaze/README.md) |

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
        Registry discovers machine_modules/* and builds Settings checkboxes
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
┌───────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│ Camera module     │     │ Supply (e.g.     │     │ Supply (e.g.     │
│ (one active);     │     │ esp_hv_supply)   │     │ faxitron)       │
│ acquisition only, │     │ Optional:        │     │ No beam_supply   │
│ api.submit_frame  │     │ gui.beam_supply  │     │ Manual Expose   │
└───────────────────┘     └─────────────────┘     └─────────────────┘
```

For full camera API and threading, see **[README_CAMERA_MODULES.md](README_CAMERA_MODULES.md)**. For a short **code reference** (entry points, module types, main APIs), see **[../docs/CODE_REFERENCE.md](../docs/CODE_REFERENCE.md)**.
