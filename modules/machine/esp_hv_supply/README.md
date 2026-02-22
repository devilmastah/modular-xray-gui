# ESP HV supply machine module

**Type:** Machine (supply)  
**Purpose:** ESP high-voltage power supply controls (serial or network). Can optionally register as **`gui.beam_supply`** for **Auto On/Off**: turn on and wait for beam ready before acquisition, turn off when acquisition ends.

---

## Integration

- **UI:** **`build_ui(gui, parent_tag)`** adds connection (serial port / network IP), kV/mA/filament sliders, Connect/Disconnect, and “Turn On Tube” / “Turn Off Tube”. It also adds an **“Auto On/Off”** checkbox and sets **`gui.beam_supply = BeamSupplyAdapter(core, "hv_auto_on_off_cb")`** so the main app can gate acquisition start and turn off when idle.
- **Core:** Uses in-process **`PSUCore`** (no ZMQ). State updates set **`gui._hv_psu_state_dirty`**; the main app’s tick runs the module’s update so sliders and status stay in sync.
- **Settings:** **`get_setting_keys()`** returns serial port, network IP, slider values, and **`esp_hv_auto_on_off`**. **`get_settings_for_save()`** returns current UI values for those keys.

---

## Beam supply contract

**`BeamSupplyAdapter`** implements:

- **`wants_auto_on_off()`** – Reads the “Auto On/Off” checkbox (**`hv_auto_on_off_cb`**).
- **`is_connected()`** – True when the PSU core reports connected.
- **`turn_on_and_wait_ready(timeout_s)`** – Sets beam on and polls **`beam_ready`** (and fault / user turn-off) until ready or timeout (default 60 s). Returns **True** if ready, **False** on timeout or fault.
- **`turn_off()`** – Sets beam off.

The main app calls **turn_on_and_wait_ready** before starting the camera when mode ≠ dark; it calls **turn_off** when acquisition transitions to idle.

---

## Settings keys

**`get_setting_keys()`** returns:

- **`esp_hv_serial_port`**, **`esp_hv_network_ip`**
- **`esp_hv_kv_slider`**, **`esp_hv_ma_slider`**, **`esp_hv_filament_slider`**
- **`esp_hv_auto_on_off`**

---

## MODULE_INFO

- **display_name:** "ESP HV Supply"
- **type:** "machine"
- **default_enabled:** False

---

## See also

- [MODULES_OVERVIEW.md](../MODULES_OVERVIEW.md) – Beam supply contract and discovery.
- [example_supply/README.md](../example_supply/README.md) – Dummy supply with same contract for testing.
