"""
Mustache (moustache) distortion correction alteration module.
Applies radial distortion with k1*r^2 + k2*r^4 so barrel and pincushion can combine (S-shape).
Center X/Y are saved; if < 0 uses frame center. Runs at slot 455 (after pincushion, before crop).
"""

import time
import numpy as np

MODULE_INFO = {
    "display_name": "Mustache correction",
    "description": "Correct mustache (barrel+pincushion) distortion. k1/k2 and center X/Y. Applies on next startup.",
    "type": "image_processing",
    "default_enabled": False,
    "pipeline_slot": 455,
}
MODULE_NAME = "mustache"


def get_setting_keys():
    return ["mustache_k1", "mustache_k2", "mustache_center_x", "mustache_center_y", "mustache_auto_apply"]


# Spec for api.get_module_settings_for_save: (key, tag, converter, default)
_MUSTACHE_SAVE_SPEC = [
    ("mustache_k1", "mustache_k1", float, 0.0),
    ("mustache_k2", "mustache_k2", float, 0.0),
    ("mustache_center_x", "mustache_center_x", float, -1.0),
    ("mustache_center_y", "mustache_center_y", float, -1.0),
]


def get_default_settings():
    """Return default settings for this module (extracted from save spec)."""
    out = {key: default for key, _tag, _conv, default in _MUSTACHE_SAVE_SPEC}
    out["mustache_auto_apply"] = True
    return out


def get_settings_for_save(gui=None):
    """Return mustache k1, k2, center, and auto_apply from UI or loaded settings (auto fallback when UI not built)."""
    if gui is None or not getattr(gui, "api", None):
        return {}
    out = gui.api.get_module_settings_for_save(_MUSTACHE_SAVE_SPEC)
    import dearpygui.dearpygui as dpg
    if dpg.does_item_exist("mustache_auto_apply"):
        out["mustache_auto_apply"] = bool(dpg.get_value("mustache_auto_apply"))
    else:
        out["mustache_auto_apply"] = getattr(gui, "mustache_auto_apply", True)
    return out


def _apply_mustache(frame, gui):
    """Apply mustache correction. Used by pipeline and by manual Apply."""
    from scipy.ndimage import map_coordinates
    api = gui.api
    h, w = frame.shape[0], frame.shape[1]
    k1, k2, cx, cy = api.get_mustache_params()
    k1, k2 = float(k1), float(k2)
    if abs(k1) < 1e-9 and abs(k2) < 1e-9:
        return np.asarray(frame, dtype=np.float32)
    cx, cy = float(cx), float(cy)
    if cx < 0 or cy < 0:
        cx = (w - 1) / 2.0
        cy = (h - 1) / 2.0
    r_max = np.sqrt(max(cx, w - 1 - cx) ** 2 + max(cy, h - 1 - cy) ** 2)
    if r_max < 1e-6:
        return np.asarray(frame, dtype=np.float32)
    rows = np.arange(h, dtype=np.float64)
    cols = np.arange(w, dtype=np.float64)
    col_grid, row_grid = np.meshgrid(cols, rows)
    dx = col_grid - cx
    dy = row_grid - cy
    r = np.sqrt(dx * dx + dy * dy)
    r_safe = np.where(r < 1e-6, 1.0, r)
    r_norm = r_safe / r_max
    r2 = r_norm * r_norm
    r4 = r2 * r2
    denom = 1.0 + k1 * r2 + k2 * r4
    r_src = np.where(r < 1e-6, 0.0, r_safe / np.maximum(denom, 0.1))
    scale = np.where(r < 1e-6, 1.0, r_src / r_safe)
    src_col = cx + scale * dx
    src_row = cy + scale * dy
    coords = np.stack([src_row, src_col], axis=0)
    out = map_coordinates(frame, coords, order=1, mode="reflect", cval=0.0)
    return np.ascontiguousarray(out.astype(np.float32))


def process_frame(frame, gui):
    """Apply mustache correction: r_src = r / (1 + k1*r_norm^2 + k2*r_norm^4). Center from saved X/Y or frame center."""
    api = gui.api
    frame = api.incoming_frame(MODULE_NAME, frame)
    if not api.alteration_auto_apply(gui, "mustache_auto_apply", default=True):
        return api.outgoing_frame(MODULE_NAME, frame)
    out = _apply_mustache(frame, gui)
    return api.outgoing_frame(MODULE_NAME, out)


def build_ui(gui, parent_tag: str = "control_panel") -> None:
    """Build Mustache correction collapsing header. Center X/Y: -1 = frame center."""
    import dearpygui.dearpygui as dpg

    api = gui.api

    def _maybe_preview():
        """Throttle distortion preview to ~5/sec so slider drag doesn't queue many updates."""
        now = time.monotonic()
        if (now - getattr(gui, "_mustache_last_preview_t", 0.0)) < 0.2:
            return
        gui._mustache_last_preview_t = now
        getattr(gui, "_refresh_distortion_preview", lambda: None)()

    def _apply(sender=None, app_data=None):
        gui.mustache_k1 = float(dpg.get_value("mustache_k1"))
        gui.mustache_k2 = float(dpg.get_value("mustache_k2"))
        gui.mustache_center_x = float(dpg.get_value("mustache_center_x"))
        gui.mustache_center_y = float(dpg.get_value("mustache_center_y"))
        api.save_settings()
        _maybe_preview()

    loaded = api.get_loaded_settings()
    k1 = float(loaded.get("mustache_k1", 0.0))
    k2 = float(loaded.get("mustache_k2", 0.0))
    center_x = float(loaded.get("mustache_center_x", -1.0))
    center_y = float(loaded.get("mustache_center_y", -1.0))
    gui.mustache_k1 = k1
    gui.mustache_k2 = k2
    gui.mustache_center_x = center_x
    gui.mustache_center_y = center_y

    def _cb_apply(g):
        raw = g.api.get_module_incoming_image(MODULE_NAME)
        if raw is None:
            g.api.set_status_message("No frame available (run acquisition first).")
            return
        g._mustache_revert_snapshot = raw.copy()
        out = _apply_mustache(raw, g)
        g.api.output_manual_from_module(MODULE_NAME, out)
        g.api.set_status_message("Mustache correction applied to current frame.")

    with dpg.collapsing_header(parent=parent_tag, label="Mustache correction", default_open=False):
        with dpg.group(indent=10):
            api.build_alteration_apply_revert_ui(
                gui,
                MODULE_NAME,
                _cb_apply,
                auto_apply_attr="mustache_auto_apply",
                revert_snapshot_attr="_mustache_revert_snapshot",
                default_auto_apply=True,
            )
            dpg.add_slider_float(
                label="k1",
                default_value=k1,
                min_value=-0.5,
                max_value=0.5,
                format="%.4f",
                tag="mustache_k1",
                width=250,
                callback=_apply,
            )
            dpg.add_slider_float(
                label="k2",
                default_value=k2,
                min_value=-0.5,
                max_value=0.5,
                format="%.4f",
                tag="mustache_k2",
                width=250,
                callback=_apply,
            )
            dpg.add_input_float(
                label="Center X",
                default_value=center_x,
                tag="mustache_center_x",
                width=250,
                callback=_apply,
            )
            dpg.add_input_float(
                label="Center Y",
                default_value=center_y,
                tag="mustache_center_y",
                width=250,
                callback=_apply,
            )
