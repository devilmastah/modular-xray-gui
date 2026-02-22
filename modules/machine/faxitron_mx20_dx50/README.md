# Faxitron MX-20 / DX-50 module

**Purpose:** Control a Faxitron MX-20 or DX-50 X-ray source over **serial** (9600 8N1). **Connect** puts the unit in remote mode; **Disconnect** (or closing the app) sets it back to **front panel**. Set **kV**; use **Auto On/Off** so the beam follows capture (on before, off when idle), or **manual HV On / HV Off**. Exposure is driven by the app/camera; the Faxitron timer is set to max and the app sends **A** (Abort) when done.

**Protocol:** See project root **"Faxitron Serial Commands for MX-20 and D.md"**. State: `?SR` (Ready), `?SW` (Warming), `?SD` (Door open).

---

## Features

- **Connect:** Opens serial and sends **!MR** (remote mode).
- **Disconnect / App exit:** Sends **!MF** (front panel) then closes the port. Unit is never left in remote.
- **State:** Polled every few seconds; shown in the module as **Warming up / Ready / Door open**. Exposure is only allowed when **Ready**; kV can be set anytime when connected.
- **kV:** Slider 10–35 kV (`!V{kv}`). Used for both Auto On/Off and manual HV On.
- **Auto On/Off:** When enabled and connected, the app turns the beam on before starting a capture (except dark) and turns it off when acquisition goes idle. If state is not Ready (e.g. door open), acquisition does not start and a warning is shown.
- **Beam on (HV On or Start):** Send **!B** → machine replies **X** → send **C** → wait **2 s** for HV to settle → beam active. Exposure timer is set to max; app runs exposures then aborts.
- **Beam off (HV Off or when idle):** Send **A** (Abort) → machine replies **S** → beam off. Unit stays in remote mode.
- **HV On:** Manual – only enabled when state is Ready; runs !B→X→C, 2 s settle.
- **HV Off:** Manual – send **A**, wait for **S**.

---

## Beam supply contract

The module registers **`gui.api.register_beam_supply(BeamSupplyAdapter(...))`** so:

| Method | Behavior |
|--------|----------|
| **wants_auto_on_off()** | Reads the "Auto On/Off" checkbox. |
| **is_connected()** | True when serial port is open. |
| **turn_on_and_wait_ready(timeout_s, should_cancel)** | If state ≠ Ready: show warning, return False (acquisition not started). If Ready: **beam on** (!B→X→C, set kV and max exposure, 2 s settle), return True. |
| **turn_off()** | **Beam off**: send **A**, wait for **S**. (Unit stays in remote.) |

---

## Settings (persisted)

- **fax_mx20_serial_port** – Last selected port.
- **fax_mx20_kv** – kV (10–35).
- **fax_mx20_auto_on_off** – Auto On/Off checkbox.

---

## Enabling the module

1. **Settings** → enable **"Load Faxitron MX-20 DX-50 module"** (default is off).
2. Restart the app.
3. In the control panel, open **"Faxitron MX-20 / DX-50"**, select the serial port, click **Connect**. Use **Disconnect** or **File → Quit** to return to front panel; the app also restores front panel on exit.

---

## References

- [MODULES_OVERVIEW.md](../MODULES_OVERVIEW.md) – Supply modules and beam_supply contract.
- **Faxitron Serial Commands for MX-20 and D.md** (project root) – Serial protocol.
