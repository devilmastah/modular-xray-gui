"""
Dark correction image alteration module.
Subtracts the loaded dark field from each frame (with optional scale-matching for different bit depths).
Runs at pipeline slot 100 (before flat). Dark data and load/save remain in the main app.
"""

import numpy as np

MODULE_INFO = {
    "display_name": "Dark correction",
    "description": "Subtract dark field from frames. Applies on next startup.",
    "type": "alteration",
    "default_enabled": True,
    "pipeline_slot": 100,
}
MODULE_NAME = "dark_correction"


def get_setting_keys():
    return ["dark_correction_auto_apply"]


def get_default_settings():
    """Return default settings for this module."""
    return {
        "dark_stack_n": 20,
        "dark_correction_auto_apply": True,
    }


def get_settings_for_save(gui=None):
    """Return dark_stack_n and dark_correction_auto_apply from our UI or from gui state when module is disabled."""
    import dearpygui.dearpygui as dpg
    out = {}
    if dpg.does_item_exist("dark_stack_slider"):
        out["dark_stack_n"] = int(dpg.get_value("dark_stack_slider"))
    elif gui is not None:
        out["dark_stack_n"] = gui.api.get_dark_capture_stack_count()
    else:
        out["dark_stack_n"] = 20
    if dpg.does_item_exist("dark_correction_auto_apply"):
        out["dark_correction_auto_apply"] = bool(dpg.get_value("dark_correction_auto_apply"))
    elif gui is not None:
        out["dark_correction_auto_apply"] = getattr(gui, "dark_correction_auto_apply", True)
    else:
        out["dark_correction_auto_apply"] = True
    return out


def _apply_dark(frame: np.ndarray, gui) -> np.ndarray:
    """Subtract dark from frame if loaded and shape matches; otherwise return frame unchanged."""
    api = gui.api
    dark = api.get_dark_field()
    if dark is None or dark.shape != frame.shape:
        return np.asarray(frame, dtype=np.float32)
    frame = np.asarray(frame, dtype=np.float32)
    d_min, d_max = float(dark.min()), float(dark.max())
    f_min, f_max = float(frame.min()), float(frame.max())
    f_range = f_max - f_min + 1e-10
    if f_range > 1e-6 and (f_max > 1.5 * d_max or f_max > 5000):
        scale = (d_max - d_min + 1e-6) / f_range
        frame = (frame - f_min) * scale + d_min
    return frame - dark


def process_frame(frame: np.ndarray, gui) -> np.ndarray:
    """
    Subtract dark field from frame if loaded and shape matches.
    If frame and dark have very different value ranges (e.g. 16-bit TIFF vs 12-bit dark),
    scale frame to dark's range so same-content images subtract to ~0.
    """
    api = gui.api
    frame = api.incoming_frame(MODULE_NAME, frame)
    if not api.alteration_auto_apply(gui, "dark_correction_auto_apply", default=True):
        return api.outgoing_frame(MODULE_NAME, frame)
    out = _apply_dark(frame, gui)
    return api.outgoing_frame(MODULE_NAME, out)


def capture_dark(gui) -> bool:
    """
    Capture a dark reference: request N frames with pipeline run only up to this module's slot (exclusive),
    so any modules before dark in the workflow are applied; then average, set dark field, save.
    Called by the app when the user clicks Capture Dark. Returns True if capture succeeded.
    """
    api = gui.api
    if not api.is_camera_connected():
        api.set_status_message("Not connected")
        return False
    n = api.get_dark_capture_stack_count()
    t_int = api.get_integration_time_seconds()
    # Timeout: n * (integration + per-frame readout margin). Same formula as flat.
    readout_margin_s = 5.0
    timeout_s = n * (t_int + readout_margin_s)
    if api.get_camera_uses_dual_shot_for_capture_n():
        timeout_s *= 2  # dual shot = 2 exposures per frame (e.g. C7942)
    api.set_progress(0.0, f"Capturing dark ({n} frames)... Click Stop to cancel.")
    avg = api.request_n_frames_processed_up_to_slot(
        n, max_slot=MODULE_INFO["pipeline_slot"], timeout_seconds=timeout_s, dark_capture=True
    )
    if avg is None:
        api.set_status_message("Dark capture failed (timeout or stopped). Try fewer frames or shorter integration.")
        return False
    api.set_dark_field(avg)
    api.save_dark_field()
    api.set_progress(1.0)
    api.set_status_message(f"Master dark saved ({n} frames avg, {api.get_integration_time_seconds()}s)")
    api.show_preview_in_main_view(avg)
    return True


def build_ui(gui, parent_tag: str = "control_panel") -> None:
    """Optional: show dark correction status. Capture/Clear are triggered by main app."""
    import dearpygui.dearpygui as dpg
    api = gui.api

    def _status():
        dark = api.get_dark_field()
        if dark is not None:
            return f"Active ({dark.shape[1]}×{dark.shape[0]})"
        return "No dark loaded"

    def _cb_apply(g):
        raw = g.api.get_module_incoming_image(MODULE_NAME)
        if raw is None:
            g.api.set_status_message("No frame available (run acquisition first).")
            return
        g._dark_correction_revert_snapshot = raw.copy()
        out = _apply_dark(raw, g)
        g.api.output_manual_from_module(MODULE_NAME, out)
        g.api.set_status_message("Dark correction applied to current frame.")

    with dpg.collapsing_header(parent=parent_tag, label="Dark correction", default_open=False):
        with dpg.group(indent=10):
            api.build_alteration_apply_revert_ui(
                gui,
                MODULE_NAME,
                _cb_apply,
                auto_apply_attr="dark_correction_auto_apply",
                revert_snapshot_attr="_dark_correction_revert_snapshot",
                default_auto_apply=True,
            )
            dpg.add_text("Subtracts dark field when loaded.", color=[150, 150, 150])
            dpg.add_text(_status(), tag="dark_correction_status")
