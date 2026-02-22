"""
Bad pixel map alteration module.
Builds a mask of bad pixels from the loaded dark and flat (cold from flat, hot from dark),
saves it per camera+resolution, and replaces bad pixels with median of 3×3 good neighbors.
Runs at pipeline slot 250 (after flat, before banding).
"""

import numpy as np
from pathlib import Path

from .bad_pixel_correction import replace_bad_pixels

MODULE_INFO = {
    "display_name": "Bad pixel map",
    "description": "Build map from dark & flat, replace bad pixels by median of neighbors.",
    "type": "alteration",
    "default_enabled": True,
    "pipeline_slot": 250,
}
MODULE_NAME = "bad_pixel_map"

# Filename for the mask (resolution in name); .npy in darks/, .tif in pixelmaps/
BAD_PIXEL_MAP_FNAME = "bad_pixel_map_{width}x{height}.npy"
BAD_PIXEL_MAP_TIFF_FNAME = "bad_pixel_map_{width}x{height}.tif"

# Default thresholds: fraction of pixels to mark as bad (cold = bottom X%, hot = top X%)
# 0.005 = 0.5% each side → ~1% total; 0.25 would mark 25% as hot (way too many)
DEFAULT_FLAT_THRESH = 0.005   # bottom 0.5% = cold
DEFAULT_DARK_THRESH = 0.005    # top 0.5% = hot



def get_setting_keys():
    return [
        "bad_pixel_map_flat_thresh",
        "bad_pixel_map_dark_thresh",
        "bad_pixel_map_auto_correct",
        "bad_pixel_map_use_histogram_preview",
    ]


def get_default_settings():
    return {
        "bad_pixel_map_flat_thresh": DEFAULT_FLAT_THRESH,
        "bad_pixel_map_dark_thresh": DEFAULT_DARK_THRESH,
        "bad_pixel_map_auto_correct": True,
        "bad_pixel_map_use_histogram_preview": False,
    }


def get_settings_for_save(gui=None):
    import dearpygui.dearpygui as dpg
    if gui is None:
        return {}
    api = gui.api
    out = {}
    if dpg.does_item_exist("bad_pixel_map_flat_thresh"):
        out["bad_pixel_map_flat_thresh"] = float(dpg.get_value("bad_pixel_map_flat_thresh"))
    else:
        out["bad_pixel_map_flat_thresh"] = getattr(gui, "bad_pixel_map_flat_thresh", DEFAULT_FLAT_THRESH)
    if dpg.does_item_exist("bad_pixel_map_dark_thresh"):
        out["bad_pixel_map_dark_thresh"] = float(dpg.get_value("bad_pixel_map_dark_thresh"))
    else:
        out["bad_pixel_map_dark_thresh"] = getattr(gui, "bad_pixel_map_dark_thresh", DEFAULT_DARK_THRESH)
    if dpg.does_item_exist("bad_pixel_map_auto_correct"):
        out["bad_pixel_map_auto_correct"] = bool(dpg.get_value("bad_pixel_map_auto_correct"))
    else:
        out["bad_pixel_map_auto_correct"] = getattr(gui, "bad_pixel_map_auto_correct", True)
    if dpg.does_item_exist("bad_pixel_map_use_histogram_preview"):
        out["bad_pixel_map_use_histogram_preview"] = bool(dpg.get_value("bad_pixel_map_use_histogram_preview"))
    else:
        out["bad_pixel_map_use_histogram_preview"] = getattr(gui, "bad_pixel_map_use_histogram_preview", False)
    return out


def _build_mask_from_dark_flat(dark: np.ndarray, flat: np.ndarray, flat_thresh: float, dark_thresh: float) -> np.ndarray:
    """Build bool mask: True = bad pixel.
    Cold = bottom (flat_thresh*100)% of flat values; hot = top (dark_thresh*100)% of dark values.
    E.g. flat_thresh=0.005 → bottom 0.5%, dark_thresh=0.005 → top 0.5%.
    """
    if dark.shape != flat.shape:
        return None
    if flat_thresh <= 0 and dark_thresh <= 0:
        return np.zeros_like(flat, dtype=bool)
    cold = np.zeros_like(flat, dtype=bool)
    if flat_thresh > 0:
        pct_flat = min(50.0, max(0.0, flat_thresh * 100.0))
        cold_thresh = np.percentile(flat, pct_flat)
        cold = flat <= cold_thresh
    hot = np.zeros_like(dark, dtype=bool)
    if dark_thresh > 0:
        pct_dark = min(50.0, max(0.0, dark_thresh * 100.0))
        hot_thresh = np.percentile(dark, 100.0 - pct_dark)
        hot = dark >= hot_thresh
    mask = cold | hot
    return mask


def _get_current_mask_for_preview(gui):
    """Return current mask (bool or float) for preview from sliders+dark/flat or loaded map, or None."""
    import dearpygui.dearpygui as dpg
    api = gui.api
    dark = api.get_dark_field()
    flat = api.get_flat_field()
    if dark is not None and flat is not None and dark.shape == flat.shape:
        flat_t = float(dpg.get_value("bad_pixel_map_flat_thresh")) if dpg.does_item_exist("bad_pixel_map_flat_thresh") else getattr(gui, "bad_pixel_map_flat_thresh", DEFAULT_FLAT_THRESH)
        dark_t = float(dpg.get_value("bad_pixel_map_dark_thresh")) if dpg.does_item_exist("bad_pixel_map_dark_thresh") else getattr(gui, "bad_pixel_map_dark_thresh", DEFAULT_DARK_THRESH)
        flat_t = min(0.5, max(0.0, flat_t))
        dark_t = min(0.5, max(0.0, dark_t))
        mask = _build_mask_from_dark_flat(dark, flat, flat_t, dark_t)
        if mask is not None:
            return mask.astype(np.float32)
    mask = getattr(gui, "bad_pixel_map_mask", None)
    if mask is not None:
        return mask.astype(np.float32)
    return None


def _update_main_view_preview(gui) -> None:
    """If 'Show pixel map preview' is on, paint current mask to main window."""
    import dearpygui.dearpygui as dpg
    if not getattr(gui, "bad_pixel_map_show_in_main_view", False):
        return
    mask = _get_current_mask_for_preview(gui)
    if mask is not None:
        use_hist = (
            dpg.get_value("bad_pixel_map_use_histogram_preview")
            if dpg.does_item_exist("bad_pixel_map_use_histogram_preview")
            else getattr(gui, "bad_pixel_map_use_histogram_preview", False)
        )
        gui.api.show_preview_in_main_view(mask, use_histogram=use_hist)


def _map_path(api) -> Path:
    """Path for saving/loading bad pixel map for current camera and frame size."""
    base = api.get_dark_dir()
    w, h = api.get_frame_size()
    if w <= 0 or h <= 0:
        return base / BAD_PIXEL_MAP_FNAME.format(width=0, height=0)
    return base / BAD_PIXEL_MAP_FNAME.format(width=w, height=h)


def _load_map(api) -> np.ndarray | None:
    """Load bad pixel map from disk if it exists and matches current size; else None."""
    path = _map_path(api)
    if not path.exists():
        return None
    try:
        mask = np.load(path)
        if not isinstance(mask, np.ndarray) or mask.dtype != bool:
            return None
        w, h = api.get_frame_size()
        if mask.shape != (h, w):
            return None
        return mask
    except Exception:
        return None


def _save_map(api, mask: np.ndarray) -> None:
    """Save bad pixel map to darks/<camera>/bad_pixel_map_<w>x<h>.npy and TIFF to pixelmaps/<camera>/ for review."""
    path = _map_path(api)
    path.parent.mkdir(parents=True, exist_ok=True)
    np.save(path, mask.astype(bool))
    # TIFF in pixelmaps/ for review (black=good, white=bad)
    w, h = api.get_frame_size()
    if w > 0 and h > 0:
        tiff_dir = api.get_pixelmaps_dir()
        tiff_dir.mkdir(parents=True, exist_ok=True)
        tiff_path = tiff_dir / BAD_PIXEL_MAP_TIFF_FNAME.format(width=w, height=h)
        try:
            import tifffile
            review = np.where(mask, np.uint8(255), np.uint8(0))
            tifffile.imwrite(tiff_path, review, photometric="minisblack", compression=None)
        except Exception:
            pass


def process_frame(frame: np.ndarray, gui) -> np.ndarray:
    """Replace bad pixels (from loaded map) with median of 3×3 good neighbors when auto correct is on."""
    api = gui.api
    frame = api.incoming_frame(MODULE_NAME, frame)
    if not api.alteration_auto_apply(gui, "bad_pixel_map_auto_correct", default=True):
        return api.outgoing_frame(MODULE_NAME, frame)
    mask = getattr(gui, "bad_pixel_map_mask", None)
    if mask is None or mask.shape != frame.shape:
        return api.outgoing_frame(MODULE_NAME, frame)
    out = replace_bad_pixels(np.asarray(frame, dtype=np.float32), mask)
    return api.outgoing_frame(MODULE_NAME, out)


def build_ui(gui, parent_tag: str = "control_panel") -> None:
    import dearpygui.dearpygui as dpg

    api = gui.api
    loaded = api.get_loaded_settings()
    flat_thresh = float(loaded.get("bad_pixel_map_flat_thresh", DEFAULT_FLAT_THRESH))
    dark_thresh = float(loaded.get("bad_pixel_map_dark_thresh", DEFAULT_DARK_THRESH))
    flat_thresh = min(0.5, max(0.0, flat_thresh))
    dark_thresh = min(0.5, max(0.0, dark_thresh))
    auto_correct = bool(loaded.get("bad_pixel_map_auto_correct", True))
    gui.bad_pixel_map_flat_thresh = flat_thresh
    gui.bad_pixel_map_dark_thresh = dark_thresh
    gui.bad_pixel_map_auto_correct = auto_correct
    gui._bad_pixel_map_raw_frame = None  # snapshot for manual Revert
    gui.bad_pixel_map_show_in_main_view = False  # when True, preview mask in main window
    gui.bad_pixel_map_use_histogram_preview = bool(loaded.get("bad_pixel_map_use_histogram_preview", False))

    def _status():
        mask = getattr(gui, "bad_pixel_map_mask", None)
        if mask is not None and np.any(mask):
            return f"Active ({int(np.sum(mask))} bad pixels)"
        return "No map (build from dark & flat)"

    def _apply_flat_thresh(sender=None, app_data=None):
        gui.bad_pixel_map_flat_thresh = float(dpg.get_value("bad_pixel_map_flat_thresh"))
        api.save_settings()
        _update_main_view_preview(gui)

    def _apply_dark_thresh(sender=None, app_data=None):
        gui.bad_pixel_map_dark_thresh = float(dpg.get_value("bad_pixel_map_dark_thresh"))
        api.save_settings()
        _update_main_view_preview(gui)

    def _step_slider(tag: str, delta: float):
        if not dpg.does_item_exist(tag):
            return
        v = float(dpg.get_value(tag)) + delta
        v = max(0.0, min(0.5, round(v, 3)))
        dpg.set_value(tag, v)
        if tag == "bad_pixel_map_flat_thresh":
            gui.bad_pixel_map_flat_thresh = v
        else:
            gui.bad_pixel_map_dark_thresh = v
        api.save_settings()
        _update_main_view_preview(gui)

    def _cb_show_in_main_view(sender=None, app_data=None):
        gui.bad_pixel_map_show_in_main_view = bool(dpg.get_value("bad_pixel_map_show_in_main_view"))
        if gui.bad_pixel_map_show_in_main_view:
            _update_main_view_preview(gui)
        else:
            api.clear_main_view_preview()

    def _cb_apply(g):
        """Apply bad pixel correction to current pipeline input and paint to display."""
        raw = g.api.get_module_incoming_image(MODULE_NAME)
        mask = getattr(g, "bad_pixel_map_mask", None)
        if raw is None:
            g.api.set_status_message("No frame available (run acquisition first).")
            return
        if mask is None or not np.any(mask):
            g.api.set_status_message("No bad pixel map loaded.")
            return
        if raw.shape != mask.shape:
            g.api.set_status_message("Map resolution does not match frame.")
            return
        g._bad_pixel_map_raw_frame = raw.copy()
        corrected = replace_bad_pixels(np.asarray(raw, dtype=np.float32), mask)
        g.api.output_manual_from_module(MODULE_NAME, corrected)
        g.api.set_status_message("Bad pixel correction applied to current frame.")

    def _cb_build(sender=None, app_data=None):
        dark = api.get_dark_field()
        flat = api.get_flat_field()
        w, h = api.get_frame_size()
        if dark is None or flat is None:
            api.set_status_message("Load dark and flat first (capture or use matching calibration).")
            return
        if dark.shape != flat.shape:
            api.set_status_message("Dark and flat must have the same resolution.")
            return
        if (h, w) != dark.shape[:2] and (dark.shape[0], dark.shape[1]) != (h, w):
            api.set_status_message("Dark/flat resolution does not match current frame size.")
            return
        flat_t = float(dpg.get_value("bad_pixel_map_flat_thresh"))
        dark_t = float(dpg.get_value("bad_pixel_map_dark_thresh"))
        with api.get_frame_lock():
            mask = _build_mask_from_dark_flat(dark, flat, flat_t, dark_t)
        if mask is None:
            api.set_status_message("Could not build bad pixel map.")
            return
        n_bad = int(np.sum(mask))
        gui.bad_pixel_map_mask = mask
        _save_map(api, mask)
        api.set_status_message(f"Bad pixel map built: {n_bad} pixels saved for current camera/resolution.")
        if dpg.does_item_exist("bad_pixel_map_status"):
            dpg.set_value("bad_pixel_map_status", _status())
        _update_main_view_preview(gui)

    def _cb_load(sender=None, app_data=None):
        mask = _load_map(api)
        if mask is None:
            api.set_status_message("No saved map found for current camera/resolution.")
            return
        gui.bad_pixel_map_mask = mask
        api.set_status_message(f"Loaded bad pixel map ({int(np.sum(mask))} pixels).")
        if dpg.does_item_exist("bad_pixel_map_status"):
            dpg.set_value("bad_pixel_map_status", _status())
        _update_main_view_preview(gui)

    def _cb_clear(sender=None, app_data=None):
        gui.bad_pixel_map_mask = None
        api.set_status_message("Bad pixel map cleared.")
        if dpg.does_item_exist("bad_pixel_map_status"):
            dpg.set_value("bad_pixel_map_status", _status())
        if gui.bad_pixel_map_show_in_main_view:
            api.clear_main_view_preview()
        else:
            _update_main_view_preview(gui)

    # Load saved map if any (matching current camera + resolution)
    existing = _load_map(api)
    if existing is not None:
        gui.bad_pixel_map_mask = existing

    with dpg.collapsing_header(parent=parent_tag, label="Bad pixel map", default_open=False):
        with dpg.group(indent=10):
            api.build_alteration_apply_revert_ui(
                gui,
                MODULE_NAME,
                _cb_apply,
                auto_apply_attr="bad_pixel_map_auto_correct",
                revert_snapshot_attr="_bad_pixel_map_raw_frame",
                default_auto_apply=True,
            )
            dpg.add_text("Uses loaded dark & flat for this sensor.", color=[150, 150, 150])
            dpg.add_text(_status(), tag="bad_pixel_map_status")
            dpg.add_slider_float(
                label="Flat % (cold)",
                default_value=flat_thresh,
                min_value=0.0,
                max_value=0.5,
                format="%.3f",
                tag="bad_pixel_map_flat_thresh",
                width=-120,
                callback=_apply_flat_thresh,
            )
            with dpg.group(horizontal=True):
                dpg.add_button(label="-", width=28, callback=lambda s, a: _step_slider("bad_pixel_map_flat_thresh", -0.001))
                dpg.add_button(label="+", width=28, callback=lambda s, a: _step_slider("bad_pixel_map_flat_thresh", 0.001))
            dpg.add_text("Bottom fraction of pixels marked cold", color=[120, 120, 120])
            dpg.add_slider_float(
                label="Dark % (hot)",
                default_value=dark_thresh,
                min_value=0.0,
                max_value=0.5,
                format="%.3f",
                tag="bad_pixel_map_dark_thresh",
                width=-120,
                callback=_apply_dark_thresh,
            )
            with dpg.group(horizontal=True):
                dpg.add_button(label="-", width=28, callback=lambda s, a: _step_slider("bad_pixel_map_dark_thresh", -0.001))
                dpg.add_button(label="+", width=28, callback=lambda s, a: _step_slider("bad_pixel_map_dark_thresh", 0.001))
            dpg.add_text("Top fraction of pixels marked hot", color=[120, 120, 120])
            dpg.add_checkbox(
                label="Show pixel map preview",
                default_value=gui.bad_pixel_map_show_in_main_view,
                tag="bad_pixel_map_show_in_main_view",
                callback=_cb_show_in_main_view,
            )
            def _cb_use_histogram_preview(sender, app_data):
                gui.bad_pixel_map_use_histogram_preview = bool(dpg.get_value(sender))
                api.save_settings()
                _update_main_view_preview(gui)

            dpg.add_checkbox(
                label="Use histogram for preview",
                default_value=gui.bad_pixel_map_use_histogram_preview,
                tag="bad_pixel_map_use_histogram_preview",
                callback=_cb_use_histogram_preview,
            )
            dpg.add_text("Off = raw (scale by min/max, better for mask). On = windowing/hist eq.", color=[120, 120, 120])
            dpg.add_separator()
            dpg.add_button(label="Save pixel map", callback=_cb_build, width=-1)
            # Control panel is 350px; content indent leaves ~330px - split for two equal buttons
            with dpg.group(horizontal=True):
                dpg.add_button(label="Load saved map", callback=_cb_load, width=150)
                dpg.add_button(label="Clear map", callback=_cb_clear, width=150)
            _update_main_view_preview(gui)
