"""
Frame pipeline: push_frame, pipeline-from-slot/module, request_n_frames for dark/flat capture.
All functions take the GUI instance. Used by gui.py and AppAPI.
"""

import time
import numpy as np


def frame_log_signature(gui, frame: np.ndarray):
    arr = np.asarray(frame)
    shape = tuple(arr.shape)
    dtype = str(arr.dtype)
    if arr.size == 0:
        return shape, dtype, [0.0]
    flat = arr.reshape(-1)
    idx = np.linspace(0, flat.size - 1, num=min(9, flat.size), dtype=np.int64)
    vals = [float(flat[int(i)]) for i in idx]
    return shape, dtype, vals


def log_pipeline_step(gui, context: str, token: int, slot: int, module_name: str, frame_in, frame_out):
    """Compact per-step pipeline diagnostics for module manipulations."""
    try:
        in_shape, in_dtype, in_vals = frame_log_signature(gui, frame_in)
        out_shape, out_dtype, out_vals = frame_log_signature(gui, frame_out)
        if len(in_vals) == len(out_vals):
            sample_mad = float(np.mean(np.abs(np.asarray(out_vals) - np.asarray(in_vals))))
        else:
            sample_mad = float("nan")
        changed = (in_shape != out_shape) or (in_dtype != out_dtype) or (sample_mad > 1e-9)
        print(
            f"[Pipeline][{context}] token={token} slot={slot} module={module_name} "
            f"in={in_shape}/{in_dtype} out={out_shape}/{out_dtype} "
            f"changed={changed} sample_mad={sample_mad:.6g}",
            flush=True,
        )
    except Exception as e:
        print(
            f"[Pipeline][{context}] token={token} slot={slot} module={module_name} "
            f"log-error={e}",
            flush=True,
        )


def push_frame(gui, frame):
    """Apply alteration pipeline (dark, flat, etc.), then banding, dead pixel, distortion, crop; buffer and signal.
    When _capture_max_slot is set, run only steps with slot < _capture_max_slot and collect result (for dark/flat capture)."""
    max_slot = getattr(gui, "_capture_max_slot", None)
    pipeline = getattr(gui, "_alteration_pipeline", [])
    gui._pipeline_frame_token += 1
    frame_token = gui._pipeline_frame_token

    if max_slot is not None:
        for slot, module_name, step in pipeline:
            if slot >= max_slot:
                break
            gui._pipeline_module_cache[module_name] = {
                "token": frame_token,
                "slot": slot,
                "frame": frame.copy(),
            }
            frame_in = frame
            try:
                frame = step(frame, gui)
            except Exception as e:
                print(
                    f"[Pipeline][capture] token={frame_token} slot={slot} module={module_name} "
                    f"step-error={e}",
                    flush=True,
                )
                raise
            log_pipeline_step(gui, "capture", frame_token, slot, module_name, frame_in, frame)
        with gui.frame_lock:
            gui._capture_frames_collect.append(frame.copy())
            if len(gui._capture_frames_collect) >= getattr(gui, "_capture_n", 0):
                gui._capture_frames_ready.set()
            gui._pending_preview_frame = np.mean(
                gui._capture_frames_collect, axis=0
            ).astype(np.float32).copy()
        return

    frame_before_distortion = None
    for slot, module_name, step in pipeline:
        if slot >= gui.DISTORTION_PREVIEW_SLOT and frame_before_distortion is None:
            frame_before_distortion = frame.copy()
        gui._pipeline_module_cache[module_name] = {
            "token": frame_token,
            "slot": slot,
            "frame": frame.copy(),
        }
        frame_in = frame
        try:
            frame = step(frame, gui)
        except Exception as e:
            print(
                f"[Pipeline][live] token={frame_token} slot={slot} module={module_name} "
                f"step-error={e}",
                flush=True,
            )
            raise
        log_pipeline_step(gui, "live", frame_token, slot, module_name, frame_in, frame)

    with gui.frame_lock:
        if frame_before_distortion is not None:
            gui._frame_before_distortion = frame_before_distortion
        gui.raw_frame = frame
        gui.frame_buffer.append(frame)
        if len(gui.frame_buffer) > gui.integration_n:
            gui.frame_buffer = gui.frame_buffer[-gui.integration_n:]
        gui._update_integrated_display()

    gui.frame_count += 1
    now = time.time()
    gui._fps_count += 1
    dt = now - gui._fps_time
    if dt >= 1.0:
        gui.fps = gui._fps_count / dt
        gui._fps_count = 0
        gui._fps_time = now

    gui.new_frame_ready.set()


def get_module_incoming_image(gui, module_name: str):
    item = gui._pipeline_module_cache.get(module_name)
    if not item:
        return None
    frame = item.get("frame")
    return frame.copy() if frame is not None else None


def incoming_frame_for_module(gui, module_name: str, frame: np.ndarray, use_cached: bool = False):
    if use_cached:
        cached = get_module_incoming_image(gui, module_name)
        if cached is not None:
            return cached
    return frame


def get_module_incoming_token(gui, module_name: str):
    item = gui._pipeline_module_cache.get(module_name)
    if not item:
        return None
    return int(item.get("token", 0))


def continue_pipeline_from_slot(gui, frame: np.ndarray, start_slot_exclusive: int):
    """Run pipeline from slot > start_slot_exclusive. Updates _pipeline_module_cache for each
    module run so get_module_incoming_image() reflects the last manual or live run."""
    out = np.asarray(frame, dtype=np.float32)
    token = int(getattr(gui, "_pipeline_frame_token", 0))
    for slot, _module_name, step in getattr(gui, "_alteration_pipeline", []):
        if slot <= start_slot_exclusive:
            continue
        gui._pipeline_module_cache[_module_name] = {
            "token": token,
            "slot": slot,
            "frame": out.copy(),
        }
        frame_in = out
        try:
            out = step(out, gui)
        except Exception as e:
            print(
                f"[Pipeline][continue] token={token} slot={slot} module={_module_name} "
                f"step-error={e}",
                flush=True,
            )
            raise
        log_pipeline_step(gui, "continue", token, slot, _module_name, frame_in, out)
    return out


def continue_pipeline_from_module(gui, module_name: str, frame: np.ndarray):
    slot = gui._pipeline_module_slots.get(module_name, None)
    if slot is None:
        for s, n, _pf in getattr(gui, "_alteration_pipeline", []):
            if n == module_name:
                slot = s
                gui._pipeline_module_slots[module_name] = s
                break
    if slot is None:
        print(
            f"[Pipeline][manual-continue] module={module_name} not in slot map; "
            f"falling back to full pipeline continuation",
            flush=True,
        )
        slot = -1
    downstream = [n for s, n, _pf in getattr(gui, "_alteration_pipeline", []) if s > slot]
    print(
        f"[Pipeline][manual-continue] module={module_name} start_slot={slot} downstream={downstream}",
        flush=True,
    )
    return continue_pipeline_from_slot(gui, frame, slot)


def output_manual_from_module(gui, module_name: str, frame: np.ndarray):
    out = continue_pipeline_from_module(gui, module_name, frame)
    with gui.frame_lock:
        gui.display_frame = out.copy()
    gui._display_mode = "live"
    gui._paint_texture_from_frame(out)
    gui._force_image_refresh()
    print(
        f"[Pipeline][manual-output] module={module_name} mode=live painted=1",
        flush=True,
    )
    return out


def outgoing_frame_from_module(gui, module_name: str, frame: np.ndarray):
    return frame


def request_n_frames_processed_up_to_slot(
    gui, n: int, max_slot: int, timeout_seconds: float = 300.0, dark_capture: bool = False
):
    """
    Run camera capture_n for N frames, running the pipeline only for steps with slot < max_slot,
    collect the results and return their average (float32). Used by dark/flat modules for capture.
    dark_capture=True skips turning on the beam (for dark reference). Returns None on timeout/error.
    """
    if gui.camera_module is None or not gui.camera_module.is_connected():
        return None
    if gui.acq_mode != "idle":
        return None
    gui._capture_max_slot = max_slot
    gui._capture_frames_collect = []
    gui._capture_n = n
    gui._capture_frames_ready.clear()
    gui._capture_skip_beam = dark_capture
    gui.integration_n = n
    gui.acq_stop.clear()
    gui._progress = 0.0
    gui.clear_frame_buffer()

    if not dark_capture:
        beam = getattr(gui, "beam_supply", None)
        if beam is not None and beam.wants_auto_on_off() and not beam.is_connected():
            if not getattr(gui, "workflow_keep_beam_on", False):
                gui._status_msg = "Auto On/Off enabled but supply not connected"
                return None
        if beam is not None and beam.wants_auto_on_off() and beam.is_connected():
            if not getattr(gui, "workflow_keep_beam_on", False):
                gui._progress_text = "Waiting for supply... (click Stop to cancel)"
                if not beam.turn_on_and_wait_ready(should_cancel=lambda: gui.acq_stop.is_set()):
                    gui._progress_text = ""
                    gui._capture_max_slot = None
                    gui._capture_frames_collect = []
                    gui._capture_n = 0
                    gui._capture_skip_beam = False
                    if gui.acq_stop.is_set():
                        gui._status_msg = "Acquisition cancelled"
                    else:
                        gui._status_msg = "Supply did not become ready (timeout or fault)"
                    return None
                gui._progress_text = ""

    gui.acq_mode = "capture_n"
    gui.camera_module.start_acquisition(gui)
    t0 = time.time()
    while not gui._capture_frames_ready.wait(timeout=0.2):
        if gui.acq_stop.is_set():
            gui._stop_acquisition()
            break
        if (time.time() - t0) > timeout_seconds:
            gui._stop_acquisition()
            break
    # Wait for worker to reach idle (e.g. DC5 can be stuck in one long frame). Give extra time.
    while gui.acq_mode != "idle" and (time.time() - t0) < timeout_seconds + 15:
        if gui.acq_stop.is_set():
            break
        time.sleep(0.05)
    collected = getattr(gui, "_capture_frames_collect", [])
    gui._capture_max_slot = None
    gui._capture_frames_collect = []
    gui._capture_n = 0
    gui._capture_skip_beam = False
    if len(collected) < n:
        return None
    return np.mean(collected, axis=0).astype(np.float32)
