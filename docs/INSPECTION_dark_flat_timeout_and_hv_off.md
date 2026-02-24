# Inspection: Dark/Flat timeouts (DC5) and HV Off crash

**Status: resolved.** Dark/flat capture with the DC5 and HV Off responsiveness (including after frame arrives) have been addressed. Fixes are listed at the end of this doc.

---

## Summary

Two related issues: (1) dark/flat capture sometimes times out with the DC5 sensor; (2) turning off Faxitron HV after that timeout can crash the application. Below are the likely causes and where they live in the code.

---

## 1. Why dark/flat can time out with DC5

### 1.1 Timeout formula (tight for DC5)

- **Dark/flat timeout** is computed in:
  - `modules/image_processing/dark_correction/__init__.py` (dark)
  - `modules/image_processing/flat_correction/__init__.py` (flat)
- Formula: `timeout_s = n * (t_int + readout_margin_s)` with `readout_margin_s = 5.0`.
- So for N frames and integration time T: total timeout = `N * (T + 5)` seconds.
- Example: 20 frames, 2 s integration → 20 * 7 = **140 s**.

DC5 can approach or exceed that if:

- USB or firmware is slow.
- Each frame can block for up to **integration_ms + 3000 ms** inside the driver (`lib/hamamatsu_dc5.py` `_capture_one_frame_sync`, and `modules/detector/hamamatsu_dc5/__init__.py` uses `timeout_ms = self._integration_ms(gui) + 3000`). So one frame can take up to ~5 s at 2 s integration; 20 frames could need 100 s even without extra delay. With any extra delay (retries, USB stalls), the overall dark/flat timeout can be hit.

### 1.2 No stop check during DC5 capture

- **DC5 worker** (`modules/detector/hamamatsu_dc5/__init__.py`):
  - `_run_worker` only checks `api.acquisition_should_stop()` **between** frames (in the `for i in range(n)` loop).
  - Each frame is acquired in `_do_single_shot` → `_trigger_and_read` → `self._cam.capture_one(timeout_ms=...)`.
- **`capture_one`** (`lib/hamamatsu_dc5.py`) blocks in USB `bulkRead` until a frame (or MSG_ABORTED) or timeout. It does **not** check `acq_stop` while waiting.
- So once a frame capture has started, the worker cannot react to Stop/timeout until that frame finishes (or times out). With long integration and/or slow readout, that can be many seconds per frame and the **total** wait can exceed the dark/flat timeout.

### 1.3 “Wait for idle” after timeout is short

- In `ui/pipeline.py`, `request_n_frames_processed_up_to_slot`:
  - On timeout it calls `gui._stop_acquisition()` and then waits for `acq_mode == "idle"` with a deadline of **`timeout_seconds + 5`** (i.e. at most 5 extra seconds).
  - If the DC5 worker is stuck in `capture_one()` for longer than that, the function **returns anyway** (with `None`) while:
    - `acq_mode` is still `"capture_n"`.
    - The acquisition thread is still running (blocked in the driver).
- So after a timeout the app can be in a state where the main thread has given up but the camera thread is still running. When that thread eventually exits, it will set `acq_mode = "idle"`, which triggers the “on idle” logic (including HV turn-off) in the main loop.

---

## 2. Why turning off Faxitron HV after timeout can crash

### 2.1 When HV is turned off

- **Automatic:** In `gui.py`, `_render_tick` runs on the main thread. When it sees a transition to idle (`_prev_acq_mode != "idle"` and `acq_mode == "idle"`), it calls `beam.turn_off()` (Faxitron HV off).
- **Manual:** User clicks “HV Off” in the Faxitron MX-20/DX-50 UI → `_make_hv_off_cb` runs on the main thread and calls `core.beam_off()`.

So both paths run on the **main thread** and go through the same Faxitron core.

### 2.2 Faxitron `beam_off()` can block or raise

- **File:** `modules/machine/faxitron_mx20_dx50/__init__.py`, `FaxitronMX20DX50Core.beam_off()`.
- It sends `"A"` (with `try/except`), then enters a **while loop** (up to `BEAM_S_TIMEOUT_S` = 15 s) waiting for a line containing `"S"` from the machine.
- In that loop it calls `_read_line_no_lock(timeout_s=0.5)` which uses `self._ser.readline()`. That can:
  - **Block** for up to 15 s if the machine never sends `"S"` (e.g. unit busy, not responding, or communication glitch after a long capture/timeout). The main thread is blocked → UI freezes and timers/callbacks can stack up.
  - **Raise** (e.g. `SerialException`) if the serial port is closed, disconnected, or in an error state. The loop is **not** wrapped in try/except, so any exception in the loop propagates and can **crash the application** on the main thread.

So after a stressful sequence (long dark/flat, timeout, possible USB/DC5 issues), serial or machine state can be such that the next HV Off (manual or automatic) blocks for a long time or hits an I/O error and crashes.

### 2.3 Lock and threading

- Faxitron core uses a single `threading.Lock()` for serial access. HV On for the “HV On” button is started in a **daemon thread** (`threading.Thread(target=_do, daemon=True).start()`). So normally only one of “HV On” or “HV Off” runs at a time; if the daemon thread were still in `beam_on()` and holding the lock, a later `beam_off()` on the main thread would block on the lock. That could contribute to UI freeze but is less likely to be the direct cause of a crash than an uncaught exception in `beam_off()`.

---

## 3. Relevant code locations

| Topic | File(s) |
|-------|--------|
| Dark/flat timeout formula | `modules/image_processing/dark_correction/__init__.py`, `flat_correction/__init__.py` |
| Request N frames, timeout and “wait for idle” | `ui/pipeline.py` → `request_n_frames_processed_up_to_slot` |
| DC5 worker and no stop inside capture | `modules/detector/hamamatsu_dc5/__init__.py` → `_run_worker`, `_do_single_shot`, `_trigger_and_read` |
| DC5 per-frame timeout | `lib/hamamatsu_dc5.py` → `_capture_one_frame_sync`; `modules/detector/hamamatsu_dc5/__init__.py` → `timeout_ms = _integration_ms(gui) + 3000` |
| Stop flag only sets event | `gui.py` → `_stop_acquisition()` only `acq_stop.set()` |
| Idle transition and auto HV off | `gui.py` → `_render_tick` (transition to idle → `beam.turn_off()`) |
| Faxitron beam off (blocking + uncaught exception) | `modules/machine/faxitron_mx20_dx50/__init__.py` → `FaxitronMX20DX50Core.beam_off()` |
| Manual HV Off button | `modules/machine/faxitron_mx20_dx50/__init__.py` → `_make_hv_off_cb` |

---

## 4. Recommended directions (for later fixes)

1. **Timeouts**
   - Increase dark/flat readout margin for DC5 (e.g. camera-specific multiplier or larger `readout_margin_s` when DC5 is active).
   - Optionally increase the “wait for idle” window after timeout (e.g. more than 5 s), or make it configurable, so the main thread doesn’t return while the worker is still likely stuck in one long frame.

2. **Stop responsiveness (DC5)**
   - Where possible, have the DC5 driver check a stop callback or make `capture_one` interruptible (e.g. shorter read timeouts and re-check between reads) so that timeout/Stop is seen sooner.

3. **HV Off robustness**
   - In `beam_off()`, wrap the entire “send A and wait for S” loop in try/except; on exception, log and return False instead of crashing.
   - Consider running the “wait for S” loop in a worker thread with a timeout and only block the main thread for a short time (e.g. 1 s) so the UI stays responsive; or at least cap the total wait and return False if the machine doesn’t respond.

4. **State after timeout**
   - Ensure that after a dark/flat timeout we don’t leave the system in a half-on state (e.g. ensure capture state and acq_mode are consistent, and that any automatic HV turn-off is safe even if it runs late when the worker finally exits).

---

## Fixes applied

1. **Faxitron `beam_off()`** (`modules/machine/faxitron_mx20_dx50/__init__.py`): Wrapped the entire send-A + wait-for-S logic in a top-level try/except. On any exception (e.g. serial I/O error), the method returns `False` and no longer crashes the app.
2. **Dark/flat timeout**: Increased `readout_margin_s` from 5.0 to 8.0 in both `dark_correction` and `flat_correction` so DC5/slow USB has more headroom before overall timeout.
3. **Wait for idle** (`ui/pipeline.py`): After a timeout, the main thread now waits up to **15 s** (was 5 s) for `acq_mode == "idle"` so the DC5 worker is more likely to exit before we return.
4. **DC5 abort during frame** (`lib/hamamatsu_dc5.py` + `modules/detector/hamamatsu_dc5/__init__.py`): Added optional `should_abort` to `_capture_one_frame_sync` and `capture_one`. When set, bulk reads use a 5 s per-read timeout and we check `should_abort()` each loop; DC5 module passes `api.acquisition_should_stop`. Added 0.15 s delay between readout and next capture for capture_n and continuous.

5. **HV Off / UI responsive after frame** (`lib/hamamatsu_dc5.py`, `ui/pipeline.py`): (a) MSG_END read after image data now uses a **1.5 s** timeout (not 5 s) so we don’t block long after the frame is in. (b) `time.sleep(0)` added after each bulk read in the DC5 driver (when `should_abort` set) and after the MSG_END read to yield the GIL so the main thread can process HV Off. (c) `time.sleep(0)` at the end of `push_frame` and after the dark/flat capture append so the main thread gets a time slice once the frame is in the pipeline. Together these address sluggish UI and HV Off not triggering right after a capture.

**HV Off and USB timeout:** Dark/flat capture **already runs in a background thread** (`gui._cb_capture_dark` / `_cb_capture_flat` start a `threading.Thread` and return immediately), so the main thread is not blocked by `request_n_frames_processed_up_to_slot`. The GIL yields above ensure the main thread can run between reads and after each frame so HV Off (Faxitron DX50) stays responsive during and after capture.
