"""GUI-independent Home logic: port of IC_IriBrivMaster_Component (the parts
that act, not draw), IC_IriBrivMaster_ChestSnatcher_Class and
IC_BrivMaster_EllywickDealer_Class from IC_BrivMaster_Home.ahk.

The Home process has its own memory reader and server-call object, exactly
like the AHK hub; it talks to the farm process only through the IPC 'shared'
scope (the COM SharedRunData replacement).
"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys
import time

from ..farm.ctx import FarmContext, precise_sleep, tick_ms
from ..farm.heroes import Heroes
from ..ipc import IpcClient, IpcError
from ..memory.functions import MemoryFunctions
from ..platform import window_backend
from ..platform.input import InputManager
from ..server_call import ServerCall

# For chests: server rate limits and costs (CONSTANT_* in the AHK)
SERVER_RATE_OPEN = 1000
SERVER_RATE_BUY = 250
GOLD_COST = 500
SILVER_COST = 50
CHEST_SILVER, CHEST_GOLD = 1, 2


def hub_setting(ctx, key, default=None):
    hub = ctx.settings.get("HUB") or {}
    value = hub.get(key, default)
    return default if value in (None, "") else value


class StatsTracker:
    """BPH/GPH and per-run aggregation from the farm's RunLog snapshots
    (the UpdateStats part of the AHK hub, condensed)."""

    def __init__(self):
        self.reset()

    def reset(self):
        self._last_runlog = None
        self.valid_runs = 0
        self.fails = 0
        self.total_ms = 0
        self.active_ms = 0
        self.bosses = 0
        self.fastest = None
        self.slowest = None
        self.last_bph = None
        self._gems_last = None
        self._gems_earned = 0
        self._started = time.time()

    def update(self, snapshot, current_gems):
        if current_gems:
            if self._gems_last is not None and current_gems > self._gems_last:
                self._gems_earned += current_gems - self._gems_last
            self._gems_last = current_gems
        runlog = snapshot.get("RunLog")
        if not runlog or runlog == self._last_runlog \
                or not isinstance(runlog, str):
            return
        self._last_runlog = runlog
        try:
            import json
            run = json.loads(runlog)
        except ValueError:
            return
        duration = (run.get("End") or 0) - (run.get("Start") or 0)
        if duration <= 0:
            return
        if run.get("Fail"):
            self.fails += 1
        if not run.get("ActiveStart"):
            return  # partial run
        self.valid_runs += 1
        self.total_ms += duration
        self.active_ms += (run.get("ResetReached", run["End"])
                           - run["ActiveStart"])
        run_bosses = (run.get("LastZone") or 0) // 5
        self.bosses += run_bosses
        self.fastest = duration if self.fastest is None \
            else min(self.fastest, duration)
        self.slowest = duration if self.slowest is None \
            else max(self.slowest, duration)
        self.last_bph = round(run_bosses * 3600000 / duration, 1)

    def summary(self):
        if not self.valid_runs:
            return "Runs: 0"
        hours = self.total_ms / 3600000
        session_hours = max((time.time() - self._started) / 3600, 1e-9)
        bph = round(self.bosses / hours, 1) if hours else 0
        gph = round(self._gems_earned / session_hours)
        return (f"Runs: {self.valid_runs} (fails {self.fails})   "
                f"BPH: {bph} (last {self.last_bph})   GPH: {gph:,}   "
                f"avg/fast/slow: {round(self.total_ms / self.valid_runs / 1000, 1)}"
                f"/{round((self.fastest or 0) / 1000, 1)}"
                f"/{round((self.slowest or 0) / 1000, 1)}s")


class HomeHub:
    def __init__(self, settings_path, offsets_path):
        self.settings_path = settings_path
        ctx = self.ctx = FarmContext()
        from ..farm.shared_data import SharedData
        from .settings_io import merge_with_template
        ctx.shared = SharedData(ctx, settings_path)  # local placeholder copy
        ctx.shared.Init()
        # Add missing settings / drop unknown ones, as the AHK LoadSettings does
        merge_with_template(ctx.settings)
        ctx.memory = MemoryFunctions(offsets_path)
        self._win = window_backend()
        exe = ctx.setting("IBM_Game_Exe", "IdleDragons.exe")
        ctx.input = InputManager(
            lambda: self._win.find_window_by_exe(exe),
            scan_codes=ctx.setting("IBM_Scan_Codes", {}))
        ctx.server = ServerCall(ctx.memory)
        ctx.heroes = Heroes(ctx)
        self.farm_ipc = None          # IpcClient to the farm process
        self.farm_process = None
        from .game_settings import GameSettingsProfiles
        self.ChestSnatcher = ChestSnatcher(self)
        self.stats = StatsTracker()
        self.GameSettings = GameSettingsProfiles(ctx)
        self.EllyDealer = None
        self.ServerCallFailCount = 0
        self.MemoryReadFailCount = 0
        self.CurrentGems = 0
        self.Chests = {"CurrentSilver": 0, "CurrentGold": 0,
                       "PurchasedSilver": 0, "PurchasedGold": 0,
                       "OpenedSilver": 0, "OpenedGold": 0}
        self.status_message = ""

    # --- farm process control (Run/Stop/Connect buttons) -----------------------

    def Connect_Clicked(self):
        """Attach to a running farm via the endpoint file."""
        try:
            client = IpcClient()
            client.ping()
            self.farm_ipc = client
            self._on_connected()
            return True
        except (IpcError, OSError, ValueError):
            self.farm_ipc = None
            self.status_message = "Gem Farm not running"
            return False

    def Run_Clicked(self):
        if self.Connect_Clicked():
            return  # already running
        args = [sys.executable, "-m", "brivmaster.run_farm",
                "--settings", self.settings_path]
        # Farm output goes to a file, never a pipe: an undrained pipe fills
        # up and blocks the farm mid-run once the buffer is full.
        logs_dir = os.path.join(os.path.dirname(os.path.abspath(
            self.settings_path)), "Logs")
        log_path = os.path.join(logs_dir, "FarmConsole.log")
        try:
            os.makedirs(logs_dir, exist_ok=True)
            with open(log_path, "w", encoding="utf-8") as log_file:
                self.farm_process = subprocess.Popen(
                    args, stdout=log_file, stderr=subprocess.STDOUT)
            self.status_message = \
                f"Farm starting (PID: {self.farm_process.pid})..."
            time.sleep(0.5)
            if self.farm_process.poll() is not None:
                try:
                    with open(log_path, encoding="utf-8") as f:
                        tail = f.read().strip().splitlines()
                    error_msg = tail[-1] if tail else "Unknown error"
                except OSError:
                    error_msg = "Unknown error"
                self.status_message = f"Farm failed: {error_msg[:150]}"
        except Exception as err:  # noqa: BLE001
            self.status_message = f"Error starting farm: {err}"
        # The GUI retries Connect on its timer until the endpoint appears

    def Stop_Clicked(self):
        self.status_message = "Closing Gem Farm"
        if self.farm_ipc is not None:
            try:
                self.farm_ipc.call("control", "Stop")
                self.status_message = "Gem Farm Stopped"
            except IpcError:
                self.status_message = "Gem Farm not running"
            self.farm_ipc.close()
            self.farm_ipc = None

    def _on_connected(self):
        exe = self.ctx.setting("IBM_Game_Exe", "IdleDragons.exe")
        self.ctx.memory.AttachToReadyInstance(exe, wait_s=5)
        try:
            self.ctx.server.Update()
        except Exception:  # noqa: BLE001 - server issues must not kill Home
            pass
        self.status_message = "Connected to Gem Farm"

    def Launch_Clicked(self):
        """Launch the game itself."""
        ctx = self.ctx
        try:
            pid = self._win.launch(ctx.setting("IBM_Game_Launch", ""),
                                   hide=bool(ctx.setting("IBM_Game_Hide_Launcher")))
        except Exception as err:  # noqa: BLE001
            self.status_message = f"Unable to launch game: {err}"
            return
        name = self._win.get_process_name(pid)
        if not (name and name.lower() == ctx.setting(
                "IBM_Game_Exe", "IdleDragons.exe").lower()):
            pids = self._win.find_pids(ctx.setting("IBM_Game_Exe",
                                                   "IdleDragons.exe"))
            pid = pids[0] if pids else 0
        if pid:
            self._win.set_priority_realtime(pid)
        self.status_message = "Game launched"

    # --- shared data access ----------------------------------------------------------

    def shared_get(self, name, default=None):
        if self.farm_ipc is None:
            return default
        try:
            value = self.farm_ipc.get("shared", name)
            return default if value is None else value
        except IpcError:
            self.farm_ipc = None
            return default

    def shared_set(self, name, value):
        if self.farm_ipc is None:
            return False
        try:
            self.farm_ipc.set("shared", name, value)
            return True
        except IpcError:
            self.farm_ipc = None
            return False

    def shared_snapshot(self):
        if self.farm_ipc is None:
            return {}
        try:
            return self.farm_ipc.snapshot("shared") or {}
        except IpcError:
            self.farm_ipc = None
            return {}

    # --- periodic update (the AHK UpdateStatus timer) ------------------------------------

    def Update(self):
        """Called by the GUI timer (~500ms). Returns the shared snapshot."""
        snapshot = self.shared_snapshot()
        self.stats.update(snapshot, self.CurrentGems)
        try:
            self.GameSettings.tick()  # hourly profile check
        except Exception:  # noqa: BLE001 - never kill the GUI timer
            pass
        if self.farm_ipc is not None:
            if self.IsGameOpen():
                self.RefreshChestCounts()
                try:
                    self.ChestSnatcher.Snatch()
                except Exception as err:  # noqa: BLE001
                    self.ChestSnatcher.AddMessage("General",
                                                  f"Snatch error: {err}")
        return snapshot

    def IsGameOpen(self):
        return bool(self._win.find_window_by_exe(
            self.ctx.setting("IBM_Game_Exe", "IdleDragons.exe")))

    def RefreshChestCounts(self):
        memory = self.ctx.memory
        if not memory.IsAttached:
            exe = self.ctx.setting("IBM_Game_Exe", "IdleDragons.exe")
            if memory.AttachToReadyInstance(exe, wait_s=0) is None:
                return
        gems = memory.ReadGems()
        if gems is not None:
            self.CurrentGems = gems
        silvers = memory.ReadChestCountByID(CHEST_SILVER)
        if silvers is not None:
            self.Chests["CurrentSilver"] = silvers
        golds = memory.ReadChestCountByID(CHEST_GOLD)
        if golds is not None:
            self.Chests["CurrentGold"] = golds

    def RefreshUserData(self):
        if self.IsGameOpen():
            try:
                self.ctx.server.Update()
            except Exception:  # noqa: BLE001
                pass


class ChestSnatcher:
    """IC_IriBrivMaster_ChestSnatcher_Class."""

    def __init__(self, hub):
        self.hub = hub
        self.Messages = []
        # Wait 3min before the first daily check to avoid spamming while testing
        self.NextDailyClaimCheck = tick_ms() + 180000
        self.StartMessage()

    def _setting(self, key, default=0):
        return hub_setting(self.hub.ctx, key, default)

    def AddMessage(self, action, comment):
        self.Messages.append({"Time": time.strftime("%H:%M:%S"),
                              "Action": action, "Comment": comment})
        if len(self.Messages) > 20:
            self.Messages.pop(0)

    def StartMessage(self):
        self.AddMessage("General", "Awaiting first order")

    def Snatch(self):
        """Process chest purchase orders (called on the update timer)."""
        hub = self.hub
        if hub.shared_get("IBM_BuyChests"):
            if self._setting("IBM_DailyRewardClaim_Enable") \
                    and tick_ms() >= self.NextDailyClaimCheck:
                self.ClaimDailyRewards()
            elif self._setting("IBM_ChestSnatcher_Options_Open_Gold") \
                    or self._setting("IBM_ChestSnatcher_Options_Open_Silver"):
                self.CheckOpenChests()
            else:
                hub.shared_set("IBM_BuyChests", 0)  # cancel the order
        elif self._setting("IBM_ChestSnatcher_Options_Min_Buy"):
            gems = hub.CurrentGems - self._setting("IBM_ChestSnatcher_Options_Min_Gem")
            amount = min(gems // GOLD_COST, SERVER_RATE_BUY)
            if amount >= self._setting("IBM_ChestSnatcher_Options_Min_Buy"):
                self.AddMessage("Buy", f"No open order, buying {amount} Gold...")
                self.BuyChests(CHEST_GOLD, int(amount))

    def _seconds_since_last_save(self):
        """The chest/daily calls are only safe just after a save (so the game
        doesn't overwrite the result). IBM_ReadLastSave is seconds since
        01Jan0001; None when unavailable."""
        last_save_epoch = self.hub.ctx.memory.IBM_ReadLastSave()
        if not last_save_epoch:
            return None
        unix_time = last_save_epoch - 62135596800
        return time.time() - unix_time

    def ClaimDailyRewards(self):
        hub = self.hub
        server = hub.ctx.server
        memory = hub.ctx.memory
        elapsed = self._seconds_since_last_save()
        if elapsed is None or elapsed >= 2:
            return
        server_string = (f"&user_id={memory.ReadUserID()}"
                         f"&hash={memory.ReadUserHash()}"
                         f"&instance_id={memory.ReadInstanceID()}"
                         "&language_id=1&timestamp=0&request_id=0"
                         f"&network_id={memory.ReadPlatform()}"
                         f"&mobile_client_version={memory.ReadBaseGameVersion()}"
                         "&instance_key=1&offline_v2_build=1"
                         "&localization_aware=true")
        response = server.ServerCall("getdailyloginrewards", server_string)
        boost_expiry = 0
        if response and response.get("success"):
            details = response.get("daily_login_details", {})
            day_mask = 1 << details.get("today_index", 0)
            premium_active = details.get("premium_active")
            if premium_active and details.get("premium_expire_seconds", 0) > 0:
                boost_expiry = details["premium_expire_seconds"] / 86400  # days
            standard_claimed = (details.get("rewards_claimed", 0) & day_mask) > 0
            premium_claimed = (details.get("premium_rewards_claimed", 0)
                               & day_mask) > 0
            if standard_claimed and (premium_claimed or not premium_active):
                next_claim = details.get("next_claim_seconds", 300)
                self.NextDailyClaimCheck = tick_ms() + min(28800000,
                                                           next_claim * 1000)
                self.AddMessage("Claim",
                                "Standard and premium daily rewards already "
                                "claimed" if premium_active else
                                "Standard daily reward already claimed. "
                                "Premium not active")
                if premium_active:
                    self.AddMessage("Claim", f"Premium daily reward expires in "
                                             f"{round(boost_expiry, 1)} days")
                return
            if premium_active:
                self.AddMessage("Claim",
                                f"Standard reward "
                                f"{'' if standard_claimed else 'un'}claimed "
                                f"and premium reward "
                                f"{'' if premium_claimed else 'un'}claimed")
                self.AddMessage("Claim", "Claiming...")
                self.AddMessage("Claim", f"Premium daily reward expires in "
                                         f"{round(boost_expiry, 1)} days")
            else:
                self.AddMessage("Claim", "Standard reward unclaimed and "
                                         "premium reward not active")
                self.AddMessage("Claim", "Claiming...")
        else:
            self.AddMessage("Claim", "Failed to check current daily reward status")
            return
        response = server.ServerCall("claimdailyloginreward",
                                     "&is_boost=0" + server_string)
        if response and response.get("success"):
            details = response.get("daily_login_details", {})
            next_claim = details.get("next_claim_seconds")
            if details.get("premium_active"):
                response = server.ServerCall("claimdailyloginreward",
                                             "&is_boost=1" + server_string)
                if response and response.get("success"):
                    next_claim = response.get("daily_login_details", {}) \
                        .get("next_claim_seconds", next_claim)
                    self.AddMessage("Claim",
                                    "Claimed standard and premium daily rewards")
                else:
                    self.AddMessage("Claim",
                                    "Claimed standard daily reward and failed "
                                    "to claim available premium reward")
            else:
                self.AddMessage("Claim", "Claimed standard daily reward")
            if not next_claim:
                next_claim = 300  # no value despite success: wait 5min
            self.NextDailyClaimCheck = tick_ms() + min(28800000,
                                                       next_claim * 1000)
        else:
            self.NextDailyClaimCheck = tick_ms() + 60000
            self.AddMessage("Claim", "Failed to claim daily rewards")
            self.hub.ServerCallFailCount += 1

    def BuyChests(self, chest_id=CHEST_SILVER, num_chests=100):
        hub = self.hub
        if num_chests <= 0:
            return
        call_time = tick_ms()
        response = hub.ctx.server.CallBuyChests(chest_id, num_chests)
        call_ms = tick_ms() - call_time
        if response and response.get("okay") and response.get("success"):
            name = "Gold" if chest_id == CHEST_GOLD else "Silver"
            key = "Gold" if chest_id == CHEST_GOLD else "Silver"
            hub.Chests[f"Purchased{key}"] += num_chests
            hub.Chests[f"Current{key}"] = response.get("chest_count",
                                                       hub.Chests[f"Current{key}"])
            hub.CurrentGems = response.get("currency_remaining", hub.CurrentGems)
            self.AddMessage("Buy", f"Bought {num_chests} {name} in {call_ms}ms")
        else:
            self.AddMessage("Buy", "Chest purchase failed")
            hub.ServerCallFailCount += 1

    def CheckOpenChests(self):
        hub = self.hub
        elapsed = self._seconds_since_last_save()
        if elapsed is None or elapsed >= 2:
            return
        hub.shared_set("IBM_BuyChests", False)  # prevent repeats this run
        open_gold = self._setting("IBM_ChestSnatcher_Options_Open_Gold")
        open_silver = self._setting("IBM_ChestSnatcher_Options_Open_Silver")
        if open_gold and open_gold + self._setting(
                "IBM_ChestSnatcher_Options_Min_Gold") <= hub.Chests["CurrentGold"]:
            self.OpenChests(CHEST_GOLD, open_gold)
        elif open_silver and open_silver + self._setting(
                "IBM_ChestSnatcher_Options_Min_Silver") <= hub.Chests["CurrentSilver"]:
            self.OpenChests(CHEST_SILVER, open_silver)
        else:
            self.AddMessage("Open", "Not enough chests to process open order")

    def OpenChests(self, chest_id=CHEST_SILVER, num_chests=250):
        hub = self.hub
        name = "Gold" if chest_id == CHEST_GOLD else "Silver"
        call_time = tick_ms()
        self.AddMessage("Open", f"Opening {num_chests} {name}...")
        results = hub.ctx.server.CallOpenChests(chest_id, num_chests)
        call_ms = tick_ms() - call_time
        if not results or not results.get("success"):
            reason = (results or {}).get("failure_reason")
            if not reason:
                self.AddMessage("Open", f"Failed attempting to open "
                                        f"{num_chests} {name} - no reason reported")
                hub.ServerCallFailCount += 1
            elif reason == "Outdated instance id":
                self.AddMessage("Open", f"Failed attempting to open "
                                        f"{num_chests} {name} - Old ID - Refreshing")
                hub.RefreshUserData()
            else:
                self.AddMessage("Open", f"Failed attempting to open "
                                        f"{num_chests} {name} - {reason}")
                hub.ServerCallFailCount += 1
            return
        key = "Gold" if chest_id == CHEST_GOLD else "Silver"
        hub.Chests[f"Opened{key}"] += num_chests
        hub.Chests[f"Current{key}"] = results.get("chests_remaining",
                                                  hub.Chests[f"Current{key}"])
        self.AddMessage("Open", f"Opened {num_chests} {name} in {call_ms}ms")


class EllywickDealer:
    """IC_BrivMaster_EllywickDealer_Class - re-rolling outside gem farming.
    Driven by the GUI timer via Tick(); status via self.status."""

    def __init__(self, hub, min_cards, max_cards):
        # Arrays indexed by card type: 1 Knight, 2 Moon, 3 Gem, 4 Fates, 5 Flames
        self.hub = hub
        self.minCards = min_cards
        self.maxCards = max_cards
        self.Redraws = 0
        self.UsedUlt = False  # cards only clear when the ult ENDS
        self.status = "Starting"
        self.running = False
        heroes = hub.ctx.heroes
        heroes[83].Reset()  # clear any previous handlers
        heroes[99].Reset()

    def Start(self):
        self.running = True
        self.hub.ctx.heroes[83].InitDoMTHandler()
        self.Tick()

    def Stop(self):
        self.running = False

    def Tick(self):
        if not self.running:
            return
        heroes = self.hub.ctx.heroes
        memory = self.hub.ctx.memory
        elly, dungeon_master = heroes[83], heroes[99]
        if elly.EFFECT_HANDLER_CARDS is None:
            elly.InitDoMTHandler()
            return  # re-check next tick
        if memory.ReadResetting() or memory.ReadCurrentZone() is None:
            return
        if self.UsedUlt and not elly.ReadEllywickUltimateActive():
            self.UsedUlt = False
        remaining = self.GetRemainingCardsToDraw()
        within_max = self.CheckWithinMax()
        num_cards = elly.ReadNumCards() or 0
        if remaining == 0 and num_cards == 5 and within_max:
            self.status = f"Complete after {self.Redraws} redraws"
            self.Stop()
        elif (5 - num_cards) < remaining or not within_max:
            if elly.CanUseUltimate() and not self.UsedUlt:
                self.status = "Using Ellywick's ultimate"
                self.UseEllywickUlt()
            elif dungeon_master.CanUseUltimate():
                self.UseDMUlt()
                self.status = "Using DM's ultimate"
            else:
                self.status = "Waiting for ultimate"
        else:
            self.status = "Drawing Cards"

    def GetRemainingCardsToDraw(self):
        elly = self.hub.ctx.heroes[83]
        return sum(max(0, want - elly.GetNumCardsOfType(card_type))
                   for card_type, want in self.minCards.items())

    def CheckWithinMax(self):
        elly = self.hub.ctx.heroes[83]
        return all(elly.GetNumCardsOfType(card_type) <= max_cards
                   for card_type, max_cards in self.maxCards.items())

    def UseEllywickUlt(self):
        heroes = self.hub.ctx.heroes
        if self.hub.ctx.memory.ReadTransitioning():
            return  # no ults during transitions - source of Weird Stuff
        if heroes[83].CanUseUltimate():
            self.UsedUlt = True  # block double presses
            retry_count = heroes[83].UseUltimate(50)
            if retry_count is None or retry_count > 50:
                self.UsedUlt = False
            else:
                self.Redraws += 1
                self.UseDMUlt()
        elif heroes[99].CanUseUltimate():
            self.UseDMUlt(0)

    def UseDMUlt(self, sleep_time=30):
        """Default 30ms sleep lets the game process Elly's ult first."""
        heroes = self.hub.ctx.heroes
        if heroes[99].CanUseUltimate():
            precise_sleep(sleep_time)
            heroes[99].UseUltimate(50)
