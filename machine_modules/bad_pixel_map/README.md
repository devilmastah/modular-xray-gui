# Bad pixel map

**Purpose:** Build a per-sensor bad pixel map from the app’s loaded dark and flat, then replace those pixels in the pipeline with the median of their 3×3 good neighbors.

- **Cold (dead) pixels:** from the flat image (pixels with very low response).
- **Hot pixels:** from the dark image (pixels with unusually high value in dark).

The map is built from the **currently loaded** dark and flat (same ones used by dark_correction and flat_correction), so it matches the loaded sensor and resolution. It is saved as:
- `app/darks/<camera_name>/bad_pixel_map_<width>x<height>.npy` (used for loading and pipeline),
- `app/pixelmaps/<camera_name>/bad_pixel_map_<width>x<height>.tif` (review image: black = good, white = bad).
The .npy is reloaded when the module’s UI is built (e.g. on startup) if it exists for the current camera and frame size.

**Pipeline:** Slot 250 (after flat, before banding). Default: disabled.

**UI:** Auto correct checkbox (apply to every frame when on); Correct / Revert buttons (one-shot apply and undo on current frame); Build from dark & flat, Load saved map, Clear map; flat/dark threshold sliders.

**UI (legacy):** “Build from dark & flat” (requires dark and flat loaded), “Load saved map”, “Clear map”, and sliders for flat/dark thresholds used when building.

- [MODULES_OVERVIEW.md](../MODULES_OVERVIEW.md) – Alteration pipeline and slots.
- [dark_correction](../dark_correction/README.md), [flat_correction](../flat_correction/README.md) – Where dark/flat come from.
