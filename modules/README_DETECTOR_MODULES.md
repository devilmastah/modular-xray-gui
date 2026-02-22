# Detector module contract

This document describes how to implement a **detector module** (imaging source) for the X-ray acquisition application. Detector modules provide the connection to a detector and deliver raw frames to the main app, which handles display, dark/flat correction, banding, dead pixels, and export.

---

## 1. Overview

- The **main application** is a generic shell: it owns the viewport, acquisition UI (mode, integration time, Start/Stop), dark/flat, corrections, and export. It does **not** talk to hardware directly.
- A **detector module** is a loadable Python package under `modules/detector/` that:
  - Exposes frame dimensions and builds its **Connection** UI (e.g. Connect/Disconnect).
  - Implements **acquisition only**: when the user clicks Start, the main app calls into the module, which runs a worker thread, acquires frames, and hands each frame to the app via **`gui.api.submit_frame(frame)`**. Dark and flat reference capture are **not** done by the detector; they are done by the **dark_correction** and **flat_correction** image processing modules.

Only one detector module is active at a time. The app loads it at startup if the corresponding setting is enabled (e.g. `load_hamamatsu_c7942_module`).

---

## 2. Required module API

Your module **must** provide the following. The main app will call these; the module is responsible for implementing them.

### 2.1 Module-level function: `get_frame_size()`

```python
def get_frame_size() -> tuple[int, int]:
    """Return (width, height) in pixels. Called once at UI build time to size texture and viewport."""
    return (2400, 2400)  # example
```

- **When:** Called at the start of `_build_ui()`, before the texture is created.
- **Purpose:** So the main app can allocate the correct texture size and aspect ratio.
- **No GUI or device required:** This may be called before any connection; return the fixed sensor dimensions.

---

### 2.2 Module-level function: `build_ui(gui, parent_tag)`

```python
def build_ui(gui, parent_tag: str = "control_panel") -> None:
    """
    Add your Connection (and optionally other) UI under the given DPG parent.
    Register the module instance on gui so the app can call start/stop/ disconnect.
    """
```

- **When:** Called once during main window build, after the control panel child window exists.
- **parent_tag:** DPG tag of the control panel (usually `"control_panel"`). Add your widgets as children (e.g. a `collapsing_header`).
- **You must:**
  - Add a **Connection** section (e.g. Connect button, status text). Use a **unique** DPG tag prefix (e.g. `"c7942_conn_status"`) to avoid clashes with other modules.
  - Register your module with **`gui.api.register_camera_module(<your module instance>)`** so the main app can call `start_acquisition`, `stop_acquisition`, `is_connected`, and `disconnect`.
- **Optional:** Set **`gui.teensy`** (or similar) on connect if other modules (e.g. Faxitron) need the same device handle.

---

### 2.3 Module instance methods

The object you **register** with **`gui.api.register_camera_module(mod)`** must implement:

| Method | Signature | Description |
|--------|-----------|-------------|
| **`get_acquisition_modes()`** | `() -> list[tuple[str, str]]` | Return modes for the **Start** combo: `[(display_label, mode_id), ...]` e.g. `[("Single Shot", "single"), ("Dual Shot", "dual"), ("Continuous", "continuous"), ("Capture N", "capture_n")]`. Do **not** include dark/flat; those are handled by dark_correction and flat_correction. The app passes the selected mode via **`api.get_acquisition_mode()`**. |
| **`is_connected()`** | `() -> bool` | Return whether the device is currently connected. Used to enable/disable Start and to show status. |
| **`start_acquisition(gui)`** | `(gui) -> None` | Start acquisition. The app has set mode, integration time, and frame count. Start a **daemon thread** that uses **`gui.api.get_acquisition_mode()`**, **`gui.api.get_integration_time_seconds()`**, **`gui.api.get_integration_frame_count()`**, **`gui.api.acquisition_should_stop()`**, and calls **`gui.api.submit_frame(frame)`** for each frame. Call **`gui.api.set_acquisition_thread(thread)`** and on worker exit **`gui.api.set_acquisition_idle()`**. |
| **`stop_acquisition(gui)`** | `(gui) -> None` | Request stop (e.g. call **`gui.api.signal_acquisition_stop()`**). Your worker must check **`gui.api.acquisition_should_stop()`** and exit. |
| **`disconnect(gui)`** | `(gui) -> None` | Disconnect the device and clear references (e.g. `gui.teensy`). Called on application exit if connected. |
| **`get_current_gain(gui)`** | `(gui) -> int` | **Optional.** Return current gain for dark/flat **file naming** and nearest-match loading. Return **`0`** if no settable gain. The app saves darks/flats as `dark_{time}_{gain}.npy` / `flat_{time}_{gain}.npy`. If not implemented, the app uses gain `0`. |
| **`get_sensor_bit_depth()`** | `() -> int` | **Optional.** Return sensor bit depth: **`12`**, **`14`**, or **`16`**. Used for display/windowing range (histogram and Min/Max clamp to 0..2^depth−1). If not implemented, the app uses **`12`** (0..4095). |

---

## 3. What the main app provides (Application API: `gui.api`)

Your module receives **`gui`** (the main **`XrayGUI`** instance). Prefer the **Application API** (**`gui.api`**) for a stable contract.

### 3.1 Submitting frames

- **`gui.api.submit_frame(frame)`**  
  Call from your **acquisition thread** for every new frame.  
  - **`frame`**: `np.ndarray`, shape **`(height, width)`**, dtype **`np.float32`**.  
  - The main app runs the pipeline (dark/flat, banding, dead-pixel, buffering/integration, display). Submit **raw** sensor values (e.g. 0–4095 for 12-bit).

### 3.2 Acquisition state (read in your worker)

Use **`gui.api`**:

- **`api.acquisition_should_stop()`** — `bool`. Check in your loop and exit when `True`.
- **`api.get_acquisition_mode()`** — string: `"single"`, `"dual"`, `"continuous"`, `"capture_n"` (no dark/flat; those are in other modules).
- **`api.get_integration_time_seconds()`** — float, seconds.
- **`api.get_integration_frame_count()`** — int, number of frames for “Capture N”.

### 3.3 Progress and status

- **`api.set_progress(value, text=None)`** — progress bar 0.0–1.0 and optional overlay text.
- **`api.set_status_message(msg)`** — main status line.

### 3.4 Dark/flat (not done by the detector)

**Detector modules do not perform dark or flat reference capture.** That is done by the **dark_correction** and **flat_correction** image processing modules (they use **`api.request_n_frames_processed_up_to_slot()`** and the app’s dark/flat load/save). Your detector only needs **`get_current_gain(gui)`** so the app can name and match dark/flat files by (time, gain). The app saves under **`app/darks/<camera_name>/`** and **`app/flats/<camera_name>/`** as **`dark_{time}_{gain}.npy`** and **`flat_{time}_{gain}.npy`**.

### 3.5 Persistence

- **`api.get_loaded_settings()`** — dict of loaded settings (for widget defaults).
- **`api.save_settings()`** — call when the user changes something that should be persisted (e.g. last used port). For callbacks that must be gui methods, use **`api.gui`** (e.g. **`api.gui._cb_foo`**).

---

## 4. Frame format and pipeline

- **Format:** 2D **float32** array, shape **`(height, width)`**, row-major. Values are raw sensor units (e.g. 0–4095 for 12-bit). The main app will:
  - Subtract dark, divide by flat (if loaded).
  - Apply banding and dead-pixel corrections.
  - Buffer and integrate (mean over last **`integration_n`** frames) for display.
  - Apply windowing and optional histogram equalization for the texture.
- **Thread:** **`api.submit_frame`** is called from your **acquisition thread**; the main app is thread-safe (lock used for buffer/display updates).

---

## 5. Acquisition modes (semantics)

The main app builds the **Start** mode combo from **`get_acquisition_modes()`**. When the user clicks Start, the selected **mode_id** is passed to your worker via **`api.get_acquisition_mode()`**. Your detector module implements **only** these four modes:

| Mode ID | Meaning |
|---------|--------|
| **`single`** | One frame; call **`api.submit_frame(frame)`** once. |
| **`dual`** | One “integrated” frame (e.g. clear, wait **`api.get_integration_time_seconds()`**, then read and submit one frame). |
| **`continuous`** | Loop until **`api.acquisition_should_stop()`**; submit each frame with **`api.submit_frame(frame)`**. |
| **`capture_n`** | **`api.get_integration_frame_count()`** frames, then call **`api.set_acquisition_idle()`**. |

**Dark** and **flat** reference capture are **not** detector modes; they are implemented by the **dark_correction** and **flat_correction** modules. When your worker finishes (any mode), call **`api.set_acquisition_idle()`** and clear progress.

---

## 6. Threading

- **Main thread:** DPG UI, **`_render_tick`**, and all callbacks (Connect, Start, Stop) run on the main thread.
- **Your worker:** **`start_acquisition(gui)`** must start a **daemon** thread that:
  - Uses **`api.get_acquisition_mode()`**, **`api.get_integration_time_seconds()`**, **`api.get_integration_frame_count()`**, **`api.acquisition_should_stop()`**.
  - Performs hardware trigger/read.
  - Calls **`api.submit_frame(frame)`** for each frame.
  - Before exiting, calls **`api.set_acquisition_idle()`** so the UI returns to Idle.
- **Stopping:** The main app calls **`stop_acquisition(gui)`**; you typically call **`api.signal_acquisition_stop()`**. Your worker must check **`api.acquisition_should_stop()`** regularly and exit cleanly.
- **On exit:** The main app joins the thread set via **`api.set_acquisition_thread(thread)`** with a short timeout, then calls **`disconnect(gui)`** if **`is_connected()`**.

---

## 7. Settings and loading (registry – no need to edit gui.py)

Modules are **discovered automatically** from the **`modules/`** folder. You do **not** need to add your module to **`gui.py`** or the Settings window.

- **Discovery:** The app uses **`modules.registry`** to find all packages under **`modules/detector/`**, **`modules/machine/`**, etc., and reads **`MODULE_INFO`** and **`get_setting_keys()`** from each.
- **In your module’s `__init__.py`:**
  - **`MODULE_INFO`** (dict): **`display_name`**, **`description`**, **`type`** (`"detector"` or **`"machine"`**), **`default_enabled`** (bool). For **`type == "detector"`** add **`camera_priority`** (int; higher = preferred when multiple detector modules are enabled).
  - **`get_setting_keys()`** (function): return a list of setting keys your module persists (e.g. **`["fax_voltage", "fax_exposure", "fax_mode"]`**). Used so the app can load/save them without listing them in **`settings.DEFAULTS`**.
- **Enable checkbox:** The Settings window is built from the registry; each discovered module gets a “Load &lt;display_name&gt; module” checkbox. The stored key is **`load_<name>_module`** (e.g. **`load_hamamatsu_c7942_module`**).
- **Apply / save:** **`gui._apply_loaded_settings`** and **`gui._save_settings`** use **`gui._module_enabled[name]`** and call **`get_settings_for_save()`** for each enabled module. **`settings.load_settings(extra_keys=...)`** and **`save_settings(..., extra_keys=...)`** persist module keys.
- **Build UI:** **`_build_ui()`** picks the enabled **detector** module with highest **`camera_priority`**, calls **`get_frame_size()`** and **`build_ui()`** for it. For **machine** modules, it calls **`build_ui()`** for each enabled one.

See **`modules/registry.py`** and any existing module (e.g. **`modules/detector/hamamatsu_c7942`**, **`modules/machine/faxitron_mx20_dx50`**) for **`MODULE_INFO`** and **`get_setting_keys()`**.

---

## 8. Reference implementation

- **`modules/detector/hamamatsu_c7942/`** and **`modules/detector/asi_camera/`** are reference detector modules:
  - **`get_frame_size()`** returns the sensor dimensions.
  - **`build_ui(gui, parent_tag)`** adds Connection UI and calls **`gui.api.register_camera_module(mod)`**.
  - The module instance implements **`is_connected`**, **`start_acquisition`**, **`stop_acquisition`**, **`disconnect`**, and runs a worker that implements **single**, **dual**, **continuous**, and **capture_n** only, calling **`api.submit_frame(frame)`** for each frame. Dark and flat capture are in the dark_correction and flat_correction modules.

Use either as the template for a new detector module.

---

## 9. Checklist for a new detector module

- [ ] Package under **`modules/detector/<module_name>/`** with **`__init__.py`**.
- [ ] **`get_frame_size() -> (width, height)`**.
- [ ] **`build_ui(gui, parent_tag)`** that adds Connection UI and calls **`gui.api.register_camera_module(mod)`**.
- [ ] Module instance: **`get_acquisition_modes()`** returning **`[(label, mode_id), ...]`** for **single**, **dual**, **continuous**, **capture_n** only (no dark/flat); **`is_connected()`**; **`start_acquisition(gui)`**; **`stop_acquisition(gui)`**; **`disconnect(gui)`**.
- [ ] Worker: use **`api.get_acquisition_mode()`**, **`api.get_integration_time_seconds()`**, **`api.get_integration_frame_count()`**, **`api.acquisition_should_stop()`**; call **`api.submit_frame(frame)`** for each frame; **`api.set_acquisition_thread(thread)`**; on exit **`api.set_acquisition_idle()`**.
- [ ] **`stop_acquisition(gui)`** calls **`api.signal_acquisition_stop()`** (or equivalent).
- [ ] **`disconnect(gui)`** closes the device and clears **`gui.teensy`** (if used).
- [ ] DPG tags use a unique prefix (e.g. **`c7942_`**) to avoid clashes.
- [ ] **`MODULE_INFO`** with **`display_name`**, **`description`**, **`type": "detector"`**, **`default_enabled`**, **`camera_priority`** (higher = preferred when multiple detectors enabled).
- [ ] **`get_setting_keys()`** returning the list of keys your module persists (or **`[]`** if none). No need to edit **gui.py** or **settings.DEFAULTS**; the registry discovers the module and adds the Settings checkbox and load/save automatically.

---

## 10. Summary diagram

```
┌─────────────────────────────────────────────────────────────────┐
│  Main application (gui)                                          │
│  - Viewport, acquisition UI (mode, integration, Start/Stop)     │
│  - Dark/flat, banding, dead pixels, deconvolution, export        │
│  - api.submit_frame(frame) → _push_frame → display               │
└───────────────────────────────┬─────────────────────────────────┘
                                │
                load_hamamatsu_c7942_module?
                                │
        ┌───────────────────────┴───────────────────────┐
        │  Yes                                           │  No
        ▼                                                ▼
┌─────────────────────────────┐              ┌──────────────────────┐
│  modules.detector.           │              │  Placeholder:        │
│  hamamatsu_c7942             │              │  "No detector module │
│  - get_frame_size()          │              │   loaded"             │
│  - build_ui() → Connection   │              └──────────────────────┘
│  - api.register_camera_module(mod) │
└──────────────┬──────────────┘
               │
               │  Start → mod.start_acquisition(gui)
               │          Worker thread:
               │            - read via api (mode, integration_time, acquisition_should_stop)
               │            - trigger/read frames
               │            - api.submit_frame(frame)
               │          Stop → mod.stop_acquisition(gui) → api.signal_acquisition_stop()
               │  Exit  → mod.disconnect(gui)
               ▼
┌─────────────────────────────┐
│  Hardware (e.g. Teensy +    │
│  C7942 sensor)              │
└─────────────────────────────┘
```

This contract keeps the main app a generic X-ray acquisition shell and keeps all hardware-specific logic in the detector module.
