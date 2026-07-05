"""Port of IC_BrivMaster_Logger_Class (IC_BrivMaster_Functions.ahk)."""

from __future__ import annotations

import json
import os
import time

from .ctx import ahk_time_format, tick_ms


class Logger:
    def __init__(self, ctx, log_dir):
        self._ctx = ctx
        stamp = time.strftime(ahk_time_format(
            ctx.setting("IBM_Format_Date_File"), "%Y%m%dT%H%M%S"))
        os.makedirs(log_dir, exist_ok=True)
        # Separate base so other logs can share the start time (Relay log)
        self.logBase = os.path.join(log_dir, f"RunLog_{stamp}")
        self.miniLogPath = os.path.join(log_dir, "MiniLog.json")
        self.logPath = self.logBase + ".csv"
        reset = ctx.memory.ReadResetsTotal()
        ctx.shared.UpdateOutbound("RunLogResetNumber",
                                  reset if reset is not None else -1)
        ctx.shared.UpdateOutbound("RunLog", {})
        self.LogEntries = {}

    def _append(self, text):
        try:
            with open(self.logPath, "a", encoding="utf-8") as f:
                f.write(text)
        except OSError:
            pass

    def NewRun(self):
        ctx = self._ctx
        start_time = tick_ms()  # so it doesn't change between entries
        run = self.LogEntries.get("Run")
        if run is not None:  # there is no entry for the first run
            run["End"] = start_time
            target_zone = ctx.farm.RouteMaster.targetZone
            if run["LastZone"] > target_zone:
                run["LastZone"] = target_zone  # nothing from bosses jumped past reset
            elif run["LastZone"] < target_zone:
                run["Fail"] = True
            ctx.shared.UpdateOutbound("RunLogResetNumber", -1)
            log_entry_json = json.dumps(run)
            ctx.shared.UpdateOutbound("RunLog", log_entry_json)
            ctx.shared.UpdateOutbound("RunLogResetNumber", run["ResetNumber"])
            load_time = run.get("ActiveStart", run["Start"]) - run["Start"]
            reset_time = run["End"] - run.get("ResetReached", run["End"])
            active = run.get("ResetReached", run["End"]) - run.get("ActiveStart", run["Start"])
            electrum = ctx.memory.ReadChestCountByID(282)
            run_string = (f'{run["ResetNumber"]},{run["StartRealTime"]},{run["Start"]},'
                          f'{run["End"] - run["Start"]},{active},{load_time + reset_time},'
                          f'{load_time},{reset_time},{run["Cycle"]},'
                          f'{run["Fail"]},{run["LastZone"]},{electrum}')
            if ctx.setting("IBM_Logger_MiniLog"):
                try:
                    with open(self.miniLogPath, "w", encoding="utf-8") as f:
                        f.write(log_entry_json)
                except OSError as err:
                    self.AddMessage(f"Minilog output failed: {err}")
            messages = ",".join(self.LogEntries.get("Messages", []))
            self._append(f"{run_string},{messages}\n")
        # Reset for new
        self.LogEntries["Messages"] = []
        self.LogEntries["Thellora"] = {}
        run = self.LogEntries["Run"] = {}
        run["Start"] = start_time
        run["StartRealTime"] = time.strftime(ahk_time_format(
            self._ctx.setting("IBM_Format_Date_Display"), "%Y-%m-%d %H:%M:%S"))
        run["ResetNumber"] = ctx.memory.ReadResetsTotal()
        run["GHActive"] = ctx.memory.IBM_IsBuffActive("Potion of the Gem Hunter")
        run["LastZone"] = 0
        run["Fail"] = False
        run["Cycle"] = ""

    def OutputHeader(self, strategy_string):
        self._append("Reset #,Start Time,Start Tick,Total,Active,Wait,Load,"
                     f"Reset,Cycle,Fail,LastZone,Electrum,{strategy_string}\n")

    def ForceFail(self):
        run = self.LogEntries.get("Run")
        if run is not None:
            run["Fail"] = True

    def SetRunCycle(self, cycle_number):
        run = self.LogEntries.get("Run")
        if run is not None:
            run["Cycle"] = cycle_number

    def SetActiveStartTime(self):
        run = self.LogEntries.get("Run")
        if run is not None:
            run["ActiveStart"] = tick_ms()

    def AddMessage(self, message):
        run = self.LogEntries.get("Run")
        messages = self.LogEntries.setdefault("Messages", [])
        if run is not None:
            messages.append(f'{tick_ms() - run["Start"]},{message}')
        else:
            messages.append(f"{tick_ms()}(Abs),{message}")

    def AddThelloraCompensationMessage(self, message, jumps):
        thellora = self.LogEntries.setdefault("Thellora", {})
        if thellora.get("LastJumps") != jumps:
            thellora["LastJumps"] = jumps
            self.AddMessage(f"{message}{jumps}")

    def ResetReached(self):
        run = self.LogEntries.get("Run")
        if run is not None:
            if not run.get("ResetReached"):
                run["ResetReached"] = tick_ms()
            current_zone = self._ctx.memory.ReadCurrentZone()
            if current_zone:
                self.UpdateZone(current_zone)

    def UpdateZone(self, zone):
        run = self.LogEntries.get("Run")
        if run is not None and zone > run["LastZone"]:
            run["LastZone"] = zone
        if self._ctx.setting("IBM_Logger_ZoneLog"):
            route_master = self._ctx.farm.RouteMaster
            intent = "E" if route_master.ShouldWalk(zone) else "Q"
            next_zone = route_master.zones[zone].nextZone
            self.AddMessage(f"z{zone} intent: {intent} to "
                            f"z{next_zone.z if next_zone else '?'}")
