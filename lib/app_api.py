"""
Application API for machine modules.

Single facade with clear names for all operations modules need.
Use gui.api.xxx() instead of gui.xxx so the contract is explicit and stable.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional
import numpy as np


class AppAPI:
    """
    Clear API for modules. All frame, acquisition, settings, and workflow
    operations go through this so names are consistent and the contract is documented.
    """

    def __init__(self, gui: Any) -> None:
        self._gui = gui

    # ─── Frames ─────────────────────────────────────────────────────────────

    def submit_frame(self, frame: np.ndarray) -> None:
        """Submit one raw frame for processing (pipeline, buffer, display). Call from acquisition thread."""
        self._gui.submit_raw_frame(frame)

    def clear_frame_buffer(self) -> None:
        """Clear the frame buffer and display so the next submitted frame(s) are the only content."""
        self._gui.clear_frame_buffer()

    def request_integration(
        self, num_frames: int, timeout_seconds: float = 300.0
    ) -> Optional[np.ndarray]:
        """
        Run one integration (same as Start/Capture N). Blocks until done.
        Returns processed frame (float32) or None. Call from workflow thread.
        """
        return self._gui.request_integration(num_frames, timeout_seconds)

    def get_last_integration_fail_reason(self) -> Optional[str]:
        """After request_integration() returns None: 'timeout', 'stopped', 'no_frame', 'not_connected', 'not_idle', 'supply_not_connected'."""
        return getattr(self._gui, "_last_integration_fail_reason", None)

    def request_n_frames_processed_up_to_slot(
        self, n: int, max_slot: int, timeout_seconds: float = 300.0, dark_capture: bool = False
    ) -> Optional[np.ndarray]:
        """
        Run camera capture for N frames with pipeline run only for steps with slot < max_slot;
        returns the average (float32) or None. For use by dark/flat modules when capturing reference.
        dark_capture=True keeps beam off (for dark). max_slot e.g. 100 = dark (raw), 200 = flat (dark applied).
        """
        return self._gui.request_n_frames_processed_up_to_slot(
            n, max_slot, timeout_seconds, dark_capture
        )

    # ─── Acquisition (camera worker) ─────────────────────────────────────────

    def acquisition_should_stop(self) -> bool:
        """True if the user requested stop. Check in your acquisition loop."""
        return self._gui.acq_stop.is_set()

    def get_acquisition_mode(self) -> str:
        """Current mode: 'single', 'dual', 'continuous', 'capture_n', 'dark', 'flat'."""
        return self._gui.acq_mode

    def get_integration_time_seconds(self) -> float:
        """Exposure/integration time in seconds."""
        return self._gui.integration_time

    def get_integration_frame_count(self) -> int:
        """Number of frames to stack (Capture N)."""
        return self._gui.integration_n

    def get_dark_capture_stack_count(self) -> int:
        """Number of frames to average for dark capture."""
        return getattr(self._gui, "_dark_stack_n", 20)

    def get_flat_capture_stack_count(self) -> int:
        """Number of frames to average for flat capture."""
        return getattr(self._gui, "_flat_stack_n", 20)

    def set_acquisition_idle(self) -> None:
        """Call when your acquisition worker has finished (sets acq_mode idle, clears progress)."""
        self._gui.acq_mode = "idle"
        self._gui._progress = 0.0
        self._gui._progress_text = ""

    def set_acquisition_thread(self, thread: Any) -> None:
        """Set the current acquisition thread (so app can join on exit)."""
        self._gui.acq_thread = thread

    def clear_acquisition_stop_flag(self) -> None:
        """Clear the stop event before starting acquisition."""
        self._gui.acq_stop.clear()

    def signal_acquisition_stop(self) -> None:
        """Request the acquisition worker to stop."""
        self._gui.acq_stop.set()

    # ─── Progress & status ──────────────────────────────────────────────────

    def set_progress(self, value: float, text: Optional[str] = None) -> None:
        """Set progress bar (0.0–1.0) and optional overlay text."""
        self._gui._progress = value
        if text is not None:
            self._gui._progress_text = text

    def set_status_message(self, msg: str) -> None:
        """Set the main status bar message."""
        self._gui._status_msg = msg

    # ─── Dark / flat ────────────────────────────────────────────────────────

    def get_dark_field(self) -> Optional[np.ndarray]:
        """Current dark reference (or None)."""
        return self._gui.dark_field

    def get_flat_field(self) -> Optional[np.ndarray]:
        """Current flat reference (or None)."""
        return self._gui.flat_field

    def set_dark_field(self, arr: np.ndarray) -> None:
        """Set dark reference (e.g. after dark capture). Use from worker with frame_lock."""
        with self._gui.frame_lock:
            self._gui.dark_field = arr

    def set_flat_field(self, arr: np.ndarray) -> None:
        """Set flat reference (e.g. after flat capture). Use from worker with frame_lock."""
        with self._gui.frame_lock:
            self._gui.flat_field = arr

    def save_dark_field(self) -> None:
        """Persist current dark to disk (call after set_dark_field)."""
        self._gui._save_dark_field()

    def save_flat_field(self) -> None:
        """Persist current flat to disk (call after set_flat_field)."""
        self._gui._save_flat_field()

    def get_camera_module_name(self) -> Optional[str]:
        """Current camera module name (e.g. 'asi_camera', 'hamamatsu_c7942') for darks/flats/bad-pixel-map paths. None if none selected."""
        return getattr(self._gui, "camera_module_name", None)

    def get_dark_dir(self) -> Path:
        """Base directory for darks (and bad pixel map .npy) for the current camera. Subfolder under app darks/."""
        return self._gui.get_dark_dir()

    def get_pixelmaps_dir(self) -> Path:
        """Base directory for pixel map TIFFs (review) for the current camera. Subfolder under app pixelmaps/."""
        return self._gui.get_pixelmaps_dir()

    def trigger_dark_flat_reload(self) -> None:
        """Reload dark/flat nearest match and update status (e.g. after ROI or gain change)."""
        getattr(self._gui, "_on_dark_flat_params_changed", lambda: None)()

    def get_frame_size(self) -> tuple[int, int]:
        """(width, height) of current frame. Set by camera on connect/ROI change."""
        return (
            getattr(self._gui, "frame_width", 0),
            getattr(self._gui, "frame_height", 0),
        )

    def set_frame_size(self, width: int, height: int) -> None:
        """Set frame dimensions (camera calls on connect or ROI change)."""
        self._gui.frame_width = width
        self._gui.frame_height = height

    def get_frame_lock(self) -> Any:
        """Lock to hold when reading/writing dark_field or flat_field from a worker."""
        return self._gui.frame_lock

    # ─── Dead pixel (for dark/flat capture and pipeline) ────────────────────

    def dead_pixel_correction_enabled(self) -> bool:
        return getattr(self._gui, "dead_lines_enabled", True)

    def get_dead_pixel_lines(self) -> tuple[list[int], list[int]]:
        """(vertical_lines, horizontal_lines) for correction."""
        return (
            getattr(self._gui, "dead_vertical_lines", []),
            getattr(self._gui, "dead_horizontal_lines", []),
        )

    # ─── Settings ───────────────────────────────────────────────────────────

    def get_loaded_settings(self) -> dict:
        """Loaded settings dict (for default_value in UI)."""
        return getattr(self._gui, "_loaded_settings", {}) or {}

    def get_setting(self, key: str, default: Any = None) -> Any:
        """Single setting from loaded dict (for default_value when building UI)."""
        return self.get_loaded_settings().get(key, default)

    def get_module_settings_for_save(
        self,
        spec: list[tuple[str, str, Any, Any]],
    ) -> dict[str, Any]:
        """
        Build a settings dict from DPG widgets or loaded settings (auto fallback when UI not built).

        spec: list of (key, tag, converter, default).
        - key: settings key to persist (e.g. "asi_gain").
        - tag: DPG item tag (e.g. "asi_gain_slider").
        - converter: callable(value) -> stored value (e.g. int, float, lambda x: bool(x)).
        - default: value if widget missing and key not in loaded (and if conversion fails).

        For each entry: if the widget exists, use dpg.get_value(tag) and apply converter;
        otherwise use loaded.get(key, default) and apply converter. Preserves saved values
        when the module's UI is not built (e.g. module disabled).
        """
        import dearpygui.dearpygui as dpg

        loaded = self.get_loaded_settings()
        out: dict[str, Any] = {}

        def _convert(conv: Any, val: Any, default: Any) -> Any:
            if conv is None:
                return val if val is not None else default
            try:
                return conv(val)
            except (TypeError, ValueError):
                return default

        for key, tag, converter, default in spec:
            if dpg.does_item_exist(tag):
                raw = dpg.get_value(tag)
                out[key] = _convert(converter, raw, default)
            else:
                raw = loaded.get(key, default)
                out[key] = _convert(converter, raw, default)
        return out

    def save_settings(self) -> None:
        """Persist current UI state to settings.json."""
        self._gui._save_settings()

    # ─── Workflow ───────────────────────────────────────────────────────────

    def set_workflow_keep_beam_on(self, value: bool) -> None:
        """When True, app does not turn beam on/off per capture (e.g. CT Keep HV on)."""
        self._gui.workflow_keep_beam_on = value

    def get_beam_supply(self) -> Any:
        """Optional beam supply adapter (turn_on_and_wait_ready, turn_off, etc.)."""
        return getattr(self._gui, "beam_supply", None)

    def get_camera_module(self) -> Any:
        """Current camera module (or None)."""
        return getattr(self._gui, "camera_module", None)

    def get_camera_uses_dual_shot_for_capture_n(self) -> bool:
        """True if the active camera does 2 exposures per frame in capture_n (e.g. C7942). Use to double dark/flat timeout."""
        cam = self.get_camera_module()
        if cam is None:
            return False
        fn = getattr(cam, "uses_dual_shot_for_capture_n", None)
        return bool(fn() if callable(fn) else False)

    def is_camera_connected(self) -> bool:
        """True if a camera module is loaded and connected."""
        cam = self.get_camera_module()
        return cam is not None and cam.is_connected()

    def get_sensor_bit_depth(self) -> int:
        """
        Sensor bit depth from the active camera module (for display/windowing range).
        Returns 12, 14, or 16. Default 12 if no camera or module does not implement it.
        """
        cam = self.get_camera_module()
        if cam is None:
            return 12
        get_bd = getattr(cam, "get_sensor_bit_depth", None)
        if get_bd is None or not callable(get_bd):
            return 12
        bd = int(get_bd())
        return max(12, min(16, bd)) if bd in (12, 14, 16) else 12

    def get_display_max_value(self) -> float:
        """Max display/windowing value (2**bit_depth - 1) from current camera. Used for histogram and Min/Max range."""
        return float((1 << self.get_sensor_bit_depth()) - 1)

    # ─── Registration (modules register themselves with the app) ────────────

    def register_camera_module(self, module: Any) -> None:
        """Register the active camera module."""
        self._gui.camera_module = module

    def register_beam_supply(self, adapter: Any) -> None:
        """Register optional beam supply for Auto On/Off."""
        self._gui.beam_supply = adapter

    # ─── Module load state (warn when an option is used but its module is not loaded) ─

    def is_module_loaded(self, module_name: str) -> bool:
        """True if the given module is enabled in Settings (e.g. 'pincushion', 'banding', 'dark_correction')."""
        return bool(getattr(self._gui, "_module_enabled", {}).get(module_name, False))

    def warn_if_option_used_but_module_not_loaded(
        self, module_name: str, option_description: Optional[str] = None
    ) -> bool:
        """
        Call before using an option that depends on a module (e.g. pincushion params).
        Returns True if the module is loaded (safe to use). Returns False if the module
        is not loaded; in that case sets a one-time status warning and returns False.
        Use: if not api.warn_if_option_used_but_module_not_loaded("pincushion", "pincushion correction"): return
        """
        if self.is_module_loaded(module_name):
            return True
        warned = getattr(self._gui, "_api_unloaded_warned", None)
        if warned is None:
            warned = set()
            self._gui._api_unloaded_warned = warned
        if module_name not in warned:
            warned.add(module_name)
            hint = option_description or module_name.replace("_", " ")
            self._gui._status_msg = (
                f"Warning: {hint} is set or in use but the '{module_name}' module is not loaded. "
                "Enable it in Settings (applies on next startup)."
            )
        return False

    def warn_about_unloaded_options_with_saved_values(self) -> None:
        """
        Call once after loading settings or building UI. If a module is not loaded
        but its option(s) have non-default values (e.g. pincushion_strength > 0),
        sets a one-time status warning so the user knows to enable the module or clear the option.
        """
        # Map module_name -> (attr_or_key, default_value) for "option is on" check
        checks: list[tuple[str, str, Any]] = [
            ("pincushion", "pincushion_strength", 0.0),
            ("mustache", "mustache_k1", 0.0),
        ]
        for module_name, attr, default in checks:
            if self.is_module_loaded(module_name):
                continue
            val = getattr(self._gui, attr, default)
            try:
                is_non_default = val != default
            except Exception:
                is_non_default = True
            if is_non_default:
                self.warn_if_option_used_but_module_not_loaded(
                    module_name, option_description=module_name.replace("_", " ") + " correction"
                )
                break  # one warning at a time

    # ─── Display (manual alteration, e.g. deconvolution) ────────────────────

    def get_current_display_frame(self) -> Optional[np.ndarray]:
        """Current frame shown in the display (for Apply to frame)."""
        return self._gui._get_current_display_frame()

    def paint_frame_to_display(self, frame: np.ndarray) -> None:
        """Paint a frame to the texture (e.g. after deconvolution)."""
        self._gui._paint_texture_from_frame(frame)

    def show_preview_in_main_view(self, frame: np.ndarray, use_histogram: bool = True) -> None:
        """
        Show a short preview in the main view.
        use_histogram=True: apply windowing/histogram/hist eq (good for dark/flat). False: raw display
        (scale to fit, normalize by frame min/max; good for masks to avoid washed-out look).
        New frames will not overwrite the preview until clear_main_view_preview() is called.
        Safe to call from a worker thread: the paint is deferred to the main thread.
        """
        with self._gui.frame_lock:
            self._gui._pending_preview_frame = np.asarray(frame, dtype=np.float32).copy()
            self._gui._pending_preview_use_histogram = use_histogram

    def clear_main_view_preview(self) -> None:
        """Leave main-view preview mode and restore the normal display (live/raw/deconvolved)."""
        self._gui._clear_main_view_preview()

    def refresh_display(self) -> None:
        """Force display refresh."""
        self._gui._force_image_refresh()

    def set_display_mode(self, mode: str) -> None:
        """'live', 'raw', 'deconvolved', etc."""
        self._gui._display_mode = mode

    # ─── Generic pipeline helpers (for manual alteration modules) ────────────

    def get_module_incoming_image(self, module_name: str) -> Optional[np.ndarray]:
        """
        Cached incoming image for a module (frame before that module's process_frame ran).
        Returns a copy or None.
        """
        return self._gui._get_module_incoming_image(module_name)

    def get_module_incoming_token(self, module_name: str) -> Optional[int]:
        """
        Token for cached incoming image (increments each new frame). Useful to detect new source frame.
        """
        return self._gui._get_module_incoming_token(module_name)

    def continue_pipeline_from_slot(self, frame: np.ndarray, start_slot_exclusive: int) -> np.ndarray:
        """Run remaining alteration steps with slot > start_slot_exclusive."""
        return self._gui._continue_pipeline_from_slot(frame, start_slot_exclusive)

    def continue_pipeline_from_module(self, module_name: str, frame: np.ndarray) -> np.ndarray:
        """Run remaining alteration steps after the given module's slot."""
        return self._gui._continue_pipeline_from_module(module_name, frame)

    def output_manual_from_module(self, module_name: str, frame: np.ndarray) -> np.ndarray:
        """
        For manual operations: continue downstream pipeline from module slot, then paint to display.
        Returns final displayed frame.
        """
        return self._gui._output_manual_from_module(module_name, frame)

    def incoming_frame(self, module_name: str, frame: np.ndarray, use_cached: bool = False) -> np.ndarray:
        """
        Canonical module input accessor.
        - use_cached=False: use frame passed by current pipeline step
        - use_cached=True: prefer cached module incoming frame when available
        """
        return self._gui._incoming_frame_for_module(module_name, frame, use_cached=use_cached)

    def outgoing_frame(self, module_name: str, frame: np.ndarray) -> np.ndarray:
        """
        Canonical module output accessor.
        Returns frame to pass to next pipeline step.
        """
        return self._gui._outgoing_frame_from_module(module_name, frame)

    # ─── Pipeline state (alteration modules read these in process_frame) ─────
    # Alteration modules receive (frame, gui); they can use api.get_dark_field() etc.
    # or keep reading gui.xxx for now. We expose the main ones above.
    # For backward compatibility, modules can still use gui.xxx; we add api getters
    # so new code and migrated code use clear names.

    def get_banding_enabled(self) -> bool:
        return getattr(self._gui, "banding_enabled", True)

    def get_vertical_banding_enabled(self) -> bool:
        return getattr(self._gui, "vertical_banding_enabled", True)

    def get_vertical_banding_first(self) -> bool:
        return getattr(self._gui, "vertical_banding_first", False)

    def get_vertical_stripe_h(self) -> int:
        return getattr(self._gui, "vertical_stripe_h", 20)

    def get_vertical_smooth_win(self) -> int:
        return getattr(self._gui, "vertical_smooth_win", 128)

    def get_banding_smooth_win(self) -> int:
        return getattr(self._gui, "banding_smooth_win", 128)

    def get_banding_black_w(self) -> int:
        return getattr(self._gui, "banding_black_w", 40)

    def get_banding_auto_optimize(self) -> bool:
        return getattr(self._gui, "banding_auto_optimize", False)

    def get_vertical_banding_auto_optimize(self) -> bool:
        return getattr(self._gui, "vertical_banding_auto_optimize", False)

    def get_banding_optimized_win(self):
        """Cached optimized smooth window for horizontal banding (or None). Pipeline modules use this via API."""
        return getattr(self._gui, "banding_optimized_win", None)

    def set_banding_optimized_win(self, value) -> None:
        self._gui.banding_optimized_win = value

    def get_vertical_banding_optimized_win(self):
        """Cached optimized smooth window for vertical banding (or None). Pipeline modules use this via API."""
        return getattr(self._gui, "vertical_banding_optimized_win", None)

    def set_vertical_banding_optimized_win(self, value) -> None:
        self._gui.vertical_banding_optimized_win = value

    def get_crop_region(self) -> tuple[int, int, int, int]:
        """(x_start, y_start, x_end, y_end)."""
        return (
            getattr(self._gui, "crop_x_start", 0),
            getattr(self._gui, "crop_y_start", 0),
            getattr(self._gui, "crop_x_end", 0),
            getattr(self._gui, "crop_y_end", 0),
        )

    def get_pincushion_params(self) -> tuple[float, float, float]:
        """(strength, center_x, center_y). If using this to apply correction, call warn_if_option_used_but_module_not_loaded('pincushion') first."""
        return (
            getattr(self._gui, "pincushion_strength", 0.0),
            getattr(self._gui, "pincushion_center_x", -1.0),
            getattr(self._gui, "pincushion_center_y", -1.0),
        )

    def get_mustache_params(self) -> tuple[float, float, float, float]:
        """(k1, k2, center_x, center_y)."""
        return (
            getattr(self._gui, "mustache_k1", 0.0),
            getattr(self._gui, "mustache_k2", 0.0),
            getattr(self._gui, "mustache_center_x", -1.0),
            getattr(self._gui, "mustache_center_y", -1.0),
        )

    def get_deconv_sigma(self) -> float:
        return getattr(self._gui, "deconv_sigma", 1.0)

    def get_deconv_iterations(self) -> int:
        return getattr(self._gui, "deconv_iterations", 10)

    def set_deconv_sigma(self, value: float) -> None:
        self._gui.deconv_sigma = value

    def set_deconv_iterations(self, value: int) -> None:
        self._gui.deconv_iterations = value

    # ─── Reusable "Apply automatically" + Apply / Revert for alteration modules ─
    def build_alteration_apply_revert_ui(
        self,
        gui: Any,
        module_name: str,
        apply_callback: Callable[[Any], None],
        *,
        auto_apply_attr: str,
        revert_snapshot_attr: Optional[str] = None,
        default_auto_apply: bool = True,
    ) -> None:
        """
        Add 'Apply automatically' checkbox and Apply / Revert buttons to the current DPG container.
        Call from inside your module's build_ui (e.g. inside a dpg.group).

        - module_name: MODULE_NAME for get_module_incoming_image / output_manual_from_module.
        - apply_callback: callable(gui). Called when Apply is clicked. It should:
          get_module_incoming_image(module_name), set gui.<revert_snapshot_attr> = raw.copy(),
          apply your step, then output_manual_from_module(module_name, out).
        - auto_apply_attr: gui attribute for the checkbox (e.g. 'dark_correction_auto_apply').
          Include this key in get_setting_keys() and get_settings_for_save() so it persists.
        - revert_snapshot_attr: gui attribute for snapshot from last Apply (e.g. '_dark_correction_revert_snapshot').
        """
        import dearpygui.dearpygui as dpg

        loaded = self.get_loaded_settings()
        setattr(gui, auto_apply_attr, loaded.get(auto_apply_attr, default_auto_apply))
        if revert_snapshot_attr is not None:
            setattr(gui, revert_snapshot_attr, None)

        def _cb_auto(sender: Any = None, app_data: Any = None) -> None:
            setattr(gui, auto_apply_attr, bool(dpg.get_value(auto_apply_attr)))
            self.save_settings()

        def _cb_revert(sender: Any = None, app_data: Any = None) -> None:
            incoming = self.get_module_incoming_image(module_name)
            raw = incoming if incoming is not None else getattr(gui, revert_snapshot_attr, None)
            if raw is None:
                self.set_status_message("No frame available (run acquisition first).")
                return
            self.output_manual_from_module(module_name, raw.copy())

        dpg.add_checkbox(
            label="Apply automatically",
            default_value=getattr(gui, auto_apply_attr),
            tag=auto_apply_attr,
            callback=_cb_auto,
        )
        with dpg.group(horizontal=True):
            dpg.add_button(label="Apply", callback=lambda: apply_callback(gui), width=100)
            dpg.add_button(label="Revert", callback=_cb_revert, width=100)
        dpg.add_separator()

    def alteration_auto_apply(self, gui: Any, auto_apply_attr: str, default: bool = True) -> bool:
        """
        Return whether the alteration step should run in the pipeline (for process_frame guard).
        When False, process_frame should return frame unchanged.
        """
        return bool(getattr(gui, auto_apply_attr, default))

    # ─── Internal ref (for callbacks that must call back into gui) ───────────
    # Some DPG callbacks are bound to gui methods (e.g. gui._cb_banding_enabled).
    # Modules that need to pass such callbacks can use api.gui to get the raw gui.
    @property
    def gui(self) -> Any:
        """Raw gui reference for callbacks that must be gui methods. Prefer API methods elsewhere."""
        return self._gui
