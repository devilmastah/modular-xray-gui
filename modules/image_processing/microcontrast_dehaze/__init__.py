"""
Microcontrast / Dehaze module.

Provides Lightroom-like "Clarity" (local midtone contrast) and "Dehaze"
enhancement with:
  - Manual "Apply to current frame" button
  - Optional auto-apply during workflow captures (request_integration)
"""

import numpy as np
import time
try:
    from skimage.restoration import richardson_lucy
    _DECONV_AVAILABLE = True
except ImportError:
    richardson_lucy = None
    _DECONV_AVAILABLE = False

try:
    from scipy.ndimage import gaussian_filter
    _HAS_SCIPY = True
except Exception:
    gaussian_filter = None
    _HAS_SCIPY = False


def is_deconv_available() -> bool:
    """Return True if scikit-image is installed and deconvolution can run."""
    return _DECONV_AVAILABLE


def gaussian_psf_2d(sigma: float, size: int = None) -> np.ndarray:
    """Create a 2D Gaussian point-spread function (normalized)."""
    if size is None:
        # ~3 sigma each side
        size = max(3, int(round(sigma * 6)) | 1)
    else:
        size = max(3, int(size) | 1)
    ax = np.arange(size) - (size - 1) / 2.0
    xx, yy = np.meshgrid(ax, ax)
    psf = np.exp(-(xx ** 2 + yy ** 2) / (2 * sigma ** 2))
    psf /= psf.sum()
    return psf.astype(np.float32)


def deconvolve_richardson_lucy(
    img: np.ndarray,
    sigma: float = 1.0,
    iterations: int = 10,
    clip_output: bool = True,
) -> np.ndarray:
    """
    Deconvolve image using Richardson-Lucy with a Gaussian PSF.
    """
    if not _DECONV_AVAILABLE:
        return img.copy()

    img = img.astype(np.float32)
    lo, hi = float(np.min(img)), float(np.max(img))
    if hi <= lo:
        return img.copy()

    scale = hi - lo
    img_norm = (img - lo) / scale

    psf = gaussian_psf_2d(sigma)
    out = richardson_lucy(img_norm, psf, num_iter=iterations, clip=False)

    out = out * scale + lo
    if clip_output:
        out = np.clip(out, lo, hi)
    return out.astype(np.float32)


MODULE_INFO = {
    "display_name": "Image Enhancement",
    "description": "Deconvolution + clarity/dehaze enhancement (manual and workflow auto modes).",
    "type": "image_processing",
    "default_enabled": True,
    # Keep room before autocrop for future modules while remaining near-final.
    "pipeline_slot": 480,
}
MODULE_NAME = "microcontrast_dehaze"


def get_setting_keys():
    return [
        "microcontrast_auto_apply",
        "microcontrast_clarity",
        "microcontrast_dehaze",
        "microcontrast_auto_workflow",
        "microcontrast_auto_deconv_workflow",
        "microcontrast_deconv_sigma",
        "microcontrast_deconv_iterations",
        "microcontrast_live_preview",
    ]


def get_default_settings():
    return {
        "microcontrast_auto_apply": False,
        "microcontrast_clarity": 0.0,
        "microcontrast_dehaze": 0.0,
        "microcontrast_auto_workflow": False,
        "microcontrast_auto_deconv_workflow": False,
        "microcontrast_deconv_sigma": 1.0,
        "microcontrast_deconv_iterations": 10,
        "microcontrast_live_preview": True,
    }


def get_settings_for_save(gui=None):
    import dearpygui.dearpygui as dpg
    if dpg.does_item_exist("microcontrast_clarity"):
        return {
            "microcontrast_auto_apply": bool(dpg.get_value("microcontrast_auto_apply")) if dpg.does_item_exist("microcontrast_auto_apply") else False,
            "microcontrast_clarity": float(dpg.get_value("microcontrast_clarity")),
            "microcontrast_dehaze": float(dpg.get_value("microcontrast_dehaze")),
            "microcontrast_auto_workflow": bool(dpg.get_value("microcontrast_auto_workflow")),
            "microcontrast_auto_deconv_workflow": bool(dpg.get_value("microcontrast_auto_deconv_workflow")),
            "microcontrast_deconv_sigma": float(dpg.get_value("microcontrast_deconv_sigma")),
            "microcontrast_deconv_iterations": int(dpg.get_value("microcontrast_deconv_iterations")),
            "microcontrast_live_preview": bool(dpg.get_value("microcontrast_live_preview")),
        }
    if gui is not None:
        return {
            "microcontrast_auto_apply": bool(getattr(gui, "microcontrast_auto_apply", False)),
            "microcontrast_clarity": float(getattr(gui, "_microcontrast_clarity", 0.0)),
            "microcontrast_dehaze": float(getattr(gui, "_microcontrast_dehaze", 0.0)),
            "microcontrast_auto_workflow": bool(getattr(gui, "_microcontrast_auto_workflow", False)),
            "microcontrast_auto_deconv_workflow": bool(getattr(gui, "_microcontrast_auto_deconv_workflow", False)),
            "microcontrast_deconv_sigma": float(getattr(gui, "_microcontrast_deconv_sigma", 1.0)),
            "microcontrast_deconv_iterations": int(getattr(gui, "_microcontrast_deconv_iterations", 10)),
            "microcontrast_live_preview": bool(getattr(gui, "_microcontrast_live_preview", True)),
        }
    return {}


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _box_blur_fallback(img: np.ndarray) -> np.ndarray:
    """Small, dependency-free blur fallback if SciPy is unavailable."""
    acc = img
    acc = (acc + np.roll(acc, 1, axis=0) + np.roll(acc, -1, axis=0)) / 3.0
    acc = (acc + np.roll(acc, 1, axis=1) + np.roll(acc, -1, axis=1)) / 3.0
    return acc


def _blur(img: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian-like blur (SciPy when available, multi-pass fallback otherwise)."""
    if _HAS_SCIPY:
        return gaussian_filter(img, sigma=float(sigma))
    # Approximate larger sigma with repeated lightweight passes.
    passes = max(1, int(round(float(sigma) * 1.8)))
    out = np.asarray(img, dtype=np.float32)
    for _ in range(passes):
        out = _box_blur_fallback(out)
    return out


def _enhance(frame: np.ndarray, clarity_amount: float, dehaze_amount: float) -> np.ndarray:
    """
    Apply local clarity + global dehaze-like enhancement.
    Input/output are float32 in the original frame scale.
    """
    arr = np.asarray(frame, dtype=np.float32)
    finite = np.isfinite(arr)
    if not finite.any():
        return arr

    lo = float(np.percentile(arr[finite], 0.5))
    hi = float(np.percentile(arr[finite], 99.5))
    if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
        return arr

    norm = np.clip((arr - lo) / (hi - lo), 0.0, 1.0).astype(np.float32)

    # Extended range for X-ray use: allow stronger-than-Adobe clarity.
    clarity = _clamp(float(clarity_amount) / 100.0, -3.0, 3.0)
    dehaze = _clamp(float(dehaze_amount) / 100.0, 0.0, 1.0)

    if dehaze > 0.0:
        # Gentler dehaze curve:
        # keep low values subtle and ramp effect toward higher slider values.
        air = float(np.percentile(norm, 99.7))
        air = max(air, 1e-3)
        base = np.clip(norm / air, 0.0, 1.0)
        strength = (dehaze ** 1.35) * 0.45
        t = np.clip(1.0 - strength * (1.0 - base), 0.60, 1.0)
        dehazed = np.clip((base - (1.0 - t)) / t, 0.0, 1.0)
        norm = ((1.0 - strength) * norm + strength * dehazed).astype(np.float32)

    if abs(clarity) > 1e-6:
        # Clarity-like local contrast:
        # - target midtones strongly
        # - use mostly medium-frequency detail (not fine noise)
        # - reduce halos near strongest edges
        blur_small = _blur(norm, sigma=1.2)
        blur_large = _blur(norm, sigma=3.2)
        detail_fine = norm - blur_small
        detail_mid = blur_small - blur_large
        detail = (0.35 * detail_fine + 0.90 * detail_mid).astype(np.float32)

        # Midtone weighting (bell around 0.5) to mimic ACR/Lightroom behavior.
        midtone = np.exp(-((norm - 0.5) ** 2) / (2.0 * (0.23 ** 2))).astype(np.float32)

        # Halo guard: reduce enhancement around strongest edges.
        edge_strength = np.clip(np.abs(detail_mid) * 10.0, 0.0, 1.0).astype(np.float32)
        halo_guard = (1.0 - 0.45 * edge_strength).astype(np.float32)

        delta = (clarity * 2.6 * detail * midtone * halo_guard).astype(np.float32)
        delta = np.clip(delta, -0.45, 0.45)
        norm = np.clip(norm + delta, 0.0, 1.0).astype(np.float32)

    out = (norm * (hi - lo) + lo).astype(np.float32)
    out = np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)
    return out


def process_frame(frame: np.ndarray, gui) -> np.ndarray:
    """
    Pipeline path:
    - Cache input frame at module entry for manual/live preview source.
    - If Apply automatically is off, pass through unchanged.
    - Otherwise run deconv (if Enable deconv) and/or contrast (if Enable contrast).
    """
    api = gui.api
    frame = api.incoming_frame(MODULE_NAME, frame)
    # Keep latest module-entry frame so manual/live tuning can reuse it directly
    gui._microcontrast_latest_input = np.asarray(frame, dtype=np.float32).copy()
    gui._microcontrast_latest_token = int(getattr(gui, "_microcontrast_latest_token", 0)) + 1
    frame_token = int(getattr(gui, "_microcontrast_latest_token", 0))
    gui._microcontrast_raw_frame = gui._microcontrast_latest_input.copy()
    gui._microcontrast_snapshot_token = frame_token
    gui._microcontrast_deconv_frame = None
    gui._microcontrast_result = None
    gui._microcontrast_last_preview_key = None

    if not api.alteration_auto_apply(gui, "microcontrast_auto_apply", default=False):
        return api.outgoing_frame(MODULE_NAME, frame)

    # Enable deconv / Enable contrast control which steps run when module is applied.
    # If both are enabled, order is deconvolution first, then enhancement.
    out = np.asarray(frame, dtype=np.float32)
    auto_deconv = bool(getattr(gui, "_microcontrast_auto_deconv_workflow", False))
    auto_contrast = bool(getattr(gui, "_microcontrast_auto_workflow", False))
    applied_deconv = False
    applied_enhance = False
    if auto_deconv and is_deconv_available():
        sigma = float(getattr(gui, "_microcontrast_deconv_sigma", 1.0))
        iterations = int(getattr(gui, "_microcontrast_deconv_iterations", 10))
        before = out
        out = deconvolve_richardson_lucy(out, sigma=sigma, iterations=iterations)
        gui._microcontrast_deconv_frame = out.copy()
        applied_deconv = True
        if int(getattr(gui, "_microcontrast_last_console_token", -1)) != frame_token:
            mad = float(np.mean(np.abs(out - before)))
            print(
                f"[ImageEnhancement] auto-deconv applied "
                f"(token={frame_token}, sigma={sigma:.2f}, iter={iterations}, mad={mad:.3f})",
                flush=True,
            )
            gui._microcontrast_last_console_token = frame_token
    elif auto_deconv:
        # Auto-deconv requested but unavailable: clear derived deconv cache.
        gui._microcontrast_deconv_frame = None
        if int(getattr(gui, "_microcontrast_last_console_token", -1)) != frame_token:
            print(
                f"[ImageEnhancement] auto-deconv requested but unavailable (token={frame_token})",
                flush=True,
            )
            gui._microcontrast_last_console_token = frame_token

    clarity = float(getattr(gui, "_microcontrast_clarity", 0.0))
    dehaze = float(getattr(gui, "_microcontrast_dehaze", 0.0))
    if auto_contrast and (abs(clarity) > 1e-6 or dehaze > 0.0):
        out = _enhance(out, clarity, dehaze)
        applied_enhance = True
        if int(getattr(gui, "_microcontrast_last_console_token", -1)) != frame_token:
            print(
                f"[ImageEnhancement] auto-enhance applied "
                f"(token={frame_token}, clarity={clarity:.2f}, dehaze={dehaze:.2f})",
                flush=True,
            )
            gui._microcontrast_last_console_token = frame_token

    if int(getattr(gui, "_microcontrast_last_console_token", -1)) != frame_token:
        print(
            f"[ImageEnhancement] auto-state "
            f"(token={frame_token}, deconv={auto_deconv}, enhance={auto_contrast}, "
            f"applied_deconv={applied_deconv}, applied_enhance={applied_enhance})",
            flush=True,
        )
        gui._microcontrast_last_console_token = frame_token
    return api.outgoing_frame(MODULE_NAME, out)


def _cb_clarity(sender, app_data, gui):
    import dearpygui.dearpygui as dpg
    gui._microcontrast_clarity = float(dpg.get_value("microcontrast_clarity"))
    _maybe_live_preview(gui)
    gui.api.save_settings()


def _cb_dehaze(sender, app_data, gui):
    import dearpygui.dearpygui as dpg
    gui._microcontrast_dehaze = float(dpg.get_value("microcontrast_dehaze"))
    _maybe_live_preview(gui)
    gui.api.save_settings()


def _cb_auto_workflow(sender, app_data, gui):
    import dearpygui.dearpygui as dpg
    gui._microcontrast_auto_workflow = bool(dpg.get_value("microcontrast_auto_workflow"))
    gui.api.save_settings()


def _cb_auto_deconv_workflow(sender, app_data, gui):
    import dearpygui.dearpygui as dpg
    gui._microcontrast_auto_deconv_workflow = bool(dpg.get_value("microcontrast_auto_deconv_workflow"))
    gui.api.save_settings()


def _cb_deconv_sigma(sender, app_data, gui):
    import dearpygui.dearpygui as dpg
    gui._microcontrast_deconv_sigma = max(0.2, min(10.0, float(dpg.get_value("microcontrast_deconv_sigma"))))
    gui.api.save_settings()


def _cb_deconv_iterations(sender, app_data, gui):
    import dearpygui.dearpygui as dpg
    gui._microcontrast_deconv_iterations = max(1, min(100, int(dpg.get_value("microcontrast_deconv_iterations"))))
    gui.api.save_settings()


def _cb_live_preview(sender, app_data, gui):
    import dearpygui.dearpygui as dpg
    gui._microcontrast_live_preview = bool(dpg.get_value("microcontrast_live_preview"))
    gui.api.save_settings()


def _ensure_snapshot(gui):
    api = gui.api
    incoming = api.get_module_incoming_image(MODULE_NAME)
    incoming_token = api.get_module_incoming_token(MODULE_NAME)
    snap_token = int(getattr(gui, "_microcontrast_snapshot_token", -1))
    if incoming is not None and incoming_token is not None and int(incoming_token) != snap_token:
        gui._microcontrast_raw_frame = np.asarray(incoming, dtype=np.float32).copy()
        gui._microcontrast_snapshot_token = int(incoming_token)
        gui._microcontrast_deconv_frame = None
        gui._microcontrast_result = None
        gui._microcontrast_last_preview_key = None
        return True
    if getattr(gui, "_microcontrast_raw_frame", None) is None:
        # Do NOT fall back to current display frame; that can already be enhanced/deconvolved
        # and causes cumulative double-application. Manual operations must start from module input cache.
        return False
    return True


def _apply_from_snapshot(gui, set_status: bool = True):
    api = gui.api
    src = getattr(gui, "_microcontrast_deconv_frame", None)
    src_kind = "deconv"
    if src is None:
        src = getattr(gui, "_microcontrast_raw_frame", None)
        src_kind = "raw"
    if src is None:
        return False
    clarity = float(getattr(gui, "_microcontrast_clarity", 0.0))
    dehaze = float(getattr(gui, "_microcontrast_dehaze", 0.0))
    out = _enhance(src, clarity, dehaze)
    gui._microcontrast_result = out
    out_post = api.output_manual_from_module(MODULE_NAME, out)
    # Paint as current live frame so windowing/histogram continue to work naturally.
    # output_manual_from_module already paints/stores output.
    if set_status:
        api.set_status_message(
            f"Enhancement applied (Clarity={clarity:.0f}, Dehaze={dehaze:.0f})"
        )
    gui._microcontrast_last_preview_key = (
        int(getattr(gui, "_microcontrast_snapshot_token", -1)),
        round(float(clarity), 4),
        round(float(dehaze), 4),
    )
    token = int(getattr(gui, "_microcontrast_snapshot_token", -1))
    if set_status:
        print(
            f"[ImageEnhancement] manual microcontrast applied "
            f"(token={token}, src={src_kind}, clarity={clarity:.2f}, dehaze={dehaze:.2f})",
            flush=True,
        )
    else:
        print(
            f"[ImageEnhancement] live microcontrast preview "
            f"(token={token}, src={src_kind}, clarity={clarity:.2f}, dehaze={dehaze:.2f})",
            flush=True,
        )
    return True


def _maybe_live_preview(gui):
    if not bool(getattr(gui, "_microcontrast_live_preview", True)):
        return
    clarity = float(getattr(gui, "_microcontrast_clarity", 0.0))
    dehaze = float(getattr(gui, "_microcontrast_dehaze", 0.0))
    preview_key = (
        int(getattr(gui, "_microcontrast_snapshot_token", -1)),
        round(float(clarity), 4),
        round(float(dehaze), 4),
    )
    # Skip redundant callbacks (DPG can queue many slider events with identical end values).
    if preview_key == getattr(gui, "_microcontrast_last_preview_key", None):
        return
    now = time.monotonic()
    last_t = float(getattr(gui, "_microcontrast_last_preview_t", 0.0))
    # Throttle preview to ~5 FPS to keep UI responsive on large frames.
    if (now - last_t) < 0.2:
        return
    if not _ensure_snapshot(gui):
        return
    # Recompute key because ensure_snapshot may update snapshot token.
    preview_key = (
        int(getattr(gui, "_microcontrast_snapshot_token", -1)),
        round(float(getattr(gui, "_microcontrast_clarity", 0.0)), 4),
        round(float(getattr(gui, "_microcontrast_dehaze", 0.0)), 4),
    )
    if preview_key == getattr(gui, "_microcontrast_last_preview_key", None):
        return
    gui._microcontrast_last_preview_t = now
    _apply_from_snapshot(gui, set_status=False)


def _apply_manual(gui):
    api = gui.api
    if not _ensure_snapshot(gui):
        print("[ImageEnhancement] manual microcontrast skipped (no module input cache)", flush=True)
        api.set_status_message("No frame to enhance")
        return
    _apply_from_snapshot(gui, set_status=True)


def _apply_deconv_manual(gui):
    api = gui.api
    if not is_deconv_available():
        api.set_status_message("Deconvolution unavailable: install scikit-image and scipy")
        return
    if not _ensure_snapshot(gui):
        api.set_status_message("No frame to enhance")
        return
    raw = getattr(gui, "_microcontrast_raw_frame", None)
    if raw is None:
        api.set_status_message("No frame to enhance")
        return
    sigma = float(getattr(gui, "_microcontrast_deconv_sigma", 1.0))
    iterations = int(getattr(gui, "_microcontrast_deconv_iterations", 10))
    deconv = deconvolve_richardson_lucy(raw, sigma=sigma, iterations=iterations)
    gui._microcontrast_deconv_frame = deconv
    gui._microcontrast_result = None
    gui._microcontrast_last_preview_key = None
    # Manual flow is explicit and split in two steps:
    # 1) Apply deconvolution (this action)
    # 2) Apply clarity/dehaze (separate button)
    api.output_manual_from_module(MODULE_NAME, deconv)
    print(
        f"[ImageEnhancement] manual deconvolution applied "
        f"(token={int(getattr(gui, '_microcontrast_snapshot_token', -1))}, "
        f"sigma={sigma:.2f}, iter={iterations})",
        flush=True,
    )
    api.set_status_message(f"Deconvolution applied (σ={sigma:.2f}, n={iterations})")


def _apply_full_manual(gui):
    """Apply full enhancement: deconv (if enabled) then contrast (if enabled)."""
    api = gui.api
    if not _ensure_snapshot(gui):
        api.set_status_message("No frame available (run acquisition first).")
        return
    raw = getattr(gui, "_microcontrast_raw_frame", None)
    if raw is None:
        api.set_status_message("No frame available (run acquisition first).")
        return
    out = np.asarray(raw, dtype=np.float32)
    enable_deconv = bool(getattr(gui, "_microcontrast_auto_deconv_workflow", False))
    enable_contrast = bool(getattr(gui, "_microcontrast_auto_workflow", False))
    if enable_deconv and is_deconv_available():
        sigma = float(getattr(gui, "_microcontrast_deconv_sigma", 1.0))
        iterations = int(getattr(gui, "_microcontrast_deconv_iterations", 10))
        out = deconvolve_richardson_lucy(out, sigma=sigma, iterations=iterations)
        gui._microcontrast_deconv_frame = out.copy()
    if enable_contrast:
        clarity = float(getattr(gui, "_microcontrast_clarity", 0.0))
        dehaze = float(getattr(gui, "_microcontrast_dehaze", 0.0))
        if abs(clarity) > 1e-6 or dehaze > 0.0:
            out = _enhance(out, clarity, dehaze)
    gui._microcontrast_result = out
    gui._microcontrast_last_preview_key = None
    api.output_manual_from_module(MODULE_NAME, out)
    api.set_status_message("Image enhancement applied.")


def _revert_manual(gui):
    api = gui.api
    incoming = api.get_module_incoming_image(MODULE_NAME)
    raw = incoming if incoming is not None else getattr(gui, "_microcontrast_raw_frame", None)
    if raw is None:
        api.set_status_message("No frame available (run acquisition first).")
        return
    gui._microcontrast_result = None
    gui._microcontrast_deconv_frame = None
    gui._microcontrast_last_preview_key = None
    api.output_manual_from_module(MODULE_NAME, np.asarray(raw, dtype=np.float32).copy())
    api.set_status_message("Reverted to frame before image enhancement.")


def build_ui(gui, parent_tag: str = "control_panel") -> None:
    import dearpygui.dearpygui as dpg
    api = gui.api
    loaded = api.get_loaded_settings()
    gui._microcontrast_clarity = float(loaded.get("microcontrast_clarity", 0.0))
    gui._microcontrast_dehaze = float(loaded.get("microcontrast_dehaze", 0.0))
    gui._microcontrast_auto_workflow = bool(loaded.get("microcontrast_auto_workflow", False))
    gui._microcontrast_auto_deconv_workflow = bool(loaded.get("microcontrast_auto_deconv_workflow", False))
    gui._microcontrast_deconv_sigma = float(loaded.get("microcontrast_deconv_sigma", 1.0))
    gui._microcontrast_deconv_iterations = int(loaded.get("microcontrast_deconv_iterations", 10))
    gui._microcontrast_live_preview = bool(loaded.get("microcontrast_live_preview", True))
    if not hasattr(gui, "_microcontrast_raw_frame"):
        gui._microcontrast_raw_frame = None
    if not hasattr(gui, "_microcontrast_snapshot_token"):
        gui._microcontrast_snapshot_token = -1
    if not hasattr(gui, "_microcontrast_latest_input"):
        gui._microcontrast_latest_input = None
    if not hasattr(gui, "_microcontrast_latest_token"):
        gui._microcontrast_latest_token = 0
    if not hasattr(gui, "_microcontrast_result"):
        gui._microcontrast_result = None
    if not hasattr(gui, "_microcontrast_deconv_frame"):
        gui._microcontrast_deconv_frame = None
    if not hasattr(gui, "_microcontrast_last_preview_t"):
        gui._microcontrast_last_preview_t = 0.0
    if not hasattr(gui, "_microcontrast_last_preview_key"):
        gui._microcontrast_last_preview_key = None
    if not hasattr(gui, "_microcontrast_last_console_token"):
        gui._microcontrast_last_console_token = -1

    with dpg.collapsing_header(parent=parent_tag, label="Image Enhancement", default_open=False):
        with dpg.group(indent=10):
            api.build_alteration_apply_revert_ui(
                gui,
                MODULE_NAME,
                _apply_full_manual,
                auto_apply_attr="microcontrast_auto_apply",
                revert_snapshot_attr="_microcontrast_raw_frame",
                default_auto_apply=False,
            )
            dpg.add_text("Deconvolution", color=[200, 200, 200])
            dpg.add_slider_float(
                label="Sigma",
                default_value=gui._microcontrast_deconv_sigma,
                min_value=0.2,
                max_value=10.0,
                format="%.2f",
                tag="microcontrast_deconv_sigma",
                callback=lambda s, a: _cb_deconv_sigma(s, a, gui),
                width=-120,
            )
            dpg.add_slider_int(
                label="Iterations",
                default_value=gui._microcontrast_deconv_iterations,
                min_value=1,
                max_value=100,
                tag="microcontrast_deconv_iterations",
                callback=lambda s, a: _cb_deconv_iterations(s, a, gui),
                width=-120,
            )
            dpg.add_checkbox(
                label="Enable deconv",
                default_value=gui._microcontrast_auto_deconv_workflow,
                tag="microcontrast_auto_deconv_workflow",
                callback=lambda s, a: _cb_auto_deconv_workflow(s, a, gui),
            )
            dpg.add_separator()
            dpg.add_text("Contrast", color=[200, 200, 200])
            dpg.add_slider_float(
                label="Clarity",
                default_value=gui._microcontrast_clarity,
                min_value=-300.0,
                max_value=300.0,
                format="%.0f",
                tag="microcontrast_clarity",
                callback=lambda s, a: _cb_clarity(s, a, gui),
                width=-120,
            )
            dpg.add_slider_float(
                label="Dehaze",
                default_value=gui._microcontrast_dehaze,
                min_value=0.0,
                max_value=100.0,
                format="%.0f",
                tag="microcontrast_dehaze",
                callback=lambda s, a: _cb_dehaze(s, a, gui),
                width=-120,
            )
            dpg.add_checkbox(
                label="Enable contrast",
                default_value=gui._microcontrast_auto_workflow,
                tag="microcontrast_auto_workflow",
                callback=lambda s, a: _cb_auto_workflow(s, a, gui),
            )
            dpg.add_separator()
            dpg.add_checkbox(
                label="Live preview while tuning",
                default_value=gui._microcontrast_live_preview,
                tag="microcontrast_live_preview",
                callback=lambda s, a: _cb_live_preview(s, a, gui),
            )
