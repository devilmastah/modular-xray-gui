"""
CT capture workflow_automation module.
Runs multiple projections: for each, (placeholder) rotate command, then triggers capture (uses N from Integration), save TIFF.
Saves to captures/<YYYY-MM-DD_HH-MM-SS>/0.tif, 1.tif, ...
"""

import threading
import time
from pathlib import Path

import numpy as np
import dearpygui.dearpygui as dpg

# App directory for captures/ (sibling of modules)
_APP_DIR = Path(__file__).resolve().parent.parent.parent
CAPTURES_DIR = _APP_DIR / "captures"

MODULE_INFO = {
    "display_name": "CT capture",
    "description": "Workflow: multiple projections, rotate (placeholder), stack frames, save TIFFs. Applies on next startup.",
    "type": "workflow_automation",
    "default_enabled": False,
}


def get_setting_keys():
    """Keys this module persists."""
    return [
        "ct_num_projections",
        "ct_total_rotation_deg",
        "ct_settle_delay_ms",
        "ct_keep_hv_on",
    ]


# Spec for api.get_module_settings_for_save: (key, tag, converter, default)
_CT_SAVE_SPEC = [
    ("ct_num_projections", "ct_num_projections", int, 360),
    ("ct_total_rotation_deg", "ct_total_rotation_deg", float, 360.0),
    ("ct_settle_delay_ms", "ct_settle_delay_ms", int, 0),
    ("ct_keep_hv_on", "ct_keep_hv_on", lambda x: bool(x), False),
]


def get_default_settings():
    """Return default settings for this module (extracted from save spec)."""
    return {key: default for key, _tag, _conv, default in _CT_SAVE_SPEC}


def get_settings_for_save(gui=None):
    """Return current CT settings from UI or loaded settings (auto fallback when UI not built)."""
    if gui is None or not getattr(gui, "api", None):
        return {}
    return gui.api.get_module_settings_for_save(_CT_SAVE_SPEC)


def _get_int(s: dict, key: str, default: int, lo: int, hi: int) -> int:
    """Get int from settings, handling JSON null; clamp to [lo, hi]."""
    v = s.get(key)
    if v is None:
        v = default
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return max(lo, min(hi, default))


def _get_float(s: dict, key: str, default: float, lo: float, hi: float) -> float:
    """Get float from settings, handling JSON null; clamp to [lo, hi]."""
    v = s.get(key)
    if v is None:
        v = default
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return max(lo, min(hi, default))


def build_ui(gui, parent_tag="control_panel"):
    api = gui.api
    s = api.get_loaded_settings()
    with dpg.collapsing_header(parent=parent_tag, label="CT capture", default_open=True):
        with dpg.group(indent=10):
            dpg.add_text("Multi-projection capture for CT. Rotate (placeholder), then capture (N from Integration), save TIFFs.", color=[150, 150, 150])
            # Input width ~half of panel so value text stays inside (panel is 350px)
            _w = 120
            num_proj = _get_int(s, "ct_num_projections", 360, 1, 3600)
            total_rot = _get_float(s, "ct_total_rotation_deg", 360.0, 0.01, 3600.0)
            step_deg = total_rot / max(1, num_proj - 1) if num_proj > 1 else 0.0
            def _update_step_hint():
                api.save_settings()
                if dpg.does_item_exist("ct_step_hint"):
                    try:
                        n = int(dpg.get_value("ct_num_projections"))
                        t = float(dpg.get_value("ct_total_rotation_deg"))
                        step = t / max(1, n - 1) if n > 1 else 0.0
                        dpg.set_value("ct_step_hint", f"→ {step:.3f}° per step")
                    except (TypeError, ValueError):
                        pass
            dpg.add_input_int(
                label="Number of projections",
                default_value=num_proj,
                min_value=1,
                max_value=3600,
                min_clamped=True,
                max_clamped=True,
                tag="ct_num_projections",
                width=_w,
                callback=lambda _s, _a: _update_step_hint(),
            )
            dpg.add_input_float(
                label="Total rotation (degrees)",
                default_value=total_rot,
                min_value=0.01,
                max_value=3600.0,
                min_clamped=True,
                max_clamped=True,
                tag="ct_total_rotation_deg",
                width=_w,
                callback=lambda _s, _a: _update_step_hint(),
            )
            dpg.add_text(f"→ {step_deg:.3f}° per step", color=[140, 140, 140], tag="ct_step_hint")
            dpg.add_text("Uses N frames from Integration section.", color=[120, 120, 120])
            settle_ms = _get_int(s, "ct_settle_delay_ms", 500, 0, 60000)
            dpg.add_input_int(
                label="Settle delay (ms)",
                default_value=settle_ms,
                min_value=0,
                max_value=60000,
                min_clamped=True,
                max_clamped=True,
                tag="ct_settle_delay_ms",
                width=_w,
                callback=lambda _s, _a: api.save_settings(),
            )
            keep_hv = bool(s.get("ct_keep_hv_on") if s.get("ct_keep_hv_on") is not None else False)
            dpg.add_checkbox(
                label="Keep HV on",
                default_value=keep_hv,
                tag="ct_keep_hv_on",
                callback=lambda _s, _a: api.save_settings(),
            )
            dpg.add_text("Leave beam on between projections (no auto turn-off).", color=[120, 120, 120])
            dpg.add_text("Motor/rotate: placeholder (no electronics yet).", color=[180, 160, 100])
            dpg.add_button(
                label="Start CT scan",
                tag="ct_start_btn",
                callback=lambda: _start_ct_scan(gui),
                width=-1,
            )
            dpg.add_text("Idle", tag="ct_status", color=[150, 150, 150])


def _send_rotate_placeholder(angle_deg: float, gui) -> None:
    """Placeholder: would send 'rotate angle_deg' to microcontroller. No-op until hardware is in place."""
    # TODO: e.g. serial write to motor controller, or gui.teensy.send_rotate(angle_deg)
    pass


def _run_ct_worker(gui):
    """Run in background: for each projection, rotate (placeholder), wait, request_integration, save TIFF."""
    api = gui.api
    try:
        num_proj = int(dpg.get_value("ct_num_projections"))
        total_rot_deg = float(dpg.get_value("ct_total_rotation_deg"))
        step_deg = total_rot_deg / max(1, num_proj - 1) if num_proj > 1 else 0.0
        settle_ms = int(dpg.get_value("ct_settle_delay_ms"))
        keep_hv_on = bool(dpg.get_value("ct_keep_hv_on"))
        stack_n = int(dpg.get_value("integ_n_slider")) if dpg.does_item_exist("integ_n_slider") else max(1, min(32, getattr(gui, "integration_n", 1)))
    except (TypeError, ValueError):
        api.set_status_message("CT: invalid parameters")
        if dpg.does_item_exist("ct_status"):
            dpg.set_value("ct_status", "Invalid parameters")
        return

    # Apply "Keep HV on" only if a beam supply is present; otherwise ignore (run acquisition without HV control).
    beam = api.get_beam_supply()
    have_beam = beam is not None and getattr(beam, "wants_auto_on_off", lambda: False)() and beam.is_connected()
    api.set_workflow_keep_beam_on(keep_hv_on and have_beam)

    try:
        CAPTURES_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        out_dir = CAPTURES_DIR / timestamp
        out_dir.mkdir(parents=True, exist_ok=True)

        api.set_status_message(f"CT scan started: {num_proj} projections → {out_dir}")
        if dpg.does_item_exist("ct_status"):
            dpg.set_value("ct_status", f"Running 0/{num_proj}...")

        try:
            import tifffile
        except ImportError:
            api.set_status_message("CT: tifffile required for saving")
            if dpg.does_item_exist("ct_status"):
                dpg.set_value("ct_status", "Error: install tifffile")
            return

        # When Keep HV on (and we have a beam supply): turn beam on once so we don't cycle it per projection
        if keep_hv_on and have_beam:
            if dpg.does_item_exist("ct_status"):
                dpg.set_value("ct_status", "Waiting for HV...")
            if not beam.turn_on_and_wait_ready(timeout_s=30.0):
                api.set_status_message("CT: HV supply did not become ready")
                if dpg.does_item_exist("ct_status"):
                    dpg.set_value("ct_status", "HV not ready")
                return

        for i in range(num_proj):
            if getattr(gui, "ct_scan_stop", None) and gui.ct_scan_stop.is_set():
                api.set_status_message("CT scan stopped by user")
                if dpg.does_item_exist("ct_status"):
                    dpg.set_value("ct_status", f"Stopped at {i}/{num_proj}")
                return

            angle_deg = i * step_deg
            _send_rotate_placeholder(angle_deg, gui)
            if settle_ms > 0:
                time.sleep(settle_ms / 1000.0)

            if dpg.does_item_exist("ct_status"):
                dpg.set_value("ct_status", f"Capturing {i + 1}/{num_proj}...")
            # Timeout per projection: allow long exposures × N frames (default 300 s = 5 min)
            frame = api.request_integration(stack_n, timeout_seconds=300.0)
            if frame is None:
                reason = api.get_last_integration_fail_reason() or "unknown"
                api.set_status_message(f"CT: projection {i} failed ({reason})")
                if dpg.does_item_exist("ct_status"):
                    dpg.set_value("ct_status", f"Failed at {i}/{num_proj} ({reason})")
                return

            out_path = out_dir / f"{i}.tif"
            try:
                arr = np.asarray(frame, dtype=np.float32)
                finite = np.isfinite(arr)
                if not np.any(finite):
                    raise ValueError("frame has no finite values")
                lo = float(np.min(arr[finite]))
                hi = float(np.max(arr[finite]))
                if hi <= lo:
                    arr16 = np.zeros(arr.shape, dtype=np.uint16)
                else:
                    safe = np.nan_to_num(arr, nan=lo, posinf=hi, neginf=lo)
                    scaled = (safe - lo) / (hi - lo)
                    arr16 = np.clip(np.rint(scaled * 65535.0), 0.0, 65535.0).astype(np.uint16)
                tifffile.imwrite(out_path, arr16, photometric="minisblack")
                print(
                    f"[CT] saved projection {i} as 16-bit normalized "
                    f"(min={lo:.3f}, max={hi:.3f}) -> {out_path}",
                    flush=True,
                )
            except Exception as e:
                api.set_status_message(f"CT: save failed {out_path}: {e}")
                if dpg.does_item_exist("ct_status"):
                    dpg.set_value("ct_status", f"Save error at {i}")
                return

        api.set_status_message(f"CT scan complete: {num_proj} projections → {out_dir}")
        if dpg.does_item_exist("ct_status"):
            dpg.set_value("ct_status", f"Done {num_proj}/{num_proj}")
    finally:
        was_keep_hv = getattr(gui, "workflow_keep_beam_on", False)
        api.set_workflow_keep_beam_on(False)
        if was_keep_hv and have_beam and beam is not None:
            beam.turn_off()
        _set_ct_button_state(gui, running=False)


def _set_ct_button_state(gui, running: bool):
    """Set Start/Abort button label and callback. running=True: Abort CT scan; False: Start CT scan."""
    if not dpg.does_item_exist("ct_start_btn"):
        return
    if running:
        dpg.set_item_label("ct_start_btn", "Abort CT scan")
        dpg.configure_item("ct_start_btn", callback=lambda: _abort_ct_scan(gui))
    else:
        dpg.set_item_label("ct_start_btn", "Start CT scan")
        dpg.configure_item("ct_start_btn", callback=lambda: _start_ct_scan(gui))


def _abort_ct_scan(gui):
    """Set stop flag so the CT worker exits at next check."""
    if getattr(gui, "ct_scan_stop", None) is not None:
        gui.ct_scan_stop.set()
        api.set_status_message("CT scan abort requested...")


def _start_ct_scan(gui):
    """Start CT scan in a daemon thread."""
    if not gui.api.is_camera_connected():
        gui.api.set_status_message("Connect a camera first")
        return
    gui.ct_scan_stop = threading.Event()
    t = threading.Thread(target=_run_ct_worker, args=(gui,), daemon=True)
    t.start()
    _set_ct_button_state(gui, running=True)
