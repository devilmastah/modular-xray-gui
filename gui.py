#!/usr/bin/env python3
"""
X-ray image acquisition application.
Generic shell: image display, dark/flat, corrections, export. Imaging source and
machine hardware (Faxitron, HV supply, etc.) are loadable modules.
UI construction is delegated to the ui package (ui.build_ui, ui.constants, etc.).
"""

import sys
import os
import time
import threading
import pathlib
import numpy as np

import dearpygui.dearpygui as dpg

# Ensure app directory is on path for module imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from modules.image_processing.banding.banding_correction import (
    DEFAULT_BLACK_W,
    DEFAULT_SMOOTH_WIN,
    DEFAULT_VERTICAL_STRIPE_H,
    DEFAULT_VERTICAL_SMOOTH_WIN,
)
from lib.image_viewport import ImageViewport
from lib.settings import load_settings, save_settings, list_profiles, save_profile, apply_profile, set_current_profile
from lib.app_api import AppAPI
from modules.registry import discover_modules, all_extra_settings_keys
from ui.constants import (
    DEFAULT_FRAME_W,
    DEFAULT_FRAME_H,
    DARK_DIR,
    FLAT_DIR,
    PIXELMAPS_DIR,
    CAPTURES_DIR,
    LAST_CAPTURED_DARK_NAME,
    LAST_CAPTURED_FLAT_NAME,
    INTEGRATION_CHOICES,
    DARK_STACK_DEFAULT,
    DARK_FLAT_MATCH_THRESHOLD,
    HIST_MIN_12BIT,
    HIST_MAX_12BIT,
    dark_dir,
    flat_dir,
    pixelmaps_dir,
    dark_path,
    flat_path,
    find_nearest_dark,
    find_nearest_flat,
)
from ui import dark_flat as ui_dark_flat
from ui import settings as ui_settings
from ui import pipeline as ui_pipeline
from ui import display as ui_display
from ui import file_ops as ui_file_ops
from ui import build_ui as ui_build_ui

# Max rate for painting live view to texture (skip frames above this; no buffering)
DISPLAY_PAINT_MAX_FPS = 30


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
        self._last_display_paint_time = 0.0   # throttle live view updates to DISPLAY_PAINT_MAX_FPS (skip frames, no buffer)

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
        # File section: image opened for preview (run through pipeline → becomes processed result; Save TIF then saves it)
        self._file_preview_frame = None  # float32 (H,W) or None
        self._tiff_save_raw = False  # True when TIFF dialog was opened for "Save unprocessed TIF"

        # DPG ids (assigned in _build_ui)
        self._texture_id = None
        # Main view short preview: when True, display is not overwritten by new frames until clear_main_view_preview()
        self._main_view_preview_active = False
        # When set (e.g. from dark/flat capture worker), main thread will paint it next _render_tick
        self._pending_preview_frame = None
        self._pending_preview_use_histogram = True
        # Current preview frame (stored so histogram/windowing/hist eq apply to preview when user changes them)
        self._preview_frame = None
        self._preview_use_histogram = True  # False = raw (scale to fit, own min/max) for e.g. mask preview

        # Image viewport (zoom and pan) - will be initialized after UI is built
        self.image_viewport = None
        # Histogram zoom: when set, override axis limits in _paint_texture_from_frame
        self._hist_zoom_lo = None
        self._hist_zoom_hi = None

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
        # Last folder used for open/save file dialogs; default to app/captures when not set or invalid
        last_dir = (s.get("last_file_dialog_dir") or "").strip()
        if last_dir and pathlib.Path(last_dir).is_dir():
            self._last_file_dialog_dir = last_dir
        else:
            self._last_file_dialog_dir = str(CAPTURES_DIR)
        # Module enable flags (from registry; key = load_<name>_module)
        self._module_enabled = {}
        for m in self._discovered_modules:
            key = f"load_{m['name']}_module"
            self._module_enabled[m["name"]] = bool(s.get(key, m.get("default_enabled", False)))

    def _get_file_dialog_default_path(self) -> str:
        """Directory to open file dialogs in; defaults to app/captures if none saved or invalid."""
        return ui_file_ops.get_file_dialog_default_path(self)

    def _get_default_tiff_filename(self) -> str:
        """Default TIFF save name: dd-mm-YYYY-{exposuretime}-{gain}-{integration count}.tif"""
        return ui_file_ops.get_default_tiff_filename(self)

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
        ui_pipeline.push_frame(self, frame)

    def _request_settings_save(self, scope: str = "full", debounce_s: float = None):
        """Schedule debounced settings save; full scope overrides window-only scope."""
        ui_settings.request_save(self, scope=scope, debounce_s=debounce_s)

    def _flush_pending_settings_save(self, force: bool = False):
        """Run pending debounced save on main thread when due (or immediately when force=True)."""
        ui_settings.flush_pending_save(self, force=force)

    def _save_settings(self):
        """Debounced full settings save request."""
        self._request_settings_save(scope="full")

    def _save_settings_now(self):
        """Read current values from UI and persist to disk immediately. No-op if UI not built yet."""
        ui_settings.save_settings_now(self)

    def _save_windowing_settings_fast(self):
        """Debounced save request for lightweight windowing-only settings."""
        self._request_settings_save(scope="window")

    def _save_windowing_settings_now(self):
        """Persist only lightweight windowing settings immediately."""
        ui_settings.save_windowing_now(self)

    def _request_window_refresh(self):
        """Schedule one redraw on next render tick (avoids callback storm backlog)."""
        self._window_refresh_pending = True

    def _get_current_settings_dict(self):
        """Build the same dict as _save_settings would persist (for saving as profile). Returns dict."""
        return ui_settings.get_current_settings_dict(self)

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
        ui_dark_flat.load_dark_field(self)

    def _save_dark_field(self):
        """Save master dark for current integration time, gain and resolution in camera subfolder."""
        ui_dark_flat.save_dark_field(self)

    def _load_flat_field(self):
        """Load nearest master flat for current integration time, gain and resolution (within threshold)."""
        ui_dark_flat.load_flat_field(self)

    def _save_flat_field(self):
        """Save master flat for current integration time, gain and resolution in camera subfolder."""
        ui_dark_flat.save_flat_field(self)

    def _on_dark_flat_params_changed(self):
        """Call when integration time or gain changes so dark/flat nearest-match and status are refreshed."""
        ui_dark_flat.on_dark_flat_params_changed(self)

    def get_dark_dir(self):
        """Base directory for darks (and bad pixel map .npy) for the current camera. For use by api.get_dark_dir()."""
        return dark_dir(self.camera_module_name)

    def get_pixelmaps_dir(self):
        """Base directory for pixel map TIFFs (review) for the current camera. For use by api.get_pixelmaps_dir()."""
        return pixelmaps_dir(self.camera_module_name)

    def _dark_status_text(self):
        """Short status for dark: Loaded (Xs @ Y), None (nearest too far), or None."""
        return ui_dark_flat.dark_status_text(self)

    def _flat_status_text(self):
        """Short status for flat."""
        return ui_dark_flat.flat_status_text(self)

    # ── Frame pipeline (called by camera module via submit_raw_frame) ─────

    DISTORTION_PREVIEW_SLOT = 450  # First pipeline_slot used for distortion (pincushion, mustache, autocrop) live preview

    def _frame_log_signature(self, frame: np.ndarray):
        return ui_pipeline.frame_log_signature(self, frame)

    def _log_pipeline_step(self, context: str, token: int, slot: int, module_name: str, frame_in, frame_out):
        ui_pipeline.log_pipeline_step(self, context, token, slot, module_name, frame_in, frame_out)

    def _push_frame(self, frame):
        """Apply alteration pipeline; buffer and signal. Delegates to ui.pipeline."""
        ui_pipeline.push_frame(self, frame)

    def _get_module_incoming_image(self, module_name: str):
        return ui_pipeline.get_module_incoming_image(self, module_name)

    def _incoming_frame_for_module(self, module_name: str, frame: np.ndarray, use_cached: bool = False):
        return ui_pipeline.incoming_frame_for_module(self, module_name, frame, use_cached)

    def _get_module_incoming_token(self, module_name: str):
        return ui_pipeline.get_module_incoming_token(self, module_name)

    def _continue_pipeline_from_slot(self, frame: np.ndarray, start_slot_exclusive: int):
        return ui_pipeline.continue_pipeline_from_slot(self, frame, start_slot_exclusive)

    def _continue_pipeline_from_module(self, module_name: str, frame: np.ndarray):
        return ui_pipeline.continue_pipeline_from_module(self, module_name, frame)

    def _output_manual_from_module(self, module_name: str, frame: np.ndarray):
        return ui_pipeline.output_manual_from_module(self, module_name, frame)

    def _outgoing_frame_from_module(self, module_name: str, frame: np.ndarray):
        return ui_pipeline.outgoing_frame_from_module(self, module_name, frame)

    def request_n_frames_processed_up_to_slot(
        self, n: int, max_slot: int, timeout_seconds: float = 300.0, dark_capture: bool = False
    ):
        """Run camera capture_n for N frames with pipeline up to max_slot; return average or None."""
        return ui_pipeline.request_n_frames_processed_up_to_slot(
            self, n, max_slot, timeout_seconds, dark_capture
        )

    def _start_acquisition(self, mode):
        """Start acquisition; mode is 'single', 'dual', 'continuous', 'capture_n'."""
        if self.camera_module is None:
            self._status_msg = "No detector module loaded"
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
        """Apply windowing and convert to RGBA float32 for DPG texture. Returns (data, disp_w, disp_h)."""
        return ui_display.frame_to_texture(self, frame)

    def _scale_frame_to_fit(self, frame: np.ndarray, target_w: int, target_h: int) -> np.ndarray:
        """Scale frame to fit inside target_w x target_h (preserve aspect, letterbox)."""
        return ui_display.scale_frame_to_fit(self, frame, target_w, target_h)

    def _paint_preview_raw(self) -> None:
        """Paint _preview_frame to main view with scale-to-fit (no histogram/windowing)."""
        ui_display.paint_preview_raw(self)

    def _paint_preview_to_main_view(self, frame: np.ndarray, use_histogram: bool = True) -> None:
        """Paint a frame to the main view; sets preview mode until clear."""
        ui_display.paint_preview_to_main_view(self, frame, use_histogram)

    def _clear_main_view_preview(self) -> None:
        """Leave preview mode and repaint the normal display."""
        ui_display.clear_main_view_preview(self)

    def _dismiss_file_preview(self) -> None:
        """Clear opened-image state and preview so display shows live/buffer again. Call when starting acquisition, stop, clear buffer, or capture N."""
        self._file_preview_frame = None
        if self._main_view_preview_active:
            self._clear_main_view_preview()

    @staticmethod
    def _histogram_equalize(img):
        """Histogram equalization for display. Delegates to ui.display."""
        return ui_display.histogram_equalize(img)

    def _update_display(self):
        """Called from main thread when new_frame_ready is set. Only updates texture when showing live."""
        ui_display.update_display(self)

    def _refresh_distortion_preview(self):
        """Re-run distortion+crop steps on the last pre-distortion frame and repaint."""
        ui_display.refresh_distortion_preview(self)

    def _force_image_refresh(self):
        """Force the image widget to re-bind/redraw with the current texture (e.g. after Apply/Revert)."""
        self._resize_image()

    def _get_display_max_value(self) -> float:
        """Max display/windowing value from current camera bit depth (12/14/16-bit)."""
        return ui_display.get_display_max_value(self)

    def _clamp_window_bounds(self, lo: float, hi: float):
        return ui_display.clamp_window_bounds(self, lo, hi)

    def _get_histogram_analysis_pixels(self, frame: np.ndarray) -> np.ndarray:
        """Pixels used for histogram/auto-window stats."""
        return ui_display.get_histogram_analysis_pixels(self, frame)

    def _paint_texture_from_frame(self, frame: np.ndarray):
        """Update texture and histogram from a given frame (used for live, Apply, Revert)."""
        ui_display.paint_texture_from_frame(self, frame)

    def _refresh_texture_from_settings(self):
        """Re-render current view with new windowing settings (live, raw, deconvolved, or preview)."""
        ui_display.refresh_texture_from_settings(self)

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
        self._dismiss_file_preview()
        combo_value = dpg.get_value("acq_mode_combo")
        mode_id = getattr(self, "_acquisition_mode_map", {}).get(combo_value, "dual")
        self.integration_time = self._parse_integration_time(dpg.get_value("integ_time_combo"))
        self.integration_n = int(dpg.get_value("integ_n_slider"))
        self._start_acquisition(mode_id)

    def _cb_stop(self, sender=None, app_data=None):
        self._dismiss_file_preview()
        self._stop_acquisition()

    def _cb_auto_window(self, sender=None, app_data=None):
        if self._main_view_preview_active and self._preview_frame is not None:
            frame = self._preview_frame.copy()
        else:
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
        self._hist_zoom_lo = None
        self._hist_zoom_hi = None
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
        self._dismiss_file_preview()
        with self.frame_lock:
            self.frame_buffer.clear()
            self.display_frame = None
        self._status_msg = "Buffer cleared"

    def _cb_capture_n(self, sender=None, app_data=None):
        self._dismiss_file_preview()
        self.integration_n = int(dpg.get_value("integ_n_slider"))
        self._start_acquisition("capture_n")

    def _cb_capture_dark(self, sender=None, app_data=None):
        """Dark capture is owned by the dark_correction module (pipeline order). Runs in a thread so main thread stays responsive (HV Off, UI). See docs/INSPECTION_dark_flat_timeout_and_hv_off.md."""
        self.integration_time = self._parse_integration_time(dpg.get_value("integ_time_combo"))
        self._dark_stack_n = max(1, min(50, int(dpg.get_value("dark_stack_slider"))))
        if not self._module_enabled.get("dark_correction", False):
            self._status_msg = "Enable Dark correction module in Settings"
            return
        if self.acq_mode != "idle":
            return
        def _run():
            try:
                mod = __import__("modules.image_processing.dark_correction", fromlist=["capture_dark"])
                mod.capture_dark(self)
            except Exception as e:
                self.api.set_status_message(f"Dark capture error: {e}")
            finally:
                self.api.set_progress(0.0)
        threading.Thread(target=_run, daemon=True).start()

    def _cb_clear_dark(self, sender=None, app_data=None):
        gain = self._get_camera_gain()
        w, h = getattr(self, "frame_width", 0), getattr(self, "frame_height", 0)
        path = dark_path(self.integration_time, gain, w, h, self.camera_module_name)
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
        """Flat capture is owned by the flat_correction module (pipeline order). Runs in a thread so main thread stays responsive (HV Off, UI). See docs/INSPECTION_dark_flat_timeout_and_hv_off.md."""
        self.integration_time = self._parse_integration_time(dpg.get_value("integ_time_combo"))
        self._flat_stack_n = max(1, min(50, int(dpg.get_value("flat_stack_slider"))))
        if not self._module_enabled.get("flat_correction", False):
            self._status_msg = "Enable Flat correction module in Settings"
            return
        if self.acq_mode != "idle":
            return
        def _run():
            try:
                mod = __import__("modules.image_processing.flat_correction", fromlist=["capture_flat"])
                mod.capture_flat(self)
            except Exception as e:
                self.api.set_status_message(f"Flat capture error: {e}")
            finally:
                self.api.set_progress(0.0)
        threading.Thread(target=_run, daemon=True).start()

    def _cb_clear_flat(self, sender=None, app_data=None):
        gain = self._get_camera_gain()
        w, h = getattr(self, "frame_width", 0), getattr(self, "frame_height", 0)
        path = flat_path(self.integration_time, gain, w, h, self.camera_module_name)
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
        ui_file_ops.cb_export_png(self)

    def _cb_save_tiff(self, sender=None, app_data=None):
        ui_file_ops.cb_save_tiff(self)

    def _cb_save_raw_tiff(self, sender=None, app_data=None):
        ui_file_ops.cb_save_raw_tiff(self)

    def _cb_file_save_raw_tiff(self, sender=None, app_data=None):
        ui_file_ops.cb_save_raw_tiff(self)

    def _load_image_file_as_float32(self, path: str) -> np.ndarray:
        """Load TIFF or PNG as 2D float32, resized to current frame size. Raises on error."""
        return ui_file_ops.load_image_file_as_float32(self, path)

    def _cb_file_open_image(self, sender=None, app_data=None):
        ui_file_ops.cb_file_open_image(self)

    def _cb_open_image_file_selected(self, sender, app_data):
        ui_file_ops.cb_open_image_file_selected(self, sender, app_data)

    def _cb_file_run_through_processing(self, sender=None, app_data=None):
        ui_file_ops.cb_file_run_through_processing(self)

    def _cb_file_save_tiff(self, sender=None, app_data=None):
        ui_file_ops.cb_file_save_tiff(self)

    def _cb_tiff_file_selected(self, sender, app_data):
        ui_file_ops.cb_tiff_file_selected(self, sender, app_data)

    def _cb_file_selected(self, sender, app_data):
        ui_file_ops.cb_file_selected(self, sender, app_data)

    def _cb_mouse_wheel(self, sender, app_data):
        """Handle mouse wheel scroll: histogram zoom when over hist plot, image zoom when over image panel."""
        if self._mouse_over_histogram():
            self._cb_histogram_wheel(app_data)
            return
        if self.image_viewport is None:
            return
        if self.image_viewport.handle_wheel(app_data):
            self._resize_image()

    def _mouse_over_histogram(self) -> bool:
        """True if mouse is over the histogram plot (rect-based; is_item_hovered unreliable for plots)."""
        if not dpg.does_item_exist("hist_plot"):
            return False
        try:
            rmin = dpg.get_item_rect_min("hist_plot")
            rmax = dpg.get_item_rect_max("hist_plot")
            mx, my = dpg.get_mouse_pos(local=False)
        except Exception:
            return False
        if rmin is None or rmax is None:
            return False
        x0, y0 = rmin
        x1, y1 = rmax
        return x0 <= mx <= x1 and y0 <= my <= y1

    def _cb_histogram_wheel(self, app_data: float):
        """Zoom histogram X-axis towards mouse position when wheel scroll over hist plot."""
        try:
            axis_lo, axis_hi = dpg.get_axis_limits("hist_x")
        except Exception:
            return
        span = axis_hi - axis_lo
        if span <= 0:
            return
        try:
            rmin = dpg.get_item_rect_min("hist_plot")
            rmax = dpg.get_item_rect_max("hist_plot")
            mx, my = dpg.get_mouse_pos(local=False)
        except Exception:
            return
        if rmin is None or rmax is None:
            return
        x0, y0 = rmin
        x1, y1 = rmax
        plot_w = x1 - x0
        if plot_w <= 0:
            return
        rel_x = max(0.0, min(1.0, (mx - x0) / plot_w))
        data_x = axis_lo + rel_x * span
        zoom_factor = 1.15 if app_data > 0 else (1.0 / 1.15)
        new_span = span * zoom_factor
        dmax = self._get_display_max_value()
        new_span = max(50.0, min(dmax, new_span))
        if abs(new_span - dmax) < 1.0:
            self._hist_zoom_lo = None
            self._hist_zoom_hi = None
            self._request_window_refresh()
            return
        half = new_span / 2.0
        new_lo = data_x - half
        new_hi = data_x + half
        if new_lo < 0:
            new_lo, new_hi = 0.0, new_span
        if new_hi > dmax:
            new_lo, new_hi = dmax - new_span, dmax
        self._hist_zoom_lo = max(0.0, new_lo)
        self._hist_zoom_hi = min(dmax, new_hi)
        dpg.set_axis_limits("hist_x", self._hist_zoom_lo, self._hist_zoom_hi)

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
        """Build full UI (frame size, pipeline, texture, dialogs, main window, settings window). Delegates to ui.build_ui."""
        ui_build_ui.build_ui(self)

    def _cb_show_settings(self, sender=None, app_data=None):
        """Open the Settings window and sync combo and module checkboxes to current values."""
        if dpg.does_item_exist("disp_scale_combo"):
            _labels = {"1": "1 - Full", "2": "2 - Half", "4": "4 - Quarter"}
            dpg.set_value("disp_scale_combo", _labels.get(str(self.disp_scale), "1 - Full"))
        # Sync detector dropdown (only one detector can be selected; fix legacy configs with multiple)
        detector_mods = [m for m in self._discovered_modules if m.get("type") == "detector"]
        if detector_mods:
            enabled_list = [m for m in detector_mods if self._module_enabled.get(m["name"], False)]
            if len(enabled_list) > 1:
                keep = max(enabled_list, key=lambda x: x.get("camera_priority", 0))
                for m in detector_mods:
                    self._module_enabled[m["name"]] = m["name"] == keep["name"]
            enabled = next((self._detector_combo_label(m) for m in detector_mods if self._module_enabled.get(m["name"], False)), None)
            if dpg.does_item_exist("settings_detector_combo"):
                dpg.set_value("settings_detector_combo", enabled or "None")
        for m in self._discovered_modules:
            if m.get("type") == "detector":
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

    def _detector_combo_label(self, m: dict) -> str:
        """Label for detector module in Settings dropdown: display_name (N-bit)."""
        depth = m.get("sensor_bit_depth", 12)
        return f"{m['display_name']} ({depth}-bit)"

    def _cb_detector_module_combo(self, sender=None, app_data=None):
        """One detector only: set selected module enabled, all other detectors disabled."""
        detector_mods = [m for m in self._discovered_modules if m.get("type") == "detector"]
        for m in detector_mods:
            self._module_enabled[m["name"]] = (app_data == self._detector_combo_label(m))
        self._save_settings()
        if app_data and app_data != "None":
            self._status_msg = f"Detector: {app_data} (applies on next startup)"
        else:
            self._status_msg = "No detector module selected (applies on next startup)"

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
        # Paint any preview requested from a worker (e.g. dark/flat capture) on main thread
        with self.frame_lock:
            pending = self._pending_preview_frame
            use_hist = getattr(self, "_pending_preview_use_histogram", True)
            self._pending_preview_frame = None
            self._pending_preview_use_histogram = True
        if pending is not None:
            self._paint_preview_to_main_view(pending, use_histogram=use_hist)

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
            now = time.time()
            if now - self._last_display_paint_time >= (1.0 / DISPLAY_PAINT_MAX_FPS):
                self._update_display()
                self._last_display_paint_time = now

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

        # File section: enable Save TIF when processed result exists; Save unprocessed TIF when raw frame exists
        if dpg.does_item_exist("file_save_tiff_btn"):
            dpg.configure_item("file_save_tiff_btn", enabled=(self._get_export_frame() is not None))
        if dpg.does_item_exist("file_save_raw_tiff_btn"):
            with self.frame_lock:
                has_raw = self.raw_frame is not None
            dpg.configure_item("file_save_raw_tiff_btn", enabled=has_raw)

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
