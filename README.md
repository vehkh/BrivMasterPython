# BrivMasterPython

A Python port of [Briv Master](https://github.com/RLee-EN/BrivMaster) - a
consolidated Briv gem-farming script for **Idle Champions of the Forgotten
Realms** - originally written in AutoHotkey v1 by Irisiri / R. Lee, itself
built on Script Hub / BrivGemFarm by MikeBaldi and Antilectual with features
derived from addons by ImpEGamer and Emmote. Full credit to those authors:
this port re-implements their design 1:1 (ported classes even keep the AHK
method names for side-by-side comparability).

**Why a port?** AutoHotkey has no Linux equivalent. Python does. The
Windows version is complete and validated; **Linux support is the next
phase** (see below).

## Status

| Platform | State |
|---|---|
| **Windows** | Working - all test stages passed. Validated on a real end-game farm including a **73-hour / 5,582-run unattended soak at -2% BPH vs the AHK original** (20.5k BPH), with 0 fails on healthy servers: Ellywick Casino with re-rolls, combined Thellora+Briv start, feat-swap jump routing, online stacking (exact stack targets), offline/blank/relay restarts, Steelbones-to-Haste conversion saves, chest buying/daily platinum, game-settings profiles, and automatic crash recovery (~50s back to farming after a killed game). Full results in `TESTING.md`. |
| **Linux** | **Next phase - not yet functional.** The memory layer is written for Linux (the game under Wine/Proton is the same Windows binary, so the offsets system carries over) but unvalidated, and the X11 input/window backend does not exist yet. See "Linux roadmap" below. |

What's included: the gem farm itself, the Home GUI (PySide6 - live status,
run control, all settings editors incl. a clickable route grid with
import/export strings compatible with Emmote's routes site, level manager +
Feat Guard, game-settings profiles, offsets download), chest buying/opening
and daily platinum claiming, the Ellywick non-gemfarm re-roll tool, relay
blank restarts, and the run Monitor. Not ported: the AHK theme colour table
(a dark-mode toggle replaces it).

## Requirements

- **Windows 10/11** (Linux: next phase)
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

```powershell
git clone https://github.com/vehkh/BrivMasterPython.git
cd BrivMasterPython
python setup_check.py     # or double-click setup.bat
```

`setup_check.py` verifies your Python (version/bitness), installs missing
libraries, imports every module, and tells you if the offsets/settings
files are missing. Safe to re-run any time (`--check` = report only).

Settings: if you have an existing AHK BrivMaster install, this port reads
its `IC_BrivMaster_Settings.json` automatically when the folders sit side
by side (or copy the file here). Starting fresh: launch the Home GUI,
configure, and Save Settings.

## Running

```powershell
python -m brivmaster.home       # Home GUI (start/stop/monitor the farm, all settings)
python -m brivmaster.run_farm   # the farm itself (Home's Start button spawns this)
python -m brivmaster.monitor    # run monitor (reads MiniLog.json)
python tools\probe.py --wait 60 # read-only memory probe - validate offsets/attach
```

Or use the shorter launcher (double-click `run.bat` for the Home GUI, or
pass a command). These are exactly equivalent to the `-m` commands above:

```powershell
python run.py home              # Home GUI
python run.py farm              # gem farm  (add --dry-run to test without input)
python run.py monitor           # run monitor
python run.py probe --wait 60   # memory probe
python run.py setup             # environment check / install dependencies
```

`TESTING.md` contains a staged validation plan (passive probes -> input
check -> supervised first run) - recommended before unattended use.
`PORTING.md` documents the architecture and every deliberate deviation from
the AHK original.

**Never run this and the AHK BrivMaster farm at the same time.**

## Linux roadmap (next phase)

The whole point of this port. Idle Champions has no native Linux build; it
runs under Wine/Proton (via Heroic/Lutris - not a container, just a
translation layer), which means the game is the same Windows binary and the
entire offsets/memory system here already targets it:

- **Done, unvalidated:** Linux memory backend (`process_vm_readv` +
  `/proc/<pid>/maps`), process discovery for Wine processes,
  SIGSTOP/SIGCONT relay hold. Requires `kernel.yama.ptrace_scope=0` or
  `CAP_SYS_PTRACE`.
- **To do:** X11 input backend (XSendEvent to the Wine window, XTEST
  fallback) and window management (find/activate/close via EWMH); launch
  integration for Heroic/Lutris.
- Recommended runtime will be **Heroic with Wine/Proton-GE** (plain wine
  process, no Steam pressure-vessel container in the way).

## Disclaimer

This automates gameplay by reading game memory, sending input and making
the same server calls the game makes - the same techniques as the AHK
original and Script Hub. Use at your own risk. Not affiliated with Codename
Entertainment. MIT licensed (see LICENSE for the full credit chain).
