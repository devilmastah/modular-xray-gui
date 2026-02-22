"""
Banding correction library for horizontal banding removal using reference pixels.

Separates slow background (scatter/drift) from fast banding component and subtracts only the banding.
"""

import numpy as np


# Default configuration (horizontal = right-side reference)
DEFAULT_BLACK_W = 20        # Width of reference stripe in pixels (use last BLACK_W columns)
DEFAULT_BLACK_OFFSET = 0    # Offset from right edge (always 0 = use rightmost columns)
DEFAULT_SMOOTH_WIN = 128    # Window size for slow background smoothing in rows

# Vertical banding (bottom reference rows)
DEFAULT_VERTICAL_STRIPE_H = 20   # Height of reference stripe in pixels (use last N rows)
DEFAULT_VERTICAL_SMOOTH_WIN = 128  # Window size for slow background smoothing in columns


def moving_average_1d(x: np.ndarray, win: int) -> np.ndarray:
    """Moving average with edge padding."""
    win = int(win)
    if win < 3:
        return x.astype(np.float32)
    
    x = x.astype(np.float32)
    k = np.ones(win, dtype=np.float32) / win
    
    pad_left = win // 2
    pad_right = win - 1 - pad_left  # makes output length exactly len(x)
    
    xp = np.pad(x, (pad_left, pad_right), mode="edge")
    y = np.convolve(xp, k, mode="valid")
    return y


def optimize_smooth_window(
    img: np.ndarray,
    black_w: int = DEFAULT_BLACK_W,
    black_offset: int = DEFAULT_BLACK_OFFSET,
    candidates: list[int] = None,
) -> tuple[int, float]:
    """
    Find optimal smooth window size by testing different values.
    
    Args:
        img: Input image (H, W) uint16 or float32
        black_w: Width of reference stripe in pixels
        black_offset: Offset from right edge
        candidates: List of smooth window sizes to test (default: 10 to 512, step 5)
    
    Returns:
        (best_window, best_score) - Best smooth window size and its quality score (lower is better)
    """
    img = img.astype(np.float32)
    h, w = img.shape
    
    if candidates is None:
        # Test from 10 to 512 in steps of 5 for thorough optimization
        max_win = min(512, h // 4)
        candidates = list(range(10, max_win + 1, 5))
        if len(candidates) == 0:
            candidates = [10, 32, 64, 128, 256]
    
    # Extract reference stripe
    col_start = w - black_offset - black_w
    col_end = w - black_offset
    stripe = img[:, col_start : col_end]
    ref = np.median(stripe, axis=1)
    
    best_window = candidates[0]
    best_score = float('inf')
    
    for smooth_win in candidates:
        # Calculate banding correction
        ref_slow = moving_average_1d(ref, smooth_win)
        band = ref - ref_slow
        
        # Apply correction to reference stripe
        corrected_stripe = stripe - band[:, np.newaxis]
        corrected_ref = np.median(corrected_stripe, axis=1)
        
        # Quality metric: std of corrected reference stripe (lower = more uniform = better)
        score = np.std(corrected_ref)
        
        if score < best_score:
            best_score = score
            best_window = smooth_win
    
    return best_window, best_score


def optimize_smooth_window_vertical(
    img: np.ndarray,
    stripe_h: int = DEFAULT_VERTICAL_STRIPE_H,
    candidates: list[int] = None,
) -> tuple[int, float]:
    """
    Find optimal vertical smooth window size by testing different values.
    
    Args:
        img: Input image (H, W) float32 or uint16
        stripe_h: Height of reference stripe (bottom rows)
        candidates: List of smooth window sizes to test (default: 10 to 512, step 5)
    
    Returns:
        (best_window, best_score) - Best smooth window size and its quality score (lower is better)
    """
    img = img.astype(np.float32)
    h, w = img.shape
    if stripe_h <= 0 or stripe_h >= h:
        return DEFAULT_VERTICAL_SMOOTH_WIN, 0.0

    if candidates is None:
        max_win = min(512, w // 4)
        candidates = list(range(10, max_win + 1, 5))
        if len(candidates) == 0:
            candidates = [10, 32, 64, 128, 256]

    row_start = h - stripe_h
    stripe = img[row_start:h, :]  # (stripe_h, W)
    ref = np.median(stripe, axis=0)  # (W,)

    best_window = candidates[0]
    best_score = float("inf")

    for smooth_win in candidates:
        ref_slow = moving_average_1d(ref, smooth_win)
        band = ref - ref_slow
        corrected_stripe = stripe - band[np.newaxis, :]
        corrected_ref = np.median(corrected_stripe, axis=0)
        score = np.std(corrected_ref)
        if score < best_score:
            best_score = score
            best_window = smooth_win

    return best_window, best_score


def correct_banding(
    img: np.ndarray,
    black_w: int = DEFAULT_BLACK_W,
    black_offset: int = DEFAULT_BLACK_OFFSET,
    smooth_win: int = DEFAULT_SMOOTH_WIN,
    auto_optimize: bool = False,
) -> np.ndarray:
    """
    Correct horizontal banding by separating slow background from fast banding.
    
    Args:
        img: Input image (H, W) float32 or uint16
        black_w: Width of reference stripe in pixels (default: 20)
        black_offset: Offset from right edge (default: 0, use rightmost columns)
        smooth_win: Window size for slow background smoothing in rows (default: 128)
        auto_optimize: If True, automatically find best smooth window (slow, use sparingly)
    
    Returns:
        Corrected image (H, W) same dtype as input
    """
    img_dtype = img.dtype
    img = img.astype(np.float32)
    h, w = img.shape
    
    # Auto-optimize smooth window if requested (slow - tests many window sizes)
    if auto_optimize:
        smooth_win, _ = optimize_smooth_window(img, black_w, black_offset)
    
    # Extract reference stripe with offset: columns [w - black_offset - black_w : w - black_offset]
    col_start = w - black_offset - black_w
    col_end = w - black_offset
    stripe = img[:, col_start : col_end]  # (H, black_w)
    ref = np.median(stripe, axis=1)  # (H,) - robust per-row measurement
    
    # Separate slow background from fast banding component
    ref_slow = moving_average_1d(ref, smooth_win)
    band = ref - ref_slow  # (H,) - fast-varying banding component only
    
    # Subtract only banding from entire image
    corrected = img - band[:, np.newaxis]
    
    # Convert back to original dtype
    if img_dtype == np.uint16:
        corrected = np.clip(corrected, 0, 65535).astype(np.uint16)
    else:
        corrected = corrected.astype(img_dtype)
    
    return corrected


def correct_vertical_banding(
    img: np.ndarray,
    stripe_h: int = DEFAULT_VERTICAL_STRIPE_H,
    smooth_win: int = DEFAULT_VERTICAL_SMOOTH_WIN,
) -> np.ndarray:
    """
    Correct vertical banding using bottom rows as reference (same logic as horizontal).
    
    Uses bottom stripe_h rows as reference; per-column median gives ref[x];
    smooth along columns to get ref_slow[x]; band[x] = ref[x] - ref_slow[x];
    subtract band[x] from each column.
    
    Args:
        img: Input image (H, W) float32 or uint16
        stripe_h: Height of reference stripe in pixels (default: 20, bottom rows)
        smooth_win: Window size for slow background smoothing in columns (default: 128)
    
    Returns:
        Corrected image (H, W) same dtype as input
    """
    img_dtype = img.dtype
    img = img.astype(np.float32)
    h, w = img.shape
    
    if stripe_h <= 0 or stripe_h >= h:
        return img.astype(img_dtype) if img_dtype != np.float32 else img
    
    # Reference stripe: bottom stripe_h rows
    row_start = h - stripe_h
    stripe = img[row_start : h, :]  # (stripe_h, W)
    ref = np.median(stripe, axis=0)  # (W,) - robust per-column measurement
    
    # Separate slow background from fast banding along columns
    ref_slow = moving_average_1d(ref, min(smooth_win, max(3, len(ref) // 4)))
    band = ref - ref_slow  # (W,) - fast-varying vertical banding
    
    # Subtract banding from entire image (each column)
    corrected = img - band[np.newaxis, :]
    
    if img_dtype == np.uint16:
        corrected = np.clip(corrected, 0, 65535).astype(np.uint16)
    else:
        corrected = corrected.astype(img_dtype)
    
    return corrected
