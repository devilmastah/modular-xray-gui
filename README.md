# X-ray Acquisition Application

A generic X-ray acquisition shell with modular hardware support. Provides viewport, acquisition controls, dark/flat correction, banding and dead-pixel correction, and export.

**Origin.** This project is based on the work at [robbederks/hamamatsu_interface](https://github.com/robbederks/hamamatsu_interface) (custom USB interface PCB + firmware + software for the Hamamatsu C7921CA / C7942 X-ray sensor).

**Use.** Use it for whatever you want. New modules are welcome—see [MODULES_OVERVIEW.md](modules/MODULES_OVERVIEW.md) and [README_DETECTOR_MODULES.md](modules/README_DETECTOR_MODULES.md) for how to add them.

## Requirements

- **Python 3.8 or higher** (tested with Python 3.13.2)
- Required Python packages (see `requirements.txt`)

### Optional: ASI Camera Module

If you want to use the ASI camera module (ZWO ASI cameras), you'll also need:

- **ZWO ASI SDK** – Download from [ZWO website](https://astronomy-imaging-camera.com/software-drivers)
- The SDK DLLs (`ASICamera2.dll` and related files) should be either:
  - Placed in `app/resources/asi_sdk/` folder, or
  - Added to your system PATH, or
  - Set the `ASI_SDK_PATH` environment variable to point to the SDK directory

The application will automatically detect the SDK if it's in any of these locations.

**Note:** On Windows, copying the DLL files to `C:\Windows\System32\` usually works well and doesn't require PATH configuration.

### Optional: Hamamatsu C7942 (Teensy) – USB on Windows

If you use the Hamamatsu C7942 camera module, the Teensy 4.1 is connected over USB. To have it work on **all USB ports** (not just one), install the WinUSB driver for the Teensy on each port. See **[Teensy USB on Windows](docs/TEENSY_USB_WINDOWS.md)** for step-by-step instructions (Zadig, one-time per port).

## Quick Start

### For Users Familiar with Python

1. Install dependencies: `pip install -r requirements.txt`
2. Run: `python gui.py`

### Detailed Setup Guide (For Beginners)

If you're new to Python on Windows, follow these steps:

#### Step 1: Install Python

1. Download Python from [python.org](https://www.python.org/downloads/)
   - Choose the latest Python 3.x version for Windows
   - Download the "Windows installer (64-bit)" if you're on a 64-bit system (most modern PCs)
2. Run the installer:
   - **Important:** Check the box "Add Python to PATH" at the bottom of the installer window
   - Click "Install Now"
3. Verify installation:
   - Press `Win + R`, type `cmd`, and press Enter to open Command Prompt
   - Type: `python --version`
   - You should see something like `Python 3.x.x`
   - Type: `pip --version` (or `python -m pip --version`)
   - You should see something like `pip x.x.x from ...`
   
   **Note:** Modern Python installations (3.4+) include `pip` automatically, so you shouldn't need to install it separately.

#### Step 2: Navigate to the Application Directory

1. Open File Explorer and navigate to where you extracted/downloaded the project
2. Navigate into the `app` folder
3. In the address bar, type `cmd` and press Enter (this opens Command Prompt in that folder)
   
   **Alternative method:**
   - Open Command Prompt (`Win + R`, type `cmd`, press Enter)
   - Use `cd` to navigate, for example:
     ```
     cd C:\Users\YourName\Desktop\hamamatsu_interface-master\app
     ```
     (Replace with your actual path)

#### Step 3: Install Required Packages

1. In Command Prompt (make sure you're in the `app` folder), type:
   ```
   pip install -r requirements.txt
   ```
   Press Enter and wait for all packages to download and install.
   
   This downloads and installs all the libraries the application needs (numpy, dearpygui, etc.)
   
   **Note:** If you see permission errors, try:
   ```
   pip install --user -r requirements.txt
   ```

#### Step 4: Run the Application

1. In Command Prompt (still in the `app` folder), type:
   ```
   python gui.py
   ```
   Press Enter
2. The application window should open. If you see any error messages, check that all dependencies installed correctly in Step 3.

#### Troubleshooting

- **"python is not recognized"**: Python is not in your PATH. Reinstall Python and make sure to check "Add Python to PATH" during installation
- **"pip is not recognized"**: Try `python -m pip install -r requirements.txt` instead
- **Permission errors**: Use `pip install --user -r requirements.txt` to install packages for your user only
- **Module not found errors**: Make sure you're in the `app` directory when running `python gui.py` (check by typing `cd` and pressing Enter - it should show the `app` folder path)

## Documentation

- **[GUI Overview](docs/README_GUI.md)** - Main application structure, frame pipeline, acquisition flow
- **[Architecture](docs/ARCHITECTURE.md)** - Design principles, data flow, module integration
- **[Code Reference](docs/CODE_REFERENCE.md)** - API reference, module types, entry points
- **[Module Overview](modules/MODULES_OVERVIEW.md)** - Camera, supply, and workflow modules
- **[Detector Modules](modules/README_DETECTOR_MODULES.md)** - Detector module contract and implementation guide

## Structure

### Core Application
- **`gui.py`** - Main application entry point (run this)

### Library Code (`lib/`)
- **`app_api.py`** - Application API facade for modules
- **`settings.py`** - Settings and profile management
- **`image_viewport.py`** - Image display and interaction
- **`hamamatsu_teensy.py`** - Shared hardware library (legacy, used by some modules)

### Modules (`modules/`)
- Loadable hardware modules (cameras, supplies, image alterations, workflow automation)
- Automatically discovered at runtime
- Each module provides its own defaults and settings

### Data Directories
- **`darks/`** - Dark field reference images (organized by camera)
- **`flats/`** - Flat field reference images (organized by camera)
- **`captures/`** - Saved capture images
- **`profiles/`** - Saved configuration profiles

### Resources
- **`docs/`** - Documentation
- **`resources/`** - Resource files (SDK DLLs, etc.)
- **`experiments/`** - Experimental/test scripts

## Features

- **Modular hardware support** - Loadable camera and supply modules
- **Image processing pipeline** - Dark/flat correction, banding, dead pixel, distortion correction
- **Multiple acquisition modes** - Single shot, dual shot, continuous, capture N
- **Dark/flat reference management** - Automatic loading by integration time and gain
- **Profile system** - Save and load different configurations
- **Export** - Save processed images as TIFF

## Module System

The application discovers modules automatically from `modules/`. No code changes needed to add new hardware support. Each module:
- Declares its metadata via `MODULE_INFO`
- Provides default settings via `get_default_settings()`
- Implements UI via `build_ui()`
- Handles settings persistence via `get_settings_for_save()`

See [MODULES_OVERVIEW.md](modules/MODULES_OVERVIEW.md) for details.
