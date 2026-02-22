# Faxitron MX-20 / DX-50 module

**Purpose:** Control a Faxitron MX-20 or DX-50 X-ray source over **serial** (9600 8N1). **Connect** puts the unit in remote mode; **Disconnect** (or closing the app) sets it back to **front panel** so the unit is never left in remote. Set **kV**; use **Auto On/Off** so HV follows capture (on before, off when idle), or **manual HV On / HV Off**. Exposure is determined by your software/camera (integration time), not the Faxitron’s internal timer.

**Protocol:** See project root **"Faxitron Serial Commands for MX-20 and D.md"**. Commands use `!` prefix and CR; state is `?SR` (Ready), `?SW` (Warming), `?SD` (Door open).

---

## Features

- **Connect:** Opens serial and sends **!MR** (remote mode). Unit is then controlled from the app.
- **Disconnect:** Sends **!MF** (front panel) then closes the port. **App exit** also runs this so the unit returns to front panel if you close without clicking Disconnect.
- **kV:** Slider 10–35 kV; sent as `!V{kv}`. Used for both Auto On/Off and manual HV On.
- **Auto On/Off:** When enabled and connected, the app turns HV on before starting a capture (except dark) and turns HV off when acquisition goes idle.
- **HV On:** Manual – set remote, set kV from slider, poll `?S` until Ready.
- **HV Off:** Manual – send **!MF** (front panel mode).

---

## Beam supply contract

The module registers **`gui.api.register_beam_supply(BeamSupplyAdapter(...))`** so:

| Method | Behavior |
|--------|----------|
| **wants_auto_on_off()** | Reads the "Auto On/Off" checkbox. |
| **is_connected()** | True when serial port is open. |
| **turn_on_and_wait_ready(timeout_s, should_cancel)** | Set remote, set current kV from UI, poll `?S` until Ready or timeout/cancel. |
| **turn_off()** | Set front panel (`!MF`). |

---

## Settings (persisted)

- **fax_mx20_serial_port** – Last selected port.
- **fax_mx20_kv** – kV (10–35).
- **fax_mx20_auto_on_off** – Auto On/Off checkbox.

---

## Enabling the module

1. **Settings** → enable **"Load Faxitron MX-20 DX-50 module"**.
2. Restart the app.
3. In the control panel, open **"Faxitron MX-20 / DX-50"**, select the serial port, click **Connect** (unit goes to remote). Use **Disconnect** before closing the app if you want to return to front panel explicitly; the app also restores front panel on exit.

---

## References

- [MODULES_OVERVIEW.md](../MODULES_OVERVIEW.md) – Supply modules and beam_supply contract.
- **Faxitron Serial Commands for MX-20 and D.md** (project root) – Serial protocol.
