"""
Bad pixel replacement: replace bad pixels with median of good neighbors in 3×3 window.
Used by the bad_pixel_map alteration module.
"""

import numpy as np


def replace_bad_pixels(frame: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """
    Replace every pixel where mask is True with the median of valid (non-bad) pixels
    in its 3×3 neighborhood. Edge pixels use whatever neighbors exist.

    Args:
        frame: (H, W) float32 image.
        mask: (H, W) bool, True = bad pixel.

    Returns:
        (H, W) float32 with bad pixels replaced (same shape as frame).
    """
    if mask is None or not np.any(mask):
        return frame
    if frame.shape != mask.shape:
        return frame
    out = np.asarray(frame, dtype=np.float32).copy()
    h, w = frame.shape
    for dy in range(-1, 2):
        for dx in range(-1, 2):
            if dx == 0 and dy == 0:
                continue
            # Shifted views: neighbor at (y+dy, x+dx) is at (y, x) in the shifted frame
            y0, y1 = max(0, dy), min(h, h + dy)
            x0, x1 = max(0, dx), min(w, w + dx)
            if y0 >= y1 or x0 >= x1:
                continue
            # This neighbor's view
            ny0, ny1 = max(0, -dy), min(h, h - dy)
            nx0, nx1 = max(0, -dx), min(w, w - dx)
            # Only at bad pixel locations we want to collect neighbor values
            bad_here = mask[y0:y1, x0:x1]
            neighbor_vals = frame[ny0:ny1, nx0:nx1]
            good_neighbor = ~mask[ny0:ny1, nx0:nx1]
            # We'll accumulate: for each bad pixel, we need median of good neighbors.
            # Doing it with a loop over (dy,dx) is tricky. Simpler: one pass per pixel.
    # Per-pixel median of good neighbors (simpler, still vectorized where possible)
    bad_ys, bad_xs = np.where(mask)
    for i in range(len(bad_ys)):
        y, x = bad_ys[i], bad_xs[i]
        vals = []
        for dy in range(-1, 2):
            for dx in range(-1, 2):
                if dx == 0 and dy == 0:
                    continue
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w and not mask[ny, nx]:
                    vals.append(frame[ny, nx])
        if vals:
            out[y, x] = float(np.median(vals))
        # else leave as is (no good neighbors)
    return out
