"""
Dark and flat field load/save and status text. All functions take the GUI instance (gui).
Used by gui.py; gui keeps _get_camera_gain and delegates to these functions.
"""

import numpy as np
import shutil
import pathlib
import dearpygui.dearpygui as dpg

from ui.constants import (
    DARK_FLAT_MATCH_THRESHOLD,
    LAST_CAPTURED_DARK_NAME,
    LAST_CAPTURED_FLAT_NAME,
    dark_dir,
    flat_dir,
    dark_path,
    flat_path,
    find_nearest_dark,
    find_nearest_flat,
)


def _load_array_from_path(path: str) -> np.ndarray:
    """Load a 2D float32 array from .npy or .tif. Squeezes to 2D. Raises on error."""
    path = pathlib.Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Not a file: {path}")
    suf = path.suffix.lower()
    if suf == ".npy":
        arr = np.load(path)
    elif suf in (".tif", ".tiff"):
        try:
            import tifffile
            arr = tifffile.imread(path)
        except Exception as e:
            raise RuntimeError(f"TIFF read failed: {e}") from e
    else:
        raise ValueError(f"Unsupported extension: {suf} (use .npy or .tif)")
    arr = np.squeeze(arr)
    if arr.ndim != 2:
        raise ValueError(f"Expected 2D array, got shape {arr.shape}")
    return arr.astype(np.float32)


def load_dark_field_from_path(gui, path: str) -> bool:
    """Load dark field from a file path (.npy or .tif). Shape must match frame size. Returns True on success."""
    w, h = getattr(gui, "frame_width", 0), getattr(gui, "frame_height", 0)
    try:
        loaded = _load_array_from_path(path)
    except Exception as e:
        gui._status_msg = f"Dark load failed: {e}"
        return False
    if w > 0 and h > 0 and (loaded.shape[0] != h or loaded.shape[1] != w):
        gui._status_msg = f"Dark shape {loaded.shape[1]}×{loaded.shape[0]} does not match frame {w}×{h}"
        return False
    gui.dark_field = loaded
    gui._dark_loaded_time_gain = None  # manual load
    gui._dark_nearest_time_gain = None
    gui._status_msg = f"Dark loaded from {pathlib.Path(path).name}"
    if dpg.does_item_exist("dark_status"):
        dpg.set_value("dark_status", dark_status_text(gui))
    gui._update_alteration_dark_flat_status()
    return True


def load_flat_field_from_path(gui, path: str) -> bool:
    """Load flat field from a file path (.npy or .tif). Shape must match frame size. Returns True on success."""
    w, h = getattr(gui, "frame_width", 0), getattr(gui, "frame_height", 0)
    try:
        loaded = _load_array_from_path(path)
    except Exception as e:
        gui._status_msg = f"Flat load failed: {e}"
        return False
    if w > 0 and h > 0 and (loaded.shape[0] != h or loaded.shape[1] != w):
        gui._status_msg = f"Flat shape {loaded.shape[1]}×{loaded.shape[0]} does not match frame {w}×{h}"
        return False
    gui.flat_field = loaded
    gui._flat_loaded_time_gain = None  # manual load
    gui._flat_nearest_time_gain = None
    gui._status_msg = f"Flat loaded from {pathlib.Path(path).name}"
    if dpg.does_item_exist("flat_status"):
        dpg.set_value("flat_status", flat_status_text(gui))
    gui._update_alteration_dark_flat_status()
    return True


def load_dark_field(gui):
    """Load nearest master dark for current integration time, gain and resolution (within threshold)."""
    gain = gui._get_camera_gain()
    cam = gui.camera_module_name
    w, h = getattr(gui, "frame_width", 0), getattr(gui, "frame_height", 0)
    path, dist, tg = find_nearest_dark(cam, gui.integration_time, gain, w, h)
    gui._dark_loaded_time_gain = None
    gui._dark_nearest_time_gain = None
    if path is not None and dist <= DARK_FLAT_MATCH_THRESHOLD:
        try:
            loaded = np.load(path).astype(np.float32)
            if w > 0 and h > 0 and (loaded.shape[0] != h or loaded.shape[1] != w):
                gui.dark_field = None
            else:
                gui.dark_field = loaded
                gui._dark_loaded_time_gain = tg
        except Exception:
            gui.dark_field = None
    else:
        gui.dark_field = None
        if tg is not None:
            gui._dark_nearest_time_gain = tg
            gui._status_msg = f"No dark within range (nearest {tg[0]}s @ {tg[1]})"


def save_dark_field(gui):
    """Save master dark for current integration time, gain and resolution in camera subfolder."""
    gain = gui._get_camera_gain()
    cam = gui.camera_module_name
    height, width = gui.dark_field.shape[0], gui.dark_field.shape[1]
    base = dark_dir(cam)
    base.mkdir(parents=True, exist_ok=True)
    path = dark_path(gui.integration_time, gain, width, height, cam)
    np.save(path, gui.dark_field)
    shutil.copy2(path, base / LAST_CAPTURED_DARK_NAME)
    arr = gui.dark_field.astype(np.float32)
    try:
        import tifffile
        tifffile.imwrite(path.with_suffix(".tif"), arr, photometric="minisblack", compression=None)
        shutil.copy2(path.with_suffix(".tif"), base / "last_captured_dark.tif")
    except Exception:
        pass
    gui._dark_loaded_time_gain = (gui.integration_time, gain)
    gui._dark_nearest_time_gain = None


def load_flat_field(gui):
    """Load nearest master flat for current integration time, gain and resolution (within threshold)."""
    gain = gui._get_camera_gain()
    cam = gui.camera_module_name
    w, h = getattr(gui, "frame_width", 0), getattr(gui, "frame_height", 0)
    path, dist, tg = find_nearest_flat(cam, gui.integration_time, gain, w, h)
    gui._flat_loaded_time_gain = None
    gui._flat_nearest_time_gain = None
    if path is not None and dist <= DARK_FLAT_MATCH_THRESHOLD:
        try:
            loaded = np.load(path).astype(np.float32)
            if w > 0 and h > 0 and (loaded.shape[0] != h or loaded.shape[1] != w):
                gui.flat_field = None
            else:
                gui.flat_field = loaded
                gui._flat_loaded_time_gain = tg
        except Exception:
            gui.flat_field = None
    else:
        gui.flat_field = None
        if tg is not None:
            gui._flat_nearest_time_gain = tg
            gui._status_msg = f"No flat within range (nearest {tg[0]}s @ {tg[1]})"


def save_flat_field(gui):
    """Save master flat for current integration time, gain and resolution in camera subfolder."""
    gain = gui._get_camera_gain()
    cam = gui.camera_module_name
    height, width = gui.flat_field.shape[0], gui.flat_field.shape[1]
    base = flat_dir(cam)
    base.mkdir(parents=True, exist_ok=True)
    path = flat_path(gui.integration_time, gain, width, height, cam)
    np.save(path, gui.flat_field)
    shutil.copy2(path, base / LAST_CAPTURED_FLAT_NAME)
    arr = gui.flat_field.astype(np.float32)
    try:
        import tifffile
        tifffile.imwrite(path.with_suffix(".tif"), arr, photometric="minisblack", compression=None)
        shutil.copy2(path.with_suffix(".tif"), base / "last_captured_flat.tif")
    except Exception:
        pass
    gui._flat_loaded_time_gain = (gui.integration_time, gain)
    gui._flat_nearest_time_gain = None


def on_dark_flat_params_changed(gui):
    """Call when integration time or gain changes so dark/flat nearest-match and status are refreshed."""
    load_dark_field(gui)
    load_flat_field(gui)
    if dpg.does_item_exist("dark_status"):
        dpg.set_value("dark_status", dark_status_text(gui))
    if dpg.does_item_exist("flat_status"):
        dpg.set_value("flat_status", flat_status_text(gui))
    gui._update_alteration_dark_flat_status()


def dark_status_text(gui):
    """Short status for dark: Loaded (Xs @ Y), Loaded (manual), None (nearest too far), or None."""
    if gui.dark_field is not None:
        if gui._dark_loaded_time_gain:
            t, g = gui._dark_loaded_time_gain
            return f"Dark ({t}s @ {g}): Loaded"
        return "Dark: Loaded (manual)"
    if gui._dark_nearest_time_gain:
        t, g = gui._dark_nearest_time_gain
        return f"Dark: None (nearest {t}s @ {g} too far)"
    return "Dark: None"


def flat_status_text(gui):
    """Short status for flat: Loaded (Xs @ Y), Loaded (manual), None (nearest too far), or None."""
    if gui.flat_field is not None:
        if gui._flat_loaded_time_gain:
            t, g = gui._flat_loaded_time_gain
            return f"Flat ({t}s @ {g}): Loaded"
        return "Flat: Loaded (manual)"
    if gui._flat_nearest_time_gain:
        t, g = gui._flat_nearest_time_gain
        return f"Flat: None (nearest {t}s @ {g} too far)"
    return "Flat: None"
