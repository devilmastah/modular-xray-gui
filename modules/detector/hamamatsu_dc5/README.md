# Hamamatsu C9730DK-11 / C9732 camera module

Camera module for **Hamamatsu C9730DK-11** and **C9732DK / C9732DT** USB X-ray sensors. The driver is reimplemented in this app (no code copied from external projects); protocol is USB bulk + control commands via libusb1.

## Requirements

- **libusb1** (`pip install libusb1`) — already in `app/requirements.txt`
- On Windows: install a libusb-based driver for the device (e.g. Zadig) so the camera is not claimed by a vendor driver

## Supported hardware

| Model        | USB PID  | Resolution   |
|-------------|----------|--------------|
| C9730DK-11 | 0xA802 | 1032×1032    |
| C9732DK     | 0xA800 | 2368×2340    |
| C9732DT     | 0x4500 | 2368×2340    |

Vendor ID: **0x0661**. 14-bit pixel range (0–16383). Exposure **30 ms–10 s** (hardware may clamp above ~2 s; longer options provided so you can try).

## Usage

1. Enable **Hamamatsu C9730DK-11 / C9732** in **Settings** (applies on next startup).
2. Restart the app; the Connection panel will show **Connection (Hamamatsu DC5/DC12)**.
3. Click **Connect**; the app opens the first detected C9730DK-11 or C9732 device, runs the init sequence, and sets exposure from the integration-time dropdown.
4. Use **Single Shot**, **Dual Shot**, **Continuous**, or **Capture N** as with other camera modules. Integration time choices are 0.03 s–10 s.

Dark and flat reference capture are handled by the **dark_correction** and **flat_correction** alteration modules (same as for other cameras).

## Implementation

- **Driver:** `app/lib/hamamatsu_dc5.py` — open by VID/PID, init sequence, `set_exp` / `get_exp`, trigger, synchronous bulk read with MSG_BEGIN / MSG_END sync-word parsing, 16-bit LE → float32 decode, 180° rotation, abort/drain.
- **Module:** `app/modules/camera/hamamatsu_dc5/` — Connection UI, `HamamatsuDC5Module` (get_frame_size, is_connected, start_acquisition worker, stop_acquisition, disconnect, get_acquisition_modes, get_integration_choices, get_current_gain=0, get_sensor_bit_depth=14).
