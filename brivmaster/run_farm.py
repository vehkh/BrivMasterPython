"""Entry point for the gem farm - the IC_BrivMaster_Run.ahk top level.

Usage:
    python -m brivmaster.run_farm [--settings PATH] [--offsets PATH]
                                  [--logs DIR] [--dry-run]

--dry-run stops after the pre-flight check without starting the farm loop -
useful to validate configuration without sending any input.
"""

from __future__ import annotations

import argparse
import os
import sys

# Linux only: run the farm (and the game it launches) against a separate
# X display, e.g. a nested Xephyr server. XTEST key injection types into
# the focused window of ITS display, so with the game isolated on :2 the
# user's desktop keeps its focus. Must happen before pynput/Xlib import
# (they bind to $DISPLAY at import time), hence before the farm imports.
if os.environ.get("BRIVMASTER_DISPLAY"):
    os.environ["DISPLAY"] = os.environ["BRIVMASTER_DISPLAY"]

from .farm.ctx import FarmContext
from .farm.gem_farm import GemFarm, PreFlightError
from .farm.heroes import Heroes
from .farm.shared_data import SharedData, default_settings_path
from .memory.functions import MemoryFunctions, OffsetsError
from .platform import window_backend
from .platform.input import InputManager
from .server_call import ServerCall


def default_offsets_path():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = [
        os.path.join(here, "Offsets", "IC_Offsets.json"),
        os.path.join(os.path.dirname(here), "BrivMaster", "Offsets",
                     "IC_Offsets.json"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return candidates[-1]


def build_context(settings_path, offsets_path):
    ctx = FarmContext()
    ctx.shared = SharedData(ctx, settings_path)
    if not ctx.shared.Init() and not ctx.settings:
        print(f"WARNING: could not load settings from {settings_path}")
    ctx.memory = MemoryFunctions(offsets_path)
    win = window_backend()
    exe_name = ctx.setting("IBM_Game_Exe", "IdleDragons.exe")

    def hwnd_provider():
        game_master = ctx.farm.GameMaster if ctx.farm else None
        if game_master and game_master.Hwnd \
                and win.window_exists(game_master.Hwnd):
            return game_master.Hwnd
        return win.find_window_by_exe(exe_name)

    ctx.input = InputManager(hwnd_provider,
                             scan_codes=ctx.setting("IBM_Scan_Codes", {}))
    ctx.server = ServerCall(ctx.memory)
    ctx.heroes = Heroes(ctx)
    ctx.server.stack_reader = lambda: (ctx.heroes[58].ReadHasteStacks(),
                                       ctx.heroes[58].ReadSBStacks())
    return ctx


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--settings", default=default_settings_path())
    parser.add_argument("--offsets", default=default_offsets_path())
    parser.add_argument("--logs", default=None,
                        help="log directory (default: Logs next to settings)")
    parser.add_argument("--dry-run", action="store_true",
                        help="stop after the pre-flight check")
    args = parser.parse_args()

    log_dir = args.logs or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(args.settings))), "Logs")
    print(f"Settings: {args.settings}\nOffsets:  {args.offsets}\n"
          f"Logs:     {log_dir}")
    try:
        ctx = build_context(args.settings, args.offsets)
    except OffsetsError as err:
        print(f"FAILED: {err}")
        return 2
    exe_name = ctx.setting("IBM_Game_Exe", "IdleDragons.exe")
    attach_state = ctx.memory.AttachToReadyInstance(exe_name)
    if attach_state is None:
        print(f"FAILED: could not attach to '{exe_name}'. Is the game running "
              "(and this script elevated if the game is)?")
        return 1
    if attach_state != "ready":
        print("WARNING: attached, but no fully-loaded game instance found "
              "(loading screen / relay login hold?) - reads may be empty.")
    ctx.heroes.Init()
    farm = GemFarm(ctx, log_dir)
    ctx.farm = farm
    if args.dry_run:
        try:
            # Enough construction to run the pre-flight without input
            from .farm.level_manager import LevelManager
            from .farm.logger import Logger
            from .farm.route_master import RouteMaster
            from .farm.game_master import GameMaster
            farm.GameMaster = GameMaster(ctx)
            farm.Logger = Logger(ctx, log_dir)
            farm.LevelManager = LevelManager(ctx)
            farm.RouteMaster = RouteMaster(
                ctx, ctx.setting("IBM_Route_Combine"), farm.Logger.logBase)
            farm.PreFlightCheck()
        except PreFlightError as err:
            print(f"PRE-FLIGHT FAILED [{err.failing_step}]: {err}")
            return 1
        print("PRE-FLIGHT OK")
        print(f"Strategy: {ctx.shared.IBM_RunControl_StatusString}")
        return 0
    # IPC server: what the Home GUI and the relay helper connect to
    from .ipc import IpcServer

    class Control:
        alive = True

        @staticmethod
        def Stop():
            farm.Stop()

    ctx.ipc = IpcServer(os.path.dirname(os.path.abspath(__file__)))
    ctx.ipc.register("shared", ctx.shared,
                     allowed_calls=("ResetRunStats", "UpdateSettingsFromFile"))
    ctx.ipc.register("control", Control(), allowed_calls=("Stop",))
    ctx.ipc.start()
    print(f"IPC listening on 127.0.0.1:{ctx.ipc.port}")
    try:
        farm.GemFarm()
    except PreFlightError as err:
        print(f"PRE-FLIGHT FAILED [{err.failing_step}]: {err}")
        return 1
    except KeyboardInterrupt:
        print("Stopped.")
    finally:
        ctx.ipc.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
