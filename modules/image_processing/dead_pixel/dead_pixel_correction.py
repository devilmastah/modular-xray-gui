"""
Dead pixel line correction library.

Fills dead horizontal and vertical lines by interpolating from neighboring pixels.
"""

import numpy as np


def correct_dead_lines(
    img: np.ndarray,
    dead_vertical_lines: list[int] = None,
    dead_horizontal_lines: list[int] = None,
) -> np.ndarray:
    """
    Correct dead pixel lines by interpolating from neighbors.
    
    Args:
        img: Input image (H, W) float32 or uint16
        dead_vertical_lines: List of column indices with dead vertical lines (e.g., [661])
        dead_horizontal_lines: List of row indices with dead horizontal lines (e.g., [100, 200])
    
    Returns:
        Corrected image (H, W) same dtype as input
    """
    if dead_vertical_lines is None:
        dead_vertical_lines = []
    if dead_horizontal_lines is None:
        dead_horizontal_lines = []
    
    if len(dead_vertical_lines) == 0 and len(dead_horizontal_lines) == 0:
        return img.copy()
    
    img_dtype = img.dtype
    corrected = img.astype(np.float32).copy()
    h, w = corrected.shape
    
    # Fix dead vertical lines (columns) - interpolate from left/right neighbors
    for col in dead_vertical_lines:
        if col < 0 or col >= w:
            continue
        
        # Find valid neighbors (left and right)
        left_col = col - 1
        right_col = col + 1
        
        # If left neighbor is also dead, look further left
        while left_col >= 0 and left_col in dead_vertical_lines:
            left_col -= 1
        
        # If right neighbor is also dead, look further right
        while right_col < w and right_col in dead_vertical_lines:
            right_col += 1
        
        # Interpolate from neighbors
        if left_col >= 0 and right_col < w:
            # Both neighbors available: average
            corrected[:, col] = (corrected[:, left_col] + corrected[:, right_col]) / 2.0
        elif left_col >= 0:
            # Only left neighbor: copy it
            corrected[:, col] = corrected[:, left_col]
        elif right_col < w:
            # Only right neighbor: copy it
            corrected[:, col] = corrected[:, right_col]
        # else: both out of bounds, leave as is
    
    # Fix dead horizontal lines (rows) - interpolate from top/bottom neighbors
    for row in dead_horizontal_lines:
        if row < 0 or row >= h:
            continue
        
        # Find valid neighbors (top and bottom)
        top_row = row - 1
        bottom_row = row + 1
        
        # If top neighbor is also dead, look further up
        while top_row >= 0 and top_row in dead_horizontal_lines:
            top_row -= 1
        
        # If bottom neighbor is also dead, look further down
        while bottom_row < h and bottom_row in dead_horizontal_lines:
            bottom_row += 1
        
        # Interpolate from neighbors
        if top_row >= 0 and bottom_row < h:
            # Both neighbors available: average
            corrected[row, :] = (corrected[top_row, :] + corrected[bottom_row, :]) / 2.0
        elif top_row >= 0:
            # Only top neighbor: copy it
            corrected[row, :] = corrected[top_row, :]
        elif bottom_row < h:
            # Only bottom neighbor: copy it
            corrected[row, :] = corrected[bottom_row, :]
        # else: both out of bounds, leave as is
    
    # Convert back to original dtype
    if img_dtype == np.uint16:
        corrected = np.clip(corrected, 0, 65535).astype(np.uint16)
    else:
        corrected = corrected.astype(img_dtype)
    
    return corrected
