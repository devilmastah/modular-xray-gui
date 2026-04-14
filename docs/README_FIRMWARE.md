# Teensy firmware (Hamamatsu C7942 interface)

Firmware for **Teensy 4.1** that bit-bangs the **Hamamatsu C7942** parallel output, packs **12-bit** pixels, and exposes a **custom USB bulk protocol** used by the desktop app (`lib/hamamatsu_teensy.py`, detector module `modules/detector/hamamatsu_c7942`).

This tree is **not** used for **Hamamatsu DC5 / DC12** USB cameras (`lib/hamamatsu_dc5.py`); those sensors use their own factory USB firmware.

---

## 1. Location in the repository

```
firmware/firmware/hamamatsu_interface/
├── platformio.ini          # Teensy 4.1 env, pre-build patch script
├── src/main.cpp            # Sensor readout + USB command handler
├── core_patches/           # Copies into PlatformIO’s Teensy 4 core at build time
│   ├── apply.py            # PlatformIO extra_script (runs before build)
│   ├── usb_desc.h / .c     # VID/PID, custom interface + endpoints
│   └── usb_custom.*        # Bulk transfer support for large frames
└── .pio/                   # Local build output (optional to keep; can be regenerated)
```

The nested `firmware/firmware/` folder is historical; treat **`hamamatsu_interface`** as the actual PlatformIO project root.

---

## 2. Prerequisites

- **PlatformIO** — [Install CLI](https://platformio.org/install/cli) or use the PlatformIO extension in VS Code / Cursor.
- **Python 3** — Required on PATH so PlatformIO can run `core_patches/apply.py` during the build.
- **Teensy 4.1** with enough **PSRAM** for a full **2400×2400** 12-bit packed frame (~8.6 MiB packed buffer in firmware; see `main.cpp` comments).
- **USB driver (Windows)** — For reliable use of the custom interface on every port, see **[TEENSY_USB_WINDOWS.md](TEENSY_USB_WINDOWS.md)** (WinUSB / Zadig).

---

## 3. What the build does

Before compiling, **`core_patches/apply.py`** copies patched core files from `core_patches/` into PlatformIO’s cached **Teensy 4** core directory (`framework-arduinoteensy` → `cores/teensy4`). That overrides stock USB descriptors and adds the custom bulk path the PC app expects.

USB identity matches the Python driver:

- **VID** `0x16C0`, **PID** `0x0483`
- **Custom interface** index **2**
- **Bulk endpoints** **5** (host → device), **6** (device → host), **7** (large frame data)

If you update PlatformIO or the Teensy platform package, the next build re-applies these copies.

---

## 4. Compiling

Open a terminal at the **PlatformIO project** directory:

```bash
cd firmware/firmware/hamamatsu_interface
pio run
```

On Windows (PowerShell or cmd), use the same path from your repo root (adjust if your clone lives elsewhere):

```powershell
cd "d:\Dropbox\XRAY AND CT\New Python CT app modular\modular-xray-gui\firmware\firmware\hamamatsu_interface"
pio run
```

Successful output ends with a linked firmware (e.g. `.pio/build/teensy41/firmware.hex`).

**Notes**

- `platformio.ini` sets **`src_filter`** to exclude `main_working_backup.cpp` so only one `main` is linked.
- Optimization: **`build_flags = -O2`**.

---

## 5. Uploading to the board

With the Teensy connected over USB:

```bash
pio run -t upload
```

If the wrong serial port is chosen, list devices and specify the port (examples):

```bash
pio device list
pio run -t upload --upload-port COM7
```

On Linux, the default `monitor_port` in `platformio.ini` is `/dev/ttyACM0`. On Windows, change **`monitor_port`** in `platformio.ini` to your **`COMx`** for `pio device monitor`, or pass **`--port`** when starting the monitor.

---

## 6. Serial monitor (optional)

Debug prints go to the USB serial (CDC) side of the composite device, not the custom bulk interface the app uses for frames.

```bash
pio device monitor
```

Baud is usually ignored for USB CDC; use the correct port for your OS.

---

## 7. Relationship to the Python application

| Piece | Role |
|--------|------|
| `src/main.cpp` | Commands `0x00`–`0x03`, `0x10` (Faxitron passthrough), state struct for `0x01` |
| `lib/hamamatsu_teensy.py` | Opens `0x16C0:0x0483`, claims interface **2**, same endpoints |
| `modules/detector/hamamatsu_c7942` | GUI + acquisition thread using `HamamatsuTeensy` |

After flashing, verify from the repo root with a venv that has **`libusb1`** installed:

```bash
python -c "from lib.hamamatsu_teensy import HamamatsuTeensy; HamamatsuTeensy().ping(); print('OK')"
```

(Run from the **repository root** where `lib/` and `gui.py` live, with that directory on `PYTHONPATH` or after `cd` to it.)

---

## 8. Troubleshooting

- **Build fails in `apply.py`** — Ensure Python 3 runs `import shutil` and that PlatformIO downloaded `framework-arduinoteensy` (first build may take longer).
- **Upload fails** — Press the Teensy **Program** button; try another USB cable/port; close serial monitors holding the port.
- **App finds no device** — Driver issue on Windows: see [TEENSY_USB_WINDOWS.md](TEENSY_USB_WINDOWS.md). On Linux, **udev** rules may be needed for non-root access.
- **Wrong or old protocol** — Mixing very old Teensy firmware with current `hamamatsu_teensy.py` can break `get_state()` sizes; current firmware uses an **18-byte** state blob; the Python code also accepts a **26-byte** extended layout when present.

---

## 9. Upstream / lineage

The application README credits **[robbederks/hamamatsu_interface](https://github.com/robbederks/hamamatsu_interface)** (custom PCB + Teensy firmware + host software). This repo’s firmware folder is the maintained copy for this GUI; keep it in sync with hardware wiring (`main.cpp` pin defines) on your bench.
