#!/usr/bin/env python3
"""
X-ray image acquisition application.
Generic shell: image display, dark/flat, corrections, export. Imaging source and
machine hardware (Faxitron, HV supply, etc.) are loadable modules.
"""

import sys
import os
import re
import time
import threading
import pathlib
import shutil
import math
import numpy as np

import dearpygui.dearpygui as dpg

# Ensure app directory is on path for module imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from machine_modules.banding.banding_correction import (
    DEFAULT_BLACK_W,
    DEFAULT_SMOOTH_WIN,
    DEFAULT_VERTICAL_STRIPE_H,
    DEFAULT_VERTICAL_SMOOTH_WIN,
)
from lib.image_viewport import ImageViewport
from lib.settings import load_settings, save_settings, list_profiles, save_profile, apply_profile, set_current_profile
from lib.app_api import AppAPI
from machine_modules.registry import discover_modules, all_extra_settings_keys

# Default frame size when no camera module loaded; camera module can override in _build_ui
DEFAULT_FRAME_W = 2400
DEFAULT_FRAME_H = 2400
# Master darks saved in app/darks/, flats in app/flats/, bad pixel maps (TIFF review) in app/pixelmaps/
DARK_DIR = pathlib.Path(__file__).resolve().parent / "darks"
FLAT_DIR = pathlib.Path(__file__).resolve().parent / "flats"
PIXELMAPS_DIR = pathlib.Path(__file__).resolve().parent / "pixelmaps"
LAST_CAPTURED_DARK_NAME = "last_captured_dark.npy"
LAST_CAPTURED_FLAT_NAME = "last_captured_flat.npy"
INTEGRATION_CHOICES = ["0.5 s", "1 s", "2 s", "5 s", "10 s", "15 s", "20 s"]
DARK_STACK_DEFAULT = 20  # default frames for dark/flat stacking (slider 1-50)
# Max distance to auto-apply dark/flat: time_diff (s) + gain_diff/100; if nearest exceeds this, show "too far" in status
DARK_FLAT_MATCH_THRESHOLD = 1.0
HIST_MIN_12BIT = 0.0
HIST_MAX_12BIT = 4095.0

def _dark_dir(camera_name):
    """Base directory for darks for this camera (subfolder under DARK_DIR)."""
    return DARK_DIR / (camera_name or "default")

def _flat_dir(camera_name):
    """Base directory for flats for this camera."""
    return FLAT_DIR / (camera_name or "default")

def _pixelmaps_dir(camera_name):
    """Base directory for pixel maps (TIFF review images) for this camera."""
    return PIXELMAPS_DIR / (camera_name or "default")

def _dark_path(integration_time_seconds: float, gain: int, width: int, height: int, camera_name) -> pathlib.Path:
    """Path for a specific dark file: darks/<camera>/dark_{time}_{gain}_{width}x{height}.npy"""
    return _dark_dir(camera_name) / f"dark_{integration_time_seconds}_{gain}_{width}x{height}.npy"

def _flat_path(integration_time_seconds: float, gain: int, width: int, height: int, camera_name) -> pathlib.Path:
    """Path for a specific flat file: flats/<camera>/flat_{time}_{gain}_{width}x{height}.npy"""
    return _flat_dir(camera_name) / f"flat_{integration_time_seconds}_{gain}_{width}x{height}.npy"

# Filename patterns: with resolution dark_1.5_100_1920x1080.npy; legacy dark_1.5_100.npy, dark_1.5.npy
_DARK_FNAME_RE = re.compile(r"^dark_([\d.]+)_(\d+)_(\d+)x(\d+)\.npy$")
_DARK_LEGACY_RE = re.compile(r"^dark_([\d.]+)_(\d+)\.npy$")
_DARK_LEGACY_T_RE = re.compile(r"^dark_([\d.]+)\.npy$")
_FLAT_FNAME_RE = re.compile(r"^flat_([\d.]+)_(\d+)_(\d+)x(\d+)\.npy$")
_FLAT_LEGACY_RE = re.compile(r"^flat_([\d.]+)_(\d+)\.npy$")
_FLAT_LEGACY_T_RE = re.compile(r"^flat_([\d.]+)\.npy$")

def _distance_time_gain(t1: float, g1: int, t2: float, g2: int) -> float:
    """Distance for nearest-match: time diff (s) + gain diff/100."""
    return abs(t1 - t2) + abs(g1 - g2) / 100.0

def _find_nearest_dark(camera_name, time_seconds: float, gain: int, width: int, height: int):
    """Return (path, distance, (t, g)) for nearest dark matching resolution, or (None, inf, None)."""
    def scan_dir(base_path):
        candidates = []
        if not base_path.exists():
            return candidates
        for p in base_path.glob("dark_*.npy"):
            m = _DARK_FNAME_RE.match(p.name)
            if m:
                tw, gw, w, h = float(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
                if width > 0 and height > 0 and (w != width or h != height):
                    continue
                candidates.append((p, (tw, gw)))
            else:
                m = _DARK_LEGACY_RE.match(p.name)
                if m:
                    t, g = float(m.group(1)), int(m.group(2))
                    candidates.append((p, (t, g)))
                else:
                    m = _DARK_LEGACY_T_RE.match(p.name)
                    if m:
                        candidates.append((p, (float(m.group(1)), 0)))
        return candidates
    all_c = scan_dir(_dark_dir(camera_name)) + scan_dir(DARK_DIR)
    best_path, best_dist, best_tg = None, math.inf, None
    for p, (t, g) in all_c:
        d = _distance_time_gain(time_seconds, gain, t, g)
        if d < best_dist:
            best_dist, best_path, best_tg = d, p, (t, g)
    return best_path, best_dist, best_tg

def _find_nearest_flat(camera_name, time_seconds: float, gain: int, width: int, height: int):
    """Return (path, distance, (t, g)) for nearest flat matching resolution, or (None, inf, None)."""
    def scan_dir(base_path):
        candidates = []
        if not base_path.exists():
            return candidates
        for p in base_path.glob("flat_*.npy"):
            m = _FLAT_FNAME_RE.match(p.name)
            if m:
                tw, gw, w, h = float(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
                if width > 0 and height > 0 and (w != width or h != height):
                    continue
                candidates.append((p, (tw, gw)))
            else:
                m = _FLAT_LEGACY_RE.match(p.name)
                if m:
                    t, g = float(m.group(1)), int(m.group(2))
                    candidates.append((p, (t, g)))
                else:
                    m = _FLAT_LEGACY_T_RE.match(p.name)
                    if m:
                        candidates.append((p, (float(m.group(1)), 0)))
        return candidates
    all_c = scan_dir(_flat_dir(camera_name)) + scan_dir(FLAT_DIR)
    best_path, best_dist, best_tg = None, math.inf, None
    for p, (t, g) in all_c:
        d = _distance_time_gain(time_seconds, gain, t, g)
        if d < best_dist:
            best_dist, best_path, best_tg = d, p, (t, g)
    return best_path, best_dist, best_tg

# Display size is set from settings (disp_scale) at runtime; see _build_ui and _frame_to_texture


class XrayGUI:
    def __init__(self):
        # Imaging source (set by camera module when loaded)
        self.camera_module = None
        self.camera_module_name = None  # e.g. "asi_camera", "hamamatsu_c7942" for darks/flats subfolder
        self.teensy = None  # Set by Hamamatsu C7942 module on connect (e.g. for Faxitron)

        # Frame dimensions (defaults; may be set from camera module in _build_ui)
        self.frame_width = DEFAULT_FRAME_W
        self.frame_height = DEFAULT_FRAME_H

        # Acquisition state
        self.acq_mode = "idle"
        self._prev_acq_mode = "idle"  # used to detect acquisition end for optional beam_supply turn-off
        self.acq_thread = None
        self.acq_stop = threading.Event()
        self.integration_time = 1.0  # seconds between dual-shot pairs

        # Optional beam supply (e.g. ESP HV): Auto On before acquisition, Off when done
        self.beam_supply = None

        # Frame data (protected by lock)
        self.frame_lock = threading.Lock()
        self.raw_frame = None           # float32 (H, W), latest single raw
        self.display_frame = None       # float32 (H, W), integrated result (mean of integration buffer)
        self.frame_buffer = []          # list of float32 processed frames for integration (each frame ran full pipeline)
        self.integration_n = 1          # integration size: we keep last N processed frames and display their mean
        self.new_frame_ready = threading.Event()

        # Dark and flat fields (loaded after _apply_loaded_settings so integration_time is restored first)
        self.dark_field = None
        self.flat_field = None
        self._dark_stack_n = DARK_STACK_DEFAULT
        self._flat_stack_n = DARK_STACK_DEFAULT
        # Which (time, gain) we loaded from / nearest when not applied (for status text)
        self._dark_loaded_time_gain = None   # (t, g) when dark loaded from file
        self._dark_nearest_time_gain = None  # (t, g) when no dark applied (nearest too far)
        self._flat_loaded_time_gain = None
        self._flat_nearest_time_gain = None

        # Bad pixel map (built from dark+flat by bad_pixel_map module; 2D bool mask or None)
        self.bad_pixel_map_mask = None

        # Banding correction
        self.banding_enabled = True
        self.banding_black_w = DEFAULT_BLACK_W
        self.banding_smooth_win = DEFAULT_SMOOTH_WIN
        self.banding_auto_optimize = True  # Auto-optimize enabled by default
        self.banding_optimized_win = None  # Cached optimized window (computed once)
        # Vertical banding (bottom rows as reference)
        self.vertical_banding_enabled = True
        self.vertical_stripe_h = DEFAULT_VERTICAL_STRIPE_H
        self.vertical_smooth_win = DEFAULT_VERTICAL_SMOOTH_WIN
        self.vertical_banding_auto_optimize = False  # Auto-optimize vertical smooth window
        self.vertical_banding_optimized_win = None   # Cached (computed once)
        self.vertical_banding_first = False  # If True, do vertical then horizontal; if False, horizontal then vertical

        # Dead pixel line correction
        self.dead_lines_enabled = True
        self.dead_vertical_lines = []  # Dead vertical lines (user-configured)
        self.dead_horizontal_lines = []  # Dead horizontal lines (user-configured)

        # Windowing
        self.win_min = 0.0
        self.win_max = 4095.0
        self.hist_eq = False

        # Deconvolution (apply to snapshot; raw stored for Revert / re-apply)
        self._deconv_raw_frame = None   # Snapshot when Apply first used (always re-use for re-apply)
        self._deconv_result = None      # Last deconvolved image (for windowing refresh)
        self._display_mode = "live"     # "live" | "raw" | "deconvolved"
        self.deconv_sigma = 1.0
        self.deconv_iterations = 10

        # Stats
        self.frame_count = 0
        self.fps = 0.0
        self._fps_time = time.time()
        self._fps_count = 0
        self._status_msg = ""

        # Progress tracking (written by worker, read by main)
        self._progress = 0.0       # 0.0 - 1.0
        self._progress_text = ""   # e.g. "Capturing 3/8..."
        # For workflow_automation (e.g. CT): last stacked frame after capture_n/single (set when acq returns to idle)
        self.last_captured_frame = None
        # True while request_integration() is actively running (used by workflow-only alteration modes)
        self._workflow_request_active = False

        # API facade for modules (clear names, single contract)
        self.api = AppAPI(self)

        # Last capture diagnostics (rows, DMA ok/timeout) for log
        self._last_capture_diag = None

        # Debounced settings persistence (coalesce many UI changes into fewer disk writes)
        self._settings_save_pending = False
        self._settings_save_deadline = 0.0
        self._settings_save_scope = "window"  # "window" or "full"
        self._settings_save_debounce_s = 0.35
        # Guard to prevent callback feedback loops while syncing histogram lines <-> min/max controls
        self._window_sync_guard = False
        # Coalesce expensive texture/histogram redraws from rapid windowing callbacks
        self._window_refresh_pending = False

        # DPG ids (assigned in _build_ui)
        self._texture_id = None
        # Main view short preview: when True, display is not overwritten by new frames until clear_main_view_preview()
        self._main_view_preview_active = False

        # Image viewport (zoom and pan) - will be initialized after UI is built
        self.image_viewport = None

        # Discover modules first so we can load/save their settings
        self._discovered_modules = discover_modules()
        self._extra_settings_keys = all_extra_settings_keys(self._discovered_modules)
        self._loaded_settings = load_settings(extra_keys=self._extra_settings_keys)
        self._apply_loaded_settings(self._loaded_settings)
        # Load dark/flat for restored integration_time so they're correct on startup
        self._load_dark_field()
        self._load_flat_field()
        # _disp_w, _disp_h set in _build_ui from frame_width/frame_height and disp_scale
        # _acquisition_mode_map: label -> mode_id, set in _build_ui from camera_module.get_acquisition_modes()
        self._acquisition_mode_map = {}
        # Image alteration pipeline: list of (pipeline_slot, process_frame) built in _build_ui
        self._alteration_pipeline = []
        # Frame after last step with slot < DISTORTION_PREVIEW_SLOT (for live distortion/crop preview)
        self._frame_before_distortion = None
        # Steps with slot >= DISTORTION_PREVIEW_SLOT (pincushion, mustache, autocrop) for re-run on slider change
        self._distortion_crop_pipeline = []
        # Collect N frames with pipeline run only up to a slot (for dark/flat capture by their modules)
        self._capture_max_slot = None
        self._capture_frames_collect = []
        self._capture_n = 0
        self._capture_frames_ready = threading.Event()
        # Per-frame pipeline cache for modules: incoming frame before each module step.
        self._pipeline_frame_token = 0
        self._pipeline_module_cache = {}   # module_name -> {"token": int, "slot": int, "frame": np.ndarray}
        self._pipeline_module_slots = {}   # module_name -> slot

    def _apply_loaded_settings(self, s: dict):
        """Apply loaded settings dict to self (used at startup)."""
        self.integration_time = self._parse_integration_time(s.get("integ_time", "1 s"))
        self.integration_n = max(1, min(32, int(s.get("integ_n", 1))))
        self._dark_stack_n = max(1, min(50, int(s.get("dark_stack_n", DARK_STACK_DEFAULT))))
        self._flat_stack_n = max(1, min(50, int(s.get("flat_stack_n", DARK_STACK_DEFAULT))))
        self.win_min = float(s.get("win_min", HIST_MIN_12BIT))
        self.win_max = float(s.get("win_max", HIST_MAX_12BIT))
        # Clamp to display range is done in build_ui after camera is registered (so bit depth is known).
        self.hist_eq = bool(s.get("hist_eq", False))
        self.banding_enabled = bool(s.get("banding_enabled", True))
        self.banding_auto_optimize = bool(s.get("banding_auto_optimize", True))
        self.banding_black_w = max(5, min(50, int(s.get("banding_black_w", DEFAULT_BLACK_W))))
        self.banding_smooth_win = max(32, min(512, int(s.get("banding_smooth_win", DEFAULT_SMOOTH_WIN))))
        self.vertical_banding_enabled = bool(s.get("vertical_banding_enabled", True))
        self.vertical_stripe_h = max(5, min(80, int(s.get("vertical_stripe_h", DEFAULT_VERTICAL_STRIPE_H))))
        self.vertical_smooth_win = max(32, min(512, int(s.get("vertical_smooth_win", DEFAULT_VERTICAL_SMOOTH_WIN))))
        self.vertical_banding_auto_optimize = bool(s.get("vertical_banding_auto_optimize", False))
        self.vertical_banding_first = bool(s.get("vertical_banding_first", False))
        self.dead_lines_enabled = bool(s.get("dead_lines_enabled", True))
        vlines = s.get("dead_vertical_lines", "")
        self.dead_vertical_lines = [int(x.strip()) for x in str(vlines).split(",") if x.strip()] if vlines else []
        hlines = s.get("dead_horizontal_lines", "") or ""
        self.dead_horizontal_lines = [int(x.strip()) for x in str(hlines).split(",") if x.strip()]
        self.deconv_sigma = max(0.2, min(10.0, float(s.get("deconv_sigma", 1.0))))
        self.deconv_iterations = max(1, min(100, int(s.get("deconv_iterations", 10))))
        self.disp_scale = max(1, min(4, int(s.get("disp_scale", 1))))  # 1=full, 2=half, 4=quarter
        # Module enable flags (from registry; key = load_<name>_module)
        self._module_enabled = {}
        for m in self._discovered_modules:
            key = f"load_{m['name']}_module"
            self._module_enabled[m["name"]] = bool(s.get(key, m.get("default_enabled", False)))

    def clear_frame_buffer(self):
        """Clear the integration buffer and display so the next submitted frame(s) are the only content. Call before submitting when loading a new image (e.g. Open Image module)."""
        with self.frame_lock:
            self.frame_buffer.clear()
            self.display_frame = None
            self.raw_frame = None

    def _update_integrated_display(self) -> None:
        """Set display_frame to the integrated result: mean of the last N processed frames in the buffer."""
        if not self.frame_buffer:
            return
        self.display_frame = np.mean(self.frame_buffer, axis=0)

    def submit_raw_frame(self, frame):
        """Called by camera module for each acquired frame. Runs dark/flat, corrections, buffer, display."""
        self._push_frame(frame)

    def _request_settings_save(self, scope: str = "full", debounce_s: float = None):
        """Schedule debounced settings save; full scope overrides window-only scope."""
        if not getattr(self, "_extra_settings_keys", None):
            return
        if scope not in ("window", "full"):
            scope = "full"
        if debounce_s is None:
            debounce_s = self._settings_save_debounce_s
        self._settings_save_pending = True
        if scope == "full" or self._settings_save_scope != "full":
            self._settings_save_scope = scope
        self._settings_save_deadline = time.monotonic() + max(0.0, float(debounce_s))

    def _flush_pending_settings_save(self, force: bool = False):
        """Run pending debounced save on main thread when due (or immediately when force=True)."""
        if not self._settings_save_pending:
            return
        if not force and time.monotonic() < self._settings_save_deadline:
            return
        scope = self._settings_save_scope
        self._settings_save_pending = False
        self._settings_save_scope = "window"
        if scope == "window":
            self._save_windowing_settings_now()
        else:
            self._save_settings_now()

    def _save_settings(self):
        """Debounced full settings save request."""
        self._request_settings_save(scope="full")

    def _save_settings_now(self):
        """Read current values from UI and persist to disk immediately. No-op if UI not built yet."""
        if not getattr(self, "_extra_settings_keys", None):
            return
        # Sync module checkboxes into _module_enabled (in case callback ran before DPG updated)
        for m in self._discovered_modules:
            tag = f"load_module_cb_{m['name']}"
            if dpg.does_item_exist(tag):
                self._module_enabled[m["name"]] = bool(dpg.get_value(tag))
        # Build dict with module enable state first (no other DPG) so it always persists
        s = {}
        for m in self._discovered_modules:
            s[f"load_{m['name']}_module"] = self._module_enabled.get(m["name"], False)
        try:
            if not dpg.does_item_exist("acq_mode_combo"):
                save_settings(s, extra_keys=self._extra_settings_keys)
                return
            s["acq_mode"] = dpg.get_value("acq_mode_combo")
            s["integ_time"] = dpg.get_value("integ_time_combo")
            s["integ_n"] = int(dpg.get_value("integ_n_slider"))
            s["win_min"] = float(dpg.get_value("win_min_drag"))
            s["win_max"] = float(dpg.get_value("win_max_drag"))
            s["hist_eq"] = dpg.get_value("hist_eq_cb")
            s["disp_scale"] = self.disp_scale
            # Each module contributes its own settings (gui passed so modules can read DPG or fallback to gui state)
            for m in self._discovered_modules:
                try:
                    mod = __import__(f"machine_modules.{m['name']}", fromlist=["get_settings_for_save"])
                    get_save = getattr(mod, "get_settings_for_save", None)
                    if callable(get_save):
                        for k, v in get_save(self).items():
                            s[k] = v
                except Exception:
                    pass
        except Exception:
            pass  # s already has module enable state
        save_settings(s, extra_keys=self._extra_settings_keys)

    def _save_windowing_settings_fast(self):
        """
        Debounced save request for lightweight windowing-only settings.
        Used by histogram/window callbacks to avoid expensive module sweeps.
        """
        self._request_settings_save(scope="window")

    def _save_windowing_settings_now(self):
        """Persist only lightweight windowing settings immediately."""
        if not getattr(self, "_extra_settings_keys", None):
            return
        s = {
            "win_min": float(self.win_min),
            "win_max": float(self.win_max),
            "hist_eq": bool(self.hist_eq),
        }
        save_settings(s, extra_keys=self._extra_settings_keys)

    def _request_window_refresh(self):
        """Schedule one redraw on next render tick (avoids callback storm backlog)."""
        self._window_refresh_pending = True

    def _get_current_settings_dict(self):
        """Build the same dict as _save_settings would persist (for saving as profile). Returns dict."""
        if not getattr(self, "_extra_settings_keys", None):
            return {}
        s = {}
        for m in self._discovered_modules:
            tag = f"load_module_cb_{m['name']}"
            if dpg.does_item_exist(tag):
                self._module_enabled[m["name"]] = bool(dpg.get_value(tag))
        for m in self._discovered_modules:
            s[f"load_{m['name']}_module"] = self._module_enabled.get(m["name"], False)
        try:
            if not dpg.does_item_exist("acq_mode_combo"):
                return s
            s["acq_mode"] = dpg.get_value("acq_mode_combo")
            s["integ_time"] = dpg.get_value("integ_time_combo")
            s["integ_n"] = int(dpg.get_value("integ_n_slider"))
            s["win_min"] = float(dpg.get_value("win_min_drag"))
            s["win_max"] = float(dpg.get_value("win_max_drag"))
            s["hist_eq"] = dpg.get_value("hist_eq_cb")
            s["disp_scale"] = self.disp_scale
            for m in self._discovered_modules:
                try:
                    mod = __import__(f"machine_modules.{m['name']}", fromlist=["get_settings_for_save"])
                    get_save = getattr(mod, "get_settings_for_save", None)
                    if callable(get_save):
                        for k, v in get_save(self).items():
                            s[k] = v
                except Exception:
                    pass
        except Exception:
            pass
        return s

    # ── Dark field persistence (per camera, time, gain; nearest-match load) ────

    def _get_camera_gain(self) -> int:
        """Current gain from camera module; 0 if module has no settable gain."""
        if self.camera_module is None:
            return 0
        get_gain = getattr(self.camera_module, "get_current_gain", None)
        if callable(get_gain):
            return int(get_gain(self))
        return 0

    def _load_dark_field(self):
        """Load nearest master dark for current integration time, gain and resolution (within threshold)."""
        gain = self._get_camera_gain()
        cam = self.camera_module_name
        w, h = getattr(self, "frame_width", 0), getattr(self, "frame_height", 0)
        path, dist, tg = _find_nearest_dark(cam, self.integration_time, gain, w, h)
        self._dark_loaded_time_gain = None
        self._dark_nearest_time_gain = None
        if path is not None and dist <= DARK_FLAT_MATCH_THRESHOLD:
            try:
                loaded = np.load(path).astype(np.float32)
                if w > 0 and h > 0 and (loaded.shape[0] != h or loaded.shape[1] != w):
                    self.dark_field = None
                else:
                    self.dark_field = loaded
                    self._dark_loaded_time_gain = tg
            except Exception:
                self.dark_field = None
        else:
            self.dark_field = None
            if tg is not None:
                self._dark_nearest_time_gain = tg
                self._status_msg = f"No dark within range (nearest {tg[0]}s @ {tg[1]})"

    def _save_dark_field(self):
        """Save master dark for current integration time, gain and resolution in camera subfolder."""
        gain = self._get_camera_gain()
        cam = self.camera_module_name
        height, width = self.dark_field.shape[0], self.dark_field.shape[1]
        base = _dark_dir(cam)
        base.mkdir(parents=True, exist_ok=True)
        path = _dark_path(self.integration_time, gain, width, height, cam)
        np.save(path, self.dark_field)
        shutil.copy2(path, base / LAST_CAPTURED_DARK_NAME)
        arr = self.dark_field.astype(np.float32)
        try:
            import tifffile
            tifffile.imwrite(path.with_suffix(".tif"), arr, photometric="minisblack", compression=None)
            shutil.copy2(path.with_suffix(".tif"), base / "last_captured_dark.tif")
        except Exception:
            pass
        # So status shows the just-saved (time, gain) without reloading
        self._dark_loaded_time_gain = (self.integration_time, gain)
        self._dark_nearest_time_gain = None

    def _load_flat_field(self):
        """Load nearest master flat for current integration time, gain and resolution (within threshold)."""
        gain = self._get_camera_gain()
        cam = self.camera_module_name
        w, h = getattr(self, "frame_width", 0), getattr(self, "frame_height", 0)
        path, dist, tg = _find_nearest_flat(cam, self.integration_time, gain, w, h)
        self._flat_loaded_time_gain = None
        self._flat_nearest_time_gain = None
        if path is not None and dist <= DARK_FLAT_MATCH_THRESHOLD:
            try:
                loaded = np.load(path).astype(np.float32)
                if w > 0 and h > 0 and (loaded.shape[0] != h or loaded.shape[1] != w):
                    self.flat_field = None
                else:
                    self.flat_field = loaded
                    self._flat_loaded_time_gain = tg
            except Exception:
                self.flat_field = None
        else:
            self.flat_field = None
            if tg is not None:
                self._flat_nearest_time_gain = tg
                self._status_msg = f"No flat within range (nearest {tg[0]}s @ {tg[1]})"

    def _save_flat_field(self):
        """Save master flat for current integration time, gain and resolution in camera subfolder."""
        gain = self._get_camera_gain()
        cam = self.camera_module_name
        height, width = self.flat_field.shape[0], self.flat_field.shape[1]
        base = _flat_dir(cam)
        base.mkdir(parents=True, exist_ok=True)
        path = _flat_path(self.integration_time, gain, width, height, cam)
        np.save(path, self.flat_field)
        shutil.copy2(path, base / LAST_CAPTURED_FLAT_NAME)
        arr = self.flat_field.astype(np.float32)
        try:
            import tifffile
            tifffile.imwrite(path.with_suffix(".tif"), arr, photometric="minisblack", compression=None)
            shutil.copy2(path.with_suffix(".tif"), base / "last_captured_flat.tif")
        except Exception:
            pass
        # So status shows the just-saved (time, gain) without reloading
        self._flat_loaded_time_gain = (self.integration_time, gain)
        self._flat_nearest_time_gain = None

    def _on_dark_flat_params_changed(self):
        """Call when integration time or gain changes so dark/flat nearest-match and status are refreshed."""
        self._load_dark_field()
        self._load_flat_field()
        if dpg.does_item_exist("dark_status"):
            dpg.set_value("dark_status", self._dark_status_text())
        if dpg.does_item_exist("flat_status"):
            dpg.set_value("flat_status", self._flat_status_text())
        self._update_alteration_dark_flat_status()

    def get_dark_dir(self):
        """Base directory for darks (and bad pixel map .npy) for the current camera. For use by api.get_dark_dir()."""
        return _dark_dir(self.camera_module_name)

    def get_pixelmaps_dir(self):
        """Base directory for pixel map TIFFs (review) for the current camera. For use by api.get_pixelmaps_dir()."""
        return _pixelmaps_dir(self.camera_module_name)

    def _dark_status_text(self):
        """Short status for dark: Loaded (Xs @ Y), None (nearest too far), or None."""
        if self.dark_field is not None and self._dark_loaded_time_gain:
            t, g = self._dark_loaded_time_gain
            return f"Dark ({t}s @ {g}): Loaded"
        if self._dark_nearest_time_gain:
            t, g = self._dark_nearest_time_gain
            return f"Dark: None (nearest {t}s @ {g} too far)"
        return "Dark: None"

    def _flat_status_text(self):
        if self.flat_field is not None and self._flat_loaded_time_gain:
            t, g = self._flat_loaded_time_gain
            return f"Flat ({t}s @ {g}): Loaded"
        if self._flat_nearest_time_gain:
            t, g = self._flat_nearest_time_gain
            return f"Flat: None (nearest {t}s @ {g} too far)"
        return "Flat: None"

    # ── Frame pipeline (called by camera module via submit_raw_frame) ─────

    # First pipeline_slot used for "distortion" (pincushion, mustache, autocrop) for live preview
    DISTORTION_PREVIEW_SLOT = 450

    def _frame_log_signature(self, frame: np.ndarray):
        arr = np.asarray(frame)
        shape = tuple(arr.shape)
        dtype = str(arr.dtype)
        if arr.size == 0:
            return shape, dtype, [0.0]
        flat = arr.reshape(-1)
        idx = np.linspace(0, flat.size - 1, num=min(9, flat.size), dtype=np.int64)
        vals = [float(flat[int(i)]) for i in idx]
        return shape, dtype, vals

    def _log_pipeline_step(self, context: str, token: int, slot: int, module_name: str, frame_in, frame_out):
        """Compact per-step pipeline diagnostics for module manipulations."""
        try:
            in_shape, in_dtype, in_vals = self._frame_log_signature(frame_in)
            out_shape, out_dtype, out_vals = self._frame_log_signature(frame_out)
            if len(in_vals) == len(out_vals):
                sample_mad = float(np.mean(np.abs(np.asarray(out_vals) - np.asarray(in_vals))))
            else:
                sample_mad = float("nan")
            changed = (in_shape != out_shape) or (in_dtype != out_dtype) or (sample_mad > 1e-9)
            print(
                f"[Pipeline][{context}] token={token} slot={slot} module={module_name} "
                f"in={in_shape}/{in_dtype} out={out_shape}/{out_dtype} "
                f"changed={changed} sample_mad={sample_mad:.6g}",
                flush=True,
            )
        except Exception as e:
            print(
                f"[Pipeline][{context}] token={token} slot={slot} module={module_name} "
                f"log-error={e}",
                flush=True,
            )

    def _push_frame(self, frame):
        """Apply alteration pipeline (dark, flat, etc.), then banding, dead pixel, distortion, crop; buffer and signal.
        When _capture_max_slot is set, run only steps with slot < _capture_max_slot and collect result (for dark/flat capture)."""
        max_slot = getattr(self, "_capture_max_slot", None)
        pipeline = getattr(self, "_alteration_pipeline", [])
        self._pipeline_frame_token += 1
        frame_token = self._pipeline_frame_token

        if max_slot is not None:
            # Run only steps with slot < max_slot (e.g. 100 for dark = raw; 200 for flat = dark applied)
            for slot, module_name, step in pipeline:
                if slot >= max_slot:
                    break
                self._pipeline_module_cache[module_name] = {
                    "token": frame_token,
                    "slot": slot,
                    "frame": frame.copy(),
                }
                frame_in = frame
                try:
                    frame = step(frame, self)
                except Exception as e:
                    print(
                        f"[Pipeline][capture] token={frame_token} slot={slot} module={module_name} "
                        f"step-error={e}",
                        flush=True,
                    )
                    raise
                self._log_pipeline_step("capture", frame_token, slot, module_name, frame_in, frame)
            with self.frame_lock:
                self._capture_frames_collect.append(frame.copy())
                if len(self._capture_frames_collect) >= getattr(self, "_capture_n", 0):
                    self._capture_frames_ready.set()
            return

        frame_before_distortion = None
        for slot, module_name, step in pipeline:
            if slot >= self.DISTORTION_PREVIEW_SLOT and frame_before_distortion is None:
                frame_before_distortion = frame.copy()
            self._pipeline_module_cache[module_name] = {
                "token": frame_token,
                "slot": slot,
                "frame": frame.copy(),
            }
            frame_in = frame
            try:
                frame = step(frame, self)
            except Exception as e:
                print(
                    f"[Pipeline][live] token={frame_token} slot={slot} module={module_name} "
                    f"step-error={e}",
                    flush=True,
                )
                raise
            self._log_pipeline_step("live", frame_token, slot, module_name, frame_in, frame)

        with self.frame_lock:
            if frame_before_distortion is not None:
                self._frame_before_distortion = frame_before_distortion
            self.raw_frame = frame
            self.frame_buffer.append(frame)
            if len(self.frame_buffer) > self.integration_n:
                self.frame_buffer = self.frame_buffer[-self.integration_n:]
            self._update_integrated_display()

        self.frame_count += 1
        now = time.time()
        self._fps_count += 1
        dt = now - self._fps_time
        if dt >= 1.0:
            self.fps = self._fps_count / dt
            self._fps_count = 0
            self._fps_time = now

        self.new_frame_ready.set()

    # ── Generic pipeline state helpers (for manual-alteration modules) ─────

    def _get_module_incoming_image(self, module_name: str):
        item = self._pipeline_module_cache.get(module_name)
        if not item:
            return None
        frame = item.get("frame")
        return frame.copy() if frame is not None else None

    def _incoming_frame_for_module(self, module_name: str, frame: np.ndarray, use_cached: bool = False):
        if use_cached:
            cached = self._get_module_incoming_image(module_name)
            if cached is not None:
                return cached
        return frame

    def _get_module_incoming_token(self, module_name: str):
        item = self._pipeline_module_cache.get(module_name)
        if not item:
            return None
        return int(item.get("token", 0))

    def _continue_pipeline_from_slot(self, frame: np.ndarray, start_slot_exclusive: int):
        out = np.asarray(frame, dtype=np.float32)
        token = int(getattr(self, "_pipeline_frame_token", 0))
        for slot, _module_name, step in getattr(self, "_alteration_pipeline", []):
            if slot <= start_slot_exclusive:
                continue
            frame_in = out
            try:
                out = step(out, self)
            except Exception as e:
                print(
                    f"[Pipeline][continue] token={token} slot={slot} module={_module_name} "
                    f"step-error={e}",
                    flush=True,
                )
                raise
            self._log_pipeline_step("continue", token, slot, _module_name, frame_in, out)
        return out

    def _continue_pipeline_from_module(self, module_name: str, frame: np.ndarray):
        slot = self._pipeline_module_slots.get(module_name, None)
        if slot is None:
            # Fallback in case slot map is stale/incomplete.
            for s, n, _pf in getattr(self, "_alteration_pipeline", []):
                if n == module_name:
                    slot = s
                    self._pipeline_module_slots[module_name] = s
                    break
        if slot is None:
            # Do not silently skip all downstream modules; run full pipeline and log warning.
            print(
                f"[Pipeline][manual-continue] module={module_name} not in slot map; "
                f"falling back to full pipeline continuation",
                flush=True,
            )
            slot = -1
        downstream = [n for s, n, _pf in getattr(self, "_alteration_pipeline", []) if s > slot]
        print(
            f"[Pipeline][manual-continue] module={module_name} start_slot={slot} downstream={downstream}",
            flush=True,
        )
        return self._continue_pipeline_from_slot(frame, slot)

    def _output_manual_from_module(self, module_name: str, frame: np.ndarray):
        out = self._continue_pipeline_from_module(module_name, frame)
        with self.frame_lock:
            self.display_frame = out.copy()
        self._display_mode = "live"
        self._paint_texture_from_frame(out)
        self._force_image_refresh()
        print(
            f"[Pipeline][manual-output] module={module_name} mode=live painted=1",
            flush=True,
        )
        return out

    def _outgoing_frame_from_module(self, module_name: str, frame: np.ndarray):
        # Reserved extension point for module-level output hooks/diagnostics.
        return frame

    def request_n_frames_processed_up_to_slot(
        self, n: int, max_slot: int, timeout_seconds: float = 300.0, dark_capture: bool = False
    ):
        """
        Run camera capture_n for N frames, running the pipeline only for steps with slot < max_slot,
        collect the results and return their average (float32). Used by dark/flat modules for capture.
        dark_capture=True skips turning on the beam (for dark reference). Returns None on timeout/error.
        """
        if self.camera_module is None or not self.camera_module.is_connected():
            return None
        if self.acq_mode != "idle":
            return None
        self._capture_max_slot = max_slot
        self._capture_frames_collect = []
        self._capture_n = n
        self._capture_frames_ready.clear()
        self._capture_skip_beam = dark_capture
        self.integration_n = n
        self.acq_stop.clear()
        self._progress = 0.0
        self.clear_frame_buffer()

        # For flat capture: turn on beam supply (with cancel support). Dark capture skips this.
        if not dark_capture:
            beam = getattr(self, "beam_supply", None)
            if beam is not None and beam.wants_auto_on_off() and not beam.is_connected():
                if not getattr(self, "workflow_keep_beam_on", False):
                    self._status_msg = "Auto On/Off enabled but supply not connected"
                    return None
            if beam is not None and beam.wants_auto_on_off() and beam.is_connected():
                if not getattr(self, "workflow_keep_beam_on", False):
                    self._progress_text = "Waiting for supply... (click Stop to cancel)"
                    if not beam.turn_on_and_wait_ready(should_cancel=lambda: self.acq_stop.is_set()):
                        self._progress_text = ""
                        self._capture_max_slot = None
                        self._capture_frames_collect = []
                        self._capture_n = 0
                        self._capture_skip_beam = False
                        if self.acq_stop.is_set():
                            self._status_msg = "Acquisition cancelled"
                        else:
                            self._status_msg = "Supply did not become ready (timeout or fault)"
                        return None
                    self._progress_text = ""

        self.acq_mode = "capture_n"
        self.camera_module.start_acquisition(self)
        t0 = time.time()
        while not self._capture_frames_ready.wait(timeout=0.2):
            if self.acq_stop.is_set():
                self._stop_acquisition()
                break
            if (time.time() - t0) > timeout_seconds:
                self._stop_acquisition()
                break
        # Wait for camera thread to finish so we don't clear capture state too early (check cancel too)
        while self.acq_mode != "idle" and (time.time() - t0) < timeout_seconds + 5:
            if self.acq_stop.is_set():
                break
            time.sleep(0.05)
        collected = getattr(self, "_capture_frames_collect", [])
        self._capture_max_slot = None
        self._capture_frames_collect = []
        self._capture_n = 0
        self._capture_skip_beam = False
        if len(collected) < n:
            return None
        return np.mean(collected, axis=0).astype(np.float32)

    def _start_acquisition(self, mode):
        """Start acquisition; mode is 'single', 'dual', 'continuous', 'capture_n'."""
        if self.camera_module is None:
            self._status_msg = "No camera module loaded"
            return
        if not self.camera_module.is_connected():
            self._status_msg = "Not connected"
            return
        if self.acq_mode != "idle":
            return
        # Optional beam supply: block acquisition if Auto On/Off is on but supply is not connected.
        skip_beam = getattr(self, "_capture_skip_beam", False)
        beam = getattr(self, "beam_supply", None)
        if beam is not None and beam.wants_auto_on_off() and not beam.is_connected() and not skip_beam:
            if not getattr(self, "workflow_keep_beam_on", False):
                self._status_msg = "Auto On/Off enabled but supply not connected"
                self._last_integration_fail_reason = "supply_not_connected"
                return
        # Clear stop flag so this run can proceed; otherwise a previous cancel leaves it set and the beam wait would immediately see "cancelled".
        self.acq_stop.clear()
        # Optional beam supply: turn on and wait for ready (skip when capture requests it, e.g. dark reference).
        if beam is not None and beam.wants_auto_on_off() and beam.is_connected() and not skip_beam:
            if not getattr(self, "workflow_keep_beam_on", False):
                self._progress_text = "Waiting for supply... (click Stop to cancel)"
                # Pass cancellation callback so user can cancel if interlock is open
                if not beam.turn_on_and_wait_ready(should_cancel=lambda: self.acq_stop.is_set()):
                    self._progress_text = ""
                    if self.acq_stop.is_set():
                        self._status_msg = "Acquisition cancelled"
                    else:
                        self._status_msg = "Supply did not become ready (timeout or fault)"
                    return
                self._progress_text = ""
        # Switch to live view so the display updates during acquisition (Start button and request_integration e.g. CT).
        self._display_mode = "live"
        self.acq_mode = mode
        self.acq_stop.clear()
        self._progress = 0.0
        # Clear capture state if leftover from dark/flat capture (so frames go to frame_buffer, not _capture_frames_collect)
        if not getattr(self, "_capture_skip_beam", False):
            self._capture_max_slot = None
            self._capture_frames_collect = []
            self._capture_n = 0
        # Always clear buffer so display uses only this run's frames (single shot, Capture N, or request_integration)
        self.clear_frame_buffer()
        self.camera_module.start_acquisition(self)

    def request_integration(self, num_frames: int, timeout_seconds: float = 300.0):
        """
        Trigger the existing capture flow (same as Start/Capture N), wait for it to finish, return the processed frame.
        Does not rebuild or replace the capture pipeline: it starts the current acquisition with integration_n = num_frames,
        then returns the frame that the pipeline produced (dark/flat, ROI, acq mode, etc. all as configured in the UI).
        Optional hook: set gui.workflow_keep_beam_on True before calling to keep HV on between captures (e.g. CT "Keep HV on").
        For use by workflow_automation modules. Call from the workflow's thread. Returns float32 (H,W) or None.
        """
        if self.camera_module is None or not self.camera_module.is_connected():
            self._last_integration_fail_reason = "not_connected"
            return None
        if self.acq_mode != "idle":
            self._last_integration_fail_reason = "not_idle"
            return None
        # Sync UI state into gui so the existing capture flow sees current settings (same as clicking Start)
        if dpg.does_item_exist("acq_mode_combo"):
            combo_value = dpg.get_value("acq_mode_combo")
            mode = getattr(self, "_acquisition_mode_map", {}).get(combo_value, "dual")
        else:
            mode = "capture_n"
        self.integration_time = self._parse_integration_time(dpg.get_value("integ_time_combo")) if dpg.does_item_exist("integ_time_combo") else self.integration_time
        self.integration_n = max(1, min(32, num_frames))
        if dpg.does_item_exist("integ_n_slider"):
            dpg.set_value("integ_n_slider", self.integration_n)
        if mode == "continuous":
            mode = "capture_n"
        elif mode in ("single", "dual") and num_frames > 1:
            # Caller asked for N frames; use capture_n so the camera delivers N (don't override integration_n).
            mode = "capture_n"
        # Leave integration_n as num_frames; do not override to 1 for single/dual (workflow e.g. CT sets N).
        self.last_captured_frame = None
        self._last_integration_fail_reason = None  # "timeout" | "stopped" | "no_frame" when returning None
        self._workflow_request_active = True
        try:
            self._start_acquisition(mode)
            t0 = time.time()
            while self.acq_mode != "idle":
                if self.acq_stop.is_set():
                    self._stop_acquisition()
                    self._last_integration_fail_reason = "stopped"
                    return None
                if (time.time() - t0) > timeout_seconds:
                    self._stop_acquisition()
                    self._last_integration_fail_reason = "timeout"
                    return None
                time.sleep(0.05)
            # Acquisition is idle; last_captured_frame is set by main thread in _render_tick. Wait for pipeline
            # to finish and main thread to copy display_frame -> last_captured_frame (can take ~1 s with many steps).
            wait_end = time.time() + 3.0  # allow up to 3 s for pipeline + main-thread tick
            out = None
            while time.time() < wait_end:
                with self.frame_lock:
                    if self.last_captured_frame is not None:
                        out = self.last_captured_frame.copy()
                        break
                time.sleep(0.05)
            if out is None:
                self._last_integration_fail_reason = "no_frame"
            return out
        finally:
            self._workflow_request_active = False

    def _stop_acquisition(self):
        self.acq_stop.set()

    # ── Display pipeline (main thread) ──────────────────────────────

    def _frame_to_texture(self, frame):
        """Apply windowing and convert to RGBA float32 for DPG texture. Uses frame shape (handles cropped frames). Returns (data, disp_w, disp_h)."""
        if self.hist_eq:
            norm = self._histogram_equalize(frame)
        else:
            lo, hi = self.win_min, self.win_max
            if hi <= lo:
                hi = lo + 1
            norm = (frame - lo) / (hi - lo)

        norm = np.clip(norm, 0.0, 1.0).astype(np.float32)

        # Display size from actual frame (so cropped frames work)
        disp_h = frame.shape[0] // self.disp_scale
        disp_w = frame.shape[1] // self.disp_scale
        # Optional display downsampling (block mean) when disp_scale > 1
        if self.disp_scale > 1:
            norm = norm.reshape(disp_h, self.disp_scale, disp_w, self.disp_scale).mean(axis=(1, 3))

        # Build RGBA (grayscale: R=G=B=val, A=1)
        rgba = np.empty((disp_h, disp_w, 4), dtype=np.float32)
        rgba[:, :, 0] = norm
        rgba[:, :, 1] = norm
        rgba[:, :, 2] = norm
        rgba[:, :, 3] = 1.0
        return rgba.ravel(), disp_w, disp_h

    def _scale_frame_to_fit(self, frame: np.ndarray, target_w: int, target_h: int) -> np.ndarray:
        """Scale frame to fit inside target_w x target_h (preserve aspect, letterbox). Return float32 (target_h, target_w) in 0–1."""
        if target_w <= 0 or target_h <= 0:
            return np.zeros((target_h, target_w), dtype=np.float32)
        arr = np.asarray(frame, dtype=np.float32)
        h, w = arr.shape[0], arr.shape[1]
        if h <= 0 or w <= 0:
            return np.zeros((target_h, target_w), dtype=np.float32)
        scale = min(target_w / w, target_h / h)
        out_w = max(1, int(round(w * scale)))
        out_h = max(1, int(round(h * scale)))
        yi = np.linspace(0, h - 1, out_h).astype(np.int32)
        xi = np.linspace(0, w - 1, out_w).astype(np.int32)
        small = arr[np.ix_(yi, xi)]
        canvas = np.zeros((target_h, target_w), dtype=np.float32)
        y0 = (target_h - out_h) // 2
        x0 = (target_w - out_w) // 2
        canvas[y0:y0 + out_h, x0:x0 + out_w] = small
        lo, hi = float(np.min(canvas)), float(np.max(canvas))
        if hi > lo:
            canvas = (canvas - lo) / (hi - lo)
        else:
            canvas[:] = 0.5
        return np.clip(canvas, 0.0, 1.0).astype(np.float32)

    def _paint_preview_to_main_view(self, frame: np.ndarray) -> None:
        """Paint a frame to the main view, scaled to fit. Sets preview mode so new frames do not overwrite until clear."""
        disp_w = getattr(self, "_disp_w", 0)
        disp_h = getattr(self, "_disp_h", 0)
        if disp_w <= 0 or disp_h <= 0 or self._texture_id is None:
            return
        scaled = self._scale_frame_to_fit(frame, disp_w, disp_h)
        rgba = np.empty((disp_h, disp_w, 4), dtype=np.float32)
        rgba[:, :, 0] = scaled
        rgba[:, :, 1] = scaled
        rgba[:, :, 2] = scaled
        rgba[:, :, 3] = 1.0
        dpg.set_value(self._texture_id, rgba.ravel().tolist())
        self._main_view_preview_active = True
        self._force_image_refresh()

    def _clear_main_view_preview(self) -> None:
        """Leave preview mode and repaint the normal display (live/raw/deconvolved)."""
        self._main_view_preview_active = False
        self._refresh_texture_from_settings()
        self._force_image_refresh()

    @staticmethod
    def _histogram_equalize(img):
        flat = img.flatten()
        # Use the actual data range, not a fixed 0-4096
        lo, hi = float(flat.min()), float(flat.max())
        if hi <= lo:
            return np.zeros_like(img)
        # Map to 4096 bins within the data range
        nbins = 4096
        hist, bins = np.histogram(flat, bins=nbins, range=(lo, hi))
        cdf = hist.cumsum().astype(np.float64)
        cdf_max = cdf[-1]
        if cdf_max == 0:
            return np.zeros_like(img)
        cdf_norm = cdf / cdf_max
        # Map pixel values to bin indices
        indices = np.clip(((img - lo) / (hi - lo) * (nbins - 1)), 0, nbins - 1).astype(np.int32)
        return cdf_norm[indices].astype(np.float32)

    def _update_display(self):
        """Called from main thread when new_frame_ready is set. Only updates texture when showing live."""
        if self._main_view_preview_active:
            return
        if self._display_mode != "live":
            return
        with self.frame_lock:
            if self.display_frame is None:
                return
            frame = self.display_frame.copy()
        self._paint_texture_from_frame(frame)

    def _refresh_distortion_preview(self):
        """Re-run distortion+crop steps on the last pre-distortion frame and repaint (live preview when adjusting sliders)."""
        if self._display_mode != "live":
            return
        with self.frame_lock:
            if self._frame_before_distortion is None:
                return
            frame = self._frame_before_distortion.copy()
        token = int(getattr(self, "_pipeline_frame_token", 0))
        for _slot, _name, step in getattr(self, "_distortion_crop_pipeline", []):
            frame_in = frame
            try:
                frame = step(frame, self)
            except Exception as e:
                print(
                    f"[Pipeline][preview] token={token} slot={_slot} module={_name} "
                    f"step-error={e}",
                    flush=True,
                )
                raise
            self._log_pipeline_step("preview", token, _slot, _name, frame_in, frame)
        self._paint_texture_from_frame(frame)

    def _force_image_refresh(self):
        """Force the image widget to re-bind/redraw with the current texture (e.g. after Apply/Revert)."""
        self._resize_image()

    def _get_display_max_value(self) -> float:
        """Max display/windowing value from current camera bit depth (12/14/16-bit)."""
        return self.api.get_display_max_value()

    def _clamp_window_bounds(self, lo: float, hi: float):
        dmin, dmax = 0.0, self._get_display_max_value()
        lo = float(max(dmin, min(lo, dmax)))
        hi = float(max(dmin, min(hi, dmax)))
        if hi <= lo:
            hi = min(dmax, lo + 1.0)
            if hi <= lo:
                lo = max(dmin, hi - 1.0)
        return lo, hi

    def _get_histogram_analysis_pixels(self, frame: np.ndarray) -> np.ndarray:
        """Pixels used for histogram/auto-window stats (can ignore separator-clipped background)."""
        flat = np.asarray(frame, dtype=np.float32).reshape(-1)
        flat = flat[np.isfinite(flat)]
        if flat.size == 0:
            return flat
        use_bgsep_mask = bool(getattr(self, "_bgsep_hist_ignore", True)) and bool(
            getattr(self, "_bgsep_hist_active", False)
        )
        cutoff = getattr(self, "_bgsep_hist_cutoff", None)
        if use_bgsep_mask and cutoff is not None and np.isfinite(float(cutoff)):
            masked = flat[flat < float(cutoff)]
            # Fallback if mask becomes too sparse.
            if masked.size >= max(128, int(0.01 * flat.size)):
                return masked
        return flat

    def _paint_texture_from_frame(self, frame: np.ndarray):
        """Update texture and histogram from a given frame (used for live, Apply, Revert). Recreates texture when frame size changes (e.g. after crop)."""
        texture_data, disp_w, disp_h = self._frame_to_texture(frame)
        if (disp_w, disp_h) != (self._disp_w, self._disp_h):
            # Frame size changed (e.g. autocrop); recreate texture and bind to image
            with dpg.texture_registry():
                new_id = dpg.add_dynamic_texture(width=disp_w, height=disp_h, default_value=texture_data)
            dpg.configure_item("main_image", texture_tag=new_id)
            dpg.delete_item(self._texture_id)
            self._texture_id = new_id
            self._disp_w, self._disp_h = disp_w, disp_h
            if self.image_viewport is not None:
                self.image_viewport.aspect_ratio = disp_w / disp_h if disp_h else 1.0
        else:
            dpg.set_value(self._texture_id, texture_data)
        flat = self._get_histogram_analysis_pixels(frame)
        frame_lo, frame_hi = float(flat.min()), float(flat.max())
        if not (np.isfinite(frame_lo) and np.isfinite(frame_hi)) or frame_hi <= frame_lo:
            frame_lo, frame_hi = 0.0, self._get_display_max_value()
        frame_lo, frame_hi = self._clamp_window_bounds(frame_lo, frame_hi)
        # Keep histogram axis and drag lines in the same visible range:
        # include both data extent and current windowing extent.
        axis_lo = min(frame_lo, float(self.win_min))
        axis_hi = max(frame_hi, float(self.win_max))
        axis_lo, axis_hi = self._clamp_window_bounds(axis_lo, axis_hi)
        hist_vals, hist_edges = np.histogram(flat, bins=256, range=(axis_lo, axis_hi))
        peak = hist_vals.max()
        if peak > 0:
            hist_norm = (hist_vals / peak).tolist()
        else:
            hist_norm = [0.0] * len(hist_vals)
        hist_centers = (hist_edges[:-1] + hist_edges[1:]) / 2
        zeros = [0] * len(hist_centers)
        dpg.set_value("hist_series", [hist_centers.tolist(), hist_norm, zeros])
        dpg.set_axis_limits_constraints("hist_x", axis_lo, axis_hi)
        dpg.set_axis_limits("hist_x", axis_lo, axis_hi)
        dpg.set_axis_limits("hist_y", 0.0, 1.05)

    def _refresh_texture_from_settings(self):
        """Re-render current view with new windowing settings (live, raw, or deconvolved)."""
        if self._display_mode == "live":
            with self.frame_lock:
                if self.display_frame is None:
                    return
                frame = self.display_frame.copy()
        elif self._display_mode == "raw" and self._deconv_raw_frame is not None:
            frame = self._deconv_raw_frame.copy()
        elif self._display_mode == "deconvolved" and self._deconv_result is not None:
            frame = self._deconv_result.copy()
        else:
            return
        self._paint_texture_from_frame(frame)

    # ── Callbacks ───────────────────────────────────────────────────

    @staticmethod
    def _parse_integration_time(combo_value: str) -> float:
        """Parse dropdown label to seconds (e.g. '1 s' -> 1.0)."""
        s = (combo_value or "1 s").replace(" s", "").strip()
        return float(s)

    def _update_alteration_dark_flat_status(self):
        """Sync dark/flat status in alteration module UIs when they exist."""
        if dpg.does_item_exist("dark_correction_status"):
            s = f"Active ({self.dark_field.shape[1]}×{self.dark_field.shape[0]})" if self.dark_field is not None else "No dark loaded"
            dpg.set_value("dark_correction_status", s)
        if dpg.does_item_exist("flat_correction_status"):
            s = f"Active ({self.flat_field.shape[1]}×{self.flat_field.shape[0]})" if self.flat_field is not None else "No flat loaded"
            dpg.set_value("flat_correction_status", s)

    def _cb_integ_time_changed(self, sender=None, app_data=None):
        """When integration dropdown changes, load master dark and flat for that time if present."""
        combo_val = dpg.get_value("integ_time_combo")
        self.integration_time = self._parse_integration_time(combo_val)
        self._on_dark_flat_params_changed()
        self._update_alteration_dark_flat_status()
        self._save_settings()

    def _cb_start(self, sender=None, app_data=None):
        combo_value = dpg.get_value("acq_mode_combo")
        mode_id = getattr(self, "_acquisition_mode_map", {}).get(combo_value, "dual")
        self.integration_time = self._parse_integration_time(dpg.get_value("integ_time_combo"))
        self.integration_n = int(dpg.get_value("integ_n_slider"))
        self._start_acquisition(mode_id)

    def _cb_stop(self, sender=None, app_data=None):
        self._stop_acquisition()

    def _cb_auto_window(self, sender=None, app_data=None):
        with self.frame_lock:
            if self.display_frame is None:
                return
            frame = self.display_frame.copy()
        analysis = self._get_histogram_analysis_pixels(frame)
        if analysis.size == 0:
            return
        lo = float(np.percentile(analysis, 1))
        hi = float(np.percentile(analysis, 99))
        # Keep some visual headroom so lines are not pinned to edges after auto-fit.
        margin = 100.0
        lo -= margin
        hi += margin
        self.win_min, self.win_max = self._clamp_window_bounds(lo, hi)
        # Nudge both lines slightly inward so they are not exactly on plot edges.
        inset = 10.0
        if (self.win_max - self.win_min) > (2.0 * inset + 1.0):
            self.win_min += inset
            self.win_max -= inset
            self.win_min, self.win_max = self._clamp_window_bounds(self.win_min, self.win_max)
        self._window_sync_guard = True
        try:
            dpg.set_value("win_min_drag", self.win_min)
            dpg.set_value("win_max_drag", self.win_max)
            dpg.set_value("hist_min_line", self.win_min)
            dpg.set_value("hist_max_line", self.win_max)
        finally:
            self._window_sync_guard = False
        self._request_window_refresh()

    def _cb_hist_eq_toggle(self, sender, value):
        self.hist_eq = value
        self._request_window_refresh()
        self._save_settings()

    def _cb_win_min_changed(self, sender, value):
        if self._window_sync_guard:
            return
        self.win_min, self.win_max = self._clamp_window_bounds(float(value), float(self.win_max))
        self._window_sync_guard = True
        try:
            dpg.set_value("win_min_drag", self.win_min)
            dpg.set_value("win_max_drag", self.win_max)
            dpg.set_value("hist_min_line", self.win_min)
            dpg.set_value("hist_max_line", self.win_max)
        finally:
            self._window_sync_guard = False
        self._request_window_refresh()
        self._save_windowing_settings_fast()

    def _cb_win_max_changed(self, sender, value):
        if self._window_sync_guard:
            return
        self.win_min, self.win_max = self._clamp_window_bounds(float(self.win_min), float(value))
        self._window_sync_guard = True
        try:
            dpg.set_value("win_min_drag", self.win_min)
            dpg.set_value("win_max_drag", self.win_max)
            dpg.set_value("hist_min_line", self.win_min)
            dpg.set_value("hist_max_line", self.win_max)
        finally:
            self._window_sync_guard = False
        self._request_window_refresh()
        self._save_windowing_settings_fast()

    def _cb_hist_min_dragged(self, sender, app_data):
        if self._window_sync_guard:
            return
        val = dpg.get_value(sender)
        if isinstance(val, (list, tuple)):
            val = val[0]
        self.win_min, self.win_max = self._clamp_window_bounds(float(val), float(self.win_max))
        self._window_sync_guard = True
        try:
            dpg.set_value("win_min_drag", self.win_min)
            dpg.set_value("win_max_drag", self.win_max)
            dpg.set_value("hist_max_line", self.win_max)
        finally:
            self._window_sync_guard = False
        self._request_window_refresh()
        self._save_windowing_settings_fast()

    def _cb_hist_max_dragged(self, sender, app_data):
        if self._window_sync_guard:
            return
        val = dpg.get_value(sender)
        if isinstance(val, (list, tuple)):
            val = val[0]
        self.win_min, self.win_max = self._clamp_window_bounds(float(self.win_min), float(val))
        self._window_sync_guard = True
        try:
            dpg.set_value("win_min_drag", self.win_min)
            dpg.set_value("win_max_drag", self.win_max)
            dpg.set_value("hist_min_line", self.win_min)
        finally:
            self._window_sync_guard = False
        self._request_window_refresh()
        self._save_windowing_settings_fast()

    def _cb_clear_buffer(self, sender=None, app_data=None):
        with self.frame_lock:
            self.frame_buffer.clear()
            self.display_frame = None
        self._status_msg = "Buffer cleared"

    def _cb_capture_n(self, sender=None, app_data=None):
        self.integration_n = int(dpg.get_value("integ_n_slider"))
        self._start_acquisition("capture_n")

    def _cb_capture_dark(self, sender=None, app_data=None):
        """Dark capture is owned by the dark_correction module (pipeline order)."""
        self.integration_time = self._parse_integration_time(dpg.get_value("integ_time_combo"))
        self._dark_stack_n = max(1, min(50, int(dpg.get_value("dark_stack_slider"))))
        if not self._module_enabled.get("dark_correction", False):
            self._status_msg = "Enable Dark correction module in Settings"
            return
        if self.acq_mode != "idle":
            return
        def _run():
            try:
                mod = __import__("machine_modules.dark_correction", fromlist=["capture_dark"])
                mod.capture_dark(self)
            except Exception as e:
                self.api.set_status_message(f"Dark capture error: {e}")
            finally:
                self.api.set_progress(0.0)
        threading.Thread(target=_run, daemon=True).start()

    def _cb_clear_dark(self, sender=None, app_data=None):
        gain = self._get_camera_gain()
        w, h = getattr(self, "frame_width", 0), getattr(self, "frame_height", 0)
        path = _dark_path(self.integration_time, gain, w, h, self.camera_module_name)
        self.dark_field = None
        self._dark_loaded_time_gain = None
        self._dark_nearest_time_gain = None
        if path.exists():
            path.unlink()
        self._status_msg = "Dark field cleared"
        if dpg.does_item_exist("dark_status"):
            dpg.set_value("dark_status", self._dark_status_text())
        self._update_alteration_dark_flat_status()

    def _cb_capture_flat(self, sender=None, app_data=None):
        """Flat capture is owned by the flat_correction module (pipeline order)."""
        self.integration_time = self._parse_integration_time(dpg.get_value("integ_time_combo"))
        self._flat_stack_n = max(1, min(50, int(dpg.get_value("flat_stack_slider"))))
        if not self._module_enabled.get("flat_correction", False):
            self._status_msg = "Enable Flat correction module in Settings"
            return
        if self.acq_mode != "idle":
            return
        def _run():
            try:
                mod = __import__("machine_modules.flat_correction", fromlist=["capture_flat"])
                mod.capture_flat(self)
            except Exception as e:
                self.api.set_status_message(f"Flat capture error: {e}")
            finally:
                self.api.set_progress(0.0)
        threading.Thread(target=_run, daemon=True).start()

    def _cb_clear_flat(self, sender=None, app_data=None):
        gain = self._get_camera_gain()
        w, h = getattr(self, "frame_width", 0), getattr(self, "frame_height", 0)
        path = _flat_path(self.integration_time, gain, w, h, self.camera_module_name)
        self.flat_field = None
        self._flat_loaded_time_gain = None
        self._flat_nearest_time_gain = None
        if path.exists():
            path.unlink()
        self._status_msg = "Flat field cleared"
        if dpg.does_item_exist("flat_status"):
            dpg.set_value("flat_status", self._flat_status_text())
        self._update_alteration_dark_flat_status()

    def _cb_banding_enabled(self, sender=None, app_data=None):
        self.banding_enabled = dpg.get_value("banding_enabled")
        self._status_msg = f"Banding correction: {'enabled' if self.banding_enabled else 'disabled'}"
        self._save_settings()

    def _cb_banding_auto_optimize(self, sender=None, app_data=None):
        self.banding_auto_optimize = dpg.get_value("banding_auto_optimize")
        self.banding_optimized_win = None  # Reset cache, will re-optimize on next frame
        self._status_msg = f"Banding auto-optimize: {'enabled' if self.banding_auto_optimize else 'disabled'}"
        self._save_settings()

    def _cb_banding_black_w(self, sender=None, app_data=None):
        self.banding_black_w = max(5, min(50, int(dpg.get_value("banding_black_w"))))
        self.banding_optimized_win = None  # Reset cache when parameters change
        self._save_settings()

    def _cb_banding_smooth_win(self, sender=None, app_data=None):
        self.banding_smooth_win = max(32, min(512, int(dpg.get_value("banding_smooth_win"))))
        self._save_settings()

    def _cb_vertical_banding_enabled(self, sender=None, app_data=None):
        self.vertical_banding_enabled = dpg.get_value("vertical_banding_enabled")
        self._status_msg = f"Vertical banding: {'enabled' if self.vertical_banding_enabled else 'disabled'}"
        self._save_settings()

    def _cb_vertical_stripe_h(self, sender=None, app_data=None):
        self.vertical_stripe_h = max(5, min(80, int(dpg.get_value("vertical_stripe_h"))))
        self.vertical_banding_optimized_win = None  # Reset cache when stripe height changes
        self._save_settings()

    def _cb_vertical_smooth_win(self, sender=None, app_data=None):
        self.vertical_smooth_win = max(32, min(512, int(dpg.get_value("vertical_smooth_win"))))
        self.vertical_banding_optimized_win = None  # Reset cache when manual window changes
        self._save_settings()

    def _cb_vertical_banding_auto_optimize(self, sender=None, app_data=None):
        self.vertical_banding_auto_optimize = dpg.get_value("vertical_banding_auto_optimize")
        self.vertical_banding_optimized_win = None  # Reset cache, will re-optimize on next frame
        self._status_msg = f"Vertical banding auto-optimize: {'enabled' if self.vertical_banding_auto_optimize else 'disabled'}"
        self._save_settings()

    def _cb_vertical_banding_first(self, sender=None, app_data=None):
        self.vertical_banding_first = dpg.get_value("vertical_banding_first")
        order = "vertical → horizontal" if self.vertical_banding_first else "horizontal → vertical"
        self._status_msg = f"Banding order: {order}"
        self.banding_optimized_win = None  # Reset cache when order changes
        self.vertical_banding_optimized_win = None
        self._save_settings()

    def _get_current_display_frame(self):
        """Return the frame that is currently shown (for snapshot when applying deconv)."""
        if self._display_mode == "live":
            with self.frame_lock:
                return self.display_frame.copy() if self.display_frame is not None else None
        if self._display_mode == "raw":
            return self._deconv_raw_frame.copy() if self._deconv_raw_frame is not None else None
        return self._deconv_result.copy() if self._deconv_result is not None else None

    def _get_export_frame(self):
        """
        Return frame for file export/save.
        Prefer final live pipeline output (includes downstream modules like autocrop),
        independent of temporary raw/deconvolved display modes.
        """
        with self.frame_lock:
            if self.display_frame is not None:
                return self.display_frame.copy()
        return self._get_current_display_frame()

    def _cb_export_png(self, sender=None, app_data=None):
        if self._get_export_frame() is None:
            self._status_msg = "No frame to export"
            return
        dpg.show_item("file_dialog")

    def _cb_save_tiff(self, sender=None, app_data=None):
        if self._get_export_frame() is None:
            self._status_msg = "No frame to save"
            return
        dpg.show_item("tiff_file_dialog")

    def _cb_tiff_file_selected(self, sender, app_data):
        filepath = app_data.get("file_path_name", "")
        if not filepath:
            return
        if not filepath.lower().endswith((".tif", ".tiff")):
            filepath += ".tif"
        frame = self._get_export_frame()
        if frame is None:
            self._status_msg = "No frame to save"
            return
        frame = frame.copy().astype(np.float32)
        finite = np.isfinite(frame)
        if not np.any(finite):
            self._status_msg = "TIFF save failed: frame has no finite values"
            return
        lo = float(np.min(frame[finite]))
        hi = float(np.max(frame[finite]))
        if hi <= lo:
            # Constant frame: map to black to avoid divide-by-zero.
            arr16 = np.zeros(frame.shape, dtype=np.uint16)
        else:
            # Use full 16-bit range from actual frame data (no in-frame clipping).
            safe = np.nan_to_num(frame, nan=lo, posinf=hi, neginf=lo)
            scaled = (safe - lo) / (hi - lo)
            arr16 = np.clip(np.rint(scaled * 65535.0), 0.0, 65535.0).astype(np.uint16)
        try:
            try:
                import tifffile
                tifffile.imwrite(filepath, arr16, photometric="minisblack", compression=None)
            except ImportError:
                from PIL import Image
                img = Image.fromarray(arr16, mode="I;16")
                img.save(filepath, compression=None)
                self._status_msg = f"Saved TIFF (16-bit normalized, min={lo:.3f}, max={hi:.3f}): {filepath}"
                return
            self._status_msg = f"Saved TIFF (16-bit normalized, min={lo:.3f}, max={hi:.3f}): {filepath}"
        except Exception as e:
            self._status_msg = f"TIFF save failed: {e}"

    def _cb_file_selected(self, sender, app_data):
        filepath = app_data.get("file_path_name", "")
        if not filepath:
            return
        if not filepath.lower().endswith(".png"):
            filepath += ".png"
        frame = self._get_export_frame()
        if frame is None:
            self._status_msg = "No frame to export"
            return
        frame = frame.copy()

        # Apply current windowing
        lo, hi = self.win_min, self.win_max
        if hi <= lo:
            hi = lo + 1
        normed = np.clip((frame - lo) / (hi - lo), 0, 1)
        img8 = (normed * 255).astype(np.uint8)

        try:
            from PIL import Image
            Image.fromarray(img8, mode='L').save(filepath)
            self._status_msg = f"Exported: {filepath}"
        except Exception as e:
            self._status_msg = f"Export failed: {e}"

    def _cb_mouse_wheel(self, sender, app_data):
        """Handle mouse wheel scroll for image zoom."""
        if self.image_viewport is None:
            return
        if self.image_viewport.handle_wheel(app_data):
            self._resize_image()

    def _cb_mouse_click(self, sender, app_data):
        """Handle mouse click to start drag/pan."""
        if self.image_viewport is None:
            return
        self.image_viewport.handle_click()

    def _cb_mouse_drag(self, sender, app_data):
        """Handle mouse drag to pan the image."""
        if self.image_viewport is None:
            return
        if self.image_viewport.handle_drag():
            self._resize_image()

    def _cb_mouse_release(self, sender, app_data):
        """Handle mouse release to stop drag/pan."""
        if self.image_viewport is None:
            return
        self.image_viewport.handle_release()

    # ── Build UI ────────────────────────────────────────────────────

    def _build_ui(self):
        # Frame size from selected camera module (highest camera_priority among enabled)
        camera_modules = [m for m in self._discovered_modules if m.get("type") == "camera" and self._module_enabled.get(m["name"], False)]
        camera_modules.sort(key=lambda m: m.get("camera_priority", 0), reverse=True)
        if camera_modules:
            try:
                cam_mod = __import__(f"machine_modules.{camera_modules[0]['name']}", fromlist=["get_frame_size"])
                self.frame_width, self.frame_height = cam_mod.get_frame_size()
            except Exception:
                self.frame_width, self.frame_height = DEFAULT_FRAME_W, DEFAULT_FRAME_H
        else:
            self.frame_width, self.frame_height = DEFAULT_FRAME_W, DEFAULT_FRAME_H
        self._disp_w = self.frame_width // self.disp_scale
        self._disp_h = self.frame_height // self.disp_scale
        self._aspect = self.frame_width / self.frame_height
        # Bad pixel map is per resolution; clear so module can load/build for current size
        self.bad_pixel_map_mask = None

        # Build image alteration pipeline (slot, process_frame) and distortion-only sublist for live preview
        alteration_modules = [m for m in self._discovered_modules if m.get("type") == "alteration" and self._module_enabled.get(m["name"], False)]
        alteration_modules.sort(key=lambda m: m.get("pipeline_slot", 0))
        self._alteration_pipeline = []
        self._pipeline_module_slots = {}
        for m in alteration_modules:
            try:
                mod = __import__(f"machine_modules.{m['name']}", fromlist=["process_frame"])
                pf = getattr(mod, "process_frame", None)
                if callable(pf):
                    slot = m.get("pipeline_slot", 0)
                    name = m["name"]
                    self._alteration_pipeline.append((slot, name, pf))
                    self._pipeline_module_slots[name] = slot
            except Exception:
                pass
        self._distortion_crop_pipeline = [(s, n, pf) for s, n, pf in self._alteration_pipeline if s >= self.DISTORTION_PREVIEW_SLOT]

        # Warn once if an option is set (e.g. pincushion strength) but its module is not loaded
        self.api.warn_about_unloaded_options_with_saved_values()

        # Texture registry (size from disp_scale; list for initial value)
        blank = [0.0] * (self._disp_w * self._disp_h * 4)
        with dpg.texture_registry():
            self._texture_id = dpg.add_dynamic_texture(
                width=self._disp_w, height=self._disp_h, default_value=blank
            )

        # File dialog for PNG export
        with dpg.file_dialog(
            directory_selector=False, show=False, tag="file_dialog",
            callback=self._cb_file_selected, width=600, height=400,
            default_filename="xray_frame.png"
        ):
            dpg.add_file_extension(".png")

        # File dialog for TIFF export (last taken image, 16-bit)
        with dpg.file_dialog(
            directory_selector=False, show=False, tag="tiff_file_dialog",
            callback=self._cb_tiff_file_selected, width=600, height=400,
            default_filename="xray_frame.tif"
        ):
            dpg.add_file_extension(".tif")
            dpg.add_file_extension(".tiff")

        # Mouse wheel and drag handler registry (must be global, created before window)
        with dpg.handler_registry(tag="wheel_handler_registry"):
            dpg.add_mouse_wheel_handler(callback=self._cb_mouse_wheel)
            dpg.add_mouse_click_handler(button=dpg.mvMouseButton_Left, callback=self._cb_mouse_click)
            dpg.add_mouse_drag_handler(button=dpg.mvMouseButton_Left, callback=self._cb_mouse_drag)
            dpg.add_mouse_release_handler(button=dpg.mvMouseButton_Left, callback=self._cb_mouse_release)

        # Main window
        with dpg.window(tag="primary"):
            # Menu bar
            with dpg.menu_bar():
                with dpg.menu(label="File"):
                    dpg.add_menu_item(label="Export PNG...", callback=self._cb_export_png)
                    dpg.add_menu_item(label="Save as TIFF...", callback=self._cb_save_tiff)
                with dpg.menu(label="Settings"):
                    dpg.add_menu_item(label="Settings...", callback=self._cb_show_settings)

            # Two-column layout
            with dpg.group(horizontal=True):
                # Left: image display + status
                with dpg.child_window(width=-370, tag="image_panel", no_scrollbar=True):
                    # Image area (fixed height, no scrollbar - zoom handles panning)
                    with dpg.child_window(width=-1, height=-115, tag="image_area", no_scrollbar=True):
                        dpg.add_image(self._texture_id, tag="main_image")
                    
                    # Initialize image viewport after image widget is created
                    self.image_viewport = ImageViewport("main_image")
                    self.image_viewport.aspect_ratio = self._aspect
                    # Status area at bottom (fixed height ~115px)
                    with dpg.group(tag="status_bar_group"):
                        dpg.add_separator()
                        dpg.add_text("Idle", tag="status_text")
                        dpg.add_progress_bar(default_value=0.0, tag="progress_bar", width=-1)
                        dpg.add_text("Frames: 0 | FPS: 0.0", tag="stats_text")
                        dpg.add_text("--", tag="diag_text")

                # Right: control panel
                with dpg.child_window(width=350, tag="control_panel"):
                    # ── Connection (from selected camera module or placeholder) ──
                    if camera_modules:
                        try:
                            cam_mod = __import__(f"machine_modules.{camera_modules[0]['name']}", fromlist=["build_ui"])
                            cam_mod.build_ui(self, "control_panel")
                            self.camera_module_name = camera_modules[0]["name"]
                            self._load_dark_field()
                            self._load_flat_field()
                        except Exception:
                            self.camera_module_name = None
                            with dpg.collapsing_header(label="Connection", default_open=True):
                                with dpg.group(indent=10):
                                    dpg.add_text("No camera module loaded.", color=[150, 150, 150])
                                    dpg.add_text("Enable a camera module in Settings (applies on next startup).", color=[120, 120, 120])
                    else:
                        self.camera_module_name = None
                        with dpg.collapsing_header(label="Connection", default_open=True):
                            with dpg.group(indent=10):
                                dpg.add_text("No camera module loaded.", color=[150, 150, 150])
                                dpg.add_text("Enable a camera module in Settings (applies on next startup).", color=[120, 120, 120])

                    # ── Acquisition (mode list from camera module if loaded) ──
                    with dpg.collapsing_header(label="Acquisition", default_open=True):
                        with dpg.group(indent=10):
                            if self.camera_module is not None:
                                modes = self.camera_module.get_acquisition_modes()
                                acq_items = [label for label, _ in modes]
                                self._acquisition_mode_map = {label: mode_id for label, mode_id in modes}
                            else:
                                acq_items = ["Single Shot", "Dual Shot", "Continuous", "Capture N"]
                                self._acquisition_mode_map = {
                                    "Single Shot": "single", "Dual Shot": "dual",
                                    "Continuous": "continuous", "Capture N": "capture_n",
                                }
                            saved_acq = self._loaded_settings.get("acq_mode", "Dual Shot")
                            default_acq = saved_acq if saved_acq in acq_items else (acq_items[0] if acq_items else "Dual Shot")
                            dpg.add_combo(
                                items=acq_items,
                                default_value=default_acq, tag="acq_mode_combo", width=-1,
                                callback=lambda s, a: self._save_settings()
                            )
                            integ_choices = getattr(self.camera_module, "get_integration_choices", lambda: None)()
                            if integ_choices is None:
                                integ_choices = INTEGRATION_CHOICES
                            saved_integ = self._loaded_settings.get("integ_time", "1 s")
                            default_integ = saved_integ if saved_integ in integ_choices else (integ_choices[0] if integ_choices else "1 s")
                            dpg.add_combo(
                                items=integ_choices,
                                default_value=default_integ, tag="integ_time_combo", width=-1,
                                callback=self._cb_integ_time_changed
                            )
                            dpg.add_text("(trigger interval = integration time)", color=[120, 120, 120, 255])
                            with dpg.group(horizontal=True):
                                dpg.add_button(label="Start", callback=self._cb_start, width=115)
                                dpg.add_button(label="Stop", callback=self._cb_stop, width=115)

                    # ── Integration ──
                    with dpg.collapsing_header(label="Integration", default_open=True):
                        with dpg.group(indent=10):
                            dpg.add_slider_int(
                                label="N frames", default_value=self._loaded_settings.get("integ_n", 1),
                                min_value=1, max_value=32, tag="integ_n_slider", width=-60,
                                callback=lambda s, a: self._save_settings()
                            )
                            with dpg.group(horizontal=True):
                                dpg.add_button(label="Clear Buffer", callback=self._cb_clear_buffer, width=115)
                                dpg.add_button(label="Capture N", callback=self._cb_capture_n, width=115)

                    # ── Image Controls ──
                    with dpg.collapsing_header(label="Image", default_open=True):
                        with dpg.group(indent=10):
                            # Clamp window to current camera bit depth (12/14/16) so saved values match sensor range.
                            self.win_min, self.win_max = self._clamp_window_bounds(self.win_min, self.win_max)
                            with dpg.group(horizontal=True):
                                dpg.add_button(label="Auto Window", callback=self._cb_auto_window, width=115)
                                dpg.add_checkbox(label="Hist EQ", default_value=self.hist_eq, callback=self._cb_hist_eq_toggle, tag="hist_eq_cb")
                            dpg.add_input_float(
                                label="Min", default_value=self.win_min,
                                callback=self._cb_win_min_changed, tag="win_min_drag", width=-40,
                                on_enter=True, min_value=0.0, min_clamped=True,
                                max_value=self._get_display_max_value(), max_clamped=True
                            )
                            dpg.add_input_float(
                                label="Max", default_value=self.win_max,
                                callback=self._cb_win_max_changed, tag="win_max_drag", width=-40,
                                on_enter=True, min_value=0.0, min_clamped=True,
                                max_value=self._get_display_max_value(), max_clamped=True
                            )

                    # ── Histogram ──
                    with dpg.collapsing_header(label="Histogram", default_open=True):
                        with dpg.group(indent=10):
                            with dpg.plot(
                                height=120, width=-1, tag="hist_plot",
                                no_title=True, no_mouse_pos=True,
                                no_box_select=True,
                            ):
                                dpg.add_plot_axis(
                                    dpg.mvXAxis, label="", tag="hist_x",
                                    no_tick_labels=True,
                                )
                                with dpg.plot_axis(
                                    dpg.mvYAxis, label="", tag="hist_y",
                                    no_tick_labels=True, lock_min=True, lock_max=True,
                                ):
                                    dpg.add_shade_series(
                                        [0], [0], y2=[0], tag="hist_series"
                                    )
                                # Draggable lines for min/max
                                dpg.add_drag_line(
                                    label="Min", color=[255, 100, 100, 255],
                                    default_value=self.win_min, tag="hist_min_line",
                                    callback=self._cb_hist_min_dragged
                                )
                                dpg.add_drag_line(
                                    label="Max", color=[100, 100, 255, 255],
                                    default_value=self.win_max, tag="hist_max_line",
                                    callback=self._cb_hist_max_dragged
                                )

                    # ── Dark Field (only when Dark correction module is enabled) ──
                    if self._module_enabled.get("dark_correction", False):
                        with dpg.collapsing_header(label="Dark Field", default_open=False):
                            with dpg.group(indent=10):
                                dpg.add_slider_int(
                                    label="Stack", default_value=self._dark_stack_n,
                                    min_value=1, max_value=50, tag="dark_stack_slider", width=-120,
                                    callback=lambda s, a: self._save_settings()
                                )
                                with dpg.group(horizontal=True):
                                    dpg.add_button(label="Capture Dark", callback=self._cb_capture_dark, width=115)
                                    dpg.add_button(label="Clear Dark", callback=self._cb_clear_dark, width=115)
                                dpg.add_text(self._dark_status_text(), tag="dark_status")

                    # ── Flat Field (only when Flat correction module is enabled) ──
                    if self._module_enabled.get("flat_correction", False):
                        with dpg.collapsing_header(label="Flat Field", default_open=False):
                            with dpg.group(indent=10):
                                dpg.add_slider_int(
                                    label="Stack", default_value=self._flat_stack_n,
                                    min_value=1, max_value=50, tag="flat_stack_slider", width=-120,
                                    callback=lambda s, a: self._save_settings()
                                )
                                with dpg.group(horizontal=True):
                                    dpg.add_button(label="Capture Flat", callback=self._cb_capture_flat, width=115)
                                    dpg.add_button(label="Clear Flat", callback=self._cb_clear_flat, width=115)
                                dpg.add_text(self._flat_status_text(), tag="flat_status")

                    # ── Image alteration modules (in pipeline order by slot: dark 100, flat 200, banding 300, dead_pixel 400) ──
                    alteration_for_ui = [m for m in self._discovered_modules if m.get("type") == "alteration" and self._module_enabled.get(m["name"], False)]
                    alteration_for_ui.sort(key=lambda m: m.get("pipeline_slot", 0))
                    for m in alteration_for_ui:
                        try:
                            mod = __import__(f"machine_modules.{m['name']}", fromlist=["build_ui"])
                            mod.build_ui(self, "control_panel")
                        except Exception:
                            pass

                    # ── Manual alteration modules (user-triggered, e.g. Deconvolution Apply/Revert) ──
                    for m in self._discovered_modules:
                        if m.get("type") != "manual_alteration" or not self._module_enabled.get(m["name"], False):
                            continue
                        try:
                            mod = __import__(f"machine_modules.{m['name']}", fromlist=["build_ui"])
                            mod.build_ui(self, "control_panel")
                        except Exception:
                            pass

                    # ── Machine modules (discovered; each enabled module builds its UI) ──
                    for m in self._discovered_modules:
                        if m.get("type") != "machine" or not self._module_enabled.get(m["name"], False):
                            continue
                        try:
                            mod = __import__(f"machine_modules.{m['name']}", fromlist=["build_ui"])
                            mod.build_ui(self, "control_panel")
                        except Exception:
                            pass

                    # ── Workflow automation modules (e.g. CT capture) ──
                    for m in self._discovered_modules:
                        if m.get("type") != "workflow_automation" or not self._module_enabled.get(m["name"], False):
                            continue
                        try:
                            mod = __import__(f"machine_modules.{m['name']}", fromlist=["build_ui"])
                            mod.build_ui(self, "control_panel")
                        except Exception:
                            pass

        # Settings window (menu: Settings → Settings...)
        _disp_scale_labels = {"1": "1 - Full", "2": "2 - Half", "4": "4 - Quarter"}
        with dpg.window(label="Settings", tag="settings_window", show=False, on_close=lambda: self._flush_pending_settings_save(force=True)):
            dpg.add_combo(
                label="Display scale",
                items=["1 - Full", "2 - Half", "4 - Quarter"],
                default_value=_disp_scale_labels.get(str(self.disp_scale), "1 - Full"),
                tag="disp_scale_combo",
                width=-1,
                callback=self._cb_disp_scale
            )
            dpg.add_text("Reduces display resolution (block average).", color=[150, 150, 150])
            dpg.add_spacer()
            # Group and sort modules by type: camera, alteration, manual_alteration, machine, workflow_automation
            _type_order = {"camera": 0, "alteration": 1, "manual_alteration": 2, "machine": 3, "workflow_automation": 4}
            _type_headers = {"camera": "Camera modules", "alteration": "Alteration modules", "manual_alteration": "Manual alteration modules", "machine": "Machine modules", "workflow_automation": "Workflow modules"}
            def _settings_module_sort_key(m):
                t = m.get("type", "machine")
                order = _type_order.get(t, 3)
                if t == "camera":
                    return (order, -m.get("camera_priority", 0))  # higher priority first
                if t == "alteration" or t == "manual_alteration":
                    return (order, m.get("pipeline_slot", 0))
                return (order, 0)
            _settings_modules = sorted(self._discovered_modules, key=_settings_module_sort_key)
            _last_type = None
            for m in _settings_modules:
                t = m.get("type", "machine")
                if t != _last_type:
                    _last_type = t
                    header = _type_headers.get(t, "Modules")
                    dpg.add_text(header, color=[200, 200, 200])
                    if t == "camera":
                        camera_mods = [x for x in _settings_modules if x.get("type") == "camera"]
                        _cam_items = ["None"] + [self._camera_combo_label(x) for x in camera_mods]
                        _cam_enabled = next((self._camera_combo_label(x) for x in camera_mods if self._module_enabled.get(x["name"], False)), None)
                        dpg.add_combo(
                            label="Camera module",
                            items=_cam_items,
                            default_value=_cam_enabled or "None",
                            tag="settings_camera_combo",
                            width=-1,
                            callback=self._cb_camera_module_combo
                        )
                        continue
                if t == "camera":
                    continue
                tag = f"load_module_cb_{m['name']}"
                label = f"Load {m['display_name']} module"
                if t == "alteration" or (t == "manual_alteration" and m.get("pipeline_slot", 0) != 0):
                    slot = m.get("pipeline_slot", 0)
                    label += f" (slot {slot})" if t == "alteration" else " (post-capture)"
                dpg.add_checkbox(
                    label=label,
                    default_value=self._module_enabled.get(m["name"], m.get("default_enabled", False)),
                    tag=tag,
                    callback=lambda s, a, name=m["name"]: self._cb_load_module(name)
                )
            dpg.add_spacer()
            dpg.add_text("Module load state and display scale apply on next startup.", color=[150, 150, 150])
            dpg.add_spacer()
            dpg.add_separator()
            dpg.add_text("Capture profiles", color=[200, 200, 200])
            dpg.add_text("Save current settings as a named profile, or load a profile (restart required).", color=[150, 150, 150])
            with dpg.group(horizontal=True):
                dpg.add_input_text(tag="profile_name_input", default_value="", hint="Profile name", width=-120)
                dpg.add_button(label="Save as profile", tag="profile_save_btn", callback=self._cb_save_profile, width=115)
            dpg.add_spacer()
            dpg.add_spacer()
            with dpg.group(horizontal=True):
                dpg.add_combo(tag="profile_load_combo", items=[], width=-120, callback=lambda s, a: None)
                dpg.add_button(label="Load and restart", tag="profile_load_btn", callback=self._cb_load_profile_restart, width=115)
            dpg.add_text("(Default: current settings.json; no profile file until you save one.)", color=[120, 120, 120])

    def _cb_show_settings(self, sender=None, app_data=None):
        """Open the Settings window and sync combo and module checkboxes to current values."""
        if dpg.does_item_exist("disp_scale_combo"):
            _labels = {"1": "1 - Full", "2": "2 - Half", "4": "4 - Quarter"}
            dpg.set_value("disp_scale_combo", _labels.get(str(self.disp_scale), "1 - Full"))
        # Sync camera dropdown (only one camera can be selected; fix legacy configs with multiple)
        camera_mods = [m for m in self._discovered_modules if m.get("type") == "camera"]
        if camera_mods:
            enabled_list = [m for m in camera_mods if self._module_enabled.get(m["name"], False)]
            if len(enabled_list) > 1:
                keep = max(enabled_list, key=lambda x: x.get("camera_priority", 0))
                for m in camera_mods:
                    self._module_enabled[m["name"]] = m["name"] == keep["name"]
            enabled = next((self._camera_combo_label(m) for m in camera_mods if self._module_enabled.get(m["name"], False)), None)
            if dpg.does_item_exist("settings_camera_combo"):
                dpg.set_value("settings_camera_combo", enabled or "None")
        for m in self._discovered_modules:
            if m.get("type") == "camera":
                continue
            tag = f"load_module_cb_{m['name']}"
            if dpg.does_item_exist(tag):
                dpg.set_value(tag, self._module_enabled.get(m["name"], False))
        # Refresh profile list in Load combo and set profile name field to current profile for quick save/overwrite
        if dpg.does_item_exist("profile_load_combo"):
            profiles = list_profiles()
            dpg.configure_item("profile_load_combo", items=profiles if profiles else ["(no profiles saved)"], default_value=profiles[0] if profiles else "(no profiles saved)")
        if dpg.does_item_exist("profile_name_input"):
            current = self._loaded_settings.get("current_profile", "") or ""
            dpg.set_value("profile_name_input", current)
        # Size and center: 80% of viewport width, 80% height (capped), centered
        try:
            vp_w = dpg.get_viewport_client_width()
            vp_h = dpg.get_viewport_client_height()
            win_w = max(320, int(vp_w * 0.8))
            win_h = max(300, min(int(vp_h * 0.8), 700))
            dpg.configure_item("settings_window", width=win_w, height=win_h)
            dpg.set_item_pos("settings_window", [(vp_w - win_w) // 2, (vp_h - win_h) // 2])
        except Exception:
            pass
        dpg.show_item("settings_window")
        dpg.focus_item("settings_window")

    def _camera_combo_label(self, m: dict) -> str:
        """Label for camera module in Settings dropdown: display_name (N-bit)."""
        depth = m.get("sensor_bit_depth", 12)
        return f"{m['display_name']} ({depth}-bit)"

    def _cb_camera_module_combo(self, sender=None, app_data=None):
        """One camera only: set selected module enabled, all other cameras disabled."""
        camera_mods = [m for m in self._discovered_modules if m.get("type") == "camera"]
        for m in camera_mods:
            self._module_enabled[m["name"]] = (app_data == self._camera_combo_label(m))
        self._save_settings()
        if app_data and app_data != "None":
            self._status_msg = f"Camera: {app_data} (applies on next startup)"
        else:
            self._status_msg = "No camera module selected (applies on next startup)"

    def _cb_load_module(self, name: str):
        """Persist Load <module> setting; applies on next startup."""
        tag = f"load_module_cb_{name}"
        if dpg.does_item_exist(tag):
            self._module_enabled[name] = bool(dpg.get_value(tag))
        self._save_settings()
        display = next((m["display_name"] for m in self._discovered_modules if m["name"] == name), name)
        self._status_msg = f"{display} module setting saved (applies on next startup)"

    def _cb_disp_scale(self, sender=None, app_data=None):
        """Persist display scale from Settings window; applies on next startup."""
        val = dpg.get_value("disp_scale_combo")
        if val == "2 - Half":
            self.disp_scale = 2
        elif val == "4 - Quarter":
            self.disp_scale = 4
        else:
            self.disp_scale = 1
        self._status_msg = f"Display scale set to {self.disp_scale} (applies on next startup)"
        self._save_settings()

    def _cb_save_profile(self, sender=None, app_data=None):
        """Save current settings as a named profile."""
        name = (dpg.get_value("profile_name_input") or "").strip()
        if not name:
            self._status_msg = "Enter a profile name"
            return
        try:
            s = self._get_current_settings_dict()
            save_profile(name, s, extra_keys=self._extra_settings_keys)
            set_current_profile(name)
            self._status_msg = f"Profile '{name}' saved"
            dpg.set_value("profile_name_input", name)
            profiles = list_profiles()
            if dpg.does_item_exist("profile_load_combo"):
                dpg.configure_item("profile_load_combo", items=profiles, default_value=name if name in profiles else profiles[0] if profiles else "(no profiles saved)")
        except Exception as e:
            self._status_msg = f"Save profile failed: {e}"

    def _cb_load_profile_restart(self, sender=None, app_data=None):
        """Apply selected profile to settings.json and restart the application."""
        sel = dpg.get_value("profile_load_combo")
        if not sel or sel == "(no profiles saved)":
            self._status_msg = "No profile selected"
            return
        try:
            apply_profile(sel, extra_keys=self._extra_settings_keys)
            self._status_msg = f"Profile '{sel}' applied; restarting..."
            dpg.stop_dearpygui()
            import os
            import sys
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as e:
            self._status_msg = f"Load profile failed: {e}"

    # ── Main loop ───────────────────────────────────────────────────

    def _resize_image(self):
        """Scale image to fill available space while maintaining aspect ratio, with zoom support."""
        if self.image_viewport is None:
            return
        try:
            pw, ph = dpg.get_item_rect_size("image_panel")
        except Exception:
            return
        
        # Use viewport to calculate size and UV coordinates
        img_w, img_h, uv_min, uv_max = self.image_viewport.resize(pw, ph, status_bar_height=115)
        dpg.configure_item("main_image", width=img_w, height=img_h, uv_min=uv_min, uv_max=uv_max)

    def _render_tick(self):
        """Called every frame from the render loop."""
        # On transition from acquisition to idle: optional beam supply turn-off (skip if workflow keeps HV on, e.g. CT scan)
        if self._prev_acq_mode != "idle" and self.acq_mode == "idle":
            beam = getattr(self, "beam_supply", None)
            if beam is not None and beam.wants_auto_on_off() and beam.is_connected():
                if not getattr(self, "workflow_keep_beam_on", False):
                    beam.turn_off()
            # Expose stacked/processed frame for workflow modules (e.g. request_integration / CT capture)
            with self.frame_lock:
                if self.display_frame is not None:
                    self.last_captured_frame = self.display_frame.copy()
                else:
                    self.last_captured_frame = None
            # Reset deconv snapshot so raw/deconvolved views are "no frame" until user clicks Apply on this run's frame
            self._deconv_raw_frame = None
            self._deconv_result = None
            # Reset microcontrast manual snapshot/result per completed run (same behavior as deconvolution snapshot)
            self._microcontrast_raw_frame = None
            self._microcontrast_snapshot_token = -1
            self._microcontrast_deconv_frame = None
            self._microcontrast_result = None
            self._display_mode = "live"
            # Repaint so user sees the last shot in live view (not stale raw/deconvolved)
            with self.frame_lock:
                if self.display_frame is not None:
                    self._paint_texture_from_frame(self.display_frame.copy())
        self._prev_acq_mode = self.acq_mode

        if self.new_frame_ready.is_set():
            self.new_frame_ready.clear()
            self._update_display()

        if self._window_refresh_pending:
            self._window_refresh_pending = False
            self._refresh_texture_from_settings()

        # Scale image to panel
        self._resize_image()
        # Flush pending debounced settings writes (main thread; safe for DPG access)
        self._flush_pending_settings_save(force=False)

        # Update progress bar
        dpg.set_value("progress_bar", self._progress)
        overlay = self._progress_text if self._progress_text else ""
        dpg.configure_item("progress_bar", overlay=overlay)

        # Update status text
        if self.acq_mode != "idle":
            mode_names = {
                "single": "Single Shot", "dual": "Dual Shot",
                "continuous": "Continuous", "capture_n": "Capture N",
                "dark": "Dark Capture", "flat": "Flat Capture",
            }
            status = mode_names.get(self.acq_mode, self.acq_mode)
        else:
            status = "Idle"
        if self._status_msg:
            status += f"  --  {self._status_msg}"
        dpg.set_value("status_text", status)

        # Update stats line (during dark/flat capture show collect progress; otherwise show integration buffer)
        dark_str = " | Dark: active" if self.dark_field is not None else ""
        flat_str = " | Flat: active" if self.flat_field is not None else ""
        if getattr(self, "_capture_max_slot", None) is not None:
            collect_n = len(getattr(self, "_capture_frames_collect", []))
            capture_total = getattr(self, "_capture_n", 0) or 1
            buf_n, buf_total = collect_n, capture_total
        else:
            buf_n = len(self.frame_buffer)
            buf_total = self.integration_n
        stats = f"Frames: {self.frame_count} | FPS: {self.fps:.1f} | Buffer: {buf_n}/{buf_total}{dark_str}{flat_str}"
        dpg.set_value("stats_text", stats)

        # Update capture diagnostics (last frame)
        dpg.set_value("diag_text", self._last_capture_diag or "--")

        # Update dark/flat status text (only when Dark/Flat Field sections exist)
        if dpg.does_item_exist("dark_status"):
            dpg.set_value("dark_status", self._dark_status_text())
        if dpg.does_item_exist("flat_status"):
            dpg.set_value("flat_status", self._flat_status_text())
        self._update_alteration_dark_flat_status()

        # Disable deconv Apply/Revert during capture; Revert only when we have a saved raw
        if dpg.does_item_exist("deconv_apply_btn"):
            idle = self.acq_mode == "idle"
            has_frame = (self.display_frame is not None) or (self._deconv_raw_frame is not None)
            dpg.configure_item("deconv_apply_btn", enabled=idle and has_frame)
            dpg.configure_item("deconv_revert_btn", enabled=idle and self._deconv_raw_frame is not None)

        # Machine module tick callbacks (e.g. ESP HV state refresh)
        for cb in getattr(self, "_machine_module_tick_callbacks", []):
            try:
                cb()
            except Exception:
                pass

    def run(self):
        dpg.create_context()
        self._build_ui()

        dpg.create_viewport(title="X-ray acquisition", width=1200, height=800)
        dpg.setup_dearpygui()
        dpg.show_viewport()
        dpg.set_primary_window("primary", True)

        while dpg.is_dearpygui_running():
            self._render_tick()
            dpg.render_dearpygui_frame()

        # Cleanup
        self._flush_pending_settings_save(force=True)
        self._stop_acquisition()
        if self.acq_thread and self.acq_thread.is_alive():
            self.acq_thread.join(timeout=2.0)
        if self.camera_module and self.camera_module.is_connected():
            self.camera_module.disconnect(self)
        dpg.destroy_context()


if __name__ == "__main__":
    XrayGUI().run()
