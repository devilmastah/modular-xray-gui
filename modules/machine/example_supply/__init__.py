"""
Example (dummy) beam supply module.
Simulates a supply that is always "connected", with 5000 ms run-up time to beam ready.
Implements the optional gui.beam_supply contract for Auto On/Off integration.
"""

import time
import dearpygui.dearpygui as dpg

BEAM_READY_DELAY_S = 5.0   # Simulated delay from HV on to beam ready (5000 ms)
BEAM_READY_POLL_INTERVAL_S = 0.1

MODULE_INFO = {
    "display_name": "Example Supply",
    "description": "Dummy supply with Auto On/Off and 5 s beam-ready delay (for testing). Applies on next startup.",
    "type": "machine",
    "default_enabled": True,
}


def get_setting_keys():
    """Keys this module persists (for registry/settings extra_keys)."""
    return ["example_supply_kv", "example_supply_ma", "example_supply_auto_on_off"]


def get_default_settings():
    """Return default settings for this module."""
    return {
        "example_supply_kv": 40,
        "example_supply_ma": 10,
        "example_supply_auto_on_off": False,
    }


class ExampleCore:
    """In-process dummy supply: always connected; beam_ready True after BEAM_READY_DELAY_S."""

    def __init__(self):
        self._beam_on = False
        self._beam_on_since = None  # time.monotonic() when beam was turned on
        self._kv = 40
        self._ma = 10  # 0.10 mA in units of 0.01

    def get_state(self):
        now = time.monotonic()
        ready = False
        if self._beam_on and self._beam_on_since is not None:
            if now - self._beam_on_since >= BEAM_READY_DELAY_S:
                ready = True
        return {
            "connected": True,
            "beam_on_requested": self._beam_on,
            "beam_ready": ready,
            "kv": self._kv,
            "ma": self._ma,
        }

    def set_beam_on(self, on: bool):
        self._beam_on = bool(on)
        if self._beam_on:
            self._beam_on_since = time.monotonic()
        else:
            self._beam_on_since = None

    def set_kv(self, value: int):
        self._kv = max(0, min(120, value))

    def set_ma(self, value: int):
        self._ma = max(0, min(100, value))


class BeamSupplyAdapter:
    """Exposes this example supply as gui.beam_supply for Auto On/Off."""

    def __init__(self, core: ExampleCore, auto_on_off_tag: str):
        self._core = core
        self._auto_on_off_tag = auto_on_off_tag

    def wants_auto_on_off(self) -> bool:
        if not dpg.does_item_exist(self._auto_on_off_tag):
            return False
        return bool(dpg.get_value(self._auto_on_off_tag))

    def is_connected(self) -> bool:
        return bool(self._core.get_state().get("connected", False))

    def turn_on_and_wait_ready(self, timeout_s: float = 15.0, should_cancel: callable = None) -> bool:
        """
        Turn beam on and block until beam_ready or timeout. Returns True if ready.
        should_cancel: Optional callable that returns True to cancel the wait (e.g. acq_stop.is_set).
        """
        self._core.set_beam_on(True)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if should_cancel is not None and should_cancel():
                return False
            if self._core.get_state().get("beam_ready", False):
                return True
            time.sleep(BEAM_READY_POLL_INTERVAL_S)
        return False

    def turn_off(self) -> None:
        self._core.set_beam_on(False)


def build_ui(gui, parent_tag="control_panel"):
    """Add Example supply collapsing header and controls. Registers beam_supply via API."""
    loaded = gui.api.get_loaded_settings()
    core = ExampleCore()
    core._kv = int(loaded.get("example_supply_kv", 40))
    core._ma = int(loaded.get("example_supply_ma", 10))

    def _apply_state():
        if not dpg.does_item_exist("ex_supply_status"):
            return
        st = core.get_state()
        # HV On / HV Off
        hv_text = "HV On" if st["beam_on_requested"] else "HV Off"
        hv_color = [200, 180, 80] if st["beam_on_requested"] else [150, 150, 150]
        dpg.set_value("ex_supply_hv_status", hv_text)
        dpg.configure_item("ex_supply_hv_status", color=hv_color)
        # Beam: Wait for beam / Beam ready
        if st["beam_ready"]:
            status = "Beam ready"
            color = [80, 200, 80]
        elif st["beam_on_requested"]:
            status = "Wait for beam"
            color = [200, 200, 80]
        else:
            status = "—"
            color = [150, 150, 150]
        dpg.set_value("ex_supply_status", status)
        dpg.configure_item("ex_supply_status", color=color)

    with dpg.collapsing_header(label="Example Supply", default_open=True, parent=parent_tag):
        with dpg.group(indent=10):
            dpg.add_text("Dummy supply (always connected). 5 s run-up", color=[120, 120, 120])
            dpg.add_text("to beam ready.", color=[120, 120, 120])
            dpg.add_checkbox(
                label="Auto On/Off",
                default_value=loaded.get("example_supply_auto_on_off", False),
                tag="ex_supply_auto_on_off_cb",
                callback=lambda s, a: gui.api.save_settings(),
            )
            dpg.add_text("Turn on before acquisition, wait for ready,", color=[120, 120, 120])
            dpg.add_text("turn off when done.", color=[120, 120, 120])
            dpg.add_slider_int(label="kV", min_value=0, max_value=120, default_value=core._kv, tag="ex_supply_kv_slider", width=-120,
                              callback=lambda s, v: (core.set_kv(int(v)), _apply_state(), gui.api.save_settings()))
            dpg.add_slider_int(label="mA (×0.01)", min_value=0, max_value=100, default_value=core._ma, tag="ex_supply_ma_slider", width=-120,
                              callback=lambda s, v: (core.set_ma(int(v)), _apply_state(), gui.api.save_settings()))
            dpg.add_text("Status:", color=[180, 180, 180])
            dpg.add_text("HV Off", tag="ex_supply_hv_status", color=[150, 150, 150])
            dpg.add_text("Wait for beam", tag="ex_supply_status", color=[150, 150, 150])
            _apply_state()

    gui.api.register_beam_supply(BeamSupplyAdapter(core, "ex_supply_auto_on_off_cb"))

    # Tick: refresh status (e.g. transition to "Beam ready" after 5 s)
    def _tick():
        _apply_state()

    if not hasattr(gui, "_machine_module_tick_callbacks"):
        gui._machine_module_tick_callbacks = []
    gui._machine_module_tick_callbacks.append(_tick)


def get_settings_for_save(gui=None):
    """Return current Example supply values for persistence (gui passed for consistency)."""
    out = {}
    if not dpg.does_item_exist("ex_supply_kv_slider"):
        return out
    try:
        out["example_supply_kv"] = int(dpg.get_value("ex_supply_kv_slider"))
        out["example_supply_ma"] = int(dpg.get_value("ex_supply_ma_slider"))
        out["example_supply_auto_on_off"] = bool(dpg.get_value("ex_supply_auto_on_off_cb"))
    except Exception:
        pass
    return out
