"""Port of IC_BrivMaster_GameMaster.ahk - game process lifecycle."""

from __future__ import annotations

import time

from ..platform import window_backend
from .ctx import precise_sleep, tick_ms


class GameMaster:
    def __init__(self, ctx):
        """Expects the game to be open and loaded into the adventure."""
        self._ctx = ctx
        self._win = window_backend()
        exe_name = ctx.setting("IBM_Game_Exe", "IdleDragons.exe")
        # Prefer the instance the memory reader is already attached to (the
        # entry point picked the fully-loaded one; blindly taking the first
        # window could grab a relay-held login instance)
        if ctx.memory.mem is not None and ctx.memory.mem.attached \
                and ctx.memory.mem.is_running():
            self.PID = ctx.memory.mem.pid
        else:
            pids = self._win.find_pids(exe_name)
            self.PID = pids[0] if pids else 0
        self.Hwnd = self._win.find_window_by_pid(self.PID) if self.PID else 0
        self.SavedActiveWindow = 0
        if self.PID:
            # Realtime needs admin; Windows silently grants High otherwise
            self._win.set_priority_realtime(self.PID)
        ctx.memory.OpenProcessReader(exe_name, self.PID or None)
        # Might fail - checked by the pre-flight check
        self.CurrentAdventure = ctx.memory.ReadCurrentObjID()
        self.KEY_ESC = ctx.input.get_key("Esc")

    def _exe_name(self):
        return self._ctx.setting("IBM_Game_Exe", "IdleDragons.exe")

    # --- opening the game --------------------------------------------------------

    def OpenIC(self, message=""):
        ctx = self._ctx
        timeout_factor = ctx.setting("IBM_OffLine_Timeout", 5)
        wait_ready_timeout = 10000 * timeout_factor  # default 50s
        timeout_val = 5000 * timeout_factor + wait_ready_timeout  # +25s
        loading_zone = False
        suffix = f" {message}" if message else ""
        ctx.shared.UpdateOutbound("LoopString", f"Starting Game{suffix}")
        ctx.log(f"Starting Game{suffix}")
        self.SavedActiveWindow = self._win.get_active_window()
        start_time = tick_ms()
        while not loading_zone and tick_ms() - start_time < timeout_val:
            self.Hwnd = 0
            if tick_ms() - start_time < timeout_val:
                self.OpenProcessAndSetPID()
                if self.PID:
                    self._win.set_priority_realtime(self.PID)
            if tick_ms() - start_time < timeout_val:
                self.SetLastActiveWindowWhileWaitingForGameExe(
                    timeout_val - (tick_ms() - start_time))
            self.ActivateLastWindow()
            ctx.memory.OpenProcessReader(self._exe_name(), self.PID or None)
            ctx.shared.UpdateOutbound("IBM_ProcessSwap", True)
            if tick_ms() - start_time < timeout_val:
                loading_zone = self.WaitForGameReady(wait_ready_timeout)
            if loading_zone:
                ctx.server.Update()
            else:
                precise_sleep(15)
        if tick_ms() - start_time >= timeout_val:
            return -1  # took too long to open
        ctx.farm.RouteMaster.ResetCycleCount()  # we've gone offline either way
        ctx.farm.DialogSwatter.Start()
        return 0

    def OpenProcessAndSetPID(self):
        ctx = self._ctx
        win = self._win
        self.PID = 0
        timeout_factor = ctx.setting("IBM_OffLine_Timeout", 5)
        timeout_left = 8000 * timeout_factor        # default 40s
        process_waiting_timeout = 3000 * timeout_factor  # default 15s
        start_time = tick_ms()
        while not self.PID and tick_ms() - start_time < timeout_left:
            ctx.shared.UpdateOutbound("LoopString", "Opening IC...")
            existing_pids = self.GetExistingPIDList()
            try:
                open_pid = win.launch(ctx.setting("IBM_Game_Launch", ""),
                                      hide=bool(ctx.setting("IBM_Game_Hide_Launcher")))
            except Exception as err:  # noqa: BLE001
                raise RuntimeError(
                    "Unable to launch game - verify the game location "
                    f"settings: {err}") from err
            precise_sleep(15)
            process_name = win.get_process_name(open_pid)
            if process_name and process_name.lower() == self._exe_name().lower():
                # Direct exe launch - the returned PID is the game
                self.PID = open_pid
                ctx.log(f"OpenProcessAndSetPID() set PID=[{self.PID}] via Run return")
            else:
                pid_start = tick_ms()
                while not self.PID and tick_ms() - pid_start < process_waiting_timeout:
                    precise_sleep(45)
                    self.PID = self.GetNewPID(existing_pids)
                ctx.log(f"OpenProcessAndSetPID() set PID=[{self.PID}] via GetNewPID()")
            if not self.PID:
                # Launched something but never found the game: kill any IC
                # process not in the existing list to clean up
                for pid in win.find_pids(self._exe_name()):
                    if pid not in existing_pids:
                        if win.terminate_process(pid):
                            ctx.log("OpenProcessAndSetPID() start fail cleanup "
                                    f"killing PID=[{pid}]")
                        else:
                            ctx.log("OpenProcessAndSetPID() start fail cleanup "
                                    f"attempted to kill PID=[{pid}] but could "
                                    "not find handle")
                    else:
                        ctx.log("OpenProcessAndSetPID() start fail cleanup "
                                f"ignoring PID=[{pid}]")
                if process_name and process_name.lower() in ("rare.exe",
                                                             "legendary.exe"):
                    # Kill queued 3rd-party EGS launchers (never explorer)
                    win.terminate_process(open_pid)
                    ctx.log("OpenProcessAndSetPID() attempted to terminate "
                            f"launcher [{process_name}] PID=[{open_pid}]")

    def GetExistingPIDList(self):
        """PIDs of existing IC windows (window-based, as in AHK)."""
        return [pid for _, pid in self._win.find_windows_by_exe(self._exe_name())]

    def GetNewPID(self, old_pid_list):
        """First game-window PID not in old_pid_list."""
        for _, pid in self._win.find_windows_by_exe(self._exe_name()):
            if pid not in old_pid_list:
                return pid
        return 0

    def SetLastActiveWindowWhileWaitingForGameExe(self, timeout_left=32000):
        start_time = tick_ms()
        while tick_ms() - start_time < timeout_left:
            self.Hwnd = self._win.find_window_by_pid(self.PID) if self.PID else 0
            if self.Hwnd:
                break
            self.SavedActiveWindow = self._win.get_active_window()
            precise_sleep(45)
        self._ctx.log(f"SetLastActiveWindowWhileWaitingForGameExe() set "
                      f"Hwnd=[{self.Hwnd}]")

    def ActivateLastWindow(self):
        if not self._ctx.setting("IBM_Route_Offline_Restore_Window"):
            return
        precise_sleep(80)
        # IC likes to be activated before it can be deactivated
        self._win.activate_window(self.Hwnd)
        self._win.activate_window(self.SavedActiveWindow)

    def WaitForGameReady(self, timeout=90000, skip_final=False):
        """Waits for the game to reach a ready state. skip_final is for relay
        returns, where the offline-calc position is unknown."""
        ctx = self._ctx
        memory = ctx.memory
        route_master = ctx.farm.RouteMaster
        if route_master is not None and not route_master.HybridBlankOffline \
                and route_master.offlineSaveTime >= 0:  # set by stack restart
            self.WaitForUserLogin()
        start_time = tick_ms()
        ctx.shared.UpdateOutbound("LoopString", "Waiting for game started...")
        game_started = 0
        last_input = -250  # input limiter for the Esc presses
        # The splash check must run at least once: the game can get stuck on
        # the splash screen (bug seen up to at least 638.2)
        while tick_ms() - start_time < timeout and not game_started:
            if tick_ms() > last_input + 250 and memory.ReadIsSplashVideoActive():
                with ctx.critical:
                    self.KEY_ESC.key_press()
                last_input = tick_ms()
                precise_sleep(15)
            else:
                precise_sleep(45)
            game_started = memory.ReadGameStarted()
        ctx.farm.RefreshImportCheck()  # version reads are available now
        offline_time = memory.ReadOfflineTime()
        if game_started and offline_time is not None and offline_time <= 0:
            return True  # no offline progress to calculate
        ctx.shared.UpdateOutbound("LoopString", "Waiting for offline progress...")
        offline_done = memory.ReadOfflineDone()
        if not offline_done:
            timeout *= 2  # allow offline progress to survive server issues
        while tick_ms() - start_time < timeout and not offline_done:
            precise_sleep(45)
            offline_done = memory.ReadOfflineDone()
        if offline_done:
            if not skip_final:
                self.WaitForFinalStatUpdates()
            ctx.farm.PreviousZoneStartTime = tick_ms()
            return True
        self.CloseIC(f"WaitForGameReady-Failed to finish in {timeout // 1000}s")
        return False

    def WaitForUserLogin(self):
        """Wait for platform login, then suspend IC until the configured time
        has passed since the game closed (the 15s offline-progress window)."""
        ctx = self._ctx
        memory = ctx.memory
        target_time = ctx.setting("IBM_OffLine_Delay_Time", 13000)
        offline_save_time = ctx.farm.RouteMaster.offlineSaveTime
        if memory.IBM_ReadIsGameUserLoaded() == 1 \
                or tick_ms() - offline_save_time >= target_time:
            return
        ctx.shared.UpdateOutbound("LoopString", "Waiting for platform login...")
        # Need to catch login completing before the userdata request
        while memory.IBM_ReadIsGameUserLoaded() != 1 \
                and tick_ms() - offline_save_time < target_time:
            pass  # spin - this must be fast
        if tick_ms() - offline_save_time >= target_time:
            return  # ran out of time waiting; don't suspend
        memory.mem.suspend()
        while tick_ms() - offline_save_time < target_time:
            precise_sleep(15)
        memory.mem.resume()

    def WaitForFinalStatUpdates(self):
        """Wait for stats to finish updating from offline progress, then set
        the right formation the moment the area activates."""
        ctx = self._ctx
        memory = ctx.memory
        ctx.shared.UpdateOutbound("LoopString",
                                  "Waiting for offline progress (Area Active)...")
        start_time = tick_ms()
        # Starts as 1, drops to 0, back to 1 when active again
        while memory.ReadAreaActive() and tick_ms() - start_time < 5000:
            precise_sleep(15)
        formation_active = False
        # Timing-critical from here to zone-active: get to the proper
        # formation before something spawns and blocks us
        with ctx.critical:
            while not memory.ReadAreaActive() and tick_ms() - start_time < 7000:
                if not formation_active:
                    precise_sleep(15)
                    if not memory.IBM_IsCurrentFormationEmpty():
                        formation_active = True
            current_zone = memory.ReadCurrentZone()
            # Don't change formation on invalid zones or z1 (would override M)
            if current_zone is not None and current_zone > 1:
                ctx.farm.RouteMaster.GetStandardFormationKey(current_zone).key_press()

    # --- closing the game ----------------------------------------------------------

    def CloseIC(self, reason="", use_pid=False):
        ctx = self._ctx
        memory = ctx.memory
        win = self._win
        ctx.shared.UpdateOutbound("LastCloseReason", reason)
        ctx.log(f"Closing Game{(' ' + reason) if reason else ''}")
        ctx.server.Update()  # in case calls are needed before the restart
        ctx.shared.UpdateOutbound("LoopString",
                                  f"Closing IC{(': ' + reason) if reason else ''}")

        def target_hwnd():
            if use_pid:
                return win.find_window_by_pid(self.PID) if self.PID else 0
            return win.find_window_by_exe(self._exe_name())

        timeout = 2000 * ctx.setting("IBM_OffLine_Timeout", 5)  # default 10s
        hwnd = target_hwnd()
        if hwnd:
            win.request_window_close(hwnd, timeout)
        save_complete_time = -1
        # Read the save flags via pinned addresses: the structure reads become
        # invalid before the saveHandler object is gone, so reading through
        # the usual chain could report a save early and kill the game mid-save
        instance = memory.GameManager.game.gameInstances[0]
        dirty_addr = instance.isDirty.resolve_address()
        save_addr = instance.Controller.userData.SaveHandler \
            .currentSave.resolve_address()

        def save_check():
            """2 = reads invalid (game closing), 1 = saved, 0 = not yet."""
            dirty = memory.mem.read(dirty_addr, "Char") if dirty_addr else None
            current_save = memory.mem.read(save_addr, "Int") if save_addr else None
            if dirty is None or current_save is None:
                return 2
            if dirty == 0 and current_save == 0:
                return 1
            return 0

        start_time = tick_ms()
        while target_hwnd() and tick_ms() - start_time < timeout:
            precise_sleep(15)
            if save_complete_time == -1:
                status = save_check()
                if status:
                    save_complete_time = tick_ms()
                    ctx.log(f"CloseIC() Standard Loop "
                            f"{'Save' if status == 1 else 'Reads Invalid'} - "
                            f"saveCompleteTime=[{save_complete_time}] "
                            f"Timeout=[{tick_ms() - start_time}/{timeout}]")
                    ctx.farm.RouteMaster.CheckRelayRelease()
                    # After the save there's no reason not to force-close
                    start_time = tick_ms()
                    timeout = 500 * ctx.setting("IBM_OffLine_Timeout", 5)
        timeout = 2000 * ctx.setting("IBM_OffLine_Timeout", 5)
        start_time = tick_ms()
        next_close_attempt = tick_ms()
        while target_hwnd() and tick_ms() - start_time < timeout:  # outright murder
            if save_complete_time == -1:
                status = save_check()
                if status:
                    save_complete_time = tick_ms()
                    ctx.farm.RouteMaster.CheckRelayRelease()
                    ctx.log(f"CloseIC() TerminateProgress Loop "
                            f"{'Save' if status == 1 else 'Reads Invalid'} - "
                            f"saveCompleteTime=[{save_complete_time}] "
                            f"Timeout=[{tick_ms() - start_time}/{timeout}]")
            if tick_ms() >= next_close_attempt:
                if win.terminate_process(self.PID):
                    ctx.log("CloseIC() failed to close cleanly: sending "
                            f"TerminateProcess saveCompleteTime="
                            f"[{save_complete_time}] "
                            f"Timeout=[{tick_ms() - start_time}/{timeout}]")
                else:
                    ctx.log("CloseIC() failed to close cleanly: failed to get "
                            "process handle for TerminateProcess "
                            f"saveCompleteTime=[{save_complete_time}] "
                            f"Timeout=[{tick_ms() - start_time}/{timeout}]")
                    break  # no handle - retrying won't help
                next_close_attempt = tick_ms() + 500
            precise_sleep(15)
        if save_complete_time == -1:
            save_complete_time = tick_ms()
            ctx.log("CloseIC() fully timed out without detecting a save")
        return save_complete_time

    # --- general -------------------------------------------------------------------

    def SafetyCheck(self):
        """Reopens IC if closed; recovers state. True if the window exists."""
        ctx = self._ctx
        memory = ctx.memory
        if not self._win.find_window_by_exe(self._exe_name()):
            open_result = self.OpenIC("Called from SafetyCheck()")
            while open_result == -1:
                self.CloseIC("Failed to start Idle Champions")
                open_result = self.OpenIC("Called from SafetyCheck() loop")
            if memory.ReadResetting() and (memory.ReadCurrentZone() or 0) <= 1 \
                    and memory.ReadCurrentObjID() is None:
                ctx.shared.UpdateOutbound("LoopString", "Zone is -1. At world map?")
                self.RestartAdventure("At world map?")
            self.RecoverFromGameClose()
            if ctx.farm.currentZone is not None:
                return_zone = memory.ReadCurrentZone()
                if return_zone is not None:
                    if ctx.farm.currentZone > return_zone:
                        ctx.farm.RollBackAction(return_zone)
                    elif ctx.farm.currentZone < return_zone:
                        ctx.shared.UpdateOutbound_Increment("BadAutoProgress")
                        ctx.log("Bad autoprogress detected - expected "
                                f"z[{ctx.farm.currentZone}] return z[{return_zone}]")
            return False
        if memory.ReadCurrentZone() is None:
            # Game loaded but zone unreadable - process changed under us?
            ctx.log(f"SafetyCheck() Resetting process reader - old "
                    f"PID=[{self.PID}] and Hwnd=[{self.Hwnd}]")
            self.Hwnd = self._win.find_window_by_exe(self._exe_name())
            pids = self._win.find_pids(self._exe_name())
            self.PID = pids[0] if pids else 0
            memory.OpenProcessReader(self._exe_name(), self.PID or None)
            ctx.server.Update()
            ctx.log(f"SafetyCheck() Reset process reader - new "
                    f"PID=[{self.PID}] and Hwnd=[{self.Hwnd}]")
        return True

    def RecoverFromGameClose(self):
        """Get back onto the right formation after a reopen."""
        ctx = self._ctx
        memory = ctx.memory
        route_master = ctx.farm.RouteMaster
        timeout = 10000
        current_zone = memory.ReadCurrentZone()
        if current_zone == 1 or current_zone is None:
            return
        game_start_formation = route_master.GetStandardFormation(current_zone)
        key = route_master.GetStandardFormationKey(current_zone)
        start_time = tick_ms()
        is_current = route_master.IsCurrentFormation(game_start_formation)
        while not is_current and tick_ms() - start_time < timeout \
                and not memory.ReadNumAttackingMonstersReached():
            key.key_press()  # mash to get in before an enemy spawns
            precise_sleep(15)
            is_current = route_master.IsCurrentFormation(game_start_formation)
        timeout *= 2
        while not is_current and memory.ReadNumAttackingMonstersReached() \
                and tick_ms() - start_time < timeout:
            route_master.FallBackFromZone()
            key.key_press()
            route_master.ToggleAutoProgress(1, True)
            is_current = route_master.IsCurrentFormation(game_start_formation)
        ctx.shared.UpdateOutbound("LoopString", "Loading game finished")

    def RestartAdventure(self, reason="", modron_fail=False):
        """Server-side adventure restart for stuck situations. modron_fail
        avoids restarting when the server looks down (we'd likely resume the
        old run once it returns)."""
        ctx = self._ctx
        memory = ctx.memory
        server = ctx.server
        ctx.shared.UpdateOutbound("LoopString", "ServerCall: Restarting adventure")
        ctx.farm.Logger.ForceFail()
        ctx.log(f"Forced Restart (Reason:{reason} at:z{memory.ReadCurrentZone()}"
                f" with haste:{ctx.heroes[58].ReadHasteStacks()})")
        self.CloseIC(reason)
        ctx.shared.UpdateOutbound("LoopString",
                                  "ServerCall: Checking stack conversion")
        if server.ShouldCallPreventStackFail(True):  # updated by CloseIC()
            response = server.CallPreventStackFail("RestartAdventure()")
            if response:
                failure = response.get("failure_reason")
                ctx.log(f"Stack save response: success=[{response.get('success')}]"
                        f" okay=[{response.get('okay')}]"
                        f"{f' failure Reason=[{failure}]' if failure else ''}")
            elif modron_fail:
                # Empty return: server/connection issues - earlier saves also
                # likely failed; reconnecting without restarting lets us resume
                ctx.log("Stack save response: empty and in modron mode - resuming")
                return
            else:
                ctx.log("Stack save response: empty and not in modron mode - "
                        "proceeding to restart adventure")
            ctx.shared.UpdateOutbound(
                "LoopString",
                "ServerCall: Restarting adventure (post stack conversion)")
        else:
            ctx.log(f"ServerCall Save not required (Haste:{server.sprint} "
                    f"raw Steelbones:{server.steelbones})")
            ctx.shared.UpdateOutbound(
                "LoopString",
                "ServerCall: Restarting adventure (no manual stack conversion)")
        response = server.CallEndAdventure()
        if response:
            if response.get("success"):
                ctx.log("End adventure response: success - loading new adventure")
            else:
                ctx.log(f"End adventure response: failure reason="
                        f"[{response.get('failure_reason')}] - resuming")
                return
        else:  # server down / no connectivity - loading would be useless
            ctx.log("End adventure response: empty - resuming")
            return
        for attempt in range(1, 4):
            call_time = tick_ms()
            response = server.CallLoadAdventure(self.CurrentAdventure)
            if response:
                if response.get("success") and response.get("okay"):
                    ctx.log(f"Load adventure response: attempt [{attempt}] "
                            "success and okay - resuming")
                    ctx.farm.TriggerStart = True  # we're in a new adventure now
                    return
                ctx.log(f"Load adventure response: attempt [{attempt}] "
                        f"success=[{response.get('success')}] "
                        f"okay=[{response.get('okay')}] failure reason="
                        f"[{response.get('failure_reason')}]")
            else:
                ctx.log(f"Load adventure response: attempt [{attempt}] empty")
            while tick_ms() < call_time + 20000:  # ensure at least 20s between
                precise_sleep(50)
        ctx.log("Load adventure: out of attempts - probably on world map")
