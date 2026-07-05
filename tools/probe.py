#!/usr/bin/env python3
"""Memory probe - Phase 1 validation tool for the PyBrivMaster memory layer.

Attaches to a running Idle Champions process and prints a snapshot of game
state read through the ported offsets system. Works on Linux (game under
Wine/Proton) and on Windows (for parity checks against the AHK original).

Usage:
    python tools/probe.py                 # auto-locate offsets + game
    python tools/probe.py --watch 1       # snapshot + live zone/gems line
    python tools/probe.py --offsets /path/to/IC_Offsets.json --pid 1234
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brivmaster.memory.functions import MemoryFunctions, OffsetsError  # noqa: E402


def default_offsets_path():
    """The AHK install's Offsets dir sits next to PyBrivMaster during the port."""
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(here, "Offsets", "IC_Offsets.json"),
        os.path.join(os.path.dirname(here), "BrivMaster", "Offsets", "IC_Offsets.json"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return candidates[-1]


def fetch(label, func, redact=False):
    try:
        value = func()
    except Exception as err:  # noqa: BLE001 - a probe should never die mid-report
        value = f"<error: {type(err).__name__}: {err}>"
    if redact and isinstance(value, str) and len(value) > 4:
        value = value[:2] + "*" * (len(value) - 4) + value[-2:]
    if value is None:
        value = "<no read>"
    print(f"  {label:<32} {value}")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--offsets", default=default_offsets_path(),
                        help="path to IC_Offsets.json (imports expected alongside)")
    parser.add_argument("--exe", default="IdleDragons.exe",
                        help="game executable name")
    parser.add_argument("--pid", type=int, default=None,
                        help="attach to this PID instead of searching")
    parser.add_argument("--watch", type=float, metavar="SECONDS", default=None,
                        help="after the snapshot, print zone/gems every N seconds")
    parser.add_argument("--wait", type=float, metavar="SECONDS", default=0,
                        help="keep retrying attach for up to N seconds "
                             "(useful while the farm is restarting the game)")
    args = parser.parse_args()

    print(f"Offsets file: {args.offsets}")
    try:
        memory = MemoryFunctions(args.offsets)
    except OffsetsError as err:
        print(f"FAILED: {err}")
        return 2

    for root, warnings in memory.import_warnings.items():
        print(f"  WARNING [{root}]: {len(warnings)} unparsed/missing import lines")
        for line in warnings[:3]:
            print(f"    {line}")

    versions = memory.Versions
    print(f"Offsets platform: {versions['Platform']}   "
          f"imports: {memory.GetImportsVersion()}   "
          f"pointers: {versions['Pointer_Version_Major']}"
          f"{versions['Pointer_Version_Minor']} {versions['Pointer_Revision']}")

    from brivmaster.memory import backend as backend_mod

    # There can be several game processes (relay restarts pre-launch a second
    # instance held at the login screen) and the farm restarts the game
    # regularly - so scan all candidates until one is fully in-game, or the
    # wait budget runs out.
    deadline = time.monotonic() + max(args.wait, 10)
    attached_fallback = False
    ready = False
    while not ready:
        candidates = ([args.pid] if args.pid
                      else backend_mod.native_backend().find_pids(args.exe))
        for pid in candidates:
            if not memory.OpenProcessReader(args.exe, pid):
                continue
            attached_fallback = True
            if memory.ReadGameStarted() and memory.ReadUserIsInited():
                ready = True
                break
        if not ready:
            if time.monotonic() >= deadline:
                break
            time.sleep(2)

    if memory.mem is None or not memory.mem.attached:
        print(f"FAILED: no readable process matching '{args.exe}' found. "
              f"(If the game is running: on Windows an elevated game needs an "
              f"elevated probe; on Linux check ptrace permission.)")
        return 1
    print(f"Attached: PID {memory.mem.pid}, module base "
          f"0x{memory.mem.module_base('mono-2.0-bdwgc.dll'):X}")
    if not ready and attached_fallback:
        print("NOTE: no fully-loaded game instance found within the wait "
              "window (loading screen / login-held relay instance?); some "
              "reads below will be empty.")
    print()
    print("Game state:")
    version = fetch("Base game version", memory.ReadBaseGameVersion)
    fetch("Version postfix", memory.IBM_ReadGameVersionMinor)
    fetch("Platform (from game)", memory.ReadPlatform)
    fetch("Game started", memory.ReadGameStarted)
    fetch("User loaded", memory.IBM_ReadIsGameUserLoaded)
    fetch("WebRoot", memory.IBM_GetWebRootFriendly)
    fetch("User ID", memory.ReadUserID)
    fetch("User hash", memory.ReadUserHash, redact=True)
    fetch("Active instance ID", memory.IBM_GetActiveGameInstanceID)
    print()
    zone = fetch("Current zone", memory.ReadCurrentZone)
    fetch("Highest zone", memory.ReadHighestZone)
    fetch("Gems", memory.ReadGems)
    fetch("Resets since manual", memory.ReadResetsCount)
    fetch("Active monsters", memory.ReadActiveMonstersCount)
    fetch("Attacking monsters", memory.ReadNumAttackingMonstersReached)
    fetch("Game speed (base)", memory.IBM_ReadBaseGameSpeed)
    fetch("Auto progress", memory.ReadAutoProgressToggled)
    fetch("Modron reset area", memory.GetModronResetArea)
    fetch("Modron auto-buffs", memory.ReadModronAutoBuffs)
    print()
    fetch("Current formation", memory.GetCurrentFormation)
    fetch("Q formation (fav 1)", lambda: memory.GetFormationByFavorite(1))
    fetch("W formation (fav 2)", lambda: memory.GetFormationByFavorite(2))
    fetch("E formation (fav 3)", lambda: memory.GetFormationByFavorite(3))
    fetch("Modron formation", memory.GetActiveModronFormation)
    fetch("Briv feats (ID 58)", lambda: memory.GetHeroFeats(58))
    fetch("Favour exponent", memory.IBM_GetCurrentCampaignFavourExponent)
    fetch("Zone HP exponent", memory.IBM_ReadCurrentZoneMonsterHealthExponent)

    from brivmaster.memory.gos import MISSING_FIELDS
    if MISSING_FIELDS:
        print(f"\nWARNING: {len(MISSING_FIELDS)} field(s) not found in the "
              f"loaded imports (outdated offsets?):")
        for field in sorted(MISSING_FIELDS):
            print(f"  {field}")

    failed = version is None or isinstance(version, str) and version.startswith("<")
    if failed:
        print("\nRESULT: core reads failed - offsets likely do not match this "
              "game build/platform, or the pointer chain changed.")
        return 1

    if args.watch:
        print(f"\nWatching (Ctrl+C to stop, every {args.watch}s):")
        try:
            while True:
                stamp = time.strftime("%H:%M:%S")
                print(f"  [{stamp}] zone={memory.ReadCurrentZone()} "
                      f"gems={memory.ReadGems()} "
                      f"monsters={memory.ReadActiveMonstersCount()} "
                      f"transitioning={memory.ReadTransitioning()}")
                time.sleep(args.watch)
        except KeyboardInterrupt:
            pass

    print("\nRESULT: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
