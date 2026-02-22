# Example Arduino powersupply

**Purpose:** Control a relay via an Arduino over serial (ON/OFF commands). Use for a simple interlock or to switch an external power supply. Provides **Auto On/Off** (turn on before acquisition, turn off when done) and manual **Turn On** / **Turn Off**.

- **UI:** **`build_ui(gui, parent_tag)`** adds serial port selection, Connect/Disconnect, **Auto On/Off** checkbox, and **Turn On** / **Turn Off** buttons. Registers as **`gui.beam_supply`** so the main app can gate acquisition and turn off when idle.
- **Arduino sketch:** **`relay_serial/relay_serial.ino`** in this folder. Upload it to an Arduino; it listens at 9600 baud for `ON` and `OFF` lines. On **ON** it turns the relay/LED on, waits **BEAM_READY_DELAY_MS** (2 s by default), then sends **READY** over serial so the app knows the beam is ready. On **OFF** it turns off and sends **OK**. Default pin is **LED_BUILTIN** (pin 13) for testing; set **RELAY_PIN** to 8 (or your relay pin) for a real relay.
- **Settings:** **`get_setting_keys()`** returns `ard_psu_serial_port`, `ard_psu_auto_on_off`. **`get_settings_for_save()`** returns current port and checkbox state.

## Beam supply contract

Same as other supply modules: when **Auto On/Off** is enabled and the module is connected, the app sends **ON**, waits for the Arduino’s **READY** reply (after the sketch’s delay), then starts acquisition. When acquisition ends, the app sends **OFF**. The delay + handshake is a small example of a full “beam ready” flow.

## Hardware

- Arduino (Uno, Nano, etc.) connected by USB (serial).
- Default sketch uses **LED_BUILTIN** (pin 13) so you can test with the board LED and no hardware. For a real relay, set `RELAY_PIN` to your relay pin (e.g. 8) in **relay_serial.ino**. Active high only; relay/LED starts off at boot.

## See also

- [MODULES_OVERVIEW.md](../MODULES_OVERVIEW.md) – Supply modules and beam_supply contract.
- [example_supply/README.md](../example_supply/README.md) – Dummy in-process supply (no hardware).
