"""
Open image camera module.
Loads a TIFF file and submits it as a frame (no hardware). Useful to test the
camera module contract and run the display pipeline on file-based images.
"""

import threading
import numpy as np
import dearpygui.dearpygui as dpg

# Fixed size so main app texture matches (same as C7942 for consistency)
FRAME_W = 2400
FRAME_H = 2400

MODULE_INFO = {
    "display_name": "Open image",
    "description": "Imaging source: load a TIFF file as frame (test module). Overrides C7942 if both enabled. Applies on next startup.",
    "type": "detector",
    "default_enabled": True,
    "camera_priority": 10,  # Higher than C7942 when both enabled
    "sensor_bit_depth": 16,
}


def get_setting_keys():
    """This camera module does not persist extra keys."""
    return []


ACQUISITION_MODES = [
    ("Single Shot", "single"),
]


def _fmt_s(t: float) -> str:
    """Format seconds as dropdown label (e.g. '0.01 s', '10 s')."""
    return f"{int(t)} s" if t >= 1 and t == int(t) else f"{t:.2f} s"


# Human-friendly exposure steps: 10 ms to 10 s (for integration-time dropdown when this module is active)
_OPEN_IMAGE_TIMES_S = (0.01, 0.02, 0.05, 0.1, 0.25, 0.5, 0.75, 1, 1.5, 2, 2.5, 3, 4, 5, 6, 7, 8, 9, 10)
INTEGRATION_CHOICES = [_fmt_s(t) for t in _OPEN_IMAGE_TIMES_S]


def get_frame_size():
    """Return (width, height). Loaded images are resized to this if needed."""
    return (FRAME_W, FRAME_H)


def _load_tiff_as_float32(path: str) -> np.ndarray:
    """Load TIFF and return float32 (H, W). Resize to FRAME_H x FRAME_W if needed."""
    try:
        import tifffile
        arr = tifffile.imread(path)
    except Exception:
        from PIL import Image
        arr = np.array(Image.open(path))
    if arr is None or arr.size == 0:
        raise ValueError("Empty or invalid image")
    # Squeeze to 2D
    if arr.ndim == 3:
        arr = arr[:, :, 0] if arr.shape[2] >= 1 else arr.squeeze()
    if arr.ndim != 2:
        arr = arr.squeeze()
    arr = np.asarray(arr, dtype=np.float32)
    if arr.shape[0] != FRAME_H or arr.shape[1] != FRAME_W:
        try:
            from skimage.transform import resize
            arr = resize(arr, (FRAME_H, FRAME_W), order=1, preserve_range=True).astype(np.float32)
        except Exception:
            from scipy.ndimage import zoom
            zoom_h = FRAME_H / arr.shape[0]
            zoom_w = FRAME_W / arr.shape[1]
            arr = zoom(arr, (zoom_h, zoom_w), order=1)[:FRAME_H, :FRAME_W].astype(np.float32)
    return arr


class OpenImageModule:
    """Camera module that uses a loaded TIFF as the 'frame'."""

    def __init__(self):
        self._current_image = None  # float32 (H, W) or None
        self._path = None

    def get_acquisition_modes(self):
        return list(ACQUISITION_MODES)

    def get_integration_choices(self):
        """Return exposure options for the main app's integration-time dropdown (10 ms–10 s, human steps)."""
        return list(INTEGRATION_CHOICES)

    def get_current_gain(self, gui):
        """No gain; return 0 for dark/flat naming."""
        return 0

    def is_connected(self):
        return self._current_image is not None

    def get_sensor_bit_depth(self):
        """Loaded images treated as 16-bit range for display/windowing."""
        return 16

    def build_ui(self, gui, parent_tag="control_panel"):
        with dpg.collapsing_header(parent=parent_tag, label="Connection (Open image)", default_open=True):
            with dpg.group(indent=10):
                dpg.add_button(
                    label="Open TIFF...",
                    callback=self._make_open_cb(gui),
                    width=-1,
                    tag="open_image_btn",
                )
                dpg.add_text("No image loaded", tag="open_image_status", color=[150, 150, 150])

        # File dialog for opening TIFF (unique tag)
        with dpg.file_dialog(
            directory_selector=False,
            show=False,
            tag="open_image_tiff_dialog",
            callback=self._make_file_selected_cb(gui),
            width=600,
            height=400,
        ):
            dpg.add_file_extension(".tif")
            dpg.add_file_extension(".tiff")
            dpg.add_file_extension(".png")

    def _make_open_cb(self, gui):
        def _cb(sender=None, app_data=None):
            dpg.show_item("open_image_tiff_dialog")
        return _cb

    def _make_file_selected_cb(self, gui):
        api = gui.api
        def _cb(sender=None, app_data=None):
            if not app_data:
                return
            path = app_data.get("file_path_name", "")
            if isinstance(path, (list, tuple)):
                path = path[0] if path else ""
            if not path:
                return
            try:
                img = _load_tiff_as_float32(path)
                self._current_image = img
                self._path = path
                api.set_status_message(f"Loaded: {path}")
                if dpg.does_item_exist("open_image_status"):
                    name = path.split("/")[-1].split("\\")[-1]
                    dpg.set_value("open_image_status", name)
                # Ensure new file loads into live display path (not stale raw/deconvolved view).
                gui._display_mode = "live"
                gui._deconv_raw_frame = None
                gui._deconv_result = None
                gui._microcontrast_raw_frame = None
                gui._microcontrast_snapshot_token = -1
                gui._microcontrast_deconv_frame = None
                gui._microcontrast_result = None
                api.clear_frame_buffer()
                api.submit_frame(img)
            except Exception as e:
                api.set_status_message(f"Open failed: {e}")
                self._current_image = None
                self._path = None
                if dpg.does_item_exist("open_image_status"):
                    dpg.set_value("open_image_status", "Load failed")
        return _cb

    def disconnect(self, gui):
        self._current_image = None
        self._path = None
        if dpg.does_item_exist("open_image_status"):
            dpg.set_value("open_image_status", "No image loaded")

    def start_acquisition(self, gui):
        api = gui.api
        if self._current_image is None:
            api.set_status_message("Open a TIFF first")
            return
        api.clear_acquisition_stop_flag()
        def _worker():
            try:
                api.set_progress(0.5, "Sending frame...")
                api.submit_frame(self._current_image.copy())
                api.set_progress(1.0)
            finally:
                api.set_acquisition_idle()

        t = threading.Thread(target=_worker, daemon=True)
        api.set_acquisition_thread(t)
        t.start()

    def stop_acquisition(self, gui):
        gui.api.signal_acquisition_stop()


def build_ui(gui, parent_tag="control_panel"):
    mod = OpenImageModule()
    mod.build_ui(gui, parent_tag)
    gui.api.register_camera_module(mod)
