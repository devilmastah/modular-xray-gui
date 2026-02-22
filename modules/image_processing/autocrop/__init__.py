"""
Autocrop alteration module.
Crops the image to a rectangle (x_start, x_end, y_start, y_end). Applied at the end of the
alteration pipeline (slot 500) so it only affects the final view. Uses 0,0,0,0 as "no crop".
"""

import time

MODULE_INFO = {
    "display_name": "Autocrop",
    "description": "Crop image to a rectangle (x/y start and end). Applies on next startup.",
    "type": "image_processing",
    "default_enabled": True,
    "pipeline_slot": 500,
}
MODULE_NAME = "autocrop"


def get_setting_keys():
    return ["crop_x_start", "crop_x_end", "crop_y_start", "crop_y_end", "autocrop_auto_apply"]


# Spec for api.get_module_settings_for_save: (key, tag, converter, default)
_AUTOCROP_SAVE_SPEC = [
    ("crop_x_start", "crop_x_start", int, 0),
    ("crop_x_end", "crop_x_end", int, 0),
    ("crop_y_start", "crop_y_start", int, 0),
    ("crop_y_end", "crop_y_end", int, 0),
]


def get_default_settings():
    """Return default settings for this module (extracted from save spec)."""
    out = {key: default for key, _tag, _conv, default in _AUTOCROP_SAVE_SPEC}
    out["autocrop_auto_apply"] = True
    return out


def get_settings_for_save(gui=None):
    """Return crop coordinates and auto_apply from UI or loaded settings (auto fallback when UI not built)."""
    if gui is None or not getattr(gui, "api", None):
        return {}
    out = gui.api.get_module_settings_for_save(_AUTOCROP_SAVE_SPEC)
    import dearpygui.dearpygui as dpg
    if dpg.does_item_exist("autocrop_auto_apply"):
        out["autocrop_auto_apply"] = bool(dpg.get_value("autocrop_auto_apply"))
    else:
        out["autocrop_auto_apply"] = getattr(gui, "autocrop_auto_apply", True)
    return out


def _apply_autocrop(frame, gui):
    """Apply crop to frame. Used by pipeline and by manual Apply."""
    import numpy as np
    api = gui.api
    h, w = frame.shape[0], frame.shape[1]
    x_start, y_start, x_end, y_end = api.get_crop_region()
    if x_end <= x_start or y_end <= y_start:
        return np.asarray(frame, dtype=np.float32)
    x_start = max(0, min(x_start, w - 1))
    x_end = max(x_start + 1, min(x_end, w))
    y_start = max(0, min(y_start, h - 1))
    y_end = max(y_start + 1, min(y_end, h))
    return np.ascontiguousarray(frame[y_start:y_end, x_start:x_end].astype(np.float32))


def process_frame(frame, gui):
    """Per-frame pipeline step: crop to rectangle."""
    import numpy as np
    api = gui.api
    frame = api.incoming_frame(MODULE_NAME, frame)
    if not api.alteration_auto_apply(gui, "autocrop_auto_apply", default=True):
        return api.outgoing_frame(MODULE_NAME, frame)
    out = _apply_autocrop(frame, gui)
    return api.outgoing_frame(MODULE_NAME, out)


def build_ui(gui, parent_tag: str = "control_panel") -> None:
    """Build Autocrop collapsing header; callbacks live in this module (no gui.py changes)."""
    import dearpygui.dearpygui as dpg

    api = gui.api

    def _maybe_preview():
        """Throttle distortion preview to ~5/sec so repeated edits don't queue many updates."""
        now = time.monotonic()
        if (now - getattr(gui, "_autocrop_last_preview_t", 0.0)) < 0.2:
            return
        gui._autocrop_last_preview_t = now
        getattr(gui, "_refresh_distortion_preview", lambda: None)()

    def _apply_crop(sender=None, app_data=None):
        gui.crop_x_start = int(dpg.get_value("crop_x_start"))
        gui.crop_x_end = int(dpg.get_value("crop_x_end"))
        gui.crop_y_start = int(dpg.get_value("crop_y_start"))
        gui.crop_y_end = int(dpg.get_value("crop_y_end"))
        api.save_settings()
        _maybe_preview()

    loaded = api.get_loaded_settings()
    x_start = int(loaded.get("crop_x_start", 0))
    x_end = int(loaded.get("crop_x_end", 0))
    y_start = int(loaded.get("crop_y_start", 0))
    y_end = int(loaded.get("crop_y_end", 0))
    gui.crop_x_start = x_start
    gui.crop_x_end = x_end
    gui.crop_y_start = y_start
    gui.crop_y_end = y_end

    def _cb_apply(g):
        raw = g.api.get_module_incoming_image(MODULE_NAME)
        if raw is None:
            g.api.set_status_message("No frame available (run acquisition first).")
            return
        g._autocrop_revert_snapshot = raw.copy()
        out = _apply_autocrop(raw, g)
        g.api.output_manual_from_module(MODULE_NAME, out)
        g.api.set_status_message("Autocrop applied to current frame.")

    with dpg.collapsing_header(parent=parent_tag, label="Autocrop", default_open=False):
        with dpg.group(indent=10):
            api.build_alteration_apply_revert_ui(
                gui,
                MODULE_NAME,
                _cb_apply,
                auto_apply_attr="autocrop_auto_apply",
                revert_snapshot_attr="_autocrop_revert_snapshot",
                default_auto_apply=True,
            )
            dpg.add_input_int(
                label="X start", default_value=x_start, tag="crop_x_start",
                min_value=0, min_clamped=True, width=80, callback=_apply_crop
            )
            dpg.add_input_int(
                label="X end", default_value=x_end, tag="crop_x_end",
                min_value=0, min_clamped=True, width=80, callback=_apply_crop
            )
            dpg.add_input_int(
                label="Y start", default_value=y_start, tag="crop_y_start",
                min_value=0, min_clamped=True, width=80, callback=_apply_crop
            )
            dpg.add_input_int(
                label="Y end", default_value=y_end, tag="crop_y_end",
                min_value=0, min_clamped=True, width=80, callback=_apply_crop
            )
            dpg.add_text("0,0,0,0 = no crop. Applied at end of pipeline (view only).", color=[150, 150, 150])
