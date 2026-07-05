"""Port of IC_BrivMaster_Relay_SharedData_Class (IC_BrivMaster_RouteMaster.ahk).

Manages the relay helper that pre-launches a second game instance and holds
it at platform login during blank restarts.

State machine (as in the AHK original):
    0  not running          4  game started, relay ended before login
    1  helper launched      5  game held after platform login
    2  helper connected     6  complete (any outcome)
    3  game started        -1  failed to launch   -2  failed to suspend

NOTE: the helper process itself (IC_BrivMaster_RouteMaster_Relay.ahk) is
ported in phase 5 together with its IPC. Until then Start() logs once and
stays in state 0, which degrades relay restarts to plain blank restarts -
the run keeps working, just without the relay speed-up.
"""

from __future__ import annotations

from ..platform import window_backend
from .ctx import precise_sleep, tick_ms


class RelaySharedData:
    def __init__(self, ctx, relay_clamp, log_file):
        self._ctx = ctx
        self._backend = window_backend()
        loaded = ctx.memory.GameManager.game.gameUser.Loaded
        # What the helper needs to find the 'user loaded' flag in the NEW
        # process: module offset + offsets chain (a fresh base is resolved in
        # the new process; an absolute address would be worthless there).
        self.MEMORY_LOADED_Offsets = list(loaded.offsets)
        self.LaunchCommand = ctx.setting("IBM_Game_Launch", "")
        self.HideLauncher = ctx.setting("IBM_Game_Hide_Launcher")
        self.ExeName = ctx.setting("IBM_Game_Exe", "IdleDragons.exe")
        # Do not relay restart until after Thellora's jump
        self.relayZone = max(ctx.setting("IBM_OffLine_Blank_Relay_Zones", 0),
                             relay_clamp)
        self.LogFile = log_file
        self.MainPID = 0
        self.MainHwnd = 0
        self.RestoreWindow = False
        # The 'user loaded' pointer chain lives under the IdleGameManager
        # root; the helper re-resolves it in the new process
        self.ModuleOffset = ctx.memory.module_offset_for("IdleGameManager")
        self.Reset()
        if ctx.ipc is not None:
            ctx.ipc.register("relay", self,
                             allowed_calls=("LogZone", "RelayCloseMain"))

    def Reset(self):
        self.RelayPID = 0
        self.RelayHwnd = 0
        self.HelperPID = 0
        self.State = 0
        self.RequestRelease = False

    def Start(self):
        import json
        import subprocess
        import sys
        import tempfile
        ctx = self._ctx
        if self.State != 0:
            return
        if ctx.ipc is None:
            ctx.log("Relay Start(): no IPC server - relay unavailable, "
                    "falling back to plain blank restarts")
            return
        self.RelayPID = 0
        self.RelayHwnd = 0
        self.HelperPID = 0
        self.RequestRelease = False
        self.MainPID = ctx.farm.GameMaster.PID
        self.MainHwnd = ctx.farm.GameMaster.Hwnd
        self.RestoreWindow = ctx.setting("IBM_Route_Offline_Restore_Window")
        config = {
            "MainPID": self.MainPID, "MainHwnd": self.MainHwnd,
            "ModuleOffset": self.ModuleOffset,
            "LoadedOffsets": self.MEMORY_LOADED_Offsets,
            "LoadedType": "Char",
            "LaunchCommand": self.LaunchCommand,
            "HideLauncher": bool(self.HideLauncher),
            "ExeName": self.ExeName,
            "RestoreWindow": bool(self.RestoreWindow),
            "LogFile": self.LogFile,
            "IpcPort": ctx.ipc.port, "IpcToken": ctx.ipc.token,
        }
        config_file = tempfile.NamedTemporaryFile(
            "w", suffix=".json", prefix="ibm_relay_", delete=False,
            encoding="utf-8")
        json.dump(config, config_file)
        config_file.close()
        helper = subprocess.Popen(
            [sys.executable, "-m", "brivmaster.relay", config_file.name],
            close_fds=True)
        ctx.log(f"Relay Start() ran helper script at "
                f"z=[{ctx.memory.ReadCurrentZone()}] with PID=[{helper.pid}]")
        self.HelperPID = helper.pid
        self.State = 1

    def LogZone(self, message):
        self._ctx.log(f"Relay LogZone() at "
                      f"z[{self._ctx.memory.ReadCurrentZone()}] "
                      f"message=[{message}]")

    def IsActive(self):
        """Currently running - any state but unstarted and complete."""
        return self.State not in (0, 6)

    def HasTriggered(self):
        """Has been activated this run."""
        return self.State != 0

    def PreRelease(self):
        """Resume the held process ASAP (called during the game save)."""
        if self.State == 5:
            self.SuspendProcess(self.RelayPID, False)
            self._ctx.log("Relay PreRelease() state 5 - resuming")
        elif self.State == 6:
            self.SuspendProcess(self.RelayPID, False)
            self._ctx.log("Relay PreRelease() state 6 - resuming - DEBUG")
        elif self.State > 0:
            self.RequestRelease = True
            self._ctx.log("Relay PreRelease() state 1 to 4 - request release")

    def Release(self):
        ctx = self._ctx
        if self.State == 5:  # expected: resume and swap over
            self.SuspendProcess(self.RelayPID, False)
            self.ProcessSwap()
            ctx.log("Relay Release() state 5")
            self.State = 6
            return
        if self.State == 4:  # never suspended (missed login or was asked to abort)
            self.ProcessSwap()
            ctx.log("Relay Release() state 4")
            self.State = 6
            return
        if self.State in (2, 3):
            # Racing the relay's own suspend - ask for release and wait
            self.RequestRelease = True
            ctx.log(f"Relay Release() state [{self.State}]")
            max_time = tick_ms() + 5000
            while tick_ms() < max_time:
                if self.State not in (2, 3):
                    ctx.log(f"Relay Release() state changed to [{self.State}]"
                            " - recursing Release()")
                    self.Release()
                    return
                precise_sleep(15)
            ctx.log(f"Relay Release() state [{self.State}] recursion exit or "
                    "failed to detect state change")
            self.CleanUpOnFail()
        elif self.State in (1, 0, -1):
            ctx.log(f"Relay Release() state [{self.State}]")
            self.CleanUpOnFail()
        elif self.State == -2:
            # Failed to stop after login; main was closed via RelayCloseMain()
            ctx.log(f"Relay Release() state [{self.State}]")
            self.ProcessSwap()
        else:
            ctx.log(f"Relay Release() with invalid state [{self.State}]")
        self.State = 6

    def SuspendProcess(self, pid, do_suspend=True):
        """For the relay instance only - the attached process uses the memory
        backend's suspend()/resume()."""
        if not pid:
            return
        from ..memory.backend import native_backend
        try:
            backend = native_backend()(pid)
            if backend.attached:
                if do_suspend:
                    backend.suspend()
                else:
                    backend.resume()
            backend.close()
        except Exception:  # noqa: BLE001 - process may be gone
            pass

    def CleanUpOnFail(self):
        ctx = self._ctx
        win = self._backend
        if self.HelperPID:
            name = win.get_process_name(self.HelperPID)
            if name and name.lower().startswith("python"):
                ctx.log(f"CleanUpOnFail() found Relay helper PID="
                        f"[{self.HelperPID}] still running - killing")
                win.terminate_process(self.HelperPID)
        pids = win.find_pids(self.ExeName)
        if pids:
            recovery_pid = pids[0]
            ctx.log(f"CleanUpOnFail() recovery PID found=[{recovery_pid}]")
            ctx.farm.GameMaster.PID = recovery_pid
            self.SuspendProcess(recovery_pid, False)  # ensure not stuck suspended
            ctx.farm.GameMaster.Hwnd = win.find_window_by_pid(recovery_pid)
            ctx.memory.OpenProcessReader(self.ExeName, recovery_pid)
            ctx.server.Update()
        else:
            ctx.farm.GameMaster.OpenIC("CleanUpOnFail()")

    def ProcessSwap(self):
        ctx = self._ctx
        game_master = ctx.farm.GameMaster
        log_text = (f"ProcessSwap() changing PID=[{game_master.PID}] and "
                    f"Hwnd=[{game_master.Hwnd}] ")
        game_master.PID = self.RelayPID
        game_master.Hwnd = self.RelayHwnd
        ctx.log(f"{log_text}to PID=[{game_master.PID}] and "
                f"Hwnd=[{game_master.Hwnd}]")
        ctx.memory.OpenProcessReader(self.ExeName, game_master.PID)
        # skip_final: we won't know where in the offline calc we are
        if game_master.WaitForGameReady(
                10000 * ctx.setting("IBM_OffLine_Timeout", 5), skip_final=True):
            ctx.log("ProcessSwap() completed switching process")
        else:
            ctx.log("ProcessSwap() WaitForGameReady() call failed whilst "
                    "switching process")
        ctx.farm.DialogSwatter.Start()
        ctx.server.Update()
        ctx.shared.UpdateOutbound("IBM_ProcessSwap", True)

    def RelayCloseMain(self):
        """Called by the relay helper to close the main game during recovery."""
        self._ctx.farm.GameMaster.CloseIC(
            "Relay failed to halt at platform login", use_pid=True)
        self.Release()
