# BrivMaster Python Farm - Quick Start Guide (Linux)

## For New Users: The Easy Way

### 1. Prerequisites
- **Python 3.10+** (64-bit)
- **Heroic Launcher** installed (https://heroicgameslauncher.com)
- **Idle Champions** installed via Heroic
- Game launched at least once from Heroic (to set up authentication)

### 2. One-Command Setup & Launch (Works on Windows, Linux, Mac)

```bash
cd PyBrivMaster
python3 setup_and_run.py
```

That's it! The script will:
- ✓ Check Python version
- ✓ Install dependencies (python-xlib, pynput)
- ✓ Verify Heroic installation
- ✓ Set up memory-read permissions
- ✓ Validate offsets and settings
- ✓ Test memory reads
- ✓ Launch the game
- ✓ Start the farm

---

## For Experienced Users: Manual Setup

### Step 1: Install Dependencies
```bash
python3 -m pip install -r requirements.txt
```

### Step 2: Grant Memory-Read Permission
```bash
sudo sysctl kernel.yama.ptrace_scope=0
```

### Step 3: Copy Settings & Offsets
Copy from Windows BrivMaster install:

**Offsets:**
```
PyBrivMaster/Offsets/
├── IC_Offsets.json
├── IC_EngineSettings_Import.ahk
├── IC_GameSettings_Import.ahk
└── IC_IdleGameManager_Import.ahk
```

**Settings:**
Also copy `IC_BrivMaster_Settings.json`, but update it for Linux:
```json
{
  "IBM_Game_Exe": "IdleDragons.exe",
  "IBM_Game_Launch": "/usr/lib64/heroic/resources/app.asar.unpacked/build/bin/x64/linux/legendary"
}
```

See [SETTINGS_BY_PLATFORM.md](SETTINGS_BY_PLATFORM.md) for platform-specific details.

### Step 4: Test Memory Reads
```bash
python3 tools/probe.py
```
Expected output: `RESULT: OK` with gems, zones, formations visible.

### Step 5: Run the Farm
```bash
# Launch game first (manually from Heroic)
# Load the gem-farm adventure
# Then:

python3 -m brivmaster.run_farm
```

---

## Running the Farm

### Basic Usage
```bash
# Start farming (auto-launches game if needed)
python3 -m brivmaster.run_farm

# Validate configuration (no input sent to game)
python3 -m brivmaster.run_farm --dry-run
```

### Stopping the Farm
- **Ctrl+C** in the terminal

### Logs & Results
```
Logs/
├── RunLog_20260706T*.csv         # Run stats (BPH, zone, time)
├── MiniLog.json                  # Latest run summary (if enabled)
└── (historic logs from Windows)
```

---

## Configuration

### Settings File
`../BrivMaster/IC_BrivMaster_Settings.json`

Key settings:
- `IBM_Route_Combine` - zone to combine to (default: 281)
- `IBM_OffLine_Freq` - cycles between offline restarts (1 = every run)
- `IBM_Scan_Codes` - custom keyboard layout

### Game Settings
- Auto-progress toggle, leveling priorities, formation keys
- Edit in the Windows AHK Home GUI or directly in JSON

---

## Troubleshooting

### "Could not attach to game"
- Make sure the game is running
- Load into an adventure (gem-farm)
- Check ptrace permission: `cat /proc/sys/kernel/yama/ptrace_scope` (should be 0)

### "Offsets not found"
- Copy from Windows BrivMaster install
- Or download via Home GUI (when available)

### "Memory reads returning <no read>"
- Game version may have changed
- Update offsets from BrivMaster-Imports repo

### Game doesn't auto-launch
- Make sure Heroic is installed
- Launch game manually from Heroic first
- Check Heroic path: `/usr/lib64/heroic/resources/app.asar.unpacked/build/bin/x64/linux/legendary`

---

## Features

✓ Automatic game restarts (via Heroic)  
✓ Casino re-rolling at z281  
✓ Dynamic formation swapping (Q/E/W)  
✓ Online and offline stacking  
✓ Stack-conversion server calls  
✓ Modron reset automation  
✓ Kill-recovery (respawn on crash)  
✓ Run logging (BPH stats)  

---

## What's Different from Windows AHK Version?

- Linux/Wine: Game runs natively under Wine/Proton
- Input: Keys via pynput instead of Win32 SendMessage
- Launch: Heroic's legendary CLI instead of EGS launcher
- Memory: process_vm_readv instead of ReadProcessMemory

**Functionality:** Identical to AHK version (see PORTING.md for details)

---

## Need Help?

1. Check `TESTING.md` for test results and validation steps
2. Read `LINUX_PORT.md` for port-specific details
3. See `README.md` for all settings and configuration options
