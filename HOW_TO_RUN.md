# How to Run BrivMaster Farm on Linux

## TL;DR - New Users

```bash
cd PyBrivMaster
bash setup_and_run.sh
```

The script handles everything. Just follow the prompts.

---

## Detailed: How It Works

### Architecture
The farm consists of 3 processes communicating via TCP/JSON:

1. **Farm** (`brivmaster.run_farm`) - Main loop
   - Reads game state (memory)
   - Sends input (keys)
   - Manages game restarts
   - Logs runs

2. **Home GUI** (optional) - Settings and monitoring
   ```bash
   python3 -m brivmaster.home
   ```

3. **Monitor** (optional) - BPH tracking
   ```bash
   python3 -m brivmaster.monitor
   ```

### Running the Farm

#### Simplest: Auto-setup & Launch (All Platforms)
```bash
# Windows, Linux, or Mac - same command
python3 setup_and_run.py
```

The script auto-detects your OS and:
- Installs dependencies
- Sets up permissions (Linux only)
- Validates Heroic/game/offsets
- Launches the game
- Starts the farm

#### Manual: Just Run It
```bash
python3 -m brivmaster.run_farm
```

#### With Options
```bash
# Validate config (no input to game)
python3 -m brivmaster.run_farm --dry-run

# Custom paths
python3 -m brivmaster.run_farm \
  --settings /path/to/IC_BrivMaster_Settings.json \
  --offsets /path/to/IC_Offsets.json \
  --logs /path/to/Logs
```

---

## What Happens When You Run It

### 1. Initialization (10-30 seconds)
- Loads settings and offsets
- Connects to game (memory reads)
- Sets up input system
- Validates configuration (pre-flight check)
- Launches game if not running

### 2. Main Loop (continuous)
```
z1: Leveling + Casino → z281: Combine + Casino → Jump → Stack → Reset → Repeat
└─ Each cycle: 50-60 seconds
└─ Runs logged to Logs/RunLog_*.csv
└─ Every X runs: Game restarts automatically
```

### 3. Stopping
- Press **Ctrl+C** to stop cleanly
- Farm saves state before exiting

---

## File Locations

```
PyBrivMaster/
├── setup_and_run.sh           ← One-command setup (new users)
├── QUICKSTART.md              ← Quick reference
├── HOW_TO_RUN.md             ← This file
├── brivmaster/
│   ├── run_farm.py           ← Farm entry point
│   ├── home/                 ← Home GUI (optional)
│   ├── platform/
│   │   ├── x11.py           ← Linux input/window backend ✓
│   │   ├── win32.py         ← Windows (reference)
│   │   └── input.py         ← Input manager
│   ├── memory/              ← Game state reading
│   ├── farm/                ← Farm logic (Casino, routing, etc.)
│   └── ...
├── tools/
│   ├── probe.py            ← Memory validator
│   ├── input_probe.py      ← Input tester
│   └── ...
├── Offsets/                ← Game offsets (copy from Windows)
└── requirements.txt        ← Dependencies

../BrivMaster/
├── IC_BrivMaster_Settings.json    ← Shared settings
├── Offsets/                       ← Shared offsets
└── Logs/                          ← Run logs
```

---

## Settings & Configuration

### Basic Settings (IC_BrivMaster_Settings.json)

**Farming strategy:**
```json
"IBM_Route_Combine": 281,           // Combine zone
"IBM_Stack_Modron_Freq": 1345,     // Modron reset zone
"IBM_Offline_Freq": 1              // Offline/blank restart cycle
```

**Leveling:**
```json
"IBM_Feat_Guard": 1,               // Avoid early resets
"IBM_Favour_Limit": 1,             // Min favor before reset
"IBM_Max_Thellora_Stacks": 15      // Thellora jump stacks
```

**Game control:**
```json
"IBM_Game_Hide_Launcher": 0,       // Show/hide game window
"IBM_Game_Launch": "..."           // Launch command (auto on Linux)
```

### Keyboard Bindings (IBM_Scan_Codes)
Can override Windows scan codes for custom layouts:
```json
"IBM_Scan_Codes": {
  "q": 16,                         // Q = formation 1
  "w": 17,                         // W = formation 2
  "e": 18,                         // E = formation 3
  "g": 34,                         // G = autoprogress
  "f1": 59,                        // F1-F12 = level keys
  ...
}
```

---

## Output & Logging

### Console Output
```
Settings: /path/to/IC_BrivMaster_Settings.json
Offsets:  /path/to/IC_Offsets.json
Logs:     /path/to/Logs

PRE-FLIGHT OK
Strategy: Combining to z281 following by Casino, jumping 15&10z/J to reset at z1345. Using 566 stacks...
```

### Run Logs (CSV format)
```
Logs/RunLog_20260706T160608.csv

Reset #,Start Time,Start Tick,Total,Active,Wait,Load,Reset,Cycle,Fail,LastZone,Electrum,...
1306469,2026-07-06T16:02:55,2865342,57404,47314,10090,0,10090,1,False,1345,927,...
1306470,2026-07-06T16:06:09,2931746,65312,55875,9437,0,9437,1,False,1345,932,...
```

### Breakdown:
- `Reset #` - Run counter (continues across restarts)
- `Total` - Time for entire run (ms)
- `Active` - Time advancing zones (ms)
- `Wait` - Time stuck/waiting (ms)
- `LastZone` - Highest zone reached
- `Electrum` - Gems earned

### Calculate BPH
```python
bph = (gems_per_run / total_time_hours) * 1000
# Or just watch the monitor or Home GUI
```

---

## Monitoring Progress

### Option 1: Watch Log File
```bash
tail -f Logs/RunLog_*.csv
```

### Option 2: Use Monitor App (optional)
```bash
# Enable mini log in settings first
# (or Home GUI: Settings tab → "Output mini log")

python3 -m brivmaster.monitor
```

### Option 3: Home GUI (optional)
```bash
python3 -m brivmaster.home
```
- Live status display
- BPH/GPH stats
- Settings editor
- Control running farm via IPC

---

## Troubleshooting

### Farm won't start
```bash
# Check what's wrong
python3 -m brivmaster.run_farm --dry-run

# Check memory reads
python3 tools/probe.py
```

### Game won't auto-launch
- Make sure game is installed via Heroic
- Launch once manually from Heroic GUI
- Verify: `ls ~/Games/Heroic/IdleChampions/IdleDragons.exe`

### Memory reads failing
```bash
# Check ptrace permission
cat /proc/sys/kernel/yama/ptrace_scope   # Should print 0

# If not 0, set it:
sudo sysctl kernel.yama.ptrace_scope=0
```

### No runs being logged
- Make sure game is in an adventure (not menu)
- Check for errors: `python3 -m brivmaster.run_farm 2>&1 | head -50`
- First cycle may take 2-5 minutes to complete

---

## Advanced: Multiple Instances

You can run multiple farms on the same machine:
```bash
# Terminal 1
python3 -m brivmaster.run_farm --logs /tmp/farm1

# Terminal 2
python3 -m brivmaster.run_farm --logs /tmp/farm2
```

Each uses its own:
- Log directory
- Settings file (shared)
- Game process (separate)
- IPC port (auto-assigned)

---

## Common Settings Adjustments

### Farm slower on weak PC?
```json
"IBM_OffLine_Freq": 2,       // More offline restarts (faster)
"IBM_Offline_Timeout": 10    // Longer timeout (more stable)
```

### Need faster BPH?
```json
"IBM_Route_Combine": 350,    // Jump higher before combining
"IBM_Stack_Modron_Freq": 1200  // Reset earlier
```

### Custom leveling?
Edit `IBM_Leveling_Config` with champion priorities and key combinations.

---

## Next Steps

1. Run: `bash setup_and_run.sh`
2. Monitor: `tail -f Logs/RunLog_*.csv`
3. Tweak settings in Home GUI or JSON
4. Watch BPH improve over time

**That's it!** The farm handles everything else automatically.
