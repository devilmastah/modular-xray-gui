"""
Example Arduino relay power supply module.
Controls a relay via serial (ON/OFF commands). Use with the relay_serial.ino sketch.
Provides Auto On/Off and manual Turn On / Turn Off. Registers as gui.beam_supply for Auto On/Off.
"""

import time
import threading
import dearpygui.dearpygui as dpg

SERIAL_BAUD = 9600
READY_LINE = b"READY"
READ_POLL_S = 0.2

MODULE_INFO = {
    "display_name": "Example Arduino powersupply",
    "description": "Relay control via Arduino (serial ON/OFF). Auto On/Off and manual On/Off. Use relay_serial.ino. Applies on next startup.",
    "type": "machine",
    "default_enabled": False,
}


def get_setting_keys():
    return ["ard_psu_serial_port", "ard_psu_auto_on_off"]


def get_default_settings():
    return {
        "ard_psu_serial_port": "",
        "ard_psu_auto_on_off": False,
    }


def _list_serial_ports():
    try:
        from serial.tools import list_ports
        return [(f"{p.device}  ({p.description})" if p.description else p.device, p.device) for p in list_ports.comports()]
    except Exception:
        return []


class ArduinoRelayCore:
    """Serial connection to Arduino; sends ON/OFF, waits for READY handshake."""

    def __init__(self):
        self._ser = None
        self._lock = threading.Lock()
        self._beam_on_requested = False
        self._beam_ready_received = False  # True when we received READY after last ON

    def is_connected(self) -> bool:
        with self._lock:
            return self._ser is not None and self._ser.is_open

    def connect(self, port: str) -> bool:
        try:
            import serial
            with self._lock:
                if self._ser is not None:
                    try:
                        self._ser.close()
                    except Exception:
                        pass
                    self._ser = None
                self._ser = serial.Serial(port, baudrate=SERIAL_BAUD, timeout=0.5)
                self._beam_on_requested = False
                self._beam_ready_received = False
                return True
        except Exception:
            return False

    def disconnect(self) -> None:
        with self._lock:
            if self._ser is not None:
                try:
                    self._ser.write(b"OFF\n")
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None
            self._beam_on_requested = False
            self._beam_ready_received = False

    def set_beam_on(self, on: bool) -> bool:
        """Send ON or OFF over serial. Returns True if command was sent."""
        with self._lock:
            if self._ser is None or not self._ser.is_open:
                return False
            try:
                self._ser.write(b"ON\n" if on else b"OFF\n")
                self._ser.flush()
                self._beam_on_requested = on
                self._beam_ready_received = False
                return True
            except Exception:
                return False

    def wait_for_ready(self, timeout_s: float, should_cancel: callable = None) -> bool:
        """After sending ON, read lines until READY or timeout/cancel. Returns True when READY received."""
        deadline = time.monotonic() + timeout_s
        with self._lock:
            if self._ser is None or not self._ser.is_open:
                return False
            self._ser.timeout = READ_POLL_S
        while time.monotonic() < deadline:
            if should_cancel is not None and should_cancel():
                return False
            with self._lock:
                if self._ser is None or not self._ser.is_open:
                    return False
                line = self._ser.readline()
            if line and line.strip() == READY_LINE:
                with self._lock:
                    self._beam_ready_received = True
                return True
        return False

    def get_state(self) -> dict:
        with self._lock:
            connected = self._ser is not None and self._ser.is_open
            ready = connected and self._beam_on_requested and self._beam_ready_received
            return {
                "connected": connected,
                "beam_on_requested": self._beam_on_requested,
                "beam_ready": ready,
            }


class BeamSupplyAdapter:
    """Exposes Arduino relay as gui.beam_supply for Auto On/Off."""

    def __init__(self, core: ArduinoRelayCore, auto_on_off_tag: str):
        self._core = core
        self._auto_on_off_tag = auto_on_off_tag

    def wants_auto_on_off(self) -> bool:
        if not dpg.does_item_exist(self._auto_on_off_tag):
            return False
        return bool(dpg.get_value(self._auto_on_off_tag))

    def is_connected(self) -> bool:
        return self._core.is_connected()

    def turn_on_and_wait_ready(self, timeout_s: float = 15.0, should_cancel: callable = None) -> bool:
        if not self._core.set_beam_on(True):
            return False
        return self._core.wait_for_ready(timeout_s, should_cancel)

    def turn_off(self) -> None:
        self._core.set_beam_on(False)


def build_ui(gui, parent_tag="control_panel"):
    loaded = gui.api.get_loaded_settings()
    core = ArduinoRelayCore()
    gui._ard_psu_core = core

    def _apply_state():
        if not dpg.does_item_exist("ard_psu_status"):
            return
        st = core.get_state()
        if st["beam_on_requested"]:
            status = "Relay ON (beam ready)" if st["beam_ready"] else "Relay ON (settling…)"
            color = [80, 200, 80] if st["beam_ready"] else [200, 200, 80]
        else:
            status = "Relay OFF"
            color = [150, 150, 150]
        dpg.set_value("ard_psu_status", status)
        dpg.configure_item("ard_psu_status", color=color)

    with dpg.collapsing_header(parent=parent_tag, label="Example Arduino powersupply", default_open=True):
        with dpg.group(indent=10):
            dpg.add_text("Relay on/off via Arduino serial. Upload relay_serial.ino and select port.", color=[150, 150, 150])
            ports = _list_serial_ports()
            port_options = [p[0] for p in ports]
            port_values = [p[1] for p in ports]
            saved_port = loaded.get("ard_psu_serial_port", "")
            try:
                default_idx = port_values.index(saved_port) if saved_port in port_values else 0
            except Exception:
                default_idx = 0
            default_combo = port_options[default_idx] if port_options else ""

            dpg.add_combo(
                items=port_options,
                default_value=default_combo,
                tag="ard_psu_port_combo",
                width=-120,
                callback=lambda s, a: gui.api.save_settings(),
            )
            with dpg.group(horizontal=True):
                dpg.add_button(label="Connect", tag="ard_psu_connect_btn", width=90, callback=_make_connect_cb(gui, core, _apply_state))
                dpg.add_button(label="Disconnect", tag="ard_psu_disconnect_btn", width=90, callback=_make_disconnect_cb(gui, core, _apply_state))
            dpg.add_checkbox(
                label="Auto On/Off",
                default_value=loaded.get("ard_psu_auto_on_off", False),
                tag="ard_psu_auto_on_off_cb",
                callback=lambda s, a: gui.api.save_settings(),
            )
            dpg.add_text("Turn on before acquisition, turn off when done.", color=[120, 120, 120])
            with dpg.group(horizontal=True):
                dpg.add_button(label="Turn On", tag="ard_psu_on_btn", width=90, callback=_make_on_cb(gui, core, _apply_state))
                dpg.add_button(label="Turn Off", tag="ard_psu_off_btn", width=90, callback=_make_off_cb(gui, core, _apply_state))
            dpg.add_text("Relay OFF", tag="ard_psu_status", color=[150, 150, 150])

    gui.api.register_beam_supply(BeamSupplyAdapter(core, "ard_psu_auto_on_off_cb"))

    def _tick():
        _apply_state()

    gui._machine_module_tick_callbacks = getattr(gui, "_machine_module_tick_callbacks", [])
    gui._machine_module_tick_callbacks.append(_tick)
    _apply_state()


def _make_connect_cb(gui, core, apply_state):
    def _connect():
        lbl = dpg.get_value("ard_psu_port_combo") if dpg.does_item_exist("ard_psu_port_combo") else ""
        port = (str(lbl).split("  (", 1)[0].strip() if lbl else "").strip()
        if not port:
            gui.api.set_status_message("Arduino PSU: Select a serial port first.")
            return
        if core.connect(port):
            gui.api.set_status_message("Arduino PSU: Connected.")
            apply_state()
        else:
            gui.api.set_status_message("Arduino PSU: Failed to open port.")
    return _connect


def _make_disconnect_cb(gui, core, apply_state):
    def _disconnect():
        core.disconnect()
        gui.api.set_status_message("Arduino PSU: Disconnected.")
        apply_state()
    return _disconnect


def _make_on_cb(gui, core, apply_state):
    def _on():
        if not core.is_connected():
            gui.api.set_status_message("Arduino PSU: Connect first.")
            return
        core.set_beam_on(True)
        apply_state()
    return _on


def _make_off_cb(gui, core, apply_state):
    def _off():
        if core.is_connected():
            core.set_beam_on(False)
        apply_state()
    return _off


def get_settings_for_save(gui=None):
    out = {}
    if not dpg.does_item_exist("ard_psu_port_combo"):
        return out
    try:
        lbl = dpg.get_value("ard_psu_port_combo")
        out["ard_psu_serial_port"] = (str(lbl).split("  (", 1)[0].strip() if lbl else "") or ""
        out["ard_psu_auto_on_off"] = bool(dpg.get_value("ard_psu_auto_on_off_cb"))
    except Exception:
        pass
    return out
