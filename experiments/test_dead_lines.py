#!/usr/bin/env python3
"""
Test script for dead pixel line correction.
"""

import numpy as np
import tifffile as tiff
import sys
from modules.image_processing.dead_pixel.dead_pixel_correction import correct_dead_lines

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python test_dead_lines.py input.tiff [vertical_lines] [horizontal_lines]")
        print("Example: python test_dead_lines.py input.tiff 661")
        print("Example: python test_dead_lines.py input.tiff '661,662' '100,200'")
        sys.exit(1)
    
    input_path = sys.argv[1]
    
    # Parse dead lines
    dead_vertical = []
    dead_horizontal = []
    
    if len(sys.argv) > 2:
        if sys.argv[2].strip():
            dead_vertical = [int(x.strip()) for x in sys.argv[2].split(",") if x.strip()]
    
    if len(sys.argv) > 3:
        if sys.argv[3].strip():
            dead_horizontal = [int(x.strip()) for x in sys.argv[3].split(",") if x.strip()]
    
    print(f"Loading: {input_path}")
    img = tiff.imread(input_path)
    
    # Handle multi-page/stacks
    if img.ndim > 2:
        if img.ndim == 3 and img.shape[0] == 1:
            img = img[0]
        elif img.ndim == 3:
            print(f"Warning: Image has {img.shape[0]} pages, using first page")
            img = img[0]
        else:
            print(f"Warning: Unexpected shape {img.shape}, using first slice")
            img = img[0]
    
    print(f"Image shape: {img.shape}, dtype: {img.dtype}")
    print(f"Dead vertical lines (columns): {dead_vertical}")
    print(f"Dead horizontal lines (rows): {dead_horizontal}")
    
    # Correct dead lines
    corrected = correct_dead_lines(
        img,
        dead_vertical_lines=dead_vertical,
        dead_horizontal_lines=dead_horizontal
    )
    
    # Save corrected image
    base = input_path.rsplit(".", 1)[0]
    ext = input_path.rsplit(".", 1)[1] if "." in input_path else "tiff"
    out_path = f"{base}_deadlines_fixed.{ext}"
    
    print(f"\nSaving corrected image: {out_path}")
    tiff.imwrite(out_path, corrected, photometric="minisblack", compression=None)
    print("Done!")
