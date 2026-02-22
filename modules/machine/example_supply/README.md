# Example supply machine module

**Type:** Machine (supply)  
**Purpose:** Dummy beam supply for testing. Always “connected”; simulates a 5 s delay from turn-on to beam ready. Implements the optional **`gui.beam_supply`** contract so you can test Auto On/Off without real hardware.

---

## Integration

- **UI:** **`build_ui(gui, parent_tag)`** adds kV/mA sliders, status text, “Turn On Tube” / “Turn Off Tube”, and an **“Auto On/Off”** checkbox. It sets **`gui.beam_supply = BeamSupplyAdapter(core, "ex_supply_auto_on_off_cb")`**.
- **Core:** **`ExampleCore`** is in-process: **`connected`** is always True; **`beam_ready`** becomes True **5 s** after **`set_beam_on(True)`**.
- **Settings:** **`get_setting_keys()`** returns **`example_supply_kv`**, **`example_supply_ma`**, **`example_supply_auto_on_off`**. **`get_settings_for_save()`** returns current values for persistence.

---

## Beam supply contract

**`BeamSupplyAdapter`** implements:

- **`wants_auto_on_off()`** – Reads the “Auto On/Off” checkbox (**`ex_supply_auto_on_off_cb`**).
- **`is_connected()`** – Always True (ExampleCore always reports connected).
- **`turn_on_and_wait_ready(timeout_s=15)`** – Turns “beam” on and blocks until **beam_ready** (after **BEAM_READY_DELAY_S = 5.0** s) or timeout.
- **`turn_off()`** – Turns beam off.

Use this module to verify that the main app correctly waits for “beam ready” before starting the camera and turns the supply off when acquisition goes idle.

---

## Settings keys

**`get_setting_keys()`** returns:

- **`example_supply_kv`**
- **`example_supply_ma`**
- **`example_supply_auto_on_off`**

---

## MODULE_INFO

- **display_name:** "Example Supply"
- **type:** "machine"
- **default_enabled:** False

---

## See also

- [MODULES_OVERVIEW.md](../MODULES_OVERVIEW.md) – Beam supply contract.
- [esp_hv_supply/README.md](../esp_hv_supply/README.md) – Real HV supply with the same contract.
