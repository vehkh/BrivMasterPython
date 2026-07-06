# BrivMasterPython

A Python port of [Briv Master](https://github.com/RLee-EN/BrivMaster) - a
consolidated Briv gem-farming script for **Idle Champions of the Forgotten
Realms** - originally written in AutoHotkey v1 by Irisiri / R. Lee, itself
built on Script Hub / BrivGemFarm by MikeBaldi and Antilectual with features
derived from addons by ImpEGamer and Emmote. Full credit to those authors:
this port re-implements their design 1:1 (ported classes even keep the AHK
method names for side-by-side comparability).

**Why a port?** AutoHotkey has no Linux equivalent. Python does. Both
Windows and Linux versions are now complete and validated.

## Status

| Platform | State |
|---|---|
| **Windows** | Working - all test stages passed. Validated on a real end-game farm including a **73-hour / 5,582-run unattended soak at -2% BPH vs the AHK original** (20.5k BPH), with 0 fails on healthy servers: Ellywick Casino with re-rolls, combined Thellora+Briv start, feat-swap jump routing, online stacking (exact stack targets), offline/blank/relay restarts, Steelbones-to-Haste conversion saves, chest buying/daily platinum, game-settings profiles, and automatic crash recovery (~50s back to farming after a killed game). Full results in `TESTING.md`. |
| **Linux** | **Working** - Full X11 backend (window management, key injection), Heroic legendary launcher integration, cross-platform auto-detection, universal setup script. Memory reading via `process_vm_readv` and `/proc/<pid>/mem`. All test stages passed. Requires `kernel.yama.ptrace_scope=0` for memory access. Run with `python setup_and_run.py`. |
| **Mac** | **Supported** - X11 backend with same architecture as Linux. Untested (no hardware available) but should work. Requires `python-xlib` and Wine/Proton. |

What's included: the gem farm itself, the Home GUI (PySide6 - live status,
run control, all settings editors incl. a clickable route grid with
import/export strings compatible with Emmote's routes site, level manager +
Feat Guard, game-settings profiles, offsets download), chest buying/opening
and daily platinum claiming, the Ellywick non-gemfarm re-roll tool, relay
blank restarts, and the run Monitor. Not ported: the AHK theme colour table
(a dark-mode toggle replaces it).

## Requirements

- **Windows 10/11, Linux, or Mac** with Wine/Proton (Heroic recommended)
- **Python 3.10+, 64-bit** (64-bit is mandatory - the game is 64-bit;
  developed/tested on 3.12) - [python.org](https://www.python.org/downloads/)
- **PySide6** (installed automatically by the setup script; only needed for
  the GUI and Monitor - the farm itself is pure standard library)
- A modron core with full automation and the usual Briv gem-farm setup
  (Q/W/E/M formations) - see the
  [original BrivMaster README](https://github.com/RLee-EN/BrivMaster) for
  the gameplay setup, which applies unchanged
- **Offsets for your platform** from
  [BrivMaster-Imports](https://github.com/RLee-EN/BrivMaster-Imports)
  (not bundled, per upstream practice - they change with game versions).
  Place the `Offsets` folder in this directory, or download via the Home
  GUI (BM Game tab) once your settings point at the game
- If the game runs elevated (as admin), run these scripts from an elevated
  prompt too - otherwise memory access is denied

## Setup

**New users (all platforms - Windows, Linux, Mac):**

```bash
git clone https://github.com/vehkh/BrivMasterPython.git
cd BrivMasterPython
python setup_and_run.py    # Universal setup (auto-detects platform)
```

**Or manual setup:**

```bash
python setup_check.py      # Windows: double-click setup.bat
```

`setup_and_run.py` is a universal cross-platform setup script that:
- Auto-detects your OS (Windows/Linux/Mac)
- Installs dependencies (python-xlib, pynput)
- Sets up permissions (Linux ptrace)
- Validates Heroic/game installation
- Finds and configures game paths
- Launches the game and starts farming

`setup_check.py` verifies your Python (version/bitness), installs missing
libraries, imports every module, and tells you if the offsets/settings
files are missing. Safe to re-run any time (`--check` = report only).

Settings: if you have an existing AHK BrivMaster install, this port reads
its `IC_BrivMaster_Settings.json` automatically when the folders sit side
by side (or copy the file here). Starting fresh: launch the Home GUI,
configure, and Save Settings.

## Running

**All platforms (Windows, Linux, Mac):**

```bash
python -m brivmaster.home       # Home GUI (start/stop/monitor the farm, all settings)
python -m brivmaster.run_farm   # the farm itself (Home's Start button spawns this)
python -m brivmaster.monitor    # run monitor (reads MiniLog.json)
python -m brivmaster.run_farm --dry-run  # validate config without sending input
python tools/probe.py --wait 60 # read-only memory probe - validate offsets/attach
```

Or use the shorter launcher (cross-platform):

```bash
python run.py home              # Home GUI
python run.py farm              # gem farm  (add --dry-run to test without input)
python run.py monitor           # run monitor
python run.py probe --wait 60   # memory probe
python run.py setup             # environment check / install dependencies
```

**Windows only (optional - shortcuts):**
- Double-click `run.bat` for the Home GUI
- Or use PowerShell: `python run.py home`

## Documentation

- **`START_HERE.md`** - Quick navigation for new users
- **`QUICKSTART.md`** - Quick start guide (all platforms)
- **`HOW_TO_RUN.md`** - Detailed running instructions
- **`CROSS_PLATFORM.md`** - Cross-platform implementation details
- **`SETTINGS_BY_PLATFORM.md`** - Platform-specific configuration
- **`TESTING.md`** - Staged validation plan (passive probes -> input check -> supervised first run) - recommended before unattended use
- **`PORTING.md`** - Architecture overview and deliberate deviations from the AHK original
- **`setup_and_run.py`** - Universal setup script for all platforms

**Never run this and the AHK BrivMaster farm at the same time.**

## Linux & Mac Support

Idle Champions has no native Linux or Mac builds; it runs under Wine/Proton 
(via Heroic/Lutris - not a container, just a translation layer). The game is 
the same Windows binary on all platforms, so the entire offsets/memory system 
targets it uniformly.

**Linux implementation (complete and tested):**
- ✅ Memory backend: `process_vm_readv` + `/proc/<pid>/mem` for reading game state
- ✅ X11 input backend: pynput for key injection to Wine windows
- ✅ Window management: EWMH protocol for window discovery/activation/close
- ✅ Process management: find Wine processes, handle lifecycle
- ✅ Heroic launcher: legendary CLI integration with EGS authentication
- ✅ Universal setup script: auto-detects and configures everything
- ✅ Cross-platform auto-detection: selects correct backend per platform
- ✅ Ptrace permission: setup script configures `kernel.yama.ptrace_scope=0`

**Recommended setup:**
- **Linux:** Heroic with Wine/Proton-GE (or native Proton/Wine)
- **Mac:** Heroic with Wine/Proton-GE
- Run `python setup_and_run.py` to auto-configure everything

## Disclaimer

This automates gameplay by reading game memory, sending input and making
the same server calls the game makes - the same techniques as the AHK
original and Script Hub. Use at your own risk. Not affiliated with Codename
Entertainment. MIT licensed (see LICENSE for the full credit chain).
