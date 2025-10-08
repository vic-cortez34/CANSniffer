# CANSniffer — OBD Protocol Setup + CAN Monitor

A PyQt5 desktop app that combines OBD protocol detection/configuration over a serial ELM/STN-style adapter with a SavvyCAN‑like CAN monitor UI. The main entry point is `main.py`. The CAN monitor used by the app lives under `Logger_analyzer/can_sniffer_sim.py`.

## Features
- OBD protocol setup tab
  - Connect to a serial port (ELM327/STN1110‑compatible).
  - Runs an initialization and detection flow (ATZ/ATWS/ATE0/ATS0/ATSP0, 0100, STPRS, FECA) to determine OBD vs J1939 and sets ATSP when applicable.
  - Live log window and ability to send manual commands (e.g., `STPRS`, `0100`).
- CAN monitor tab
  - Launches a SavvyCAN‑like table viewer (timestamp, frequency, ID, b0..b7).
  - Continuous or Latched modes, with per‑byte coloring on changes.
  - Includes example DBC/CSV files in `Logger_analyzer/` (bundled into the build).

## Requirements
- Python 3.8+ (tested with 3.12)
- Windows (build instructions target PyInstaller on Windows)
- Dependencies (see `requirements.txt`):
  - PyQt5, pyqtgraph, pyserial, requests, openai (not required for basic use but listed by the project)

## Install (PowerShell)
```powershell
python -m pip install --upgrade pip ; pip install -r requirements.txt
```

Optionally, use a virtual environment:
```powershell
python -m venv .venv ; .\.venv\Scripts\Activate.ps1 ; pip install -r requirements.txt
```

## Run the App
```powershell
python .\main.py
```

### Using the OBD Protocol Setup tab
1. Click Refresh and select your COM port (e.g., `COM3`).
2. Choose a baud rate (default 115200).
3. Click “Connect & Detect”. The log will show the AT/diagnostic sequence and detected protocol (OBD or J1939‑based).
4. Use the “Command” box to send manual commands (e.g., `STPRS`, `0100`).

### Using the CAN Monitor tab
- Click “Open CAN Monitor” to launch the window powered by `Logger_analyzer/can_sniffer_sim.py`.
- Switch between Continuous/Latched modes, filter high‑rate IDs, and clear the table as needed.

## Run the CAN Monitor by itself (optional)
```powershell
python .\Logger_analyzer\can_sniffer_sim.py
```

## Build a Windows EXE with PyInstaller

This project includes a ready‑to‑use spec file `CANSniffer.spec` that:
- Adds required hidden imports for PyQt5 and pyserial
- Bundles the entire `Logger_analyzer/` folder (DBC/CSV/etc.)
- Produces a single GUI app named `CANSniffer`

### 1) Install PyInstaller
```powershell
pip install pyinstaller
```

### 2) Build using the spec file
```powershell
pyinstaller .\CANSniffer.spec
```

On success, the executable will be at:
- `dist\CANSniffer\CANSniffer.exe`

Run it from PowerShell:
```powershell
.\dist\CANSniffer\CANSniffer.exe
```

Notes:
- The spec builds a windowed app (`console=False`). Set to `True` in the spec for a console window during debugging.
- If UPX is installed, it will be used (`upx=True`). You can disable if issues arise.
- To change the app icon, set the `icon` path in `CANSniffer.spec` to a `.ico` file.

### Alternative: one‑line build (without the spec)
If you prefer a direct command (the spec is recommended):
```powershell
pyinstaller --name CANSniffer --noconsole --add-data "Logger_analyzer;Logger_analyzer" .\main.py
```
Depending on your setup, you may also need hidden imports similar to those in `CANSniffer.spec` (e.g., `serial.tools.list_ports`, `PyQt5.sip`).

## Troubleshooting
- Missing Qt DLLs or startup errors: ensure PyQt5 is installed in the same environment and that you’re launching the built EXE from `dist\CANSniffer` with all bundled files intact.
- Serial/COM port not listed: click Refresh and confirm the adapter shows up in Windows Device Manager; install drivers if needed.
- “Stopped” responses during `STPRS`: the app retries automatically; check adapter compatibility and baud rate.

## Repository Layout (key files)
- `main.py` — main GUI app (OBD setup + CAN monitor launcher)
- `Logger_analyzer/can_sniffer_sim.py` — CAN monitor window implementation
- `Logger_analyzer/*.dbc`, `Logger_analyzer/*.csv` — example data bundled into the build
- `CANSniffer.spec` — PyInstaller spec to create the Windows executable
- `requirements.txt` — Python dependencies

