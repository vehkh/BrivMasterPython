"""Port of IC_BrivMaster_RouteMaster_Relay.ahk - the relay helper process.

Starts a second game instance during a blank restart and suspends it right
after platform login, so the main script can hand over with minimal downtime.

Spawned by RelaySharedData.Start() as:
    python -m brivmaster.relay <config-file.json>

The config file carries the one-shot data (launch command, exe name, main
PID/hwnd, the 'user loaded' memory chain, log file) plus the IPC endpoint;
dynamic state (State, RelayPID, RelayHwnd, RequestRelease) flows over IPC to
the farm's 'relay' scope. The state numbers match the AHK original:

    0 not running, 1 launched, 2 connected, 3 game started,
    4 started but ended before login, 5 held after platform login,
    6 complete, -1 failed to launch, -2 failed to suspend
"""

from __future__ import annotations

import json
import os
import sys
import time

from .ipc import IpcClient, IpcError
from .memory.backend import native_backend
from .platform import window_backend

MODULE_NAME = "mono-2.0-bdwgc.dll"


def tick_ms():
    return int(time.monotonic() * 1000)


class Relay:
    def __init__(self, config_path):
        self.log_lines = [f"{tick_ms()},Creating Relay"]
        self.win = window_backend()
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except (OSError, ValueError) as err:
            self._fail_log(f"Relay launched with unreadable config: {err}")
            raise SystemExit(1) from err
        self.config = config
        self.LogFile = config.get("LogFile") or self._fail_log_path()
        self.MainPID = config["MainPID"]
        self.MainHwnd = config.get("MainHwnd", 0)
        self.module_offset = config["ModuleOffset"]   # MEMORY_baseAddress
        self.loaded_offsets = config["LoadedOffsets"]
        self.loaded_type = config.get("LoadedType", "Char")
        self.LaunchCommand = config["LaunchCommand"]
        self.HideLauncher = config.get("HideLauncher", False)
        self.ExeName = config["ExeName"]
        self.RestoreWindow = config.get("RestoreWindow", False)
        self.ForceRelease = False
        try:
            self.data = IpcClient(port=config["IpcPort"],
                                  token=config["IpcToken"])
            self.data.set("relay", "State", 2)  # connected
            self.log(f"Connected to farm IPC on port {config['IpcPort']}")
        except (IpcError, OSError) as err:
            self._fail_log(f"Failed to connect to Relay IPC: {err}")
            raise SystemExit(1) from err
        self.PID = 0
        self.Hwnd = 0
        self.Stage = 0
        self.SavedActiveWindow = 0
        self.mem = None
        self.loaded_address = None

    def _fail_log_path(self):
        stamp = time.strftime("%Y%m%dT%H%M%S")
        return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            f"RelayFail_{stamp}.csv")

    def _fail_log(self, message):
        try:
            with open(self._fail_log_path(), "a", encoding="utf-8") as f:
                f.write(f"{tick_ms()} {message}\n")
        except OSError:
            pass

    def log(self, message):
        self.log_lines.append(f"{tick_ms()},{message}")

    def UpdateState(self, state):
        try:
            self.data.set("relay", "State", state)
        except IpcError:
            self.log(f"UpdateState() failed to update script status to [{state}]")

    # --- main loop -------------------------------------------------------------

    def RunRelay(self):
        self.log("Starting Game(Relay)")
        self.SavedActiveWindow = self.win.get_active_window()
        max_time = tick_ms() + 50000
        last_stage = 6
        last_start_stage = 5  # last start-up stage before waiting for .Loaded
        next_release_check = 0
        stages = {-2: self.CleanUpOverlap, -1: self.CleanUpOnFailedStart,
                  0: self.OpenProcess,
                  1: lambda: self.SetPID(10000),
                  2: self.SetProcessToRealTime,
                  3: lambda: self.SetLastActiveWindowWhileWaitingForGameExe(15000),
                  4: self.ActivateLastWindow,
                  5: lambda: self.OpenProcessReader(5000),
                  6: lambda: self.WaitForUserLogin(30000)}
        self._stage_deadlines = {}
        while self.Stage <= last_stage and tick_ms() <= max_time:
            if tick_ms() > next_release_check:  # throttle the IPC poll
                try:
                    if self.data.get("relay", "RequestRelease"):
                        self.ForceRelease = True
                except IpcError:
                    pass
                next_release_check = tick_ms() + 200
            handler = stages.get(self.Stage)
            if handler is None:
                self.log(f"RunRelay() invalid Stage:[{self.Stage}]")
                break
            handler()
            if self.Stage < 6:
                time.sleep(0.06)
            # Stage 6 samples as fast as possible to catch the login
        if 0 <= self.Stage <= last_stage:
            self.log(f"RunRelay() timed out whilst still at stage=[{self.Stage}]")
            if self.Stage > last_start_stage:
                self.UpdateState(-2)
                self.CleanUpOverlap()
            else:
                self.UpdateState(-1)
                self.CleanUpOnFailedStart()
        self.ExitRelay()

    def ExitRelay(self, comment="Standard"):
        self.log(f"Relay Exit: {comment}")
        try:
            with open(self.LogFile, "a", encoding="utf-8") as f:
                f.write("\n".join(self.log_lines) + "\n\n")
        except OSError:
            pass
        sys.exit(0)

    def _deadline(self, stage, timeout):
        """Per-stage lazy deadline (the AHK static MaxTime pattern)."""
        if stage not in self._stage_deadlines:
            self._stage_deadlines[stage] = tick_ms() + timeout
        return self._stage_deadlines[stage]

    # --- stages ------------------------------------------------------------------

    def OpenProcess(self):
        try:
            open_pid = self.win.launch(self.LaunchCommand,
                                       hide=bool(self.HideLauncher))
        except Exception:  # noqa: BLE001 - failed to start
            self.log("OpenProcess() failed to launch game")
            self.Stage = -1
            return
        name = self.win.get_process_name(open_pid)
        if name and name.lower() == self.ExeName.lower():
            self.PID = open_pid
            self.Stage += 2  # skip finding the PID via process scan
            self.log(f"OpenProcess() opened with PID=[{open_pid}]")
        else:
            self.Stage += 1
            self.log("OpenProcess() opened without PID")

    def SetPID(self, timeout):
        if tick_ms() < self._deadline(1, timeout):
            self.PID = self.GetNewPID()
            if self.PID:
                self.log(f"SetPID()=[{self.PID}] success")
                self.Stage += 1
        else:
            self.log("SetPID() timed out")
            self.Stage = -1

    def GetNewPID(self):
        """A game PID that is NOT the main script's instance."""
        for pid in self.win.find_pids(self.ExeName):
            if pid != self.MainPID:
                return pid
        return 0

    def SetProcessToRealTime(self):
        try:
            self.data.set("relay", "RelayPID", self.PID)
            self.data.set("relay", "State", 3)  # game started
        except IpcError:
            self.log(f"SetProcessToRealTime() failed to pass PID=[{self.PID}]")
        self.win.set_priority_realtime(self.PID)
        self.Stage += 1

    def SetLastActiveWindowWhileWaitingForGameExe(self, timeout):
        if tick_ms() <= self._deadline(3, timeout):
            self.Hwnd = self.win.find_window_by_pid(self.PID)  # 2 windows exist
            if not self.Hwnd:
                self.SavedActiveWindow = self.win.get_active_window()
            else:
                self.log(f"Relay SetLastActiveWindow...() success "
                         f"Hwnd=[{self.Hwnd}]")
                try:
                    self.data.set("relay", "RelayHwnd", self.Hwnd)
                except IpcError:
                    self.log(f"...failed to pass Hwnd=[{self.Hwnd}] to main script")
                self.Stage += 1
        else:
            self.log("Relay SetLastActiveWindow...() timed out")
            self.Stage = -1

    def ActivateLastWindow(self):
        if not self.RestoreWindow or self.SavedActiveWindow == self.MainHwnd:
            self.Stage += 1
            return
        if tick_ms() >= self._deadline(4, 80):
            # IC likes to be activated before it can be deactivated
            self.win.activate_window(self.Hwnd)
            self.win.activate_window(self.SavedActiveWindow)
            self.Stage += 1

    def OpenProcessReader(self, timeout):
        if tick_ms() <= self._deadline(5, timeout):
            if self.MemoryManagerRefresh():
                self.log(f"OpenProcessReader() with PID=[{self.PID}]")
                self.Stage += 1
        else:
            self.log("OpenProcessReader() timed out")
            self.Stage = -1

    def MemoryManagerRefresh(self):
        try:
            self.mem = native_backend()(self.PID)
        except Exception:  # noqa: BLE001
            return False
        if not self.mem.attached:
            return False
        module_base = self.mem.module_base(MODULE_NAME)
        if not module_base or module_base <= 0:
            return False  # dll not mapped yet; retried next tick
        self.game_base_address = module_base + self.module_offset
        self.log(f"MemoryManagerRefresh() complete with gameBaseAddress="
                 f"[{self.game_base_address}]")
        return True

    def WaitForUserLogin(self, timeout):
        if 6 not in self._stage_deadlines:
            self.loaded_address = self.mem.resolve(self.game_base_address,
                                                   self.loaded_offsets)
            if self._read_loaded() == 1:
                # Initial call made after login: not playing the state game
                self.log("WaitForUserLogin() was called after platform login")
                self.Stage = -2
                return
            self._deadline(6, timeout)
            return  # already did one check this tick
        if self.ForceRelease:
            # Not a fail: the main instance closes and the script picks the
            # new game instance up when ready
            self.Stage += 1
            self.log(f"WaitForUserLogin() exit via ForceRelease "
                     f"Loaded read=[{self._read_loaded()}]")
            self.UpdateState(4)
            return
        if tick_ms() > self._stage_deadlines[6]:
            self.Stage += 1
            self.log(f"WaitForUserLogin() exit via Timeout "
                     f"Loaded read=[{self._read_loaded()}]")
            self.UpdateState(4)
            return
        if not self.loaded_address:  # may not resolve until the game loads
            self.loaded_address = self.mem.resolve(self.game_base_address,
                                                   self.loaded_offsets)
        if self._read_loaded() == 1:
            self.mem.suspend()
            self.log("WaitForUserLogin() exit via Suspend")
            self.UpdateState(5)
            try:
                self.data.call("relay", "LogZone", "State 5")
            except IpcError:
                pass
            self.Stage += 1

    def _read_loaded(self):
        if not self.loaded_address:
            return None
        return self.mem.read(self.loaded_address, self.loaded_type)

    # --- failure paths -------------------------------------------------------------

    def CleanUpOverlap(self):
        self.log("CleanUpOverlap() called")
        self.UpdateState(-2)
        try:
            self.data.call("relay", "RelayCloseMain")
        except IpcError as err:
            self.log(f"CleanUpOverlap() RelayCloseMain failed: {err}")
        self.Stage = -3
        self.ExitRelay("CleanUpOverlap()")

    def CleanUpOnFailedStart(self):
        self.log("CleanUpOnFailedStart() called")
        if self.PID:
            if self.win.terminate_process(self.PID):
                self.log("CleanUpOnFailedStart() known PID - sent TerminateProcess")
            else:
                self.log("CleanUpOnFailedStart() known PID - no process handle")
        else:  # kill any game copies other than the main one
            self.log("CleanUpOnFailedStart() no PID - closing non-main IC processes")
            for pid in self.win.find_pids(self.ExeName):
                if pid != self.MainPID:
                    self.win.terminate_process(pid)
        self.UpdateState(-1)
        self.ExitRelay("CleanUpOnFailedStart()")


def main():
    if len(sys.argv) < 2:
        print("usage: python -m brivmaster.relay <config-file.json>")
        return 2
    relay = Relay(sys.argv[1])
    try:
        relay.RunRelay()
    finally:
        try:
            os.unlink(sys.argv[1])
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
