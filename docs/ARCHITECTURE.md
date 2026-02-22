# Application architecture and data flow

This document describes the **intended design**: what each part of the app does, how data flows, and how modules fit in. Code should align with this; the Application API (`gui.api`) is the contract between the app and modules.

---

## 1. Design principles

- **Single responsibility:** Each module has one clear job. Use the API where it helps; use custom code for the rest.
- **One active camera:** There is always at most one active camera. It does **frame acquisition only** (gain, resolution, ROI, modes).
- **Acquisition and integration** are app-level concerns: they trigger the camera and (for integration) combine multiple **fully processed** frames.
- **Workflow pipeline** is an ordered list of steps: each step takes an image in and outputs an image to the next step. Order is defined by module **pipeline_slot** (e.g. dark → flat → banding → dead pixel → distortion → crop).
- **Display** shows the result of the pipeline (or the integrated result) and updates histogram and view.

---

## 2. High-level data flow

```
┌─────────────┐     frame      ┌──────────────────────────────────────────┐     frame      ┌─────────────┐
│   Camera    │ ──────────────►│  Workflow pipeline (loaded modules,       │ ──────────────►│  Histogram  │
│ (acquisition│                │  in order: dark, flat, banding, …)       │                │  + View     │
│  only)      │                │  Each step: image in → image out          │                │             │
└─────────────┘                └──────────────────────────────────────────┘                └─────────────┘
        ▲                                      │
        │                                      │
        │ triggered by                         │ one processed frame per input frame
        │                                      ▼
┌───────┴───────┐                    ┌─────────────────────┐
│  Acquisition  │                    │  Integration        │
│  (mode, N)    │                    │  (e.g. average of   │
│               │                    │   N processed frames)│
└───────────────┘                    └─────────────────────┘
```

1. **Camera** returns a frame (raw).
2. **Pipeline** runs the frame through all loaded workflow steps in order; each step is **image in → image out**.
3. **Display** updates histogram and view from the pipeline output (or from the integrated result when in “integrated” mode).

---

## 3. Detailed flow: one frame, frame buffer, and example module

This section traces **one raw frame** from the camera through the pipeline, frame buffer, and display, and shows how one alteration module (e.g. **flat_correction**) fits in. It also shows how manual **Apply / Revert** interacts with the pipeline cache and buffer.

### 3.1 Entry: camera to pipeline

```
  Camera thread                    Main (render) thread
       │                                    │
       │  api.submit_frame(raw_frame)       │
       │ ─────────────────────────────────►│
       │                                    │ submit_raw_frame(frame)
       │                                    │   └─► _push_frame(frame)
       │                                    │
```

- **`submit_frame`** (from the camera module) enqueues the raw frame. The main thread's **`submit_raw_frame`** runs **`_push_frame(frame)`** (under the app's frame lock as needed).

### 3.2 Pipeline loop and module cache (live path)

Inside **`_push_frame(frame)`** (normal live path, no dark/flat capture):

1. **Token:** `_pipeline_frame_token += 1`; this token is used for logging and cache consistency.
2. **Pre-distortion snapshot:** Before the first step with **slot ≥ 450**, the current `frame` is stored as **`_frame_before_distortion`** (for live distortion/preview).
3. **For each step** in **`_alteration_pipeline`** (sorted by `pipeline_slot`):
   - **Store incoming in cache:** `_pipeline_module_cache[module_name] = { "token", "slot", "frame": frame.copy() }` (incoming to this module).
   - **Run step:** `frame = step(frame, self)` (e.g. `flat_correction.process_frame(frame, gui)`).
   - Log and continue to next step.
4. **After the loop:** Append the final **`frame`** to **`frame_buffer`**, trim to **`integration_n`**, call **`_update_integrated_display()`**, then **`display_frame = np.mean(frame_buffer, axis=0)`**.

So each step sees the **output of the previous step**; the cache always holds the **input** to each module for that run (used by **Apply / Revert** and **get_module_incoming_image**).

```
  _push_frame(frame)
       │
       │  frame_token += 1
       │  pipeline = [ (100, dark_correction, ...), (200, flat_correction, ...), ... ]
       │
       ▼
  ┌────────────────────────────────────────────────────────────────────────────┐
  │  for (slot, module_name, step) in pipeline:                                │
  │     _pipeline_module_cache[module_name] = { token, slot, frame }   (in)    │
  │     frame = step(frame, gui)  ───────────────────────────────────── (out)  │
  └────────────────────────────────────────────────────────────────────────────┘
       │
       │  frame_before_distortion stored before first slot >= 450
       │
       ▼
  ┌────────────────────────────────────────────────────────────────────────────┐
  │  frame_buffer.append(frame)                                                 │
  │  frame_buffer = frame_buffer[-integration_n:]                               │
  │  _update_integrated_display()  ──►  display_frame = mean(frame_buffer)      │
  └────────────────────────────────────────────────────────────────────────────┘
```

### 3.3 Example: flat_correction (slot 200)

One alteration module in the chain:

- **Input:** Frame that has already passed dark (slot 100). So `frame` is dark-subtracted.
- **Contract:** `process_frame(frame, gui)` uses `api.incoming_frame(MODULE_NAME, frame)` (for hooks/logging), then applies the step, then `return api.outgoing_frame(MODULE_NAME, out)`.
- **Step:** Load flat via `api.get_flat_field()`, normalize and divide `frame` by it, clip and return float32.
- **Output:** Flat-corrected frame; next step (e.g. bad_pixel_map at 250, then banding at 300) receives this.

**Apply automatically:** If the user turned off "Apply automatically" for flat, `process_frame` returns the incoming frame unchanged (via **`api.alteration_auto_apply(gui, "flat_correction_auto_apply", True)`**).

**Manual Apply:** User clicks Apply → module gets **`get_module_incoming_image("flat_correction")`** (the cached incoming for this module), runs the same flat-divide step, then **`output_manual_from_module("flat_correction", out)`**, which runs the **rest of the pipeline** (slots > 200) and paints the result. The continuation loop **updates _pipeline_module_cache** for each downstream module, so later Apply/Revert see the correct incoming frames.

**Manual Revert:** User clicks Revert → app takes the same cached incoming (frame before flat), calls **`output_manual_from_module("flat_correction", incoming)`**, which runs only **downstream** steps (flat is skipped) and paints. Cache is updated for downstream modules so they don't "re-apply" flat.

```
  Example: flat_correction (slot 200)

  Before step:
    frame = output of dark_correction (slot 100)
    _pipeline_module_cache["flat_correction"] = { "frame": frame.copy() }  (incoming)

  process_frame(frame, gui):
    frame = api.incoming_frame("flat_correction", frame)
    if not api.alteration_auto_apply(gui, "flat_correction_auto_apply", True):
        return api.outgoing_frame("flat_correction", frame)
    out = divide_by_flat(frame, gui)
    return api.outgoing_frame("flat_correction", out)

  After step:
    frame = out  (goes to next module, e.g. bad_pixel_map)
```

### 3.4 Frame buffer and display

- **frame_buffer:** List of **processed** frames (each has gone through the **full** pipeline). Length is at most **integration_n** (e.g. Capture N = 5 → buffer holds up to 5 frames).
- **display_frame:** **`np.mean(frame_buffer, axis=0)`** — the integrated image shown in the view and used for histogram.
- At **acquisition start**, **clear_frame_buffer()** clears the list and **display_frame**, so the next run only uses frames from that run.
- **Manual Apply/Revert** does **not** change **frame_buffer**. It only paints the result of "run from module X onward" into the texture and sets **display_frame** to that result (so the display and histogram show the manual result until the next live frame or another manual action).

### 3.5 Manual Apply/Revert and pipeline cache

When **output_manual_from_module(module_name, frame)** is used (Apply or Revert):

1. **frame** is either the result of that module (Apply) or the incoming to that module (Revert).
2. **`_continue_pipeline_from_module(module_name, frame)`** runs the pipeline for all steps with **slot > module's slot**.
3. Inside **`_continue_pipeline_from_slot`**, **before each step** the app does **`_pipeline_module_cache[module_name] = { "token", "slot", "frame": current_frame.copy() }`**. So every downstream module's "incoming" in the cache is updated to what was actually used in this run.
4. The final image is painted and **display_frame** is set to it.

So after **Revert at dead_pixel**, the cache for **pincushion** (and all later modules) holds the frame that **did not** have dead_pixel applied. A subsequent **Apply at pincushion** then uses **get_module_incoming_image("pincushion")** and gets that reverted frame; modules no longer "re-apply" each other incorrectly.

---

## 4. Roles in detail

### 4.1 Camera (one active)

- **Only job:** Frame acquisition.
- **Owns:** Connection, gain, resolution, ROI, and modes: Single, Dual, Continuous, Capture N.
- **Does not own:** Dark/flat capture, corrections, or any workflow logic. It just submits frames via **`gui.api.submit_frame(frame)`** when the app has started acquisition.
- **Triggered by:** Acquisition (app layer).

### 4.2 Acquisition

- **Job:** Trigger the camera with the correct mode (single, dual, continuous, capture N).
- **Owns:** When to start/stop, which mode, how many frames (for Capture N).
- **Can involve:** Optional **power supply** (e.g. turn on before start, turn off after stop, or “keep beam on” between captures). Power supply modules hook into **acquisition start/stop**, not into the image pipeline.
- **Flow:** User (or workflow) clicks Start / Capture N → acquisition starts camera in the chosen mode → camera submits frames one by one.

### 4.3 Integration

- **Job:** Produce one result image from **multiple frames that have each gone through the full workflow**.
- **Important:** Integration is **not** “N raw frames → average.” It is:
  - For each of **N** frames: **camera → full pipeline** → one processed frame.
  - Then **integrate** (e.g. average) those **N processed frames** → one integrated image.
- So: **N × (camera → full workflow) → then integrate.** Every frame used for integration has already passed dark, flat, banding, distortion, etc.

### 4.4 Workflow pipeline (alteration modules)

- **Job:** Transform the image step by step, in a fixed order.
- **Contract:** Each step is **`process_frame(frame, gui) → frame`**. Order is defined by **`pipeline_slot`** (e.g. dark 100, flat 200, banding 300, dead pixel 400, distortion 450–455, image enhancement 480, crop 500, post-crop separator 600).
- **Module I/O standard:** alteration modules use:
  - `frame = gui.api.incoming_frame(MODULE_NAME, frame)` at the start of `process_frame`
  - `return gui.api.outgoing_frame(MODULE_NAME, frame_out)` at return
  This keeps module boundaries explicit and gives one place to extend input/output hooks later.
- **Loaded modules only:** Only enabled alteration modules run; their order is by slot.
- **Single responsibility:** Each alteration module does one thing (e.g. dark subtract, flat divide, banding correct, distort, crop). It reads state from **`gui.api`** (or gui) and returns the transformed image.

### 4.5 Display

- **Job:** Show the current result and update histogram.
- **Input:** The output of the pipeline (per frame) or the **integrated** result (after N frames have been captured and averaged). No extra processing here; just histogram + view.

### 4.6 Exceptions (not in the per-frame pipeline)

- **Manual apply/revert from a pipeline module**  
  Some modules expose manual actions while still being normal pipeline steps (for example, Image Enhancement at slot 480). In this case, manual actions should **not** start from current display. Instead they use:
  - **`gui.api.incoming_frame(module_name, frame, use_cached=True)`** or **`gui.api.get_module_incoming_image(module_name)`**: cached image before this module step for the current frame token
  - process from that cached source
  - **`gui.api.output_manual_from_module(module_name, frame)`**: continue only remaining downstream pipeline steps and paint result
  
  This prevents cumulative/double-application and keeps manual output consistent with pipeline order.

- **Power supply (and similar)**  
  **Do not** process images. They interact with **acquisition start/stop** (turn on/off, or “keep beam on”). They are part of the acquisition lifecycle, not the image workflow.

---

## 5. Summary table

| Part              | Single job                          | Uses API for              | Custom code for        |
|-------------------|-------------------------------------|---------------------------|-------------------------|
| **Camera**        | Frame acquisition only             | submit_frame, registration| Driver, ROI, modes     |
| **Acquisition**   | Trigger camera with mode            | start/stop, progress      | Mode logic, power hooks|
| **Integration**   | N × (camera → pipeline) → combine   | request_integration, etc. | Averaging / stacking   |
| **Pipeline step** | Image in → image out (one step)     | get_dark_field, etc.      | Algorithm               |
| **Display**       | Histogram + view                    | —                         | Windowing, texture     |
| **Image Enhancement** | Deconvolution + clarity/dehaze (auto + manual) | incoming_frame/outgoing_frame, output_manual_from_module | Enhancement algorithms |
| **Power supply**  | On/off around acquisition           | register_beam_supply      | Hardware protocol       |

---

## 6. Gap analysis: current code vs this design

How far the implementation is from the architecture above. Use this to prioritize cleanup or refactors.

### Aligned

| Design | Current code |
|--------|--------------|
| **Camera = acquisition only** | ASI and Hamamatsu only expose Single, Dual, Continuous, Capture N. They call `submit_frame`; no dark/flat logic. |
| **One active camera** | One `camera_module` is registered; UI builds from one enabled camera. |
| **Acquisition triggers camera** | `_start_acquisition(mode)` sets mode, clears buffer, optionally turns on beam (unless dark capture), calls `camera_module.start_acquisition(self)`. |
| **Power supply at start/stop** | `beam_supply` is used in `_start_acquisition` (turn on before start, turn off when idle in render tick). `workflow_keep_beam_on` skips per-capture on/off. |
| **Pipeline = ordered image→image** | `_alteration_pipeline` is `(slot, module_name, process_frame)`; `_push_frame` runs each step in order. Each step gets `(frame, gui)` and returns a frame. |
| **Integration = N fully processed frames** | Each camera frame goes through full `_push_frame` (all pipeline steps), then is appended to `frame_buffer`. `display_frame = np.mean(frame_buffer, axis=0)`. So we average **processed** frames. Buffer is cleared at start of each run. |
| **request_integration returns integrated result** | It starts Capture N with `integration_n = num_frames`, waits for idle, then returns `last_captured_frame` (copy of `display_frame`), i.e. the mean of N processed frames. |
| **Dark/flat capture in their modules** | Dark and flat reference capture are in `dark_correction.capture_dark` and `flat_correction.capture_flat`; they use `request_n_frames_processed_up_to_slot` (pipeline run only up to their slot). |
| **Image Enhancement = pipeline + manual** | `microcontrast_dehaze` runs as an alteration step (slot 480) and also supports manual apply/revert using module incoming-frame cache + downstream output API. |
| **Display = pipeline/integrated result** | Histogram and view are driven by `display_frame` (and deconv state). |

### Addressed (fixed)

| Issue | Fix |
|-------|-----|
| **Legacy mode check** | Removed `(mode == "dark" or mode == "flat")` from `_start_acquisition`; only `_capture_skip_beam` controls beam skip (e.g. dark reference capture). |
| **Banding cache on gui** | API now has `get_banding_optimized_win` / `set_banding_optimized_win` and `get_vertical_banding_optimized_win` / `set_vertical_banding_optimized_win`. Banding module uses the API only. |
| **Integration implicit** | `frame_buffer` and `display_frame` comments clarify “integration buffer” and “integrated result”. `_update_integrated_display()` computes and sets `display_frame` from the buffer; `_push_frame` calls it. |

### Optional (unchanged)

| Issue | Where | Notes |
|-------|--------|------|
| **Acquisition / integration not separate layers** | `gui.py` | Logic lives in the GUI (`_start_acquisition`, `request_integration`, `_push_frame`, `frame_buffer`). The doc treats them as “app-level”; no separate module is required. Optional: extract an “acquisition controller” later if desired. |

### Not wrong, just different

- **Rolling buffer:** We append each processed frame and take the mean; for Capture N we clear at start so we get exactly N frames. So “integrate” is “mean of last N processed frames” and matches the design.
- **Dark/flat capture path:** Uses `_capture_max_slot` so the pipeline runs only up to a slot and we collect frames (for reference capture). This is intentional and not the main display path.

### Summary

The implementation **matches** the architecture: camera is acquisition-only, pipeline is ordered image→image, integration is over fully processed frames (with explicit naming and `_update_integrated_display()`), power supply hooks into start/stop, and manual module actions can safely reuse per-module incoming-frame cache + downstream output API to avoid double application. The only remaining optional item is whether to extract acquisition/integration into a separate layer; behaviour is already correct.

---

## 7. Where to read more

- **API and module reference:** [CODE_REFERENCE.md](CODE_REFERENCE.md)
- **Module types and discovery:** [../modules/MODULES_OVERVIEW.md](../modules/MODULES_OVERVIEW.md)
- **Camera contract:** [../modules/README_DETECTOR_MODULES.md](../modules/README_DETECTOR_MODULES.md)
