"""
ASI camera capture module.
ZWO ASI camera via zwoasi: gain, exposure (10 ms–5 s), single/continuous/capture N/dark/flat.
"""

import os
import time
import threading
from typing import Optional

import numpy as np
import dearpygui.dearpygui as dpg

def _fmt_s(t: float) -> str:
    """Format seconds as dropdown label (e.g. '0.01 s', '5 s')."""
    return f"{int(t)} s" if t >= 1 and t == int(t) else f"{t:.2f} s"


# Human-friendly exposure steps: 10 ms to 5 s
_ASI_TIMES_S = (0.01, 0.02, 0.05, 0.1, 0.15, 0.2, 0.25, 0.5, 0.75, 1, 1.25, 1.5, 2, 2.5, 3, 4, 5)
ASI_INTEGRATION_CHOICES = [_fmt_s(t) for t in _ASI_TIMES_S]

DEFAULT_FRAME_W = 1920
DEFAULT_FRAME_H = 1080
DARK_STACK_DEFAULT = 20
# ASI SDK requires width % 8 == 0, height % 2 == 0. Enforce a larger minimum so tiny ROIs (e.g. 8×2) don't break acquisition.
ASI_ROI_MIN_W = 64
ASI_ROI_MIN_H = 64

MODULE_INFO = {
    "display_name": "ASI camera capture",
    "description": "Imaging source: ZWO ASI camera (zwoasi). Gain and exposure 10 ms–5 s. Applies on next startup.",
    "type": "detector",
    "default_enabled": False,
    "camera_priority": 8,
    "sensor_bit_depth": 16,
}


def get_setting_keys():
    """Keys this module persists (gain, ROI)."""
    return ["asi_gain", "asi_x_start", "asi_x_end", "asi_y_start", "asi_y_end"]


# Spec for api.get_module_settings_for_save: (key, tag, converter, default)
_ASI_SAVE_SPEC = [
    ("asi_gain", "asi_gain_slider", int, 50),
    ("asi_x_start", "asi_x_start", int, 0),
    ("asi_x_end", "asi_x_end", int, 0),
    ("asi_y_start", "asi_y_start", int, 0),
    ("asi_y_end", "asi_y_end", int, 0),
]


def get_default_settings():
    """Return default settings for this module (extracted from save spec)."""
    return {key: default for key, _tag, _conv, default in _ASI_SAVE_SPEC}


def get_settings_for_save(gui=None):
    """Return asi_gain and ROI from UI or loaded settings (auto fallback when UI not built)."""
    if gui is None or not getattr(gui, "api", None):
        return {}
    return gui.api.get_module_settings_for_save(_ASI_SAVE_SPEC)


def get_frame_size():
    """Return (width, height). Default before connect; actual size from camera ROI when connected."""
    return (DEFAULT_FRAME_W, DEFAULT_FRAME_H)


ACQUISITION_MODES = [
    ("Single Shot", "single"),
    ("Dual Shot", "dual"),
    ("Continuous", "continuous"),
    ("Capture N", "capture_n"),
]


def _asi_camera_open(
    camera_index: int = 0,
    sdk_path: Optional[str] = None,
    start_x: Optional[int] = None,
    start_y: Optional[int] = None,
    width: Optional[int] = None,
    height: Optional[int] = None,
):
    """Open ASI camera. Returns (cam, width, height). Optional start_x, start_y, width, height set ROI (SDK: width multiple of 8, height multiple of 2)."""
    import zwoasi as asi
    from pathlib import Path
    
    # Try: explicit sdk_path -> ASI_SDK_PATH env -> resources/asi_sdk -> system path
    env_path = os.environ.get("ASI_SDK_PATH")
    if not sdk_path and not env_path:
        # Check for DLLs in resources/asi_sdk folder (relative to this module)
        _module_dir = Path(__file__).resolve().parent.parent.parent
        _resources_sdk = _module_dir / "resources" / "asi_sdk"
        if _resources_sdk.exists() and (_resources_sdk / "ASICamera2.dll").exists():
            sdk_path = str(_resources_sdk)
    
    chosen = sdk_path or env_path
    if chosen:
        asi.init(chosen)
    num = asi.get_num_cameras()
    if num <= 0:
        raise RuntimeError("No ASI cameras found")
    if camera_index < 0 or camera_index >= num:
        raise RuntimeError(f"Invalid camera index {camera_index}. Found {num} camera(s).")
    cam = asi.Camera(camera_index)
    cam.set_image_type(asi.ASI_IMG_RAW16)
    if width is not None and height is not None:
        cam.set_roi(start_x=start_x, start_y=start_y, width=width, height=height)
    else:
        cam.set_roi()
    cam.start_video_capture()
    roi = cam.get_roi()
    w, h = roi[2], roi[3]
    return cam, w, h


class ASICameraModule:
    """ASI camera module: connection UI, gain, acquisition worker using gui.integration_time and gui.submit_raw_frame."""

    def __init__(self):
        self._cam = None
        self._frame_width = DEFAULT_FRAME_W
        self._frame_height = DEFAULT_FRAME_H
        self._acq_thread = None

    def get_acquisition_modes(self):
        return list(ACQUISITION_MODES)

    def get_integration_choices(self):
        """Return exposure options for the main app's integration-time dropdown (10 ms–5 s, human steps)."""
        return list(ASI_INTEGRATION_CHOICES)

    def get_current_gain(self, gui):
        """Current gain for dark/flat naming and nearest-match. ASI has settable gain."""
        if dpg.does_item_exist("asi_gain_slider"):
            return int(dpg.get_value("asi_gain_slider"))
        return int(getattr(gui, "asi_gain", 50))

    def is_connected(self):
        return self._cam is not None

    def get_sensor_bit_depth(self):
        """ASI cameras use 16-bit raw (ASI_IMG_RAW16). Used for display/windowing range."""
        return 16

    def build_ui(self, gui, parent_tag="control_panel"):
        with dpg.collapsing_header(parent=parent_tag, label="Connection (ASI camera capture)", default_open=True):
            with dpg.group(indent=10):
                dpg.add_button(label="Connect", callback=self._make_connect_cb(gui), tag="asi_connect_btn", width=-1)
                dpg.add_button(label="Disconnect", callback=self._make_disconnect_cb(gui), tag="asi_disconnect_btn", width=-1, show=False)
                dpg.add_text("Disconnected", tag="asi_conn_status", color=[150, 150, 150])
                gain_default = max(0, min(600, int(gui.api.get_setting("asi_gain", 50))))
                def _cb_gain(s, a):
                    gui.asi_gain = int(dpg.get_value("asi_gain_slider"))
                    gui.api.trigger_dark_flat_reload()
                    gui.api.save_settings()
                dpg.add_slider_int(
                    label="Gain",
                    min_value=0, max_value=600,
                    default_value=gain_default, tag="asi_gain_slider", width=-30,
                    callback=_cb_gain
                )
                if not hasattr(gui, "asi_gain"):
                    gui.asi_gain = gain_default
                roi_x_start = int(gui.api.get_setting("asi_x_start", 0))
                roi_x_end   = int(gui.api.get_setting("asi_x_end", DEFAULT_FRAME_W))
                roi_y_start = int(gui.api.get_setting("asi_y_start", 0))
                roi_y_end   = int(gui.api.get_setting("asi_y_end", DEFAULT_FRAME_H))
                with dpg.group(tag="asi_roi_group", show=False):
                    dpg.add_text("ROI (X start, X end, Y start, Y end):", color=[180, 180, 180])
                    dpg.add_input_int(label="X start", default_value=roi_x_start, min_value=0, min_clamped=True, tag="asi_x_start", width=-1, callback=self._make_roi_cb(gui))
                    dpg.add_input_int(label="X end",   default_value=roi_x_end,   min_value=ASI_ROI_MIN_W, min_clamped=True, tag="asi_x_end",   width=-1, callback=self._make_roi_cb(gui))
                    dpg.add_input_int(label="Y start", default_value=roi_y_start, min_value=0, min_clamped=True, tag="asi_y_start", width=-1, callback=self._make_roi_cb(gui))
                    dpg.add_input_int(label="Y end",   default_value=roi_y_end,   min_value=ASI_ROI_MIN_H, min_clamped=True, tag="asi_y_end",   width=-1, callback=self._make_roi_cb(gui))

    def _apply_roi(self, gui, start_x: int, start_y: int, width: int, height: int) -> bool:
        """Apply ROI: round up to SDK alignment (width multiple of 8, height multiple of 2), enforce minimum size. Returns True on success."""
        if self._cam is None:
            return False
        max_w = getattr(self, "_asi_max_w", 0)
        max_h = getattr(self, "_asi_max_h", 0)
        # Round up so we don't drop pixels; then enforce minimum and clamp to sensor
        width = max(ASI_ROI_MIN_W, ((max(1, width) + 7) // 8) * 8)
        height = max(ASI_ROI_MIN_H, ((max(1, height) + 1) // 2) * 2)
        width = min(width, max_w)
        height = min(height, max_h)
        start_x = max(0, min(start_x, max_w - width))
        start_y = max(0, min(start_y, max_h - height))
        try:
            self._cam.stop_video_capture()
            self._cam.set_roi(start_x=start_x, start_y=start_y, width=width, height=height)
            self._cam.start_video_capture()
            self._frame_width = width
            self._frame_height = height
            gui.api.set_frame_size(width, height)
            # Don't write rounded values back to inputs – round only in the background so typing isn't interrupted
            return True
        except Exception:
            return False

    def _make_roi_cb(self, gui):
        def _cb(sender=None, app_data=None):
            if self._cam is None:
                return
            try:
                x_start = int(dpg.get_value("asi_x_start"))
                x_end   = int(dpg.get_value("asi_x_end"))
                y_start = int(dpg.get_value("asi_y_start"))
                y_end   = int(dpg.get_value("asi_y_end"))
            except (TypeError, ValueError):
                return
            width = max(0, x_end - x_start)
            height = max(0, y_end - y_start)
            if self._apply_roi(gui, x_start, y_start, width, height):
                gui.api.set_status_message(f"ROI {self._frame_width}×{self._frame_height}")
                dpg.set_value("asi_conn_status", self._asi_conn_status_text())
                gui.api.trigger_dark_flat_reload()
                gui.api.save_settings()
            else:
                gui.api.set_status_message("ROI change failed")
        return _cb

    def _asi_conn_status_text(self):
        """Connected status: current ROI and full sensor size."""
        s = f"Connected ({self._frame_width}×{self._frame_height})"
        max_w = getattr(self, "_asi_max_w", 0)
        max_h = getattr(self, "_asi_max_h", 0)
        if max_w and max_h:
            s += f" (full {max_w}×{max_h})"
        return s

    def _make_connect_cb(self, gui):
        def _cb(sender=None, app_data=None):
            try:
                cam, w, h = _asi_camera_open(camera_index=0)
                self._cam = cam
                info = cam.get_camera_property()
                max_w = int(info["MaxWidth"])
                max_h = int(info["MaxHeight"])
                max_w -= max_w % 8
                max_h -= max_h % 2
                self._asi_max_w = max_w
                self._asi_max_h = max_h
                # Restore saved ROI only if valid and at least minimum size (ignore corrupted/tiny saved ROI)
                s = gui.api.get_loaded_settings()
                x_start = max(0, min(max_w - ASI_ROI_MIN_W, int(s.get("asi_x_start", 0))))
                x_end   = max(x_start + ASI_ROI_MIN_W, min(max_w, int(s.get("asi_x_end", max_w))))
                y_start = max(0, min(max_h - ASI_ROI_MIN_H, int(s.get("asi_y_start", 0))))
                y_end   = max(y_start + ASI_ROI_MIN_H, min(max_h, int(s.get("asi_y_end", max_h))))
                roi_w = ((x_end - x_start) // 8) * 8
                roi_h = ((y_end - y_start) // 2) * 2
                if roi_w >= ASI_ROI_MIN_W and roi_h >= ASI_ROI_MIN_H and x_start + roi_w <= max_w and y_start + roi_h <= max_h:
                    self._cam.stop_video_capture()
                    self._cam.set_roi(start_x=x_start, start_y=y_start, width=roi_w, height=roi_h)
                    self._cam.start_video_capture()
                    roi = self._cam.get_roi()
                    w, h = roi[2], roi[3]
                    self._frame_width = w
                    self._frame_height = h
                    if dpg.does_item_exist("asi_x_start"):
                        dpg.set_value("asi_x_start", roi[0])
                        dpg.set_value("asi_x_end", roi[0] + w)
                        dpg.set_value("asi_y_start", roi[1])
                        dpg.set_value("asi_y_end", roi[1] + h)
                else:
                    self._frame_width = w
                    self._frame_height = h
                    if dpg.does_item_exist("asi_x_start"):
                        dpg.set_value("asi_x_start", 0)
                        dpg.set_value("asi_x_end", max_w)
                        dpg.set_value("asi_y_start", 0)
                        dpg.set_value("asi_y_end", max_h)
                gui.api.set_frame_size(self._frame_width, self._frame_height)
                if dpg.does_item_exist("asi_roi_group"):
                    dpg.configure_item("asi_roi_group", show=True)
                if dpg.does_item_exist("asi_x_start"):
                    dpg.configure_item("asi_x_start", max_value=max_w - ASI_ROI_MIN_W)
                    dpg.configure_item("asi_x_end",   min_value=ASI_ROI_MIN_W, max_value=max_w)
                    dpg.configure_item("asi_y_start", max_value=max_h - ASI_ROI_MIN_H)
                    dpg.configure_item("asi_y_end",   min_value=ASI_ROI_MIN_H, max_value=max_h)
                gui.api.set_status_message("ASI camera connected")
                dpg.set_value("asi_conn_status", self._asi_conn_status_text())
                dpg.configure_item("asi_connect_btn", show=False)
                dpg.configure_item("asi_disconnect_btn", show=True)
                gui.api.trigger_dark_flat_reload()
            except Exception as e:
                self._cam = None
                gui.api.set_status_message(f"ASI connection failed: {e}")
                dpg.set_value("asi_conn_status", f"Failed: {e}")
        return _cb

    def _make_disconnect_cb(self, gui):
        def _cb(sender=None, app_data=None):
            self.disconnect(gui)
        return _cb

    def disconnect(self, gui):
        if self._cam is not None:
            try:
                import zwoasi as asi
                try:
                    self._cam.stop_video_capture()
                except Exception:
                    pass
                try:
                    self._cam.close()
                except Exception:
                    pass
            except Exception:
                pass
            self._cam = None
        if dpg.does_item_exist("asi_conn_status"):
            dpg.set_value("asi_conn_status", "Disconnected")
        if dpg.does_item_exist("asi_connect_btn"):
            dpg.configure_item("asi_connect_btn", show=True)
        if dpg.does_item_exist("asi_disconnect_btn"):
            dpg.configure_item("asi_disconnect_btn", show=False)
        if dpg.does_item_exist("asi_roi_group"):
            dpg.configure_item("asi_roi_group", show=False)

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

    def _get_frame(self, gui):
        """One frame as float32 (H, W). Returns None if stopped or error."""
        api = gui.api
        if api.acquisition_should_stop():
            return None
        exposure_us = int(api.get_integration_time_seconds() * 1e6)
        gain = int(dpg.get_value("asi_gain_slider")) if dpg.does_item_exist("asi_gain_slider") else getattr(gui, "asi_gain", 50)
        self._cam.set_control_value(self._asi.ASI_EXPOSURE, exposure_us)
        self._cam.set_control_value(self._asi.ASI_GAIN, gain)
        if api.acquisition_should_stop():
            return None
        frame = self._cam.capture_video_frame()
        if not isinstance(frame, np.ndarray):
            frame = np.array(frame)
        if frame.dtype != np.uint16:
            frame = frame.astype(np.uint16, copy=False)
        if frame.ndim == 3:
            frame = frame[:, :, 0]
        return frame.astype(np.float32, copy=False)

    def _run_worker(self, gui):
        import zwoasi as asi
        self._asi = asi
        api = gui.api
        mode = api.get_acquisition_mode()
        try:
            if mode == "single":
                api.set_progress(0.5, "Acquiring...")
                frame = self._get_frame(gui)
                if frame is not None:
                    api.submit_frame(frame)
                api.set_progress(1.0)
            elif mode == "dual":
                api.set_progress(0.5, "Acquiring...")
                frame = self._get_frame(gui)
                if frame is not None:
                    api.submit_frame(frame)
                api.set_progress(1.0)
            elif mode == "continuous":
                i = 0
                while not api.acquisition_should_stop():
                    i += 1
                    api.set_progress(0.0, f"Continuous #{i}")
                    frame = self._get_frame(gui)
                    if frame is not None:
                        api.submit_frame(frame)
            elif mode == "capture_n":
                n = api.get_integration_frame_count()
                for i in range(n):
                    if api.acquisition_should_stop():
                        break
                    api.set_progress(i / max(n, 1), f"Capturing {i+1}/{n}")
                    frame = self._get_frame(gui)
                    if frame is not None:
                        api.submit_frame(frame)
                api.set_progress(1.0)
        except Exception as e:
            api.set_status_message(f"Error: {e}")
        finally:
            api.set_acquisition_idle()


def build_ui(gui, parent_tag="control_panel"):
    """Add Connection UI and register as camera module via API."""
    mod = ASICameraModule()
    mod.build_ui(gui, parent_tag)
    gui.api.register_camera_module(mod)
