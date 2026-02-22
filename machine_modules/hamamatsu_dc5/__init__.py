"""
Hamamatsu C9730DK-11 (DC5) / C9732 (DC12) camera module.
USB connection via lib/hamamatsu_dc5; integration time 30 ms–10 s, 14-bit sensor.
"""

from typing import Optional
import threading
import numpy as np
import dearpygui.dearpygui as dpg

MODULE_INFO = {
    "display_name": "Hamamatsu C9730DK-11 / C9732",
    "description": "C9730DK-11 or C9732DK/C9732DT USB. 30 ms–10 s exposure (try >2 s to test). Applies on next startup.",
    "type": "camera",
    "default_enabled": False,
    "camera_priority": 5,
    "sensor_bit_depth": 14,
}

# Default frame size when driver not loaded (DC5 resolution)
_DEFAULT_FRAME_W = 1032
_DEFAULT_FRAME_H = 1032

_driver_error: Optional[str] = None
try:
    from lib.hamamatsu_dc5 import open_device, HamamatsuDC5
    _driver_error = None
except Exception as e:
    open_device = None
    HamamatsuDC5 = None
    _driver_error = f"{type(e).__name__}: {e}"


def get_setting_keys():
    return []


def get_frame_size():
    """Return (width, height). Before connect we return DC5 default."""
    return (_DEFAULT_FRAME_W, _DEFAULT_FRAME_H)


def get_acquisition_modes():
    return [
        ("Single Shot", "single"),
        ("Continuous", "continuous"),
        ("Capture N", "capture_n"),
    ]


class HamamatsuDC5Module:
    def __init__(self):
        self._cam: Optional[HamamatsuDC5] = None
        self._ctx = None
        self._dev = None

    def get_acquisition_modes(self):
        return get_acquisition_modes()

    def get_integration_choices(self):
        """Exposure options for app dropdown: 30 ms to 10 s (hardware may clamp above ~2 s)."""
        return [
            "0.03 s", "0.05 s", "0.1 s", "0.25 s", "0.5 s",
            "0.75 s", "1 s", "1.25 s", "1.5 s", "2 s",
            "3 s", "4 s", "5 s", "6 s", "7 s", "8 s", "9 s", "10 s",
        ]

    def get_current_gain(self, gui):
        return 0

    def get_sensor_bit_depth(self):
        return 14

    def is_connected(self):
        return self._cam is not None

    def build_ui(self, gui, parent_tag="control_panel"):
        with dpg.collapsing_header(parent=parent_tag, label="Connection (C9730DK-11 / C9732)", default_open=True):
            with dpg.group(indent=10):
                dpg.add_button(label="Connect", callback=self._connect_cb(gui), width=-1)
                dpg.add_text("Disconnected", tag="ham_dc5_conn_status")

    def _connect_cb(self, gui):
        api = gui.api
        def _cb(sender=None, app_data=None):
            global open_device, HamamatsuDC5, _driver_error
            if HamamatsuDC5 is None:
                try:
                    from lib.hamamatsu_dc5 import open_device as _od, HamamatsuDC5 as _H
                    open_device, HamamatsuDC5 = _od, _H
                    _driver_error = None
                except Exception as e:
                    _driver_error = f"{type(e).__name__}: {e}"
            if HamamatsuDC5 is None:
                api.set_status_message(f"DC5 driver not available — {_driver_error}")
                if dpg.does_item_exist("ham_dc5_conn_status"):
                    dpg.set_value("ham_dc5_conn_status", "Driver not available")
                return
            try:
                dev, ctx = open_device()
                self._dev = dev
                self._ctx = ctx
                exp_s = api.get_integration_time_seconds()
                exp_ms = max(30, min(10000, int(round(exp_s * 1000))))
                self._cam = HamamatsuDC5(dev, ctx, exp_ms=exp_ms, init=True)
                api.set_status_message("Connected (C9730DK-11 / C9732)")
                if dpg.does_item_exist("ham_dc5_conn_status"):
                    dpg.set_value("ham_dc5_conn_status", "Connected")
            except Exception as e:
                self._cam = None
                self._dev = None
                self._ctx = None
                err = str(e).strip()
                api.set_status_message(f"Connection failed: {err}")
                if dpg.does_item_exist("ham_dc5_conn_status"):
                    dpg.set_value("ham_dc5_conn_status", f"Failed: {err}")
        return _cb

    def disconnect(self, gui):
        if self._cam is not None:
            try:
                self._cam.abort_and_drain()
            except Exception:
                pass
        self._cam = None
        self._dev = None
        self._ctx = None
        if dpg.does_item_exist("ham_dc5_conn_status"):
            dpg.set_value("ham_dc5_conn_status", "Disconnected")

    def start_acquisition(self, gui):
        api = gui.api
        if self._cam is None:
            api.set_status_message("Not connected")
            return
        api.clear_acquisition_stop_flag()
        t = threading.Thread(target=self._run_worker, args=(gui,), daemon=True)
        api.set_acquisition_thread(t)
        t.start()

    def stop_acquisition(self, gui):
        gui.api.signal_acquisition_stop()

    def _integration_ms(self, gui):
        s = gui.api.get_integration_time_seconds()
        return max(30, min(10000, int(round(s * 1000))))

    def _trigger_and_read(self, gui):
        """Block until one frame is ready. Returns float32 (H,W) or None if stopped."""
        api = gui.api
        self._cam.set_exp(self._integration_ms(gui))
        timeout_ms = self._integration_ms(gui) + 3000
        frame = self._cam.capture_one(timeout_ms=timeout_ms)
        return frame

    def _do_single_shot(self, gui):
        api = gui.api
        api.set_progress(0.5, "Acquiring...")
        frame = self._trigger_and_read(gui)
        if frame is not None and not api.acquisition_should_stop():
            api.submit_frame(frame)
        api.set_progress(1.0)

    def _run_worker(self, gui):
        api = gui.api
        mode = api.get_acquisition_mode()
        try:
            if mode in ("single", "dual"):
                # DC5 uses single shot only; "dual" can appear from app default/saved state
                self._do_single_shot(gui)
            elif mode == "continuous":
                i = 0
                while not api.acquisition_should_stop():
                    i += 1
                    api.set_progress(0.0, f"Continuous #{i}")
                    self._do_single_shot(gui)
            elif mode == "capture_n":
                n = api.get_integration_frame_count()
                for i in range(n):
                    if api.acquisition_should_stop():
                        break
                    api.set_progress(i / max(n, 1), f"Capturing {i+1}/{n}")
                    self._do_single_shot(gui)
                api.set_progress(1.0)
        except Exception as e:
            api.set_status_message(f"Error: {e}")
        finally:
            api.set_acquisition_idle()


def build_ui(gui, parent_tag="control_panel"):
    mod = HamamatsuDC5Module()
    mod.build_ui(gui, parent_tag)
    gui.api.register_camera_module(mod)
