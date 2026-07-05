# PyBrivMaster

Python port of [Briv Master](https://github.com/RLee-EN/BrivMaster) (an Idle
Champions gem farming script, originally AutoHotkey v1) targeting **Linux**,
where the game runs under Wine/Proton via Heroic/Lutris/Steam.

Because the game on Linux is the *same Windows binary*, the entire offsets
system (`IC_Offsets.json` + the generated `IC_*_Import.ahk` files from
[BrivMaster-Imports](https://github.com/RLee-EN/BrivMaster-Imports)) works
unchanged - this port parses the generated import files directly.

## Status

The port is Windows-first (so it can be compared 1:1 against the AHK
original); Linux support is a separate track picked up afterwards.

**Status (2026-07-05): functionally complete on Windows.** Live-validated
2026-07-02 - the farm ran 6 full unattended cycles on the real game with 0
fails (Casino, feat-swap jumps, online stacking Tar=Gen=432, stack-conversion
server calls, ~19k BPH); see the results table in TESTING.md. Since then:
full settings editors, game-settings profiles, offsets download, stats, and
setup_check.py were added (offline-verified). Remaining work: the leftover
live-test stages in TESTING.md and the Linux track.

| Phase | Contents | State |
|---|---|---|
| 1 | Memory layer (process reader, structures, imports parser, probe CLI) | **done** - live-validated |
| 2 | Platform layer: key injection, window/process control, server calls | **done** - live-validated (incl. active input, `tools/input_probe.py`) |
| 3 | Farm loop (GameMaster, RouteMaster, LevelManager, Heroes, Casino, logging) | **done** - live-validated: 6 unattended cycles, 0 fails (TESTING.md Stage C) |
| 4 | PySide6 Home GUI + side tools + two-process IPC | **done** - IPC live-validated; full settings editors (route grid + Emmote import/export, level manager + Feat Guard, offsets download, BPH/GPH stats, game-settings profiles with hourly check + Set Now); chests/daily + GUI live test pending (Stage D4). Not ported: theme colour table (dark-mode toggle instead) |
| 5 | Relay helper process, Monitor | **done** - offline-verified; live relay/monitor validation pending (Stages D2/D5) |
| 6 | Linux track: X11 input backend, Wine/Proton attach validation, Heroic/Lutris launch | pending (resume on the Linux machine) |

See **TESTING.md** for the staged test plan (passive checks -> input probe ->
supervised first run -> subsystem checks -> parity soak).

## Processes (mirrors the AHK layout)

| AHK | Python |
|---|---|
| IC_BrivMaster.ahk (Home) | `python -m brivmaster.home` |
| IC_BrivMaster_Run.ahk (farm) | `python -m brivmaster.run_farm` (Home's Start button spawns it) |
| IC_BrivMaster_RouteMaster_Relay.ahk | `python -m brivmaster.relay <config>` (spawned by the farm) |
| Monitor | `python -m brivmaster.monitor` |
| COM active objects | JSON/TCP localhost IPC (`brivmaster/ipc.py`), endpoint in `LastEndpoint_IBM_GemFarm.json` |

## Running the farm

```powershell
# Validate configuration without sending any input (safe alongside AHK):
python -m brivmaster.run_farm --dry-run

# Run the farm (requires the gem-farm adventure loaded; stop the AHK farm first!):
python -m brivmaster.run_farm
```

Both auto-locate `IC_BrivMaster_Settings.json` and the `Offsets` folder from
the sibling AHK install; `--settings/--offsets/--logs` override. Run from an
elevated prompt when the game runs elevated.

## Requirements & setup

- Python **3.10+ (64-bit required** - the game is 64-bit); developed and
  tested on 3.12. Only third-party dependency: PySide6 (GUI/Monitor).
- One-shot environment check + dependency install (safe to re-run):

```powershell
python setup_check.py          # or double-click setup.bat on Windows
python setup_check.py --check  # report only, install nothing
```

It verifies the interpreter (version/bitness), pip, installs missing
libraries (PySide6, plus tzdata on Windows), imports every brivmaster
module, and confirms the offsets/settings files are findable.

## Linux setup

### 1. Memory read permission

Reading another process's memory needs ptrace permission. Pick one:

```sh
# Temporary (until reboot):
sudo sysctl kernel.yama.ptrace_scope=0

# Permanent:
echo 'kernel.yama.ptrace_scope = 0' | sudo tee /etc/sysctl.d/10-ptrace.conf

# Alternative without changing ptrace_scope - grant the capability to the
# python binary of a dedicated venv (re-apply after python upgrades):
sudo setcap cap_sys_ptrace+ep /path/to/venv/bin/python3
```

### 2. Offsets

Copy your `Offsets` folder (with `IC_Offsets.json` and the three
`IC_*_Import.ahk` files) from the AHK BrivMaster install, or download the
**EGS-platform** offsets from the offsets GitHub. Place it either at
`PyBrivMaster/Offsets/` or leave the AHK layout intact next to this folder
(`../BrivMaster/Offsets/` is found automatically).

Note: offsets are per-platform (EGS vs Steam builds differ). Running the EGS
game under Heroic/Lutris still needs the EGS offsets.

### 3. Validate with the probe

Start the game (under Wine/Proton), let it load to the play screen, then:

```sh
python3 tools/probe.py
```

Expected: `Attached: PID ...`, sensible values for game version / zone / gems
/ formations, and `RESULT: OK`. If you get `<no read>` on everything, your
offsets don't match the game build or the platform is wrong. If you get a
PermissionError, revisit step 1.

`python3 tools/probe.py --watch 1` keeps printing zone/gems each second -
change zones in game and confirm the values track.

## Windows parity checks

The probe also runs on Windows against a native game install, which allows
comparing this port's reads 1:1 with the AHK original during development:

```powershell
python tools\probe.py --wait 60
```

Note: if the game/launcher runs elevated (common when the AHK BrivMaster is
run as admin), the probe must run from an elevated prompt too, or OpenProcess
fails with ACCESS_DENIED. `--wait` keeps retrying while the farm restarts the
game and skips relay-held login instances until a fully-loaded one appears.

## Port notes (differences from the AHK original)

- AHK identifiers are case-insensitive; the generated imports rely on this
  (`formationCampaignID` vs `FormationCampaignID`). Field lookup here is
  case-insensitive to match.
- Fields missing from the loaded imports degrade to failed reads (None), as
  in AHK, but are recorded in `brivmaster.memory.gos.MISSING_FIELDS` for
  diagnosis; the probe prints them.
- Failed reads return `None` (the AHK `""`).
- `IC_Offsets.json` and the generated `IC_*_Import.ahk` files are consumed
  as-is - no conversion step, so offset updates keep working unchanged.
- Keys are sent as WM_KEYDOWN/WM_KEYUP messages to the game window handle
  with AHK-identical lParams (`brivmaster/platform/input.py`); the window
  backend is injected so the Linux/X11 implementation swaps in cleanly.
- The AHK "Budget Zlib" produces a standard RFC1950 stream in base64;
  `server_call.deflate_b64` is byte-compatible via Python's zlib. The whole
  `Lib\` folder therefore has no Python counterpart.
- Server calls use stdlib urllib; failed calls return None. The async stack
  save runs as `python -m brivmaster.save_stacks` with the body in a temp
  file (argv is too small for save bodies).
- The AHK globals (g_IBM, g_SF, g_Heroes, ...) became an explicit FarmContext
  (`brivmaster/farm/ctx.py`); 'Critical'/'Thread, NoTimers' sections became
  ctx.critical (an RLock shared with the DialogSwatter thread).
- Pre-flight MsgBoxes became PreFlightError exceptions; the one Yes/No prompt
  (imperfect familiar counts) logs a warning and continues.
- Two AHK quirks fixed rather than ported: RouteMaster.StackFarmSetup called
  FormationCheckWithFari on the wrong object (silently always-falsy in AHK,
  making its wait loop always run to the 5s timeout) - the port calls the
  online stacker's real check; BrivHasThunderStep's E-formation check called
  a non-existent method (silently skipped in AHK) - the port checks E as
  evidently intended. JumpsRecurse's always-true 'not yet processed' test
  (assignment instead of comparison) IS ported as-is, since it affects which
  value wins on shared route segments.
- Empty AHK settings values ('' where a dict/number is expected, e.g.
  Feat_List, priolimit) are treated as empty, as AHK's .Count()/arithmetic
  did.
- The relay helper is not ported yet (phase 5): with relay restarts enabled
  the farm falls back to plain blank restarts and logs a notice.
