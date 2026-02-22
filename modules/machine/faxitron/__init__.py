"""
Faxitron X-ray source machine module.
Builds Faxitron UI and callbacks; uses the GUI's teensy reference and settings.
Does not register as beam_supply (manual Expose only); compatible with optional
beam_supply modules (ESP HV, Example supply) for acquisition Auto On/Off.
"""

import threading
import dearpygui.dearpygui as dpg

MODULE_INFO = {
    "display_name": "Faxitron",
    "description": "Show Faxitron X-ray source controls in the control panel. Applies on next startup.",
    "type": "machine",
    "default_enabled": False,
}


def get_setting_keys():
    """Keys this module persists (for registry/settings extra_keys)."""
    return ["fax_voltage", "fax_exposure", "fax_mode"]


def get_default_settings():
    """Return default settings for this module."""
    return {
        "fax_voltage": 20,
        "fax_exposure": 5.0,
        "fax_mode": "Remote",
    }


# HamamatsuTeensy is a shared hardware library (legacy - used by hamamatsu_c7942 and faxitron)
# Import after defaults are defined so defaults can be collected even if import fails
try:
    from lib.hamamatsu_teensy import HamamatsuTeensy
except ImportError:
    HamamatsuTeensy = None  # Module can still provide defaults even if hardware unavailable


def build_ui(gui, parent_tag="control_panel"):
    """
    Add Faxitron collapsing header and controls under the given parent.
    gui: XrayGUI instance (must have .teensy; uses gui.api for settings and status).
    """
    loaded = gui.api.get_loaded_settings()

    with dpg.collapsing_header(parent=parent_tag, label="Faxitron", default_open=False):
        with dpg.group(indent=10):
            dpg.add_slider_int(
                label="kV",
                default_value=loaded.get("fax_voltage", 20),
                min_value=1,
                max_value=35,
                tag="fax_voltage",
                width=-40,
                callback=lambda s, a: gui.api.save_settings(),
            )
            dpg.add_input_float(
                label="Exp (s)",
                default_value=loaded.get("fax_exposure", 5.0),
                step=0.1,
                min_value=0.1,
                max_value=99.9,
                tag="fax_exposure",
                width=-60,
                callback=lambda s, a: gui.api.save_settings(),
            )
            dpg.add_combo(
                items=["Remote", "Front Panel"],
                default_value=loaded.get("fax_mode", "Remote"),
                tag="fax_mode_combo",
                width=-1,
                callback=lambda s, a: gui.api.save_settings(),
            )
            with dpg.group(horizontal=True):
                dpg.add_button(label="Expose", callback=_make_expose_cb(gui), width=115)
                dpg.add_button(label="Refresh", callback=_make_refresh_cb(gui), width=115)
            dpg.add_text("--", tag="fax_status")


def get_settings_for_save(gui=None):
    """Return current Faxitron UI values for persistence. Call from gui._save_settings (gui passed for consistency)."""
    out = {}
    if not dpg.does_item_exist("fax_voltage"):
        return out
    try:
        out["fax_voltage"] = int(dpg.get_value("fax_voltage"))
        out["fax_exposure"] = float(dpg.get_value("fax_exposure"))
        out["fax_mode"] = dpg.get_value("fax_mode_combo")
    except Exception:
        pass
    return out


def _make_expose_cb(gui):
    def _cb(sender=None, app_data=None):
        if gui.teensy is None:
            gui.api.set_status_message("Not connected")
            return

        def _do():
            try:
                v = int(dpg.get_value("fax_voltage"))
                t = float(dpg.get_value("fax_exposure"))
                mode_str = dpg.get_value("fax_mode_combo")
                mode = (
                    HamamatsuTeensy.FAXITRON_MODE_REMOTE
                    if mode_str == "Remote"
                    else HamamatsuTeensy.FAXITRON_MODE_FRONT_PANEL
                )
                gui.teensy.set_faxitron_mode(mode)
                gui.teensy.set_faxitron_voltage(v)
                gui.teensy.set_faxitron_exposure_time(t)
                dpg.set_value("fax_status", "Exposing...")
                gui.teensy.perform_faxitron_exposure()
                dpg.set_value("fax_status", "Done")
            except Exception as e:
                dpg.set_value("fax_status", f"Error: {e}")

        threading.Thread(target=_do, daemon=True).start()

    return _cb


def _make_refresh_cb(gui):
    def _cb(sender=None, app_data=None):
        if gui.teensy is None:
            return

        def _do():
            try:
                state = gui.teensy.get_faxitron_state() or "unknown"
                dpg.set_value("fax_status", state)
            except Exception as e:
                dpg.set_value("fax_status", f"Error: {e}")

        threading.Thread(target=_do, daemon=True).start()

    return _cb
