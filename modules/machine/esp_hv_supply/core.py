"""
ESP HV power supply core: serial/TCP transport, protocol parsing, state.
No ZMQ or network server; used in-process with a state-changed callback.
"""

import re
import time
import threading
from dataclasses import dataclass, asdict
from typing import Callable, Optional, Dict, Any

import serial


@dataclass
class PSUState:
    connected: bool = False
    port: str = ""
    baud: int = 9600

    connection_type: str = "none"
    net_host: str = ""
    net_port: int = 7777

    kv_set: int = 0
    ma_set: float = 0.0
    fil_lim_set: float = 0.0
    beam_on_requested: bool = False

    kv_read: float = 0.0
    ma_read: float = 0.0
    fil_read: float = 0.0

    hv_out: bool = False
    hv_val_reached: bool = False
    filament_fault: bool = False

    imaging_window: bool = False

    spinup_ms: int = 0
    spinup_done: bool = False
    hv_on_time_ms: int = 0

    exposure_active: bool = False
    beam_ready: bool = False

    hard_kv_lim: float = 50.0
    hard_ma_lim: float = 1.5
    hard_fil_lim: float = 3.5
    limits_known: bool = False

    last_error: str = ""


class SerialWorker:
    def __init__(self, on_line: Callable[[str], None], on_error: Callable[[str], None]):
        self._on_line = on_line
        self._on_error = on_error

        self._ser: Optional[serial.Serial] = None
        self._rx_thread: Optional[threading.Thread] = None
        self._tx_thread: Optional[threading.Thread] = None

        self._stop = threading.Event()
        self._tx_lock = threading.Lock()
        self._tx_queue = []
        self._tx_event = threading.Event()

    def connect(self, port: str, baud: int) -> None:
        self.disconnect()
        self._stop.clear()
        self._ser = serial.Serial(port, baudrate=baud, timeout=1)
        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self._tx_thread = threading.Thread(target=self._tx_loop, daemon=True)
        self._rx_thread.start()
        self._tx_thread.start()

    def disconnect(self) -> None:
        self._stop.set()
        self._tx_event.set()
        if self._ser:
            try:
                self._ser.close()
            except Exception:
                pass
        self._ser = None

    def send_line(self, line: str) -> None:
        with self._tx_lock:
            self._tx_queue.append(line)
        self._tx_event.set()

    def is_connected(self) -> bool:
        return self._ser is not None and self._ser.is_open

    def _rx_loop(self) -> None:
        while not self._stop.is_set():
            try:
                if not self._ser:
                    time.sleep(0.05)
                    continue
                raw = self._ser.readline()
                if not raw:
                    continue
                line = raw.decode(errors="ignore").strip()
                if line:
                    self._on_line(line)
            except Exception as e:
                self._on_error(f"Serial RX error: {e}")
                time.sleep(0.2)

    def _tx_loop(self) -> None:
        while not self._stop.is_set():
            self._tx_event.wait(0.5)
            self._tx_event.clear()

            while True:
                with self._tx_lock:
                    if not self._tx_queue:
                        break
                    line = self._tx_queue.pop(0)

                try:
                    if self._ser and self._ser.is_open:
                        self._ser.write((line + "\n").encode())
                except Exception as e:
                    self._on_error(f"Serial TX error: {e}")
                    time.sleep(0.2)


class TcpWorker:
    def __init__(self, on_line: Callable[[str], None], on_error: Callable[[str], None]):
        self._on_line = on_line
        self._on_error = on_error

        self._sock = None
        self._rx_thread: Optional[threading.Thread] = None
        self._tx_thread: Optional[threading.Thread] = None

        self._stop = threading.Event()
        self._tx_lock = threading.Lock()
        self._tx_queue = []
        self._tx_event = threading.Event()

    def connect(self, host: str, port: int) -> None:
        import socket
        self.disconnect()
        self._stop.clear()

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(5.0)
        s.connect((host, int(port)))
        s.settimeout(None)
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._sock = s

        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self._tx_thread = threading.Thread(target=self._tx_loop, daemon=True)
        self._rx_thread.start()
        self._tx_thread.start()

    def disconnect(self) -> None:
        self._stop.set()
        self._tx_event.set()
        if self._sock:
            try:
                self._sock.shutdown(2)
            except Exception:
                pass
            try:
                self._sock.close()
            except Exception:
                pass
        self._sock = None

    def send_line(self, line: str) -> None:
        with self._tx_lock:
            self._tx_queue.append(line)
        self._tx_event.set()

    def is_connected(self) -> bool:
        return self._sock is not None

    def _rx_loop(self) -> None:
        buf = b""
        while not self._stop.is_set():
            try:
                if not self._sock:
                    time.sleep(0.05)
                    continue
                data = self._sock.recv(4096)
                if not data:
                    self._on_error("TCP disconnected")
                    self.disconnect()
                    return
                buf += data
                while b"\n" in buf:
                    raw, buf = buf.split(b"\n", 1)
                    line = raw.decode(errors="ignore").strip()
                    if line:
                        self._on_line(line)
            except Exception as e:
                self._on_error(f"TCP RX error: {e}")
                time.sleep(0.2)

    def _tx_loop(self) -> None:
        while not self._stop.is_set():
            self._tx_event.wait(0.5)
            self._tx_event.clear()

            while True:
                with self._tx_lock:
                    if not self._tx_queue:
                        break
                    line = self._tx_queue.pop(0)

                try:
                    if not self._sock:
                        break
                    payload = (line.strip() + "\n").encode()
                    self._sock.sendall(payload)
                except Exception as e:
                    self._on_error(f"TCP TX error: {e}")
                    self.disconnect()
                    break


class PSUCore:
    def __init__(self, publish_event: Callable[[Dict[str, Any]], None]):
        self._publish_event = publish_event
        self._lock = threading.Lock()
        self.state = PSUState()

        self._serial = SerialWorker(self._handle_line, self._handle_error)
        self._tcp = TcpWorker(self._handle_line, self._handle_error)
        self._exposure_lock = threading.Lock()

        self._ma_close_count = 0
        self._beam_ready_published = False

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            return asdict(self.state)

    def get_imaging_window(self) -> bool:
        with self._lock:
            return bool(self.state.imaging_window)

    def _publish(self, msg: Dict[str, Any]) -> None:
        try:
            self._publish_event(msg)
        except Exception:
            pass

    def _is_connected(self) -> bool:
        return self._serial.is_connected() or self._tcp.is_connected()

    def _send_line(self, line: str) -> None:
        if self._serial.is_connected():
            self._serial.send_line(line)
        elif self._tcp.is_connected():
            self._tcp.send_line(line)

    def _set_imaging_window(self, value: bool, reason: str = "") -> None:
        value = bool(value)
        changed = False
        with self._lock:
            if self.state.imaging_window != value:
                self.state.imaging_window = value
                changed = True

        if changed:
            self._publish({"type": "imaging_window", "active": value, "reason": reason})

    @staticmethod
    def _clamp_float(v: float, lo: float, hi: float) -> float:
        return float(max(lo, min(hi, float(v))))

    @staticmethod
    def _clamp_int(v: int, lo: int, hi: int) -> int:
        return int(max(lo, min(hi, int(v))))

    def _current_limits(self) -> Dict[str, float]:
        with self._lock:
            return {
                "kv": float(self.state.hard_kv_lim) if self.state.hard_kv_lim > 0 else 0.0,
                "ma": float(self.state.hard_ma_lim) if self.state.hard_ma_lim > 0 else 0.0,
                "fil": float(self.state.hard_fil_lim) if self.state.hard_fil_lim > 0 else 0.0,
            }

    def _request_limits(self) -> None:
        if not self._is_connected():
            return
        self._send_line("get:Limits")
        self._send_line("get:HardKVLim")
        self._send_line("get:HardmALim")
        self._send_line("get:HardFilLim")

    def _apply_limits(self, kv: Optional[float] = None, ma: Optional[float] = None, fil: Optional[float] = None, source: str = "") -> None:
        changed = False

        with self._lock:
            if kv is not None:
                kvv = self._clamp_float(kv, 0.0, 50.0)
                if abs(self.state.hard_kv_lim - kvv) > 1e-6:
                    self.state.hard_kv_lim = kvv
                    changed = True

            if ma is not None:
                mav = self._clamp_float(ma, 0.0, 1.5)
                if abs(self.state.hard_ma_lim - mav) > 1e-6:
                    self.state.hard_ma_lim = mav
                    changed = True

            if fil is not None:
                filv = self._clamp_float(fil, 0.0, 3.5)
                if abs(self.state.hard_fil_lim - filv) > 1e-6:
                    self.state.hard_fil_lim = filv
                    changed = True

            if kv is not None or ma is not None or fil is not None:
                self.state.limits_known = True

        if changed:
            self._enforce_setpoints_against_limits()
            lims = self._current_limits()
            self._publish({
                "type": "limits",
                "hard_kv_lim": lims["kv"],
                "hard_ma_lim": lims["ma"],
                "hard_fil_lim": lims["fil"],
                "source": source,
            })

    def _enforce_setpoints_against_limits(self) -> None:
        lims = self._current_limits()
        needs_send = {"kv": False, "ma": False, "fil": False}

        with self._lock:
            kv_old = int(self.state.kv_set)
            ma_old = float(self.state.ma_set)
            fil_old = float(self.state.fil_lim_set)

            kv_new = self._clamp_int(kv_old, 0, int(round(lims["kv"])))
            ma_new = self._clamp_float(ma_old, 0.0, lims["ma"])
            fil_new = self._clamp_float(fil_old, 0.0, lims["fil"])

            if kv_new != kv_old:
                self.state.kv_set = kv_new
                needs_send["kv"] = True
            if abs(ma_new - ma_old) > 1e-6:
                self.state.ma_set = ma_new
                needs_send["ma"] = True
            if abs(fil_new - fil_old) > 1e-6:
                self.state.fil_lim_set = fil_new
                needs_send["fil"] = True

        if self._is_connected():
            if needs_send["kv"]:
                self._send_line(f"kVOut:{int(self.state.kv_set)}")
            if needs_send["ma"]:
                self._send_line(f"mAOut:{float(self.state.ma_set):.2f}")
            if needs_send["fil"]:
                self._send_line(f"filLim:{float(self.state.fil_lim_set):.2f}")

    def _safe_shutdown(self, reason: str) -> None:
        if self._is_connected():
            self._send_line("BeamOn:0")
            self._send_line("mAOut:0.00")
            self._send_line("kVOut:0")

        with self._lock:
            self.state.beam_on_requested = False
            self.state.exposure_active = False
            self.state.beam_ready = False
            self.state.hv_val_reached = False
            self.state.spinup_done = False
            self._ma_close_count = 0
            self._beam_ready_published = False

            if reason:
                self.state.last_error = reason

        self._set_imaging_window(False, reason="safe_shutdown")
        self._publish({"type": "safety", "action": "safe_shutdown", "reason": reason})

    def connect_serial(self, port: str, baud: int = 9600) -> Dict[str, Any]:
        with self._lock:
            self.state.last_error = ""
            self.state.port = port
            self.state.baud = int(baud)

        try:
            self._tcp.disconnect()
        except Exception:
            pass

        try:
            self._serial.connect(port, int(baud))
        except Exception as e:
            with self._lock:
                self.state.connected = False
                self.state.connection_type = "none"
                self.state.last_error = str(e)
            return {"ok": False, "error": str(e)}

        with self._lock:
            self.state.connected = True
            self.state.connection_type = "serial"
            self.state.net_host = ""
            self.state.beam_on_requested = False
            self.state.exposure_active = False
            self.state.beam_ready = False
            self.state.hv_val_reached = False
            self.state.filament_fault = False
            self.state.imaging_window = False
            self.state.spinup_done = False
            self.state.spinup_ms = 0
            self.state.hv_on_time_ms = 0
            self._ma_close_count = 0
            self._beam_ready_published = False
            self.state.hard_kv_lim = 50.0
            self.state.hard_ma_lim = 1.5
            self.state.hard_fil_lim = 3.5
            self.state.limits_known = False

        self._send_line("BeamOn:0")
        self._send_line("mAOut:0.00")
        self._send_line("kVOut:0")
        self._request_limits()

        self._publish({"type": "serial", "connected": True, "port": port, "baud": int(baud)})
        self._publish({"type": "imaging_window", "active": False, "reason": "connect"})
        lims = self._current_limits()
        self._publish({"type": "limits", "hard_kv_lim": lims["kv"], "hard_ma_lim": lims["ma"], "hard_fil_lim": lims["fil"], "source": "defaults"})
        return {"ok": True}

    def disconnect_serial(self) -> Dict[str, Any]:
        self._safe_shutdown("Disconnected")
        try:
            self._serial.disconnect()
        except Exception:
            pass

        with self._lock:
            self.state.connected = False
            self.state.hv_out = False

        self._publish({"type": "serial", "connected": False})
        return {"ok": True}

    def connect_network(self, host: str, port: int = 7777) -> Dict[str, Any]:
        host = str(host).strip()
        port = int(port)

        with self._lock:
            self.state.last_error = ""
            self.state.net_host = host
            self.state.net_port = port

        try:
            self._serial.disconnect()
        except Exception:
            pass

        try:
            self._tcp.connect(host, port)
        except Exception as e:
            with self._lock:
                self.state.connected = False
                self.state.connection_type = "none"
                self.state.last_error = str(e)
            return {"ok": False, "error": str(e)}

        with self._lock:
            self.state.connected = True
            self.state.connection_type = "network"
            self.state.port = ""
            self.state.baud = 9600
            self.state.beam_on_requested = False
            self.state.exposure_active = False
            self.state.beam_ready = False
            self.state.hv_val_reached = False
            self.state.filament_fault = False
            self.state.imaging_window = False
            self.state.spinup_done = False
            self.state.spinup_ms = 0
            self.state.hv_on_time_ms = 0
            self._ma_close_count = 0
            self._beam_ready_published = False
            self.state.hard_kv_lim = 50.0
            self.state.hard_ma_lim = 1.5
            self.state.hard_fil_lim = 3.5
            self.state.limits_known = False

        self._send_line("BeamOn:0")
        self._send_line("mAOut:0.00")
        self._send_line("kVOut:0")
        self._request_limits()

        self._publish({"type": "network", "connected": True, "host": host, "port": port})
        self._publish({"type": "imaging_window", "active": False, "reason": "connect"})
        lims = self._current_limits()
        self._publish({"type": "limits", "hard_kv_lim": lims["kv"], "hard_ma_lim": lims["ma"], "hard_fil_lim": lims["fil"]})
        return {"ok": True}

    def disconnect_network(self) -> Dict[str, Any]:
        self._safe_shutdown("Disconnected")
        try:
            self._tcp.disconnect()
        except Exception:
            pass

        with self._lock:
            self.state.connected = False
            self.state.connection_type = "none"

        self._publish({"type": "network", "connected": False})
        self._publish({"type": "imaging_window", "active": False, "reason": "disconnect"})
        return {"ok": True}

    def set_kv(self, kv: int) -> Dict[str, Any]:
        lims = self._current_limits()
        kv = self._clamp_int(int(kv), 0, int(round(lims["kv"])))

        with self._lock:
            self.state.kv_set = kv

        if self._is_connected():
            self._send_line(f"kVOut:{kv}")

        self._publish({"type": "set_kv", "kv": kv})
        return {"ok": True}

    def set_fil_lim(self, fil: float) -> Dict[str, Any]:
        lims = self._current_limits()
        fil = self._clamp_float(float(fil), 0.0, lims["fil"])

        with self._lock:
            self.state.fil_lim_set = fil

        if self._is_connected():
            self._send_line(f"filLim:{fil:.2f}")

        self._publish({"type": "set_fil_lim", "fil": fil})
        return {"ok": True}

    def set_ma(self, ma: float) -> Dict[str, Any]:
        lims = self._current_limits()
        ma = self._clamp_float(float(ma), 0.0, lims["ma"])

        with self._lock:
            self.state.ma_set = ma

        if self._is_connected():
            self._send_line(f"mAOut:{ma:.2f}")

        self._publish({"type": "set_ma", "ma": ma})
        return {"ok": True}

    def set_beam_on(self, on: bool) -> Dict[str, Any]:
        on = bool(on)

        with self._exposure_lock:
            if not self._is_connected():
                return {"ok": False, "error": "Not connected"}

            if on:
                with self._lock:
                    if self.state.filament_fault:
                        return {"ok": False, "error": "Filament fault latched. Clear via interlock cycle."}
                    if self.state.exposure_active:
                        return {"ok": False, "error": "Exposure already active"}
                    self.state.exposure_active = True
                    self.state.beam_ready = False
                    self.state.hv_val_reached = False
                    self.state.spinup_done = False
                    self.state.spinup_ms = 0
                    self.state.hv_on_time_ms = 0
                    self.state.last_error = ""
                    self._ma_close_count = 0
                    self._beam_ready_published = False
                    self.state.beam_on_requested = True
                self._send_line("BeamOn:1")
                self._publish({"type": "set_beam_on", "on": True})
                self._publish({"type": "exposure", "phase": "requested"})
                return {"ok": True}

            with self._lock:
                self.state.beam_on_requested = False
                self.state.beam_ready = False
                self.state.exposure_active = False
                self.state.hv_val_reached = False
                self._ma_close_count = 0
                self._beam_ready_published = False

            self._send_line("BeamOn:0")
            self._publish({"type": "set_beam_on", "on": False})
            self._publish({"type": "exposure", "phase": "stop_requested"})
            return {"ok": True}

    def estop(self) -> Dict[str, Any]:
        self._safe_shutdown("EStop")
        self._publish({"type": "estop"})
        return {"ok": True}

    def do_exposure(self) -> Dict[str, Any]:
        if not self._is_connected():
            return {"ok": False, "error": "Not connected"}

        with self._exposure_lock:
            with self._lock:
                if self.state.filament_fault:
                    return {"ok": False, "error": "Filament fault latched. Clear via interlock cycle."}
                if self.state.exposure_active:
                    return {"ok": False, "error": "Exposure already active"}

                self.state.exposure_active = True
                self.state.beam_ready = False
                self.state.hv_val_reached = False
                self.state.spinup_done = False
                self.state.spinup_ms = 0
                self.state.hv_on_time_ms = 0
                self.state.last_error = ""
                self._ma_close_count = 0
                self._beam_ready_published = False

            self._send_line("BeamOn:1")
            with self._lock:
                self.state.beam_on_requested = True

            self._publish({"type": "exposure", "phase": "requested"})
            return {"ok": True}

    def exposure_done(self) -> Dict[str, Any]:
        if not self._is_connected():
            return {"ok": True}

        with self._exposure_lock:
            with self._lock:
                was_active = self.state.exposure_active
                self.state.beam_ready = False
                self.state.exposure_active = False
                self.state.hv_val_reached = False
                self.state.beam_on_requested = False
                self._ma_close_count = 0
                self._beam_ready_published = False

            self._send_line("BeamOn:0")
            self._publish({"type": "exposure", "phase": "stop_requested" if was_active else "stop_requested_while_idle"})
            return {"ok": True}

    def _handle_error(self, msg: str) -> None:
        with self._lock:
            self.state.last_error = msg
            exposure_active = self.state.exposure_active

        self._publish({"type": "error", "message": msg})

        if exposure_active:
            self._safe_shutdown(msg)

    def _handle_line(self, line: str) -> None:
        self._publish({"type": "serial_line", "line": line})

        if line.startswith("Status:ReadStats:"):
            self._parse_readstats(line)
            return
        if line.startswith("Status:Spinup:"):
            self._parse_spinup(line)
            return
        if line.startswith("Status:HVOut:"):
            self._parse_hvout(line)
            return
        if line.startswith("Status:HVOnTime:"):
            self._parse_hvontime(line)
            return
        if line.startswith("Status:HVValreached:"):
            self._parse_hvvalreached(line)
            return

        if line.startswith("Limits:"):
            self._parse_limits_summary(line)
            return
        if line.startswith("HardKVLim:"):
            self._parse_single_limit(line, key="kv")
            return
        if line.startswith("HardmALim:"):
            self._parse_single_limit(line, key="ma")
            return
        if line.startswith("HardFilLim:"):
            self._parse_single_limit(line, key="fil")
            return

    def _parse_limits_summary(self, line: str) -> None:
        kv = None
        ma = None
        fil = None

        try:
            payload = line.replace("Limits:", "").strip()
            parts = [p.strip() for p in payload.split(":") if p.strip()]
            for p in parts:
                if p.lower().startswith("hardkvlim/"):
                    kv = float(p.split("/", 1)[1])
                elif p.lower().startswith("hardmalim/"):
                    ma = float(p.split("/", 1)[1])
                elif p.lower().startswith("hardfillim/"):
                    fil = float(p.split("/", 1)[1])
        except Exception:
            return

        self._apply_limits(kv=kv, ma=ma, fil=fil, source="get:Limits")

    def _parse_single_limit(self, line: str, key: str) -> None:
        try:
            _, val = line.split(":", 1)
            v = float(val.strip())
        except Exception:
            return

        if key == "kv":
            self._apply_limits(kv=v, source="get:HardKVLim")
        elif key == "ma":
            self._apply_limits(ma=v, source="get:HardmALim")
        elif key == "fil":
            self._apply_limits(fil=v, source="get:HardFilLim")

    def _parse_readstats(self, line: str) -> None:
        try:
            parts = line.split(":")
            if len(parts) < 6:
                return

            kv_part = parts[2]
            ma_part = parts[3]
            fil_part = parts[4]

            kv_split = kv_part.split("/")
            ma_split = ma_part.split("/")
            fil_split = fil_part.split("/")

            kv_read = float(kv_split[1]) if len(kv_split) > 1 else 0.0
            ma_read = float(ma_split[1]) if len(ma_split) > 1 else 0.0
            fil_read = float(fil_split[1]) if len(fil_split) > 1 else 0.0

            with self._lock:
                self.state.kv_read = kv_read
                self.state.ma_read = ma_read
                self.state.fil_read = fil_read

            self._publish({"type": "stats", "kv_read": kv_read, "ma_read": ma_read, "fil_read": fil_read})
            self._extract_limits_from_readstats(line)
            self._check_beam_ready_from_readbacks()
        except Exception:
            return

    def _extract_limits_from_readstats(self, line: str) -> None:
        kv = None
        ma = None
        fil = None

        try:
            m = re.search(r"HardKVLim/([0-9]+(?:\.[0-9]+)?)", line, flags=re.IGNORECASE)
            if m:
                kv = float(m.group(1))

            m = re.search(r"HardmALim/([0-9]+(?:\.[0-9]+)?)", line, flags=re.IGNORECASE)
            if m:
                ma = float(m.group(1))

            m = re.search(r"HardFilLim/([0-9]+(?:\.[0-9]+)?)", line, flags=re.IGNORECASE)
            if m:
                fil = float(m.group(1))
        except Exception:
            return

        if kv is not None or ma is not None or fil is not None:
            self._apply_limits(kv=kv, ma=ma, fil=fil, source="ReadStats")

    def _parse_spinup(self, line: str) -> None:
        payload = line.replace("Status:Spinup:", "").strip()

        with self._lock:
            if payload.lower() == "done":
                self.state.spinup_done = True
            else:
                try:
                    self.state.spinup_ms = int(payload)
                except Exception:
                    pass

        self._publish({"type": "spinup", "value": payload})

    def _parse_hvvalreached(self, line: str) -> None:
        payload = line.replace("Status:HVValreached:", "").strip().lower()
        if payload == "true":
            with self._lock:
                self.state.hv_val_reached = True
            self._publish({"type": "hvvalreached", "value": True})
            self._set_imaging_window(True, reason="HVValreached:true")
            self._check_beam_ready_from_readbacks()
        elif payload == "false":
            with self._lock:
                self.state.hv_val_reached = False
            self._publish({"type": "hvvalreached", "value": False})

    def _parse_hvout(self, line: str) -> None:
        payload = line.replace("Status:HVOut:", "").strip()
        p = payload.lower()

        overtime = False
        filament_trip = False

        if p == "true":
            hv = True
        elif p == "false":
            hv = False
        elif p == "overtimelimit":
            hv = False
            overtime = True
        elif p == "filamentovertcurrent" or p == "filamentovercurrent":
            hv = False
            filament_trip = True
        else:
            return

        with self._lock:
            self.state.hv_out = hv

        self._publish({"type": "hvout", "hv_out": hv, "overtime": overtime, "filament_trip": filament_trip})

        if filament_trip:
            with self._lock:
                self.state.filament_fault = True
                self.state.last_error = "Filament over current trip (firmware)"
                self.state.beam_ready = False
                self.state.exposure_active = False
                self.state.beam_on_requested = False
                self.state.hv_val_reached = False
                self._ma_close_count = 0
                self._beam_ready_published = False

            self._set_imaging_window(False, reason="filamentOverCurrent")
            if self._is_connected():
                self._send_line("BeamOn:0")
            self._publish({"type": "fault", "name": "filamentOverCurrent"})
            return

        if overtime:
            with self._lock:
                self.state.last_error = "HV overtime limit (firmware)"
                self.state.beam_ready = False
                self.state.exposure_active = False
                self.state.beam_on_requested = False
                self.state.hv_val_reached = False
                self._ma_close_count = 0
                self._beam_ready_published = False

            self._set_imaging_window(False, reason="overTimeLimit")
            if self._is_connected():
                self._send_line("BeamOn:0")
            self._publish({"type": "fault", "name": "overTimeLimit"})
            return

        if not hv:
            self._set_imaging_window(False, reason="HVOut:false")

        with self._lock:
            exposure_active = self.state.exposure_active
        if exposure_active and not hv:
            with self._lock:
                self.state.beam_ready = False
                self.state.exposure_active = False
                self.state.beam_on_requested = False
                self.state.hv_val_reached = False
                self._ma_close_count = 0
                self._beam_ready_published = False
            self._publish({"type": "safety", "action": "hv_dropped"})
            return

        self._check_beam_ready_from_readbacks()

    def _parse_hvontime(self, line: str) -> None:
        payload = line.replace("Status:HVOnTime:", "").strip()
        try:
            ms = int(payload)
        except Exception:
            return

        with self._lock:
            self.state.hv_on_time_ms = ms

        self._publish({"type": "hvontime", "ms": ms})

    def _check_beam_ready_from_readbacks(self) -> None:
        with self._lock:
            exposure_active = self.state.exposure_active
            hv_out = self.state.hv_out
            hv_val_reached = self.state.hv_val_reached
            ma_set = float(self.state.ma_set)
            ma_read = float(self.state.ma_read)
            already_ready = self.state.beam_ready
            filament_fault = self.state.filament_fault

        if filament_fault:
            return
        if not exposure_active:
            return
        if not hv_out or not hv_val_reached:
            self._ma_close_count = 0
            return
        if ma_set <= 0.001:
            if not already_ready:
                with self._lock:
                    self.state.beam_ready = True
                if not self._beam_ready_published:
                    self._beam_ready_published = True
                    self._publish({"type": "exposure", "phase": "beam_ready"})
            return

        close_enough = ma_read >= (ma_set * 0.95)
        if close_enough:
            self._ma_close_count += 1
        else:
            self._ma_close_count = 0

        if self._ma_close_count >= 2 and not already_ready:
            with self._lock:
                self.state.beam_ready = True
            if not self._beam_ready_published:
                self._beam_ready_published = True
                self._publish({"type": "exposure", "phase": "beam_ready"})
