"""
Hamamatsu C7942 camera module.
Provides Connection UI and acquisition worker (trigger, read, submit frames to main app).
"""

import time
import threading
import numpy as np
import dearpygui.dearpygui as dpg

MODULE_INFO = {
    "display_name": "Hamamatsu C7942",
    "description": "Imaging source: C7942 camera + Teensy. Applies on next startup.",
    "type": "detector",
    "default_enabled": False,
    "camera_priority": 5,  # Lower than open_image when both enabled
    "sensor_bit_depth": 12,
}


def get_setting_keys():
    """This camera module does not persist extra keys via get_settings_for_save."""
    return []


# Default frame size when driver not available (must match HamamatsuTeensy when loaded)
_DEFAULT_FRAME_W = 2400
_DEFAULT_FRAME_H = 2400

# Import hardware library after MODULE_INFO so registry can discover this as a camera even if import fails
_teensy_import_error = None
try:
    from lib.hamamatsu_teensy import HamamatsuTeensy
    FRAME_W = HamamatsuTeensy.FRAME_WIDTH
    FRAME_H = HamamatsuTeensy.FRAME_HEIGHT
except Exception as e:
    HamamatsuTeensy = None
    FRAME_W = _DEFAULT_FRAME_W
    FRAME_H = _DEFAULT_FRAME_H
    _teensy_import_error = f"{type(e).__name__}: {e}"

def _fmt_s(t: float) -> str:
    """Format seconds as dropdown label (e.g. '0.5 s', '10 s')."""
    return f"{int(t)} s" if t >= 1 and t == int(t) else f"{t:.2f} s"


# Human-friendly exposure steps: 500 ms to 10 s
_HAMAMATSU_TIMES_S = (0.5, 0.75, 1, 1.25, 1.5, 2, 2.5, 3, 4, 5, 6, 7, 8, 9, 10)
INTEGRATION_CHOICES = [_fmt_s(t) for t in _HAMAMATSU_TIMES_S]
DARK_STACK_DEFAULT = 20


def get_frame_size():
    """Return (width, height) for texture/viewport. Call before building UI."""
    return (FRAME_W, FRAME_H)


def _parse_integration_time(combo_value: str) -> float:
    s = (combo_value or "1 s").replace(" s", "").strip()
    return float(s)


# Acquisition modes this sensor supports: (display label, internal mode id for gui.acq_mode)
ACQUISITION_MODES = [
    ("Single Shot", "single"),
    ("Dual Shot", "dual"),
    ("Continuous", "continuous"),
    ("Capture N", "capture_n"),
]


class HamamatsuC7942Module:
    """Holds device and runs acquisition worker; builds Connection UI."""

    def __init__(self):
        self._teensy = None
        self._acq_thread = None

    def get_acquisition_modes(self):
        """Return list of (display_label, mode_id) for the Start combo. Mode ids are used as gui.acq_mode."""
        return list(ACQUISITION_MODES)

    def get_integration_choices(self):
        """Return exposure options for the main app's integration-time dropdown (500 ms–10 s, human steps)."""
        return list(INTEGRATION_CHOICES)

    def get_current_gain(self, gui):
        """No settable gain on C7942; return 0 for dark/flat naming."""
        return 0

    def is_connected(self):
        return self._teensy is not None

    def get_sensor_bit_depth(self):
        """C7942 sensor is 12-bit. Used for display/windowing range."""
        return 12

    def uses_dual_shot_for_capture_n(self):
        """True: capture_n runs _do_dual_shot per frame (2 exposures per frame). Dark/flat timeout is doubled."""
        return True

    def build_ui(self, gui, parent_tag="control_panel"):
        with dpg.collapsing_header(parent=parent_tag, label="Connection (C7942)", default_open=True):
            with dpg.group(indent=10):
                dpg.add_button(label="Connect", callback=self._make_connect_cb(gui), width=-1)
                dpg.add_text("Disconnected", tag="c7942_conn_status")

    def _make_connect_cb(self, gui):
        api = gui.api
        def _cb(sender=None, app_data=None):
            # Use module-level name (may be updated by retry)
            import importlib; _mod = importlib.import_module(__name__)
            driver = _mod.HamamatsuTeensy
            if driver is None:
                # Retry import once (e.g. app started before venv or from wrong cwd)
                try:
                    from lib.hamamatsu_teensy import HamamatsuTeensy as _HT
                    _mod.HamamatsuTeensy = _HT
                    _mod.FRAME_W = _HT.FRAME_WIDTH
                    _mod.FRAME_H = _HT.FRAME_HEIGHT
                    driver = _HT
                except Exception as e:
                    err = f"{type(e).__name__}: {e}"
                    msg = _mod._teensy_import_error or err
                    api.set_status_message(f"C7942 driver not available — {msg}")
                    dpg.set_value("c7942_conn_status", "Driver not available")
                    return
            if driver is None:
                api.set_status_message(f"C7942 driver not available — {_mod._teensy_import_error or 'unknown error'}")
                dpg.set_value("c7942_conn_status", "Driver not available")
                return
            try:
                self._teensy = driver()
                self._teensy.ping()
                gui.teensy = self._teensy  # so Faxitron module can use it
                api.set_status_message("Connected")
                dpg.set_value("c7942_conn_status", "Connected")
            except Exception as e:
                self._teensy = None
                gui.teensy = None
                err = str(e).strip()
                if "not find" in err.lower() or "could not find" in err.lower():
                    api.set_status_message("Teensy not found. Try another USB port or reconnect. On Windows you may need the libusb driver (Zadig).")
                    dpg.set_value("c7942_conn_status", "Not found – try another USB port")
                else:
                    api.set_status_message(f"Connection failed: {err}")
                    dpg.set_value("c7942_conn_status", f"Failed: {err}")
        return _cb

    def disconnect(self, gui):
        self._teensy = None
        gui.teensy = None
        if dpg.does_item_exist("c7942_conn_status"):
            dpg.set_value("c7942_conn_status", "Disconnected")

    def start_acquisition(self, gui):
        """Start acquisition. Caller sets mode, integration time/count and dark/flat stack count via app state."""
        api = gui.api
        if self._teensy is None:
            api.set_status_message("Not connected")
            return
        api.clear_acquisition_stop_flag()
        t = threading.Thread(target=self._run_worker, args=(gui,), daemon=True)
        api.set_acquisition_thread(t)
        t.start()

    def stop_acquisition(self, gui):
        gui.api.signal_acquisition_stop()

    def _trigger_and_read(self, gui):
        """Block until frame ready. Returns float32 (H,W) or None if stopped."""
        api = gui.api
        self._teensy.start_trigger()
        for _ in range(200):
            if api.acquisition_should_stop():
                return None
            state = self._teensy.get_state()
            if state["state"] == 3:  # DONE
                break
            time.sleep(0.1)
        else:
            raise TimeoutError("Frame acquisition timed out")
        raw = self._teensy.get_frame()
        try:
            st = self._teensy.get_state()
            gui._last_capture_diag = (
                f"rows={st['row']} | row_max={st['overhead_max_us']} us | "
                f"DMA ok={st['dma_ok']} | timeout={st['dma_timeout']}"
            )
        except Exception:
            gui._last_capture_diag = "(diag unavailable)"
        pixels = HamamatsuTeensy.unpack_12bit(raw)
        return pixels.reshape((FRAME_H, FRAME_W)).astype(np.float32)

    def _do_single_shot(self, gui):
        api = gui.api
        api.set_progress(0.5, "Acquiring...")
        frame = self._trigger_and_read(gui)
        if frame is not None:
            api.submit_frame(frame)
        api.set_progress(1.0)

    def _do_dual_shot(self, gui):
        api = gui.api
        api.set_progress(0.0, "Clearing sensor...")
        frame = self._trigger_and_read(gui)
        if frame is None or api.acquisition_should_stop():
            return
        api.set_progress(0.0, "Integrating...")
        t_start = time.time()
        integ = api.get_integration_time_seconds()
        while time.time() < t_start + integ:
            if api.acquisition_should_stop():
                return
            elapsed = time.time() - t_start
            api.set_progress(min(elapsed / max(integ, 0.01), 1.0))
            time.sleep(0.05)
        api.set_progress(0.0, "Reading...")
        frame = self._trigger_and_read(gui)
        if frame is not None:
            api.submit_frame(frame)

    def _run_worker(self, gui):
        api = gui.api
        mode = api.get_acquisition_mode()
        try:
            if mode == "single":
                self._do_single_shot(gui)
            elif mode == "dual":
                self._do_dual_shot(gui)
            elif mode == "continuous":
                i = 0
                while not api.acquisition_should_stop():
                    i += 1
                    api.set_progress(0.0, f"Continuous #{i}")
                    self._do_dual_shot(gui)
            elif mode == "capture_n":
                n = api.get_integration_frame_count()
                for i in range(n):
                    if api.acquisition_should_stop():
                        break
                    api.set_progress(i / n, f"Capturing {i+1}/{n}")
                    self._do_dual_shot(gui)
                api.set_progress(1.0)
        except Exception as e:
            api.set_status_message(f"Error: {e}")
        finally:
            api.set_acquisition_idle()


def build_ui(gui, parent_tag="control_panel"):
    """Add Connection UI and register as camera module via API."""
    mod = HamamatsuC7942Module()
    mod.build_ui(gui, parent_tag)
    gui.api.register_camera_module(mod)
