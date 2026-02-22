"""
ESP HV power supply machine module.
Builds connection + power supply UI; uses in-process PSUCore (no ZMQ).
Optionally registers as gui.beam_supply for Auto On/Off: turn on and wait for
ready before acquisition, turn off when acquisition finishes.
"""

import time
import threading
import dearpygui.dearpygui as dpg

from .core import PSUCore

MODULE_INFO = {
    "display_name": "ESP HV Supply",
    "description": "Show ESP high-voltage power supply controls (serial/network). Applies on next startup.",
    "type": "machine",
    "default_enabled": False,
}


def get_setting_keys():
    """Keys this module persists (for registry/settings extra_keys)."""
    return [
        "esp_hv_serial_port", "esp_hv_network_ip",
        "esp_hv_kv_slider", "esp_hv_ma_slider", "esp_hv_filament_slider",
        "esp_hv_auto_on_off",
    ]


def get_default_settings():
    """Return default settings for this module."""
    return {
        "esp_hv_serial_port": "",
        "esp_hv_network_ip": "",
        "esp_hv_kv_slider": 0,
        "esp_hv_ma_slider": 0,
        "esp_hv_filament_slider": 0,
        "esp_hv_auto_on_off": False,
    }


# Optional beam supply contract: any module can set gui.beam_supply to an object
# with wants_auto_on_off(), is_connected(), turn_on_and_wait_ready(timeout), turn_off().
# Main app uses this to gate acquisition start and turn off when done.
BEAM_READY_TIMEOUT_S = 60
BEAM_READY_POLL_INTERVAL_S = 0.25


class BeamSupplyAdapter:
    """Exposes this PSU as the optional gui.beam_supply for Auto On/Off integration."""

    def __init__(self, core: PSUCore, auto_on_off_tag: str):
        self._core = core
        self._auto_on_off_tag = auto_on_off_tag

    def wants_auto_on_off(self) -> bool:
        if not dpg.does_item_exist(self._auto_on_off_tag):
            return False
        return bool(dpg.get_value(self._auto_on_off_tag))

    def is_connected(self) -> bool:
        return bool(self._core.get_state().get("connected", False))

    def turn_on_and_wait_ready(self, timeout_s: float = BEAM_READY_TIMEOUT_S, should_cancel: callable = None) -> bool:
        """
        Turn beam on and block until beam_ready, timeout, fault, or user turns tube off. Returns True if ready.
        should_cancel: Optional callable that returns True to cancel the wait (e.g. acq_stop.is_set).
        """
        self._core.set_beam_on(True)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if should_cancel is not None and should_cancel():
                return False
            st = self._core.get_state()
            if st.get("beam_ready", False):
                return True
            if st.get("filament_fault", False):
                return False
            # User clicked "Turn Off Tube" (or supply was turned off) – abort wait so we don't stay stuck
            if not st.get("beam_on_requested", True):
                return False
            time.sleep(BEAM_READY_POLL_INTERVAL_S)
        return False

    def turn_off(self) -> None:
        self._core.set_beam_on(False)


def _list_serial_ports():
    try:
        from serial.tools import list_ports
        return [(f"{p.device}  ({p.description})" if p.description else p.device, p.device) for p in list_ports.comports()]
    except Exception:
        return []


def _clamp_int(v: int, lo: int, hi: int) -> int:
    return int(max(lo, min(hi, v)))


def build_ui(gui, parent_tag="control_panel"):
    """
    Add ESP HV supply collapsing header and controls.
    gui: XrayGUI instance; uses gui.api for settings, status, and beam_supply registration.
    Registers a tick callback on gui for state updates.
    """
    loaded = gui.api.get_loaded_settings()

    # In-process core; state changes set dirty flag for next-frame UI update
    def _on_state_change(msg):
        setattr(gui, "_hv_psu_state_dirty", True)

    core = PSUCore(publish_event=_on_state_change)
    gui._hv_psu_core = core
    gui._hv_psu_state_dirty = True

    def _apply_state(st):
        if not dpg.does_item_exist("hv_kv_slider"):
            return
        connected = bool(st.get("connected", False))
        port = st.get("port", "")
        err = st.get("last_error", "")

        hard_kv = float(st.get("hard_kv_lim", 50.0))
        hard_ma = float(st.get("hard_ma_lim", 1.5))
        hard_fil = float(st.get("hard_fil_lim", 3.5))
        kv_max = max(1, int(round(hard_kv))) if hard_kv > 0 else 1
        ma_max = max(1, int(round(hard_ma * 100.0))) if hard_ma > 0 else 1
        fil_max = max(1, int(round(hard_fil * 100.0))) if hard_fil > 0 else 1

        try:
            dpg.configure_item("hv_kv_slider", max_value=kv_max)
            dpg.set_value("hv_kv_slider", _clamp_int(int(dpg.get_value("hv_kv_slider")), 0, kv_max))
        except Exception:
            pass
        try:
            dpg.configure_item("hv_ma_slider", max_value=ma_max)
            dpg.set_value("hv_ma_slider", _clamp_int(int(dpg.get_value("hv_ma_slider")), 0, ma_max))
        except Exception:
            pass
        try:
            dpg.configure_item("hv_fil_slider", max_value=fil_max)
            dpg.set_value("hv_fil_slider", _clamp_int(int(dpg.get_value("hv_fil_slider")), 0, fil_max))
        except Exception:
            pass

        kv_set = int(st.get("kv_set", 0))
        ma_set = float(st.get("ma_set", 0.0))
        fil_set = float(st.get("fil_lim_set", 0.0))
        dpg.set_value("hv_kv_set_text", f"Set: {kv_set}")
        dpg.set_value("hv_ma_set_text", f"Set: {ma_set:.2f}")
        dpg.set_value("hv_fil_set_text", f"Set: {fil_set:.2f}")

        # Keep sliders user-driven/persisted; do not mirror PSU setpoints back into sliders.
        # This avoids connect/readback forcing slider values to 0.

        conn_type = str(st.get("connection_type", "serial"))
        net_host = str(st.get("net_host", ""))
        net_port = int(st.get("net_port", 7777))
        if connected:
            status = f"Connected {net_host}:{net_port}" if conn_type == "network" else f"Connected {port}"
        else:
            status = "Not connected"
        if err:
            status += f"  Error: {err}"
        dpg.set_value("hv_status_text", status)

        dpg.set_value("hv_kv_read_text", f"Read: {float(st.get('kv_read', 0.0)):.2f}")
        dpg.set_value("hv_ma_read_text", f"Read: {float(st.get('ma_read', 0.0)):.2f}")
        dpg.set_value("hv_fil_read_text", f"Read: {float(st.get('fil_read', 0.0)):.2f}")

        spinup_done = bool(st.get("spinup_done", False))
        spinup_ms = int(st.get("spinup_ms", 0))
        dpg.set_value("hv_spinup_text", "Spinup: Done" if spinup_done else f"Spinup: {spinup_ms} ms")
        hv = bool(st.get("hv_out", False))
        dpg.set_value("hv_hvout_text", f"HVOut: {'true' if hv else 'false'}")
        beam_ready = bool(st.get("beam_ready", False))
        dpg.set_value("hv_beam_ready_text", f"BeamReady: {'true' if beam_ready else 'false'}")
        hv_time = int(st.get("hv_on_time_ms", 0))
        dpg.set_value("hv_hvtime_text", f"HVOnTime: {hv_time} ms")

        beam_on = bool(st.get("beam_on_requested", False))
        try:
            dpg.set_item_label("hv_beam_btn", "Turn Off Tube" if beam_on else "Turn On Tube")
        except Exception:
            pass
        try:
            dpg.configure_item("hv_connect_btn", enabled=not connected)
            dpg.configure_item("hv_disconnect_btn", enabled=connected)
            dpg.configure_item("hv_net_connect_btn", enabled=not connected)
            dpg.configure_item("hv_net_disconnect_btn", enabled=connected)
            dpg.configure_item("hv_kv_slider", enabled=connected)
            dpg.configure_item("hv_ma_slider", enabled=connected)
            dpg.configure_item("hv_fil_slider", enabled=connected)
            dpg.configure_item("hv_beam_btn", enabled=connected)
            dpg.configure_item("hv_estop_btn", enabled=connected)
        except Exception:
            pass

    with dpg.collapsing_header(parent=parent_tag, label="ESP HV Supply", default_open=False):
        with dpg.group(indent=10):
            # Connection
            ports = _list_serial_ports()
            port_options = [p[0] for p in ports]
            port_values = [p[1] for p in ports]
            saved_port = loaded.get("esp_hv_serial_port", "")
            try:
                default_idx = port_values.index(saved_port) if saved_port in port_values else 0
            except Exception:
                default_idx = 0
            default_combo = port_options[default_idx] if port_options else ""

            dpg.add_combo(
                items=port_options,
                default_value=default_combo,
                tag="hv_serial_combo",
                width=-120,
                callback=lambda s, a: gui.api.save_settings(),
            )
            with dpg.group(horizontal=True):
                dpg.add_button(label="Refresh", tag="hv_refresh_btn", width=80, callback=_make_refresh_ports_cb(gui, core))
                dpg.add_button(label="Connect", tag="hv_connect_btn", width=80, callback=_make_connect_cb(gui, core, _apply_state))
                dpg.add_button(label="Disconnect", tag="hv_disconnect_btn", width=80, callback=_make_disconnect_cb(gui, core, _apply_state))
            dpg.add_input_text(
                label="IP",
                default_value=loaded.get("esp_hv_network_ip", ""),
                tag="hv_ip_input",
                width=-120,
                hint="192.168.1.5",
                callback=lambda s, a: gui.api.save_settings(),
            )
            with dpg.group(horizontal=True):
                dpg.add_button(label="Net Connect", tag="hv_net_connect_btn", width=100, callback=_make_net_connect_cb(gui, core, _apply_state))
                dpg.add_button(label="Net Disconnect", tag="hv_net_disconnect_btn", width=100, callback=_make_net_disconnect_cb(gui, core, _apply_state))
            dpg.add_text("Not connected", tag="hv_status_text", color=[150, 150, 150])

            # Power supply
            dpg.add_checkbox(
                label="Auto On/Off",
                default_value=loaded.get("esp_hv_auto_on_off", False),
                tag="hv_auto_on_off_cb",
                callback=lambda s, a: gui.api.save_settings(),
            )
            dpg.add_text("Turn supply on before acquisition, wait for ready, then turn off when done.", color=[120, 120, 120])
            dpg.add_button(label="Turn On Tube", tag="hv_beam_btn", width=-1, callback=_make_beam_cb(gui, core, _apply_state))

            dpg.add_text("Set: 0", tag="hv_kv_set_text")
            dpg.add_text("Read: 0.00", tag="hv_kv_read_text")
            dpg.add_slider_int(
                label="kV",
                default_value=min(int(loaded.get("esp_hv_kv_slider", 0)), 50),
                min_value=0,
                max_value=50,
                tag="hv_kv_slider",
                width=-120,
                callback=_make_kv_cb(gui, core, _apply_state),
            )
            dpg.add_text("mA Set: 0.00", tag="hv_ma_set_text")
            dpg.add_text("mA Read: 0.00", tag="hv_ma_read_text")
            dpg.add_slider_int(
                label="mA (x100)",
                default_value=min(int(loaded.get("esp_hv_ma_slider", 0)), 150),
                min_value=0,
                max_value=150,
                tag="hv_ma_slider",
                width=-120,
                callback=_make_ma_cb(gui, core, _apply_state),
            )
            dpg.add_text("Fil Set: 0.00", tag="hv_fil_set_text")
            dpg.add_text("Fil Read: 0.00", tag="hv_fil_read_text")
            dpg.add_slider_int(
                label="Filament (x100)",
                default_value=min(int(loaded.get("esp_hv_filament_slider", 0)), 350),
                min_value=0,
                max_value=350,
                tag="hv_fil_slider",
                width=-120,
                callback=_make_fil_cb(gui, core, _apply_state),
            )
            dpg.add_text("Spinup: 0 ms", tag="hv_spinup_text", color=[120, 120, 120])
            dpg.add_text("HVOut: false", tag="hv_hvout_text", color=[120, 120, 120])
            dpg.add_text("BeamReady: false", tag="hv_beam_ready_text", color=[120, 120, 120])
            dpg.add_text("HVOnTime: 0 ms", tag="hv_hvtime_text", color=[120, 120, 120])
            dpg.add_button(label="EStop", tag="hv_estop_btn", width=-1, callback=_make_estop_cb(gui, core, _apply_state))

    # Register as optional beam supply for main app (Auto On/Off before/after acquisition)
    gui.api.register_beam_supply(BeamSupplyAdapter(core, "hv_auto_on_off_cb"))

    # Tick: refresh UI when state changed (async from serial/TCP)
    def _tick():
        if getattr(gui, "_hv_psu_state_dirty", False):
            gui._hv_psu_state_dirty = False
            try:
                _apply_state(core.get_state())
            except Exception:
                pass

    gui._machine_module_tick_callbacks = getattr(gui, "_machine_module_tick_callbacks", [])
    gui._machine_module_tick_callbacks.append(_tick)

    # Initial state
    _apply_state(core.get_state())


def _make_refresh_ports_cb(gui, core):
    def _cb(sender=None, app_data=None):
        ports = _list_serial_ports()
        port_options = [p[0] for p in ports]
        current = dpg.get_value("hv_serial_combo")
        dpg.configure_item("hv_serial_combo", items=port_options)
        if port_options and current in port_options:
            dpg.set_value("hv_serial_combo", current)
        elif port_options:
            dpg.set_value("hv_serial_combo", port_options[0])
    return _cb


def _get_selected_port():
    ports = _list_serial_ports()
    port_options = [p[0] for p in ports]
    port_values = [p[1] for p in ports]
    current_label = dpg.get_value("hv_serial_combo")
    try:
        idx = port_options.index(current_label)
        return port_values[idx]
    except Exception:
        return ""


def get_settings_for_save(gui=None):
    """Return current HV UI values for persistence. Call from gui._save_settings (gui passed for consistency)."""
    out = {}
    if not dpg.does_item_exist("hv_kv_slider"):
        return out
    try:
        # Fast path: avoid scanning serial ports during every save.
        # Combo label format is either "<device>" or "<device>  (<description>)".
        lbl = dpg.get_value("hv_serial_combo")
        out["esp_hv_serial_port"] = (str(lbl).split("  (", 1)[0] if lbl else "")
        out["esp_hv_network_ip"] = dpg.get_value("hv_ip_input").strip()
        out["esp_hv_kv_slider"] = int(dpg.get_value("hv_kv_slider"))
        out["esp_hv_ma_slider"] = int(dpg.get_value("hv_ma_slider"))
        out["esp_hv_filament_slider"] = int(dpg.get_value("hv_fil_slider"))
        out["esp_hv_auto_on_off"] = bool(dpg.get_value("hv_auto_on_off_cb"))
    except Exception:
        pass
    return out


def _make_connect_cb(gui, core, apply_state):
    def _cb(sender=None, app_data=None):
        port = _get_selected_port()
        if not port:
            gui.api.set_status_message("HV: Select a serial port first.")
            return
        resp = core.connect_serial(port, 9600)
        if not resp.get("ok", False):
            gui.api.set_status_message(f"HV: {resp.get('error', 'Connect failed')}")
            return
        gui.api.save_settings()
        kv = int(dpg.get_value("hv_kv_slider"))
        ma = int(dpg.get_value("hv_ma_slider")) / 100.0
        fil = int(dpg.get_value("hv_fil_slider")) / 100.0
        _start_restore_setpoints_sequence(gui, core, kv, ma, fil)
        apply_state(core.get_state())
        gui.api.set_status_message("HV: Connected (serial), restoring saved setpoints...")
    return _cb


def _make_disconnect_cb(gui, core, apply_state):
    def _cb(sender=None, app_data=None):
        core.disconnect_serial()
        apply_state(core.get_state())
        gui.api.set_status_message("HV: Disconnected")
    return _cb


def _make_net_connect_cb(gui, core, apply_state):
    def _cb(sender=None, app_data=None):
        host = dpg.get_value("hv_ip_input").strip()
        if not host:
            gui.api.set_status_message("HV: Enter IP first.")
            return
        resp = core.connect_network(host, 7777)
        if not resp.get("ok", False):
            gui.api.set_status_message(f"HV: {resp.get('error', 'Connect failed')}")
            return
        gui.api.save_settings()
        kv = int(dpg.get_value("hv_kv_slider"))
        ma = int(dpg.get_value("hv_ma_slider")) / 100.0
        fil = int(dpg.get_value("hv_fil_slider")) / 100.0
        _start_restore_setpoints_sequence(gui, core, kv, ma, fil)
        apply_state(core.get_state())
        gui.api.set_status_message("HV: Connected (network), restoring saved setpoints...")
    return _cb


def _start_restore_setpoints_sequence(gui, core, kv: int, ma: float, fil: float) -> None:
    """
    Restore saved setpoints in clean staged steps after connect:
      1) wait 2.0 s, set kV
      2) wait 0.5 s, set tube current (mA)
      3) wait 0.5 s, set filament current limit
    """
    kv = int(kv)
    ma = float(ma)
    fil = float(fil)

    def _worker():
        time.sleep(2.0)
        if not core.get_state().get("connected", False):
            return
        core.set_kv(kv)

        time.sleep(0.5)
        if not core.get_state().get("connected", False):
            return
        core.set_ma(ma)

        time.sleep(0.5)
        if not core.get_state().get("connected", False):
            return
        core.set_fil_lim(fil)

    threading.Thread(target=_worker, daemon=True).start()


def _make_net_disconnect_cb(gui, core, apply_state):
    def _cb(sender=None, app_data=None):
        core.disconnect_network()
        apply_state(core.get_state())
        gui.api.set_status_message("HV: Disconnected")
    return _cb


def _make_kv_cb(gui, core, apply_state):
    def _cb(sender=None, app_data=None):
        v = int(dpg.get_value("hv_kv_slider"))
        gui.api.save_settings()
        if core.get_state().get("connected", False):
            core.set_kv(v)
        apply_state(core.get_state())
    return _cb


def _make_ma_cb(gui, core, apply_state):
    def _cb(sender=None, app_data=None):
        v = int(dpg.get_value("hv_ma_slider")) / 100.0
        gui.api.save_settings()
        if core.get_state().get("connected", False):
            core.set_ma(v)
        apply_state(core.get_state())
    return _cb


def _make_fil_cb(gui, core, apply_state):
    def _cb(sender=None, app_data=None):
        v = int(dpg.get_value("hv_fil_slider")) / 100.0
        gui.api.save_settings()
        if core.get_state().get("connected", False):
            core.set_fil_lim(v)
        apply_state(core.get_state())
    return _cb


def _make_beam_cb(gui, core, apply_state):
    def _cb(sender=None, app_data=None):
        st = core.get_state()
        if not st.get("connected", False):
            gui.api.set_status_message("HV: Not connected")
            return
        # Toggle: if beam currently on, turn off; else turn on
        on = not st.get("beam_on_requested", False)
        resp = core.set_beam_on(on)
        if not resp.get("ok", True):
            gui.api.set_status_message(resp.get("error", "Rejected"))
        apply_state(core.get_state())
        dpg.set_item_label("hv_beam_btn", "Turn Off Tube" if on else "Turn On Tube")
    return _cb


def _make_estop_cb(gui, core, apply_state):
    def _cb(sender=None, app_data=None):
        core.estop()
        apply_state(core.get_state())
        dpg.set_item_label("hv_beam_btn", "Turn On Tube")
        gui.api.set_status_message("HV: EStop")
    return _cb
