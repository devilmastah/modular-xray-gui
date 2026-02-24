"""
Faxitron MX-20 / DX-50 machine module (serial).

- Connect → unit set to remote mode; Disconnect (or app exit) → front panel.
- State polled every few seconds: Warming up / Ready / Door open. Shown in module; exposure
  only allowed when Ready; kV and exposure time can be set anytime when connected.
- Beam on (HV On or Start with Auto On/Off): send !B → machine replies X → send C → wait 2s
  for HV to settle → beam active, app runs exposures. Exposure timer set to max; we abort
  when done. Beam off: send A → machine replies S.
- Registers as gui.beam_supply for Auto On/Off (beam on before capture, beam off when idle).
"""

import atexit
import time
import threading
import re
import dearpygui.dearpygui as dpg

MODULE_INFO = {
    "display_name": "Faxitron MX-20 DX-50",
    "description": "Faxitron MX-20/DX-50 over serial (9600 8N1). Set kV; Auto On/Off with capture; manual HV On/Off. Applies on next startup.",
    "type": "machine",
    "default_enabled": False,
}


def get_setting_keys():
    return [
        "fax_mx20_serial_port",
        "fax_mx20_kv",
        "fax_mx20_auto_on_off",
    ]


def get_default_settings():
    return {
        "fax_mx20_serial_port": "",
        "fax_mx20_kv": 26,
        "fax_mx20_auto_on_off": False,
    }


# Module-level ref so atexit can restore front panel if app closes without Disconnect
_fax_mx20_core_for_cleanup = None


# Serial: 9600 8N1, no handshake. Commands end with CR; 'C' and 'A' do not.
# DX-50 may miss commands; retry until response.
BAUD = 9600
QUERY_RETRIES = 5
QUERY_RETRY_DELAY_S = 0.15
READY_POLL_S = 0.3
BEAM_READY_TIMEOUT_S = 60
STATE_POLL_INTERVAL_S = 5.0  # only query ?S every 5 s to avoid hammering serial
HV_SETTLE_S = 2.0  # wait after !B→X→C before beam is considered on for exposures
MAX_EXPOSURE_S = 999.9  # set Faxitron timer to max; we abort with A when done
BEAM_X_TIMEOUT_S = 10.0  # max wait for X after !B
BEAM_S_TIMEOUT_S = 15.0  # max wait for S after A


def _list_serial_ports():
    try:
        from serial.tools import list_ports
        return [(f"{p.device}  ({p.description})" if p.description else p.device, p.device) for p in list_ports.comports()]
    except Exception:
        return []


class FaxitronMX20DX50Core:
    """Serial communication with Faxitron MX-20 / DX-50."""

    def __init__(self):
        self._ser = None
        self._lock = threading.Lock()

    def is_connected(self) -> bool:
        with self._lock:
            return self._ser is not None and self._ser.is_open

    def connect(self, port: str) -> bool:
        try:
            import serial
            with self._lock:
                if self._ser is not None:
                    try:
                        self._send_no_lock("!MF")
                        self._ser.close()
                    except Exception:
                        pass
                    self._ser = None
                self._ser = serial.Serial(
                    port, baudrate=BAUD, bytesize=8, stopbits=1, parity='N',
                    timeout=0.5, write_timeout=2.0,
                )
                self._send_no_lock("!MR")  # set remote mode on connect
                return True
        except Exception:
            return False

    def disconnect(self) -> None:
        with self._lock:
            if self._ser is not None:
                try:
                    self._send_no_lock("!MF")  # front panel so unit is not left in remote
                    self._ser.close()
                except Exception:
                    pass
                self._ser = None

    def _send_no_lock(self, cmd: str, add_cr: bool = True) -> None:
        if self._ser is None or not self._ser.is_open:
            raise RuntimeError("Not connected")
        raw = (cmd + "\r").encode("ascii") if add_cr else cmd.encode("ascii")
        self._ser.write(raw)
        self._ser.flush()

    def _read_line_no_lock(self, timeout_s: float = 0.5) -> str:
        self._ser.timeout = timeout_s
        line = self._ser.readline()
        return line.decode("ascii", errors="replace").strip()

    def send_cmd(self, cmd: str, add_cr: bool = True) -> None:
        """Send command (e.g. !MR, !V26, !T0140). No response expected."""
        with self._lock:
            self._send_no_lock(cmd, add_cr=add_cr)

    def query(self, cmd: str) -> str:
        """Send query (e.g. ?S, ?V) and return response line. Retries on miss."""
        with self._lock:
            if self._ser is None or not self._ser.is_open:
                return ""
            for _ in range(QUERY_RETRIES):
                try:
                    self._ser.reset_input_buffer()
                    self._send_no_lock(cmd)
                    time.sleep(0.05)
                    line = self._read_line_no_lock(timeout_s=0.5)
                    if line and (cmd in line or line.startswith("?")):
                        return line
                except Exception:
                    pass
                time.sleep(QUERY_RETRY_DELAY_S)
            return ""

    def set_remote(self) -> bool:
        self.send_cmd("!MR")
        return "?MR" in self.get_mode()

    def set_front_panel(self) -> bool:
        self.send_cmd("!MF")
        return "?MF" in self.get_mode()

    def set_kv(self, kv: int) -> None:
        kv = max(10, min(35, int(kv)))
        self.send_cmd(f"!V{kv}")

    def set_exposure(self, sec: float) -> None:
        sec = max(0.1, min(999.9, float(sec)))
        val = int(round(sec * 10))
        self.send_cmd(f"!T{val:04d}")

    def get_state(self) -> str:
        """Return ?SW (Warming), ?SD (Door open), ?SR (Ready)."""
        r = self.query("?S")
        if "?SR" in r or "Ready" in r:
            return "Ready"
        if "?SD" in r or "Door" in r:
            return "Door open"
        if "?SW" in r or "Warming" in r:
            return "Warming up"
        return r or "Unknown"

    def get_mode(self) -> str:
        r = self.query("?M")
        return r.strip() if r else ""

    def get_kv(self) -> int:
        r = self.query("?V")
        m = re.search(r"[?]V(\d+)", r)
        return int(m.group(1)) if m else 0

    def get_exposure(self) -> float:
        r = self.query("?T")
        m = re.search(r"T(\d+)", r)
        if m:
            return int(m.group(1)) / 10.0
        return 0.0

    def wait_for_ready(self, timeout_s: float, should_cancel: callable = None) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if should_cancel is not None and should_cancel():
                return False
            if self.get_state() == "Ready":
                return True
            time.sleep(READY_POLL_S)
        return False

    def beam_on(self, kv: int, should_cancel: callable = None) -> bool:
        """Enable beam: set remote, kV, exposure to max; send !B, wait for X, send C, wait 2s. Returns True when beam is on."""
        with self._lock:
            if self._ser is None or not self._ser.is_open:
                return False
            try:
                self._send_no_lock("!MR")
                time.sleep(0.05)
                kv = max(10, min(35, int(kv)))
                self._send_no_lock(f"!V{kv}")
                val = int(round(MAX_EXPOSURE_S * 10))
                self._send_no_lock(f"!T{val:04d}")
                self._ser.reset_input_buffer()
                self._send_no_lock("!B")
            except Exception:
                return False
        deadline = time.monotonic() + BEAM_X_TIMEOUT_S
        while time.monotonic() < deadline:
            if should_cancel is not None and should_cancel():
                return False
            with self._lock:
                line = self._read_line_no_lock(timeout_s=0.5)
            if line and ("X" in line or "x" in line):
                with self._lock:
                    try:
                        self._send_no_lock("C", add_cr=False)
                    except Exception:
                        return False
                time.sleep(HV_SETTLE_S)
                return True
        return False

    def beam_off(self) -> bool:
        """Turn off beam: send A, wait for S. Returns True when machine confirmed. Never raises."""
        try:
            with self._lock:
                if self._ser is None or not self._ser.is_open:
                    return False
                try:
                    self._send_no_lock("A", add_cr=False)
                except Exception:
                    return False
            deadline = time.monotonic() + BEAM_S_TIMEOUT_S
            while time.monotonic() < deadline:
                with self._lock:
                    line = self._read_line_no_lock(timeout_s=0.5)
                if line and "S" in line and "?S" not in line:
                    return True
            return False
        except Exception as e:
            if __debug__:
                import traceback
                traceback.print_exc()
            return False


class BeamSupplyAdapter:
    """Exposes Faxitron MX-20/DX-50 as gui.beam_supply for Auto On/Off."""

    def __init__(self, core: FaxitronMX20DX50Core, auto_on_off_tag: str, get_kv: callable, set_wait_message: callable = None):
        self._core = core
        self._auto_on_off_tag = auto_on_off_tag
        self._get_kv = get_kv
        self._set_wait_message = set_wait_message  # show warning when not Ready so acquisition does not start

    def wants_auto_on_off(self) -> bool:
        if not dpg.does_item_exist(self._auto_on_off_tag):
            return False
        return bool(dpg.get_value(self._auto_on_off_tag))

    def is_connected(self) -> bool:
        return self._core.is_connected()

    def turn_on_and_wait_ready(self, timeout_s: float = BEAM_READY_TIMEOUT_S, should_cancel: callable = None) -> bool:
        if not self._core.is_connected():
            return False
        state = self._core.get_state()
        if state != "Ready":
            if self._set_wait_message is not None:
                self._set_wait_message(f"Faxitron: {state} — acquisition not started. Close door / wait for Ready.")
            return False
        return self._core.beam_on(self._get_kv(), should_cancel)

    def turn_off(self) -> None:
        self._core.beam_off()


def build_ui(gui, parent_tag="control_panel"):
    global _fax_mx20_core_for_cleanup
    loaded = gui.api.get_loaded_settings()
    core = FaxitronMX20DX50Core()
    gui._fax_mx20_core = core
    gui._fax_mx20_last_state = None
    gui._fax_mx20_last_state_time = 0.0
    _fax_mx20_core_for_cleanup = core

    def _get_kv():
        if dpg.does_item_exist("fax_mx20_kv"):
            return int(dpg.get_value("fax_mx20_kv"))
        return loaded.get("fax_mx20_kv", 26)

    def _apply_state():
        if not dpg.does_item_exist("fax_mx20_status"):
            return
        if not core.is_connected():
            dpg.set_value("fax_mx20_status", "Disconnected")
            dpg.configure_item("fax_mx20_status", color=[150, 150, 150])
            if dpg.does_item_exist("fax_mx20_on_btn"):
                dpg.configure_item("fax_mx20_on_btn", enabled=False)
            if dpg.does_item_exist("fax_mx20_off_btn"):
                dpg.configure_item("fax_mx20_off_btn", enabled=False)
            return
        try:
            now = time.time()
            if now - getattr(gui, "_fax_mx20_last_state_time", 0) >= STATE_POLL_INTERVAL_S:
                gui._fax_mx20_last_state = core.get_state()
                gui._fax_mx20_last_state_time = now
            state = gui._fax_mx20_last_state or "Unknown"
            kv = core.get_kv()
            dpg.set_value("fax_mx20_status", f"State: {state}  |  kV={kv}")
            color = [80, 200, 80] if state == "Ready" else [200, 200, 80]
            dpg.configure_item("fax_mx20_status", color=color)
            # HV On only when Ready; door open or warming up => cannot turn on
            if dpg.does_item_exist("fax_mx20_on_btn"):
                dpg.configure_item("fax_mx20_on_btn", enabled=(state == "Ready"))
            # Safety: HV Off always available when connected (even during exposure or wait)
            if dpg.does_item_exist("fax_mx20_off_btn"):
                dpg.configure_item("fax_mx20_off_btn", enabled=True)
        except Exception as e:
            dpg.set_value("fax_mx20_status", f"Error: {e}")
            dpg.configure_item("fax_mx20_status", color=[200, 80, 80])
            if dpg.does_item_exist("fax_mx20_on_btn"):
                dpg.configure_item("fax_mx20_on_btn", enabled=False)
            if dpg.does_item_exist("fax_mx20_off_btn"):
                dpg.configure_item("fax_mx20_off_btn", enabled=True)  # keep HV Off available on error

    with dpg.collapsing_header(parent=parent_tag, label="Faxitron MX-20 / DX-50", default_open=True):
        with dpg.group(indent=10):
            dpg.add_text("State: Warming up / Ready / Door open (polled every few s). Exposure only when Ready; kV settable anytime.", color=[150, 150, 150])
            ports = _list_serial_ports()
            port_options = [p[0] for p in ports]
            port_values = [p[1] for p in ports]
            saved_port = loaded.get("fax_mx20_serial_port", "")
            try:
                default_idx = port_values.index(saved_port) if saved_port in port_values else 0
            except Exception:
                default_idx = 0
            default_combo = port_options[default_idx] if port_options else ""
            dpg.add_combo(
                items=port_options,
                default_value=default_combo,
                tag="fax_mx20_port_combo",
                width=-120,
                callback=lambda s, a: gui.api.save_settings(),
            )
            with dpg.group(horizontal=True):
                dpg.add_button(label="Connect", tag="fax_mx20_connect_btn", width=90, callback=_make_connect_cb(gui, core, _apply_state))
                dpg.add_button(label="Disconnect", tag="fax_mx20_disconnect_btn", width=90, callback=_make_disconnect_cb(gui, core, _apply_state))
            dpg.add_slider_int(
                label="kV",
                default_value=loaded.get("fax_mx20_kv", 26),
                min_value=10,
                max_value=35,
                tag="fax_mx20_kv",
                width=-40,
                callback=lambda s, a: gui.api.save_settings(),
            )
            dpg.add_checkbox(
                label="Auto On/Off",
                default_value=loaded.get("fax_mx20_auto_on_off", False),
                tag="fax_mx20_auto_on_off_cb",
                callback=lambda s, a: gui.api.save_settings(),
            )
            dpg.add_text("With Auto On/Off: HV on before capture, HV off when idle. Or use manual HV On / HV Off.", color=[120, 120, 120])
            with dpg.group(horizontal=True):
                dpg.add_button(label="HV On", tag="fax_mx20_on_btn", width=90, callback=_make_hv_on_cb(gui, core, _get_kv, _apply_state))
                dpg.add_button(label="HV Off", tag="fax_mx20_off_btn", width=90, callback=_make_hv_off_cb(gui, core, _apply_state))
            dpg.add_text("Disconnected", tag="fax_mx20_status", color=[150, 150, 150])

    def _set_wait_message(msg: str) -> None:
        gui.api.set_progress(0.0, msg)

    gui.api.register_beam_supply(BeamSupplyAdapter(core, "fax_mx20_auto_on_off_cb", _get_kv, set_wait_message=_set_wait_message))

    gui._machine_module_tick_callbacks = getattr(gui, "_machine_module_tick_callbacks", [])
    gui._machine_module_tick_callbacks.append(_apply_state)
    _apply_state()


def _make_connect_cb(gui, core, apply_state):
    def _cb(sender=None, app_data=None):
        port_combo = dpg.get_value("fax_mx20_port_combo")
        ports = _list_serial_ports()
        port_values = [p[1] for p in ports]
        port_options = [p[0] for p in ports]
        try:
            idx = port_options.index(port_combo) if port_combo in port_options else 0
            port = port_values[idx]
        except Exception:
            gui.api.set_status_message("Select a port")
            return
        gui.api.set_status_message("Connecting...")
        if core.connect(port):
            gui.api.set_status_message("Faxitron MX-20/DX-50 connected")
            gui._fax_mx20_last_state_time = 0  # force state poll on next tick
            apply_state()
        else:
            gui.api.set_status_message("Connection failed")
        gui.api.save_settings()
    return _cb


def _make_disconnect_cb(gui, core, apply_state):
    def _cb(sender=None, app_data=None):
        core.disconnect()
        gui.api.set_status_message("Faxitron MX-20/DX-50 disconnected")
        apply_state()
        gui.api.save_settings()
    return _cb


def _make_hv_on_cb(gui, core, get_kv, apply_state):
    def _cb(sender=None, app_data=None):
        if not core.is_connected():
            gui.api.set_status_message("Not connected")
            return
        state = core.get_state()
        if state != "Ready":
            gui.api.set_status_message(f"Cannot turn on HV: {state} (only when Ready)")
            return
        def _do():
            gui.api.set_progress(0.0, "Beam on (!B→X→C, 2s settle)...")
            if core.beam_on(get_kv()):
                gui.api.set_progress(1.0)
                gui.api.set_status_message("Beam on — take exposures; use HV Off to abort")
            else:
                gui.api.set_status_message("Beam on failed (no X or timeout)")
            apply_state()
        threading.Thread(target=_do, daemon=True).start()
    return _cb


def _make_hv_off_cb(gui, core, apply_state):
    def _cb(sender=None, app_data=None):
        if not core.is_connected():
            return
        gui.api.set_progress(0.0, "Beam off (A→S)...")
        if core.beam_off():
            gui.api.set_status_message("Beam off")
        else:
            gui.api.set_status_message("Beam off sent (no S confirm)")
        gui.api.set_progress(1.0)
        apply_state()
    return _cb


def _atexit_fax_mx20_disconnect():
    if _fax_mx20_core_for_cleanup is not None and _fax_mx20_core_for_cleanup.is_connected():
        _fax_mx20_core_for_cleanup.disconnect()


atexit.register(_atexit_fax_mx20_disconnect)


def get_settings_for_save(gui=None):
    out = {}
    if not dpg.does_item_exist("fax_mx20_kv"):
        return out
    try:
        out["fax_mx20_serial_port"] = ""
        if dpg.does_item_exist("fax_mx20_port_combo"):
            combo = dpg.get_value("fax_mx20_port_combo")
            ports = _list_serial_ports()
            port_options = [p[0] for p in ports]
            port_values = [p[1] for p in ports]
            if combo in port_options:
                out["fax_mx20_serial_port"] = port_values[port_options.index(combo)]
        out["fax_mx20_kv"] = int(dpg.get_value("fax_mx20_kv"))
        out["fax_mx20_auto_on_off"] = bool(dpg.get_value("fax_mx20_auto_on_off_cb"))
    except Exception:
        pass
    return out
