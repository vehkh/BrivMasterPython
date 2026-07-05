"""Port of IC_BrivMaster_Run.ahk - the gem farm main loop, first-zone logic,
stuck handling and the pre-flight check.

GUI-dependent parts (the small farm status window, MsgBox prompts) are
deferred to phase 4; status goes to SharedData.LoopString / the log instead.
Pre-flight failures raise PreFlightError rather than showing a MsgBox; the
one user-choice prompt (imperfect familiar config) logs a warning and
continues.
"""

from __future__ import annotations

import time

from .casino import DialogSwatter, DianaCheese, EllywickCasino
from .ctx import precise_sleep, sleep_offset, tick_ms
from .game_master import GameMaster
from .level_manager import LevelManager
from .logger import Logger
from .route_master import RouteMaster


class PreFlightError(Exception):
    def __init__(self, failing_step, message):
        super().__init__(f"{failing_step}: {message}")
        self.failing_step = failing_step


class GemFarm:
    """IC_BrivMaster_GemFarm_Class."""

    def __init__(self, ctx, log_dir):
        self._ctx = ctx
        self._log_dir = log_dir
        self.TriggerStart = True
        self.offRamp = False
        self.failedConversionMode = False
        self.currentZone = None
        self.previousZone = None
        self.PreviousZoneStartTime = tick_ms()
        self.CheckifStuck_lastCheck = 0
        self.CheckifStuck_fallBackTries = 0
        self.Logger = None
        self.GameMaster = None
        self.RouteMaster = None
        self.LevelManager = None
        self.EllywickCasino = None
        self.DialogSwatter = None
        self.DianaCheeseHelper = None
        self._stop_requested = False

    # Capitalised alias used by ported code that wrote g_IBM.levelManager
    @property
    def levelManager(self):
        return self.LevelManager

    def Stop(self):
        self._stop_requested = True

    def GemFarm(self):
        ctx = self._ctx
        memory = ctx.memory
        last_reset_count = 0
        self.TriggerStart = True
        self.GameMaster = GameMaster(ctx)  # does the initial OpenProcessReader
        self.RefreshImportCheck()
        ctx.server.Update()
        self.Logger = Logger(ctx, self._log_dir)
        ctx.server.logger = self.Logger
        # Must precede PreFlightCheck - it uses the formation data loaded here
        self.LevelManager = LevelManager(ctx)
        self.RouteMaster = RouteMaster(ctx, ctx.setting("IBM_Route_Combine"),
                                       self.Logger.logBase)
        ctx.server.stack_conversion_rate = self.RouteMaster.stackConversionRate
        self.PreFlightCheck()  # raises PreFlightError on failure
        self.offRamp = False  # missed-reset detection near the end of a run
        self.EllywickCasino = EllywickCasino(ctx)
        self.DialogSwatter = DialogSwatter(ctx)
        if ctx.setting("IBM_Level_Diana_Cheese"):
            self.DianaCheeseHelper = DianaCheese(ctx)
        ctx.shared.UpdateOutbound("IBM_BuyChests", False)
        self.PreviousZoneStartTime = tick_ms()
        self.CheckifStuck_lastCheck = 0
        self.CheckifStuck_fallBackTries = 0

        while not self._stop_requested:
            self.currentZone = memory.ReadCurrentZone()
            if self.currentZone is None:
                self.GameMaster.SafetyCheck()
            if not self.TriggerStart:  # check for unexpected resets
                resets = memory.ReadResetsCount()
                if resets is not None and resets > last_reset_count:
                    self.TriggerStart = True
                    self.Logger.AddMessage(
                        f"Missed Reset: Core reset count=[{resets}] "
                        f"lastResetCount=[{last_reset_count}]")
                elif (last_reset_count == 0 and self.offRamp
                      and self.currentZone is not None
                      and self.currentZone <= self.RouteMaster.thelloraTarget):
                    # First run after a forced restart: can't tell run 0 from
                    # run 0 if another forced restart happened within it
                    self.TriggerStart = True
                    self.Logger.AddMessage(
                        "Missed Reset: Core reset count=0 offramp=true and "
                        f"z[{self.currentZone}] is at or before Thellora "
                        f"target z[{self.RouteMaster.thelloraTarget}]")
            if self.TriggerStart:  # first loop of a run
                ctx.shared.UpdateOutbound("IBM_BuyChests", False)
                if ctx.shared.BossesHitThisRun:
                    self.Logger.AddMessage(
                        f"Bosses:{ctx.shared.BossesHitThisRun}")
                    ctx.shared.UpdateOutbound("BossesHitThisRun", 0)
                self.Logger.NewRun()
                self.currentZone = self.WaitForZoneLoad(self.currentZone)
                # Set initial autoprogress ASAP
                self.RouteMaster.ToggleAutoProgress(
                    1 if ctx.heroes[139].inM else 0)
                self.offRamp = False
                self.failedConversionMode = False
                self.LevelManager.Reset()
                self.RouteMaster.Reset()
                self.EllywickCasino.Reset()
                self.IBM_FirstZone(self.currentZone)
                last_reset_count = memory.ReadResetsCount() or 0
                # During hybrid, no online chests on offline runs (early save)
                if not self.RouteMaster.ExpectingGameRestart() \
                        or self.RouteMaster.cycleMax == 1:
                    ctx.shared.UpdateOutbound("IBM_BuyChests", True)
                self.PreviousZoneStartTime = tick_ms()
                self.TriggerStart = False
                last_loop_end = time.perf_counter()
                ctx.shared.UpdateOutbound("LoopString", "Main Loop")
                # May have progressed during first-zone logic
                self.previousZone = self.currentZone
                self.currentZone = memory.ReadCurrentZone()
            ctx.shared.UpdateOutbound("LoopString", "Main Loop")
            if memory.ReadResetting():
                self.Logger.ResetReached()
                self.ModronResetCheck()
                continue  # PreviousZoneStartTime was updated; skip stuck check
            elif self.currentZone is not None \
                    and self.currentZone <= self.RouteMaster.targetZone:
                zone_now = memory.ReadCurrentZone() or 0
                highest_now = memory.ReadHighestZone() or 0
                if zone_now % 5 == 0 and highest_now % 5 != 0 \
                        and not memory.ReadTransitioning():
                    self.RouteMaster.ToggleAutoProgress(1, True)  # skip boss bag
                if self.RouteMaster.TestForSteelBonesStackFarming():
                    continue  # failure case - straight back to loop start
                self.RouteMaster.SetFormation(True)  # fastCheck while cruising
                self.RouteMaster.TestForBlankOffline(self.currentZone)
                if self.currentZone > 1:
                    self.LevelManager.LevelFormation("Q", "min", 0)
                if self.previousZone is not None \
                        and self.currentZone > self.previousZone:
                    # Things to be done every new zone
                    self.Logger.UpdateZone(self.currentZone)
                    self.previousZone = self.currentZone
                    self.RouteMaster.InitZone()
                    if (memory.ReadCurrentZone() or 1) % 5 == 0 \
                            and (memory.ReadHighestZone() or 1) % 5 == 0:
                        ctx.shared.UpdateOutbound_Increment("TotalBossesHit")
                        ctx.shared.UpdateOutbound_Increment("BossesHitThisRun")
                        if (ctx.setting("IBM_Level_Recovery_Softcap")
                                and not self.failedConversionMode
                                and self.RouteMaster.NeedToStack()
                                and (ctx.heroes[58].ReadHasteStacks() or 0) < 50):
                            self.failedConversionMode = True
                            self.LevelManager.SetupFailedConversion()
                    if not self.offRamp and self.currentZone >= \
                            self.RouteMaster.targetZone \
                            - self.RouteMaster.zonesPerJumpQ * 3:
                        self.offRamp = True  # backup missed-reset check
                else:
                    self.RouteMaster.StartAutoProgressSoft()
            else:
                self.Logger.ResetReached()
                ctx.shared.UpdateOutbound("LoopString", "Pending modron reset")
            self.CheckifStuck()
            sleep_offset(last_loop_end, 30)
            last_loop_end = time.perf_counter()

    def WaitForZoneLoad(self, current_zone):
        """Force restarts can reach the main loop before z1 has loaded."""
        if current_zone is not None:
            return current_zone
        end_time = tick_ms() + 2000
        while current_zone is None and tick_ms() < end_time:
            precise_sleep(15)
            current_zone = self._ctx.memory.ReadCurrentZone()
        return current_zone

    def IBM_FirstZone(self, current_zone):
        ctx = self._ctx
        memory = ctx.memory
        heroes = ctx.heroes
        if current_zone != 1:
            self.RouteMaster.InitZone()  # incl. click damage so we can move
            return
        if ctx.setting("IBM_Level_Diana_Cheese") and self.DianaCheeseHelper \
                and self.DianaCheeseHelper.InWindow():
            # Diana gives excess chests after the daily reset until a restart
            self.LevelManager.OverrideLevelByIDRaiseToMin(148, "min", 200)
        if heroes[139].inM:
            # Thellora in M: combine or non-combine, then Casino
            self.RouteMaster.CheckThelloraBossRecovery()
            self.EllywickCasino.lockedFrontColumnChamps = \
                self.LevelManager.SetupFirstZoneFrontRow()
            ctx.shared.UpdateOutbound("LoopString", "Start Zone Levelling")
            # Level until priority champions hit target only
            self.LevelManager.LevelFormation("M", "z1", force_priority=True,
                                             wait_for_gold=True)
            self.DoRushWait(True)
            self.RouteMaster.ToggleAutoProgress(0, False, True)
            ctx.shared.UpdateOutbound("LoopString", "Standard Levelling: M")
            self.LevelManager.LevelFormation("M", "min")
            self.RouteMaster.UpdateThellora()
            self.LevelManager.LevelClickDamage()
            ctx.shared.UpdateOutbound("LoopString", "Ellywick's Casino")
            unlock_required = self.EllywickCasino.Casino()
            if self.RouteMaster.IsFeatSwap():
                # Swap here - we can't be blocked in the transition
                self.RouteMaster.StartAutoProgressSoft()
                self.RouteMaster.SetFormation(use_high_zone=True)
            else:
                # Check Briv's placement so we do/don't jump out of the waitroom
                briv_should_be_benched = self.RouteMaster.ShouldWalk(
                    memory.ReadCurrentZone() or 0)
                swap_attempts = 0
                while True:
                    self.RouteMaster.SetFormation()
                    swap_attempts += 1
                    if bool(briv_should_be_benched) == \
                            bool(heroes[58].ReadBenched()) or swap_attempts > 10:
                        break
                self.RouteMaster.StartAutoProgressSoft()
            if unlock_required:
                # After the movement key presses - nothing gained doing it before
                self.EllywickCasino.UnlockHeroes()
            # min so BBEG->Dyna, Tatyana->Hew swaps happen; 500ms allows Hew
            # modifier key levelling
            self.LevelManager.LevelFormation("Q", "min", 500)
        else:
            # No Thellora: Casino in z1
            self.EllywickCasino.lockedFrontColumnChamps = \
                self.LevelManager.SetupFirstZoneFrontRow()
            self.LevelManager.LevelFormation("M", "z1", force_priority=True,
                                             wait_for_gold=True)
            ctx.shared.UpdateOutbound("LoopString", "Ellywick's Casino")
            self.LevelManager.LevelClickDamage()
            if self.EllywickCasino.Casino():
                self.EllywickCasino.UnlockHeroes()
            # Wait for zone completion so we can level Briv (z1c)
            quest = memory.ReadQuestRemaining()
            while quest is not None and quest > 0:
                self.LevelManager.LevelWorklist()
                precise_sleep(15)
                quest = memory.ReadQuestRemaining()
            self.LevelManager.LevelWorklist(force_priority=True)
            swap_attempts = 0
            while True:
                self.RouteMaster.SetFormation()
                swap_attempts += 1
                if not heroes[139].ReadBenched() or swap_attempts > 10:
                    break
            self.LevelManager.LevelFormation("Q", "min", 0)
            if heroes[139].inQ or heroes[139].inE:
                self.DoRushWait()
                self.RouteMaster.UpdateThellora()

    def DoRushWait(self, stop_progress=False):
        """Wait for Thellora (139) to activate her Rush ability."""
        ctx = self._ctx
        memory = ctx.memory
        level_champions = True  # alternate levelling types each loop
        ctx.shared.UpdateOutbound("LoopString", "Rush Wait")
        start_time = tick_ms()
        while not ((memory.ReadCurrentZone() or 0) > 1
                   or ctx.heroes[139].ReadRushTriggered()) \
                and tick_ms() - start_time < 8000:
            if stop_progress:
                # Doing the Casino after the rush: stop ASAP so one kill
                # doesn't jump us an extra time on the wrong formation
                if (memory.ReadHighestZone() or 0) > 1:
                    self.RouteMaster.ToggleAutoProgress(0)
                    stop_progress = False
            if level_champions:
                self.LevelManager.LevelWorklist()
            else:
                self.LevelManager.LevelClickDamage(0)
            level_champions = not level_champions

    def CheckifStuck(self):
        """After 35s toggles autoprogress every 5s; after 45s falls back up to
        2 times; after 65s restarts the level."""
        ctx = self._ctx
        dt = tick_ms() - self.PreviousZoneStartTime
        if dt <= 35000:
            return False
        if 35000 < dt <= 45000 and dt - self.CheckifStuck_lastCheck > 5000:
            self.RouteMaster.ToggleAutoProgress(1, True)
            if dt < 40000:
                self.CheckifStuck_lastCheck = dt
        if dt > 45000 and self.CheckifStuck_fallBackTries < 3 \
                and dt - self.CheckifStuck_lastCheck > 15000:
            # Reset memory values in case they missed an update
            win = self.GameMaster._win
            self.GameMaster.Hwnd = win.find_window_by_exe(
                ctx.setting("IBM_Game_Exe", "IdleDragons.exe"))
            ctx.memory.OpenProcessReader(
                ctx.setting("IBM_Game_Exe", "IdleDragons.exe"),
                self.GameMaster.PID or None)
            ctx.server.Update()
            self.RouteMaster.FallBackFromZone()
            self.RouteMaster.SetFormation()
            self.RouteMaster.ToggleAutoProgress(1, True)
            self.CheckifStuck_lastCheck = dt
            self.CheckifStuck_fallBackTries += 1
        if dt > 65000:
            self.GameMaster.RestartAdventure(
                f"Game is stuck z[{ctx.memory.ReadCurrentZone()}]")
            self.GameMaster.SafetyCheck()
            self.PreviousZoneStartTime = tick_ms()
            self.CheckifStuck_lastCheck = 0
            self.CheckifStuck_fallBackTries = 0
            return True
        return False

    def RollBackAction(self, return_zone):
        if self.offRamp:
            self.offRamp = False
        self.Logger.AddMessage(f"Rollback detected - expected "
                               f"z[{self.currentZone}] return z[{return_zone}]")
        self.previousZone = 1  # else currentZone > previousZone stays false
        self.currentZone = return_zone
        self._ctx.shared.UpdateOutbound_Increment("TotalRollBacks")

    def ModronResetCheck(self):
        """Waits for the modron to reset; closes IC if it fails."""
        if self.WaitForModronReset(45000):
            # Some users' reset count doesn't increase post reset
            self.TriggerStart = True
        else:
            self.GameMaster.RestartAdventure(
                f"Modron reset timed out z[{self._ctx.memory.ReadCurrentZone()}]",
                True)
            self.GameMaster.SafetyCheck()
            self.CheckifStuck_lastCheck = 0
            self.CheckifStuck_fallBackTries = 0
        self.PreviousZoneStartTime = tick_ms()

    def WaitForModronReset(self, timeout=60000):
        ctx = self._ctx
        memory = ctx.memory
        start_time = tick_ms()
        ctx.shared.UpdateOutbound("LoopString", "Modron Resetting...")
        ctx.server.UpdateStackData()
        if ctx.server.ShouldCallPreventStackFail():
            # Manual save only if it hasn't already happened; async helper
            ctx.server.CallPreventStackFail("WaitForModronReset()", True)
        while memory.ReadResetting() and tick_ms() - start_time < timeout:
            precise_sleep(20)
        ctx.shared.UpdateOutbound("LoopString", "Loading z1...")
        precise_sleep(100)  # the loading part of the reset takes >1s in reality
        while not memory.ReadUserIsInited() \
                and (memory.ReadCurrentZone() or 0) < 1 \
                and tick_ms() - start_time < timeout:
            precise_sleep(20)
        return tick_ms() - start_time < timeout

    def RefreshImportCheck(self):
        """GUI-less version: report game/imports version match to the log."""
        ctx = self._ctx
        game_major = ctx.memory.ReadBaseGameVersion()
        game_minor = ctx.memory.IBM_ReadGameVersionMinor()
        imports_major = ctx.memory.Versions["Import_Version_Major"]
        imports_minor = ctx.memory.Versions["Import_Version_Minor"]
        game = f"{game_major}{game_minor or ''}" if game_major else "Unable to detect"
        imports = (f"{imports_major}{imports_minor or ''} "
                   f"{ctx.memory.Versions['Import_Revision']}"
                   if imports_major else "Unable to detect")
        state = "OK" if (game_major and str(game_major) == str(imports_major)
                         and str(game_minor or "") == str(imports_minor or "")) \
            else "MISMATCH"
        ctx.shared.UpdateOutbound("VersionString",
                                  f"game {game} / imports {imports} [{state}]")

    # --- pre-flight check ---------------------------------------------------------

    def PreFlightCheck(self):
        ctx = self._ctx
        memory = ctx.memory
        heroes = ctx.heroes
        # Active adventure
        if self.GameMaster.CurrentAdventure is None \
                or self.GameMaster.CurrentAdventure <= 0:
            raise PreFlightError(
                "Adventure",
                "Unable to read adventure data. Please load into a valid "
                f"adventure. Current adventure shows as: "
                f"{self.GameMaster.CurrentAdventure}"
                + self.PreFlightCheck_GenericMessage())
        # Briv in the expected formations
        briv = heroes[58]
        feat_swap = self.RouteMaster.IsFeatSwap()
        if not briv.inM or not briv.inQ or not briv.inW \
                or feat_swap != bool(briv.inE):
            raise PreFlightError(
                "Briv Formations",
                "Briv's presence in the saved formations is not as expected: "
                f"M={briv.inM} Q={briv.inQ} W={briv.inW} E={briv.inE} "
                f"(E expected: {'Yes (FS)' if feat_swap else 'No'})"
                + self.PreFlightCheck_GenericMessage())
        # Metalborn
        if not briv.HasCoreSpec(3455):
            raise PreFlightError(
                "Briv Formations",
                "Briv must have the Metalborn specialisation saved in the "
                "Modron formation." + self.PreFlightCheck_GenericMessage())
        # Familiars: M, Q, E should have 3; W always 0
        counts = {
            "M": memory.IBM_GetFormationFieldFamiliarCountBySlot(
                memory.GetActiveModronFormationSaveSlot()),
            "Q": memory.IBM_GetFormationFieldFamiliarCountBySlot(
                memory.GetSavedFormationSlotByFavorite(1)),
            "W": memory.IBM_GetFormationFieldFamiliarCountBySlot(
                memory.GetSavedFormationSlotByFavorite(2)),
            "E": memory.IBM_GetFormationFieldFamiliarCountBySlot(
                memory.GetSavedFormationSlotByFavorite(3)),
        }
        if any(count is None for count in counts.values()):
            raise PreFlightError(
                "Familiars",
                f"Familiars in saved formations could not be checked {counts}"
                + self.PreFlightCheck_GenericMessage())
        if counts["M"] == 0 or counts["Q"] == 0 or counts["W"] > 0 \
                or counts["E"] == 0:
            raise PreFlightError(
                "Familiars",
                "Familiars in saved formations are not as expected "
                f"(need M/Q/E=3, W=0): {counts}")
        if counts["M"] != 3 or counts["Q"] != 3 or counts["W"] > 0 \
                or counts["E"] != 3:
            # The AHK original asks Yes/No here; headless: warn and continue
            ctx.log("PreFlight WARNING: familiars meet the minimum but not "
                    f"the expected config (M/Q/E=3, W=0): {counts} - continuing")
        # Modron automation
        modron_f = memory.ReadModronAutoFormation() == 1
        modron_r = memory.ReadModronAutoReset() == 1
        modron_b = memory.ReadModronAutoBuffs() == 1
        modron_b_ok = bool(ctx.setting("IBM_Allow_Modron_Buff_Off")) or modron_b
        if not modron_f or not modron_r or not modron_b_ok:
            raise PreFlightError(
                "Modron",
                "All 3 Modron Core automation functions must be enabled: "
                f"Set Formation: {modron_f}, Set Area Goal: {modron_r}, "
                f"Set Buffs: {modron_b}" + self.PreFlightCheck_GenericMessage())
        # Hero index map
        if not heroes.Init():
            raise PreFlightError(
                "Hero Manager", "Unable to generate HeroID to HeroIndex map"
                + self.PreFlightCheck_GenericMessage())
        # Availability: configured champions active in other parties
        game_instance_id = memory.IBM_GetActiveGameInstanceID()
        locked = []
        for hero_id in self.LevelManager.savedFormationChamps["A"]:
            hero_instance = heroes[hero_id].ReadActiveGameInstanceID()
            if hero_instance and hero_instance > 0 \
                    and hero_instance != game_instance_id:
                locked.append(f"{heroes[hero_id].ReadName()} ({hero_id}) - "
                              f"Party {hero_instance}")
        if locked:
            raise PreFlightError(
                "Hero Manager",
                "The following champions are configured for Briv Master but "
                "are active in other adventure parties: " + "; ".join(locked)
                + ". Either recall them or end their current adventures")
        # Feat Guard
        level_settings = ctx.setting("IBM_LevelManager_Levels", {}) or {}
        for hero_id in self.LevelManager.savedFormationChamps["A"]:
            champ = level_settings.get(str(hero_id)) or level_settings.get(hero_id)
            if not champ or "Feat_List" not in champ \
                    or "Feat_Exclusive" not in champ:
                continue
            hero_feats = memory.GameManager.game.gameInstances[0] \
                .Controller.userData.FeatHandler.heroFeatSlots \
                .dict_value(hero_id)
            feat_list_node = hero_feats.child("List") if hero_feats else None
            size = feat_list_node.size() if feat_list_node else None
            if size is None or size < 0 or size > 6:
                raise PreFlightError(
                    "Feat Guard",
                    f"Unable to read equipped feats for heroID: {hero_id}"
                    + self.PreFlightCheck_GenericMessage())
            extra_feats = {}
            # AHK stores 'no feats' as an empty string, not an empty object
            feat_list_cfg = champ["Feat_List"]
            check_list = dict(feat_list_cfg) if isinstance(feat_list_cfg, dict) else {}
            for index in range(size):
                feat_id = feat_list_node[index].ID.read()
                feat_name = feat_list_node[index].Name.read()
                if feat_id:  # heroFeatSlots always has the 4 slots
                    key = str(feat_id) if str(feat_id) in check_list else feat_id
                    if key in check_list:
                        del check_list[key]
                    elif champ["Feat_Exclusive"]:
                        extra_feats[feat_id] = feat_name
            if check_list or extra_feats:
                message = (f"Feat Guard found inconsistencies for "
                           f"{heroes[hero_id].ReadName()} ({hero_id}).")
                if check_list:
                    message += f" Missing required feats: {check_list}."
                if extra_feats:
                    message += (" Exclusive mode is enabled and extra feats "
                                f"were found: {extra_feats}.")
                raise PreFlightError("Feat Guard", message)

    def PreFlightCheck_GenericMessage(self):
        import struct
        return ("\nOther potential solutions:\n"
                "1. Be sure Imports are up to date. Current imports are for: "
                f"v{self._ctx.memory.GetImportsVersion()}\n"
                "2. If IC is running with admin privileges, this script also "
                "requires admin privileges.\n"
                f"3. Python must be 64-bit. (Currently "
                f"{struct.calcsize('P') * 8}-bit)")
