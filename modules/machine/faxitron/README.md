# Faxitron machine module

**Type:** Machine (supply / X-ray source)  
**Purpose:** Show Faxitron X-ray source controls (kV, exposure, mode) and **Expose** / **Refresh** buttons. Does **not** register as **`gui.beam_supply`** — exposure is manual only. Compatible with optional beam_supply modules (e.g. ESP HV, Example supply) for acquisition Auto On/Off.

---

## Integration

- **UI only:** **`build_ui(gui, parent_tag)`** adds a “Faxitron” collapsing header under the control panel. It does **not** set **`gui.beam_supply`**.
- **Teensy:** Expose and Refresh use **`gui.teensy`** (set by the Hamamatsu C7942 module when it connects). If no Teensy is connected, Faxitron actions will fail or no-op.
- **Settings:** The module provides **`get_settings_for_save()`** and persists **`fax_voltage`**, **`fax_exposure`**, **`fax_mode`** via the registry extra keys.

---

## UI

- **kV** – Slider (1–35). Saved as **`fax_voltage`**.
- **Exp (s)** – Exposure time in seconds. Saved as **`fax_exposure`**.
- **Mode** – Combo: “Remote” / “Front Panel”. Saved as **`fax_mode`**.
- **Expose** – Triggers exposure via Teensy (uses current kV and exposure time).
- **Refresh** – Refreshes/reads state from the source if applicable.
- **Status** – Text widget **`fax_status`** for feedback.

---

## Settings keys

**`get_setting_keys()`** returns:

- **`fax_voltage`** (int)
- **`fax_exposure`** (float)
- **`fax_mode`** (str: "Remote" | "Front Panel")

These are loaded in **`build_ui`** from **`gui._loaded_settings`** and saved via **`get_settings_for_save()`** when the user changes the controls (and when the Settings window is closed).

---

## See also

- [MODULES_OVERVIEW.md](../MODULES_OVERVIEW.md) – Supply modules and beam_supply contract.
- [esp_hv_supply/README.md](../esp_hv_supply/README.md) – Example of a module that does register as **`gui.beam_supply`**.
