"""
Dead pixel line correction image alteration module.
Fills dead horizontal/vertical lines by interpolation. Runs at pipeline slot 400 (after banding).
State and settings remain on the main app (gui); this module provides process_frame and build_ui.
"""

from .dead_pixel_correction import correct_dead_lines

MODULE_INFO = {
    "display_name": "Dead pixel correction",
    "description": "Interpolate dead vertical/horizontal lines. Applies on next startup.",
    "type": "alteration",
    "default_enabled": False,  # Off by default; bad_pixel_map is preferred and they can conflict
    "pipeline_slot": 400,
}
MODULE_NAME = "dead_pixel"


def get_setting_keys():
    return ["dead_pixel_auto_apply"]


def get_default_settings():
    """Return default settings for this module."""
    return {
        "dead_lines_enabled": True,
        "dead_vertical_lines": "",
        "dead_horizontal_lines": "",
        "dead_pixel_auto_apply": True,
    }


def get_settings_for_save(gui=None):
    """Return dead_lines settings from our UI or from gui state when module is disabled."""
    import dearpygui.dearpygui as dpg
    if dpg.does_item_exist("dead_vertical_lines_input"):
        out = {
            "dead_lines_enabled": True,
            "dead_vertical_lines": dpg.get_value("dead_vertical_lines_input") or "",
            "dead_horizontal_lines": dpg.get_value("dead_horizontal_lines_input") or "",
        }
    elif gui is not None:
        vlines, hlines = gui.api.get_dead_pixel_lines()
        out = {
            "dead_lines_enabled": True,
            "dead_vertical_lines": ",".join(str(x) for x in vlines) if vlines else "",
            "dead_horizontal_lines": ",".join(str(x) for x in hlines),
        }
    else:
        out = {"dead_lines_enabled": True}
    if dpg.does_item_exist("dead_pixel_auto_apply"):
        out["dead_pixel_auto_apply"] = bool(dpg.get_value("dead_pixel_auto_apply"))
    elif gui is not None:
        out["dead_pixel_auto_apply"] = getattr(gui, "dead_pixel_auto_apply", True)
    else:
        out["dead_pixel_auto_apply"] = True
    return out


def _apply_dead_pixel(frame, gui):
    """Apply dead line correction. Used by pipeline and by manual Apply."""
    api = gui.api
    if not api.dead_pixel_correction_enabled():
        return frame
    vlines, hlines = api.get_dead_pixel_lines()
    if len(vlines) == 0 and len(hlines) == 0:
        return frame
    return correct_dead_lines(
        frame,
        dead_vertical_lines=vlines,
        dead_horizontal_lines=hlines,
    )


def process_frame(frame, gui):
    """Per-frame pipeline step."""
    api = gui.api
    frame = api.incoming_frame(MODULE_NAME, frame)
    if not api.alteration_auto_apply(gui, "dead_pixel_auto_apply", default=True):
        return api.outgoing_frame(MODULE_NAME, frame)
    out = _apply_dead_pixel(frame, gui)
    return api.outgoing_frame(MODULE_NAME, out)


def build_ui(gui, parent_tag: str = "control_panel") -> None:
    """Build Dead Pixel Lines collapsing header; callbacks live in this module (no gui.py changes)."""
    import dearpygui.dearpygui as dpg

    api = gui.api

    def _apply_vertical_lines(sender=None, app_data=None):
        raw = (dpg.get_value("dead_vertical_lines_input") or "").strip()
        gui.dead_vertical_lines = [int(x.strip()) for x in raw.split(",") if x.strip()] if raw else []
        api.save_settings()

    def _apply_horizontal_lines(sender=None, app_data=None):
        raw = (dpg.get_value("dead_horizontal_lines_input") or "").strip()
        gui.dead_horizontal_lines = [int(x.strip()) for x in raw.split(",") if x.strip()]
        api.save_settings()

    loaded = api.get_loaded_settings()
    vlines_str = loaded.get("dead_vertical_lines", "")
    hlines_str = loaded.get("dead_horizontal_lines", "") or ""
    gui.dead_lines_enabled = True  # no separate Enable checkbox; Apply automatically is the only gate

    def _cb_apply(g):
        raw = g.api.get_module_incoming_image(MODULE_NAME)
        if raw is None:
            g.api.set_status_message("No frame available (run acquisition first).")
            return
        g._dead_pixel_revert_snapshot = raw.copy()
        out = _apply_dead_pixel(raw, g)
        g.api.output_manual_from_module(MODULE_NAME, out)
        g.api.set_status_message("Dead pixel correction applied to current frame.")

    with dpg.collapsing_header(parent=parent_tag, label="Dead Pixel Lines", default_open=False):
        with dpg.group(indent=10):
            api.build_alteration_apply_revert_ui(
                gui,
                MODULE_NAME,
                _cb_apply,
                auto_apply_attr="dead_pixel_auto_apply",
                revert_snapshot_attr="_dead_pixel_revert_snapshot",
                default_auto_apply=True,
            )
            dpg.add_input_text(
                label="Vertical (cols)", default_value=vlines_str,
                tag="dead_vertical_lines_input", width=-120,
                callback=_apply_vertical_lines
            )
            dpg.add_input_text(
                label="Horizontal (rows)", default_value=hlines_str,
                tag="dead_horizontal_lines_input", width=-120,
                callback=_apply_horizontal_lines
            )
            dpg.add_text("Comma-separated list (e.g. 661 or 100,200,300)", color=[150, 150, 150])
