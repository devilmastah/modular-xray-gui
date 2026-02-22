"""
Background separator alteration module.
Finds the bright uncovered sensor region robustly (hot-pixel resistant), then clips values
near that white reference to a flat white level to suppress background noise around objects.
Runs at slot 510 (after autocrop).
"""

import time
import numpy as np

MODULE_INFO = {
    "display_name": "Background separator",
    "description": "Flatten bright uncovered background by clipping near-white values to white level.",
    "type": "image_processing",
    "default_enabled": False,
    "pipeline_slot": 600,
}
MODULE_NAME = "background_separator"

# Spec for api.get_module_settings_for_save: (key, tag, converter, default)
_BACKGROUND_SEPARATOR_SAVE_SPEC = [
    ("background_separator_offset", "background_separator_offset", float, 5.0),
    ("background_separator_auto_workflow", "background_separator_auto_workflow", bool, False),
    ("background_separator_live_preview", "background_separator_live_preview", bool, True),
]


def get_setting_keys():
    return [key for key, _tag, _conv, _default in _BACKGROUND_SEPARATOR_SAVE_SPEC]


def get_default_settings():
    return {key: default for key, _tag, _conv, default in _BACKGROUND_SEPARATOR_SAVE_SPEC}


def get_settings_for_save(gui=None):
    if gui is None or not getattr(gui, "api", None):
        return {}
    return gui.api.get_module_settings_for_save(_BACKGROUND_SEPARATOR_SAVE_SPEC)


def _estimate_white_reference(frame: np.ndarray) -> float:
    vals = np.asarray(frame, dtype=np.float32)
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:
        return 0.0
    if vals.size < 32:
        return float(np.max(vals))

    # Robust high-end sampling to avoid hot-pixel bias:
    # keep a near-white band, but exclude the extreme tail.
    p_lo = float(np.percentile(vals, 98.8))
    p_hi = float(np.percentile(vals, 99.9))
    samples = vals[(vals >= p_lo) & (vals <= p_hi)]

    if samples.size < 32:
        p_top = float(np.percentile(vals, 99.5))
        samples = vals[vals >= p_top]
    if samples.size == 0:
        return float(np.max(vals))

    return float(np.mean(samples))


def _separate_background(frame: np.ndarray, offset: float):
    src = np.asarray(frame, dtype=np.float32)
    white_ref = _estimate_white_reference(src)
    threshold = white_ref - max(0.0, float(offset))
    out = np.asarray(src, dtype=np.float32).copy()
    mask = out >= threshold
    affected = int(np.count_nonzero(mask))
    out[mask] = white_ref
    out = np.nan_to_num(out, nan=0.0, posinf=white_ref, neginf=0.0).astype(np.float32)
    return out, white_ref, threshold, affected


def process_frame(frame: np.ndarray, gui) -> np.ndarray:
    api = gui.api
    frame = api.incoming_frame(MODULE_NAME, frame)
    frame = np.asarray(frame, dtype=np.float32)

    # Keep latest module input snapshot for manual operations.
    gui._bgsep_raw_frame = frame.copy()
    gui._bgsep_snapshot_token = int(api.get_module_incoming_token(MODULE_NAME) or -1)
    gui._bgsep_result = None
    gui._bgsep_last_preview_key = None

    if not gui.api.alteration_auto_apply(gui, "background_separator_auto_workflow", default=False):
        gui._bgsep_hist_active = False
        gui._bgsep_hist_cutoff = None
        return api.outgoing_frame(MODULE_NAME, frame)

    offset = float(getattr(gui, "_bgsep_offset", 5.0))
    out, white_ref, threshold, affected = _separate_background(frame, offset)
    gui._bgsep_hist_active = True
    gui._bgsep_hist_cutoff = float(threshold)
    print(
        f"[BackgroundSeparator] auto applied "
        f"(token={int(getattr(gui, '_bgsep_snapshot_token', -1))}, "
        f"offset={offset:.2f}, threshold={threshold:.2f}, white={white_ref:.2f}, "
        f"affected={affected})",
        flush=True,
    )
    return api.outgoing_frame(MODULE_NAME, out)


def _ensure_snapshot(gui):
    api = gui.api
    incoming = api.get_module_incoming_image(MODULE_NAME)
    incoming_token = api.get_module_incoming_token(MODULE_NAME)
    snap_token = int(getattr(gui, "_bgsep_snapshot_token", -1))
    if incoming is not None and incoming_token is not None and int(incoming_token) != snap_token:
        gui._bgsep_raw_frame = np.asarray(incoming, dtype=np.float32).copy()
        gui._bgsep_snapshot_token = int(incoming_token)
        gui._bgsep_result = None
        gui._bgsep_last_preview_key = None
        return True
    return getattr(gui, "_bgsep_raw_frame", None) is not None


def _apply_manual(gui, set_status: bool = True):
    api = gui.api
    if not _ensure_snapshot(gui):
        api.set_status_message("No frame to separate")
        return False
    raw = getattr(gui, "_bgsep_raw_frame", None)
    if raw is None:
        api.set_status_message("No frame to separate")
        return False
    offset = float(getattr(gui, "_bgsep_offset", 5.0))
    out, white_ref, threshold, affected = _separate_background(raw, offset)
    gui._bgsep_result = out
    gui._bgsep_hist_active = True
    gui._bgsep_hist_cutoff = float(threshold)
    api.output_manual_from_module(MODULE_NAME, out)
    gui._bgsep_last_preview_key = (
        int(getattr(gui, "_bgsep_snapshot_token", -1)),
        round(float(offset), 4),
    )
    if set_status:
        api.set_status_message(
            f"Background separator applied (offset={offset:.1f}, threshold={threshold:.1f})"
        )
        print(
            f"[BackgroundSeparator] manual applied "
            f"(token={int(getattr(gui, '_bgsep_snapshot_token', -1))}, "
            f"offset={offset:.2f}, threshold={threshold:.2f}, white={white_ref:.2f}, "
            f"affected={affected})",
            flush=True,
        )
    else:
        print(
            f"[BackgroundSeparator] live preview "
            f"(token={int(getattr(gui, '_bgsep_snapshot_token', -1))}, "
            f"offset={offset:.2f}, threshold={threshold:.2f}, white={white_ref:.2f}, "
            f"affected={affected})",
            flush=True,
        )
    return True


def _maybe_live_preview(gui):
    if not bool(getattr(gui, "_bgsep_live_preview", True)):
        return
    preview_key = (
        int(getattr(gui, "_bgsep_snapshot_token", -1)),
        round(float(getattr(gui, "_bgsep_offset", 5.0)), 4),
    )
    if preview_key == getattr(gui, "_bgsep_last_preview_key", None):
        return
    now = time.monotonic()
    last_t = float(getattr(gui, "_bgsep_last_preview_t", 0.0))
    if (now - last_t) < 0.2:
        return
    gui._bgsep_last_preview_t = now
    _apply_manual(gui, set_status=False)


def _cb_offset(sender, app_data, gui):
    import dearpygui.dearpygui as dpg

    gui._bgsep_offset = max(0.0, float(dpg.get_value("background_separator_offset")))
    _maybe_live_preview(gui)
    gui.api.save_settings()


def _cb_live_preview(sender, app_data, gui):
    import dearpygui.dearpygui as dpg

    gui._bgsep_live_preview = bool(dpg.get_value("background_separator_live_preview"))
    gui.api.save_settings()


def _cb_apply_manual(gui):
    _apply_manual(gui, set_status=True)


def _cb_revert(gui):
    api = gui.api
    if not _ensure_snapshot(gui):
        api.set_status_message("No raw snapshot to revert")
        return
    raw = getattr(gui, "_bgsep_raw_frame", None)
    if raw is None:
        api.set_status_message("No raw snapshot to revert")
        return
    gui._bgsep_result = None
    gui._bgsep_hist_active = False
    gui._bgsep_hist_cutoff = None
    gui._bgsep_last_preview_key = None
    api.output_manual_from_module(MODULE_NAME, raw)
    api.set_status_message("Background separator reverted to raw snapshot")
    print(
        f"[BackgroundSeparator] manual revert (token={int(getattr(gui, '_bgsep_snapshot_token', -1))})",
        flush=True,
    )


def build_ui(gui, parent_tag: str = "control_panel") -> None:
    import dearpygui.dearpygui as dpg

    api = gui.api
    loaded = api.get_loaded_settings()
    gui._bgsep_offset = float(loaded.get("background_separator_offset", 5.0))
    gui._bgsep_live_preview = bool(loaded.get("background_separator_live_preview", True))
    gui._bgsep_last_preview_t = 0.0
    gui._bgsep_last_preview_key = None
    gui._bgsep_raw_frame = None
    gui._bgsep_result = None
    gui._bgsep_snapshot_token = -1
    gui._bgsep_hist_ignore = True
    gui._bgsep_hist_active = False
    gui._bgsep_hist_cutoff = None

    with dpg.collapsing_header(parent=parent_tag, label="Background separator", default_open=False):
        with dpg.group(indent=10):
            api.build_alteration_apply_revert_ui(
                gui,
                MODULE_NAME,
                _cb_apply_manual,
                auto_apply_attr="background_separator_auto_workflow",
                revert_snapshot_attr="_bgsep_raw_frame",
                default_auto_apply=False,
            )
            dpg.add_text(
                "Flattens bright uncovered sensor background by clipping near-white values.",
                color=[150, 150, 150],
            )
            dpg.add_slider_float(
                label="White clip offset",
                default_value=gui._bgsep_offset,
                min_value=0.0,
                max_value=300.0,
                format="%.1f",
                tag="background_separator_offset",
                callback=lambda s, a: _cb_offset(s, a, gui),
                width=-120,
            )
            dpg.add_checkbox(
                label="Live preview while tuning",
                default_value=gui._bgsep_live_preview,
                tag="background_separator_live_preview",
                callback=lambda s, a: _cb_live_preview(s, a, gui),
            )
