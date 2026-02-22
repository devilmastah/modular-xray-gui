"""
Banding correction image alteration module.
Applies horizontal and/or vertical banding correction (sensor-specific). Runs at pipeline slot 300 (after dark/flat).
State and settings remain on the main app (gui); this module provides process_frame and build_ui.
"""

import numpy as np

from .banding_correction import (
    correct_banding,
    correct_vertical_banding,
    optimize_smooth_window,
    optimize_smooth_window_vertical,
    DEFAULT_BLACK_W,
    DEFAULT_SMOOTH_WIN,
    DEFAULT_VERTICAL_STRIPE_H,
    DEFAULT_VERTICAL_SMOOTH_WIN,
)

MODULE_INFO = {
    "display_name": "Banding correction",
    "description": "Horizontal/vertical banding removal (sensor-specific). Applies on next startup.",
    "type": "image_processing",
    "default_enabled": False,
    "pipeline_slot": 300,
}
MODULE_NAME = "banding"


def get_setting_keys():
    return ["banding_auto_apply"]


def get_default_settings():
    """Return default settings for this module."""
    return {
        "banding_enabled": True,
        "banding_auto_optimize": True,
        "banding_black_w": DEFAULT_BLACK_W,
        "banding_smooth_win": DEFAULT_SMOOTH_WIN,
        "vertical_banding_enabled": True,
        "vertical_stripe_h": DEFAULT_VERTICAL_STRIPE_H,
        "vertical_smooth_win": DEFAULT_VERTICAL_SMOOTH_WIN,
        "vertical_banding_auto_optimize": False,
        "vertical_banding_first": False,
        "banding_auto_apply": True,
    }


def get_settings_for_save(gui=None):
    """Return banding-related keys from our UI or from gui state when module is disabled."""
    import dearpygui.dearpygui as dpg
    if dpg.does_item_exist("banding_auto_optimize"):
        out = {
            "banding_enabled": True,
            "banding_auto_optimize": dpg.get_value("banding_auto_optimize"),
            "banding_black_w": int(dpg.get_value("banding_black_w")),
            "banding_smooth_win": int(dpg.get_value("banding_smooth_win")),
            "vertical_banding_enabled": dpg.get_value("vertical_banding_enabled"),
            "vertical_stripe_h": int(dpg.get_value("vertical_stripe_h")),
            "vertical_smooth_win": int(dpg.get_value("vertical_smooth_win")),
            "vertical_banding_auto_optimize": dpg.get_value("vertical_banding_auto_optimize"),
            "vertical_banding_first": dpg.get_value("vertical_banding_first"),
        }
    elif gui is not None:
        api = gui.api
        out = {
            "banding_enabled": True,
            "banding_auto_optimize": api.get_banding_auto_optimize(),
            "banding_black_w": api.get_banding_black_w(),
            "banding_smooth_win": api.get_banding_smooth_win(),
            "vertical_banding_enabled": api.get_vertical_banding_enabled(),
            "vertical_stripe_h": api.get_vertical_stripe_h(),
            "vertical_smooth_win": api.get_vertical_smooth_win(),
            "vertical_banding_auto_optimize": api.get_vertical_banding_auto_optimize(),
            "vertical_banding_first": api.get_vertical_banding_first(),
        }
    else:
        out = {}
    if dpg.does_item_exist("banding_auto_apply"):
        out["banding_auto_apply"] = bool(dpg.get_value("banding_auto_apply"))
    elif gui is not None:
        out["banding_auto_apply"] = getattr(gui, "banding_auto_apply", True)
    else:
        out["banding_auto_apply"] = True
    return out


def _apply_banding(frame: np.ndarray, gui) -> np.ndarray:
    """Apply horizontal and/or vertical banding correction. Used by pipeline and by manual Apply."""
    api = gui.api
    vert_first = api.get_vertical_banding_first()
    if vert_first:
        if api.get_vertical_banding_enabled():
            v_smooth_win = api.get_vertical_smooth_win()
            if api.get_vertical_banding_auto_optimize() and api.get_vertical_banding_optimized_win() is not None:
                v_smooth_win = api.get_vertical_banding_optimized_win()
            frame = correct_vertical_banding(
                frame,
                stripe_h=api.get_vertical_stripe_h(),
                smooth_win=v_smooth_win,
            )
        if api.get_banding_enabled():
            smooth_win = api.get_banding_smooth_win()
            if api.get_banding_auto_optimize() and api.get_banding_optimized_win() is not None:
                smooth_win = api.get_banding_optimized_win()
            frame = correct_banding(
                frame,
                black_w=api.get_banding_black_w(),
                black_offset=0,
                smooth_win=smooth_win,
            )
    else:
        if api.get_banding_enabled():
            smooth_win = api.get_banding_smooth_win()
            if api.get_banding_auto_optimize() and api.get_banding_optimized_win() is not None:
                smooth_win = api.get_banding_optimized_win()
            frame = correct_banding(
                frame,
                black_w=api.get_banding_black_w(),
                black_offset=0,
                smooth_win=smooth_win,
            )
        if api.get_vertical_banding_enabled():
            v_smooth_win = api.get_vertical_smooth_win()
            if api.get_vertical_banding_auto_optimize() and api.get_vertical_banding_optimized_win() is not None:
                v_smooth_win = api.get_vertical_banding_optimized_win()
            frame = correct_vertical_banding(
                frame,
                stripe_h=api.get_vertical_stripe_h(),
                smooth_win=v_smooth_win,
            )
    return frame


def process_frame(frame: np.ndarray, gui) -> np.ndarray:
    """
    Applies horizontal and/or vertical banding correction using app banding state.
    Order: vertical first or horizontal first depending on api.get_vertical_banding_first().
    """
    api = gui.api
    frame = api.incoming_frame(MODULE_NAME, frame)
    if not api.alteration_auto_apply(gui, "banding_auto_apply", default=True):
        return api.outgoing_frame(MODULE_NAME, frame)
    # Auto-optimize on first frame when enabled (only in pipeline)
    vert_first = api.get_vertical_banding_first()
    if vert_first:
        if api.get_vertical_banding_enabled() and api.get_vertical_banding_auto_optimize():
            if api.get_vertical_banding_optimized_win() is None:
                win, score = optimize_smooth_window_vertical(
                    frame, stripe_h=api.get_vertical_stripe_h()
                )
                api.set_vertical_banding_optimized_win(win)
                api.set_status_message(f"Vertical banding: optimized smooth window = {win} (score: {score:.2f})")
        if api.get_banding_enabled() and api.get_banding_auto_optimize():
            if api.get_banding_optimized_win() is None:
                win, score = optimize_smooth_window(
                    frame, black_w=api.get_banding_black_w(), black_offset=0
                )
                api.set_banding_optimized_win(win)
                api.set_status_message(f"Banding: optimized smooth window = {win} (score: {score:.2f})")
    else:
        if api.get_banding_enabled() and api.get_banding_auto_optimize():
            if api.get_banding_optimized_win() is None:
                win, score = optimize_smooth_window(
                    frame, black_w=api.get_banding_black_w(), black_offset=0
                )
                api.set_banding_optimized_win(win)
                api.set_status_message(f"Banding: optimized smooth window = {win} (score: {score:.2f})")
        if api.get_vertical_banding_enabled() and api.get_vertical_banding_auto_optimize():
            if api.get_vertical_banding_optimized_win() is None:
                win, score = optimize_smooth_window_vertical(
                    frame, stripe_h=api.get_vertical_stripe_h()
                )
                api.set_vertical_banding_optimized_win(win)
                api.set_status_message(f"Vertical banding: optimized smooth window = {win} (score: {score:.2f})")
    out = _apply_banding(frame, gui)
    return api.outgoing_frame(MODULE_NAME, out)


def build_ui(gui, parent_tag: str = "control_panel") -> None:
    """Build Banding Correction collapsing header; callbacks remain on gui."""
    import dearpygui.dearpygui as dpg
    api = gui.api
    gui.banding_enabled = True  # no separate Enable checkbox; Apply automatically is the only gate for horizontal

    def _cb_apply(g):
        raw = g.api.get_module_incoming_image(MODULE_NAME)
        if raw is None:
            g.api.set_status_message("No frame available (run acquisition first).")
            return
        g._banding_revert_snapshot = raw.copy()
        out = _apply_banding(raw, g)
        g.api.output_manual_from_module(MODULE_NAME, out)
        g.api.set_status_message("Banding correction applied to current frame.")

    with dpg.collapsing_header(parent=parent_tag, label="Banding Correction", default_open=False):
        with dpg.group(indent=10):
            api.build_alteration_apply_revert_ui(
                gui,
                MODULE_NAME,
                _cb_apply,
                auto_apply_attr="banding_auto_apply",
                revert_snapshot_attr="_banding_revert_snapshot",
                default_auto_apply=True,
            )
            dpg.add_text("Horizontal", color=[200, 200, 200])
            dpg.add_checkbox(
                label="Auto-optimize smooth window",
                default_value=api.get_banding_auto_optimize(),
                tag="banding_auto_optimize",
                callback=api.gui._cb_banding_auto_optimize,
            )
            dpg.add_slider_int(
                label="Stripe width",
                default_value=api.get_banding_black_w(),
                min_value=5,
                max_value=50,
                tag="banding_black_w",
                callback=api.gui._cb_banding_black_w,
                width=-120,
            )
            dpg.add_slider_int(
                label="Smooth win",
                default_value=api.get_banding_smooth_win(),
                min_value=32,
                max_value=512,
                tag="banding_smooth_win",
                callback=api.gui._cb_banding_smooth_win,
                width=-120,
            )
            dpg.add_separator()
            dpg.add_text("Vertical", color=[200, 200, 200])
            dpg.add_checkbox(
                label="Also correct vertical",
                default_value=api.get_vertical_banding_enabled(),
                tag="vertical_banding_enabled",
                callback=api.gui._cb_vertical_banding_enabled,
            )
            dpg.add_checkbox(
                label="Vertical first",
                default_value=api.get_vertical_banding_first(),
                tag="vertical_banding_first",
                callback=api.gui._cb_vertical_banding_first,
            )
            dpg.add_checkbox(
                label="Auto-optimize smooth window (vertical)",
                default_value=api.get_vertical_banding_auto_optimize(),
                tag="vertical_banding_auto_optimize",
                callback=api.gui._cb_vertical_banding_auto_optimize,
            )
            dpg.add_slider_int(
                label="Bottom rows",
                default_value=api.get_vertical_stripe_h(),
                min_value=5,
                max_value=80,
                tag="vertical_stripe_h",
                callback=api.gui._cb_vertical_stripe_h,
                width=-120,
            )
            dpg.add_slider_int(
                label="Vert. smooth win",
                default_value=api.get_vertical_smooth_win(),
                min_value=32,
                max_value=512,
                tag="vertical_smooth_win",
                callback=api.gui._cb_vertical_smooth_win,
                width=-120,
            )
            dpg.add_separator()
            dpg.add_text("Auto-optimize finds best window on first", color=[150, 150, 150])
            dpg.add_text("frame", color=[150, 150, 150])
