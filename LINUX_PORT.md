# Linux port - continuation brief

**Read this first if you are continuing the Linux port** (human or AI). It
is self-contained: everything needed to resume lives in this repo. The
Windows version is complete and validated; Linux is the only remaining work.

## Where things stand

- **Windows: done and live-validated** (73h / 5,582-run unattended soak at
  -2% BPH vs the AHK original). See `TESTING.md` and `PORTING.md`.
- **Linux memory backend: written but UNVALIDATED.** `LinuxProcessMemory`
  in `brivmaster/memory/backend.py` (uses `process_vm_readv` + parsing
  `/proc/<pid>/maps` for the `mono-2.0-bdwgc.dll` base, and SIGSTOP/SIGCONT
  for the relay hold). It has never actually read the game on Linux.
- **X11 input/window backend: does NOT exist yet.** This is the main job.
  `brivmaster/platform/__init__.py` raises `NotImplementedError` on
  non-Windows; only `win32.py` implements the backend interface.

## Why this is expected to work: Wine/Proton is not a container

Idle Champions has no native Linux build; it runs under Wine/Proton (via
Heroic/Lutris). Wine/Proton is a *translation layer*, not a VM or container:
`IdleDragons.exe` runs as an ordinary Linux process, shows up in `ps`, has a
normal `/proc/<pid>/mem`, and `mono-2.0-bdwgc.dll` appears in
`/proc/<pid>/maps` like any mapped file. The entire offsets/memory system
here already targets that binary, so memory reads should work as-is.

**Prefer Heroic with Wine-GE / Proton-GE** (a plain wine process we can
fully control). The *Steam client* wraps games in "pressure-vessel"
(bubblewrap) - reads still work, but launch/close lifecycle gets murkier.
Offsets are EGS platform 21, same binary as Windows.

## First session on the Linux box (do this before writing any code)

```sh
git clone https://github.com/vehkh/BrivMasterPython.git
cd BrivMasterPython
python3 setup_check.py                 # checks Python 3.10+/64-bit, installs PySide6

# Grant memory-read permission (one of):
sudo sysctl kernel.yama.ptrace_scope=0            # simplest, until reboot
# or persist: echo 'kernel.yama.ptrace_scope = 0' | sudo tee /etc/sysctl.d/10-ptrace.conf
# or:   sudo setcap cap_sys_ptrace+ep $(readlink -f $(which python3))

# Put the EGS offsets in place (copy the Offsets/ folder from the Windows
# install - identical EGS platform-21 binary - or download via the Home GUI
# later). Expected at ./Offsets/IC_Offsets.json (+ the 3 IC_*_Import.ahk).

# Start Idle Champions under Heroic, let it load to the play screen, then:
python3 tools/probe.py --wait 60
```

**If the probe prints your gems/zone/formations and says `RESULT: OK`, the
single riskiest assumption of the whole Linux port is proven** and memory is
done. If it can't attach: check ptrace permission and that the game is
actually running (not still in the launcher).

## What to build: the platform backend

Implement a Linux backend exposing the **same module interface as
`brivmaster/platform/win32.py`**, then wire it into
`brivmaster/platform/__init__.py`'s `window_backend()`. The farm code calls
this interface and nothing else, so no farm logic should need to change.

Functions to provide (see win32.py for exact signatures/semantics):
`find_pids`, `get_process_name`, `terminate_process`,
`set_priority_realtime` (skip on Linux - needs root; make it a no-op),
`launch`, window discovery (`find_window_by_pid`, `find_windows_by_exe`,
`find_window_by_exe`, `window_pid`, `window_exists`, `get_active_window`),
`activate_window`, `control_focus`, `request_window_close`,
`vk_from_scancode`, `send_key_down`, `send_key_up`.

Design notes / recommended approach:
- **Input:** the AHK/Windows version posts key *messages* to the window
  handle (no focus needed). X11 has no true equivalent. Try **XSendEvent**
  to the Wine window first (Wine largely honours synthetic events, preserving
  the no-focus-steal behaviour); fall back to **XTEST** (`python-xlib`
  `record`/`fake_input`), which synthesizes *global* events to the *focused*
  window - works with Unity but means the game must hold focus while farming.
- **Scan codes -> X keycodes:** the `IBM_Scan_Codes` map is Windows scan
  codes. Map to X keycodes (they differ). `brivmaster/platform/input.py`
  (the `InputManager`) already isolates key handling behind the backend, so
  the change is contained.
- **Window mgmt:** find by `WM_CLASS`/`_NET_WM_PID`; activate via
  `_NET_ACTIVE_WINDOW`; close via `WM_DELETE_WINDOW` (Wine translates it to
  WM_CLOSE, so the game's save-on-exit is preserved, matching the Windows
  `WM_SYSCOMMAND` close).
- **Launch:** Heroic's command spawns wrappers (legendary -> wine), so the
  returned PID isn't the game - but the existing "scan for a new game PID"
  logic in `game_master.py` already handles exactly this (it's the same as
  EGS on Windows). Relay's two simultaneous instances share one wine prefix;
  fine (the game has no single-instance mutex - the Windows relay proves it).
- **Suspend/resume:** already implemented (SIGSTOP/SIGCONT in the Linux
  memory backend); the relay uses it for the login hold.

Likely dependency: `python-xlib` (add to `requirements.txt`). Consider a
`uinput` fallback for Wayland later, but target X11 first.

## Test as you go (mirrors TESTING.md staging)

1. `tools/probe.py` - memory (do this first, before any input code).
2. A tiny input check: send G (autoprogress) and Q/E (formation) and verify
   via memory reads - the Linux analogue of `tools/input_probe.py`.
3. `python3 -m brivmaster.run_farm --dry-run` - pre-flight without input.
4. Supervised real run; then the same subsystem checks as `TESTING.md`.

## Code map & gotchas a fresh session must know

Three processes (like the AHK original), talking over JSON/TCP localhost IPC
(`brivmaster/ipc.py`, replacing AHK COM): **Home** (`brivmaster/home/`, the
PySide6 GUI + hub tools), **farm** (`brivmaster/run_farm.py` ->
`brivmaster/farm/`), **relay** (`brivmaster/relay.py`). Monitor is a 4th,
optional. Ported classes keep the AHK method names for line-by-line
comparison with the AHK sources (the `IC_BrivMaster_*.ahk` files, which the
BrivMaster upstream repo has).

- Failed memory reads return `None` (the AHK `""`); handle accordingly.
- AHK identifiers are case-insensitive and the generated offset imports rely
  on it - the memory layer lowercases field lookups. Don't "fix" this.
- AHK `log()` is base-10; any ported maths using it uses `math.log10`.
- During relay restarts there can be **two** game processes; attach to the
  one where `ReadGameStarted()` and `ReadUserIsInited()` both succeed (see
  `MemoryFunctions.AttachToReadyInstance`).
- Offsets/imports are consumed as-is from `Offsets/` (no conversion); they
  come from the separate BrivMaster-Imports repo and change with game
  versions.
- `PORTING.md` has the full architecture and every deliberate deviation from
  the AHK original (including two AHK bugs fixed and one kept as-is).

## Telling a fresh AI session where to start

On the Linux machine, a new session has no memory of this project. Point it
here: *"Read LINUX_PORT.md, PORTING.md and TESTING.md, then continue the
Linux port starting with tools/probe.py."*
