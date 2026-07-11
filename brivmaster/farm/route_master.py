"""Port of IC_BrivMaster_RouteMaster.ahk - routing, stack management,
formation control, blank/relay restarts, online stackers and Briv Boost."""

from __future__ import annotations

import math
import time

from .ctx import precise_sleep, tick_ms

# Steelbones cost per jump with Metalborn (index = jumps, 1-based in AHK)
JUMP_COSTS_METALBORN = [int(v) for v in (
    "50,52,54,56,58,60,62,64,66,68,70,72,74,76,78,81,84,87,90,93,96,99,102,"
    "105,108,112,116,120,124,128,132,136,140,145,150,155,160,165,170,176,182,"
    "188,194,200,207,214,221,228,236,244,252,260,269,278,287,296,306,316,326,"
    "337,348,359,371,383,396,409,423,437,451,466,481,497,513,530,548,566,585,"
    "604,624,645,666,688,711,734,758,783,809,836,864,893,923,953,984,1017,"
    "1051,1086,1122,1159,1197,1237,1278,1320,1364,1409,1456,1504,1554,1605,"
    "1658,1713,1770,1828,1888,1950,2014,2081,2150,2221,2294,2370,2448,2529,"
    "2613,2699,2788,2880,2975,3073,3175,3280,3388,3500,3616,3736,3859,3987,"
    "4119,4255,4396,4541,4691,4846,5006,5171,5342,5519,5701,5889,6084,6285,"
    "6493,6708,6930,7159,7396,7640,7893,8154,8424,8702,8990,9287,9594,9911,"
    "10239,10577,10927,11288,11661,12046,12444,12855,13280,13719,14173,14642,"
    "15126,15626,16143,16677,17228,17798,18386,18994,19622,20271,20941,21633,"
    "22348,23087,23850,24638,25452,26293,27162,28060,28988,29946,30936,31959,"
    "33015,34106,35233,36398,37601,38844,40128,41455,42825,44241,45703,47214,"
    "48775,50387,52053,53774,55552,57388,59285,61245,63270,65362,67523,69755,"
    "72061,74443,76904,79446,82072,84785,87588,90483,93474,96564,99756,103054,"
    "106461,109980,113616,117372,121252,125260,129401,133679,138098,142663,"
    "147379,152251,157284,162483,167854,173403,179135,185057,191175,197495,"
    "204024,210769,217737,224935,232371,240053,247989,256187,264656,273405,"
    "282443,291780,301426,311390,321684,332318,343304,354653,366377,378489,"
    "391001,403927,417280,431074,445324,460045,475253,490964,507194,523961,"
    "541282,559176,577661,596757,616484,636864,657917,679666,702134,725345,"
    "749323,774094,799684,826120,853430,881643,910788,940897,972001,1004133,"
    "1037327,1071619,1107044,1143640,1181446,1220502,1260849,1302530,1345589,"
    "1390071,1436024,1483496,1532537,1583199,1635536,1689603,1745458,1803159,"
    "1862768,1924347,1987962,2053680,2121570,2191705,2264158,2339006,2416328,"
    "2496207,2578726,2663973,2752038,2843014,2936998,3034089,3134389,3238005,"
    "3345046,3455626,3569862,3687874,3809787,3935730,4065837,4200245,4339096,"
    "4482537,4630720,4783802,4941944,5105314,5274085,5448435,5628549,5814617,"
    "6006836,6205409,6410546,6622465,6841389,7067551,7301189,7542551,7791892,"
    "8049475,8315573,8590468,8874450,9167820,9470888,9783975,10107412,"
    "10441541,10786716,11143302,11511676,11892227,12285358,12691486,13111039,"
    "13544462,13992213,14454765,14932608,15426248,15936207,16463024,17007256,"
    "17569479,18150288,18750298,19370143,20010478,20671981,21355352").split(",")]


def jump_cost(jumps):
    """1-based lookup as in AHK (jumpCosts[jumps])."""
    if jumps < 1:
        return 0
    if jumps > len(JUMP_COSTS_METALBORN):
        return JUMP_COSTS_METALBORN[-1]
    return JUMP_COSTS_METALBORN[jumps - 1]


class Zone:
    """IC_BrivMaster_RouteMaster_Zone_Class."""

    __slots__ = ("z", "nextZone", "jumpZone", "stackZone", "incomingZones",
                 "jumpsToFinish", "stacksToFinish")

    def __init__(self):
        self.z = 0
        self.nextZone = None
        self.jumpZone = False   # Q-jump vs walk (E-jump for feat swap)
        self.stackZone = False  # online stacking allowed
        self.incomingZones = {}
        self.jumpsToFinish = -1
        self.stacksToFinish = -1


class RouteMaster:
    zoneCap = 2501

    def __init__(self, ctx, combine, log_base):
        self._ctx = ctx
        heroes = ctx.heroes
        self.zones = {}
        self.leftoverCalculated = False
        self.leftoverHaste = 48
        self.cycleCount = 0
        self.cycleMax = 1
        self.cycleForceOffline = False
        self.cycleDisableOffline = False
        self.offlineSaveTime = -1
        self.StackFailRetryAttempt = 0
        self.combining = combine
        # We want the actual zones covered, so +1 (9J goes z1 -> z11: 10 zones)
        self.zonesPerJumpQ = ctx.setting("IBM_Route_BrivJump_Q", 0) + 1
        if heroes[58].inE:  # feat swap; ignored if Briv not saved in E
            self.zonesPerJumpE = ctx.setting("IBM_Route_BrivJump_E", 0) + 1
        else:
            self.zonesPerJumpE = 1  # walking progresses one zone
        self.zonesPerJumpM = ctx.setting("IBM_Route_BrivJump_M", 0) + 1
        self.targetZone = ctx.memory.GetModronResetArea()
        self.thelloraTarget = 0
        self.UpdateThellora(True)
        if self.BrivHasThunderStep():  # feat 2131: +20% on conversion
            self.stackConversionRate = 1.2
        else:
            self.stackConversionRate = 1
        self.KEY_autoProgress = ctx.input.get_key("g")
        self.KEY_Q = ctx.input.get_key("q")
        self.KEY_W = ctx.input.get_key("w")
        self.KEY_E = ctx.input.get_key("e")
        self.KEY_LEFT = ctx.input.get_key("Left")
        self.HybridBlankOffline = ctx.setting("IBM_OffLine_Blank")
        self.RelayBlankOffline = ctx.setting("IBM_OffLine_Blank_Relay")
        self.RelayData = None
        if self.RelayBlankOffline:
            self.RelaySetup(log_base)
        condition = ctx.setting("IBM_Online_Farideh_Condition", 1)
        if condition == 3:
            self.OnlineStacker = OnlineStackerTatyanaReturn(ctx, self)
        elif condition == 2:
            self.OnlineStacker = OnlineStackerAttacking(ctx, self)
        else:
            self.OnlineStacker = OnlineStackerActiveEnemies(ctx, self)
        self.ThelloraBossAvoidance = ctx.setting("IBM_Route_Combine_Boss_Avoidance")
        ctx.shared.UpdateOutbound("IBM_RunControl_DisableOffline", False)
        ctx.shared.UpdateOutbound("IBM_RunControl_ForceOffline", False)
        self.LastSafeStackZone = self.GetLastSafeStackZone()
        ctx.shared.UpdateOutbound("IBM_ProcessSwap", False)
        self._trust_recent = False  # SetFormation static
        self.LoadRoute()
        self.SetStrategyStrings()

    def Reset(self):
        ctx = self._ctx
        self.leftoverCalculated = False
        self.leftoverHaste = 48
        self.cycleCount += 1
        ctx.farm.Logger.SetRunCycle(self.cycleCount)
        self.cycleMax = ctx.setting("IBM_OffLine_Freq", 1)
        # Only process Run Control input at the start of a run
        self.cycleDisableOffline = ctx.shared.IBM_RunControl_DisableOffline
        if ctx.shared.IBM_RunControl_ForceOffline:
            self.cycleForceOffline = True  # queue
            ctx.shared.UpdateOutbound("IBM_RunControl_ForceOffline", False)
        if self.RelayBlankOffline:
            self.RelayData.Reset()
        ctx.shared.UpdateOutbound(
            "IBM_RunControl_CycleString",
            f"Cycle {self.cycleCount}/{self.cycleMax}"
            f"{' FO' if self.cycleForceOffline else ''}")
        self.SetInitialStackString()
        ctx.shared.UpdateOutbound("IBM_ProcessSwap", False)

    # --- relay ---------------------------------------------------------------

    def RelaySetup(self, log_base):
        from .relay_data import RelaySharedData
        heroes = self._ctx.heroes
        if heroes.InA(139):
            after_thellora = self.GetThelloraTarget(
                heroes[139].rushCap or 0, self.combining) + 1
        else:
            after_thellora = 2
        self.RelayData = RelaySharedData(self._ctx, after_thellora,
                                         log_base + "_Relay.csv")

    def CheckRelayRelease(self):
        if self.RelayBlankOffline:
            self.RelayData.PreRelease()

    # --- strategy strings ---------------------------------------------------------

    def SetStrategyStrings(self):
        ctx = self._ctx
        target_stacks = self.GetTargetStacks(True)
        jump_string = (f"{self.zonesPerJumpQ}"
                       f"{'&' + str(self.zonesPerJumpE) if self.zonesPerJumpE > 1 else ''}z/J")
        if self.stackConversionRate != 1:
            stacking = f"{math.ceil((target_stacks - 48) / self.stackConversionRate)} w/TS"
        else:
            stacking = f"{target_stacks - 48}"
        stack_string = f"Using {target_stacks} stacks (stacking {stacking})"
        if ctx.heroes[139].inM:
            prefix = "Combining" if self.combining else "Non-combined"
            status = (f"{prefix} to z{self.thelloraTarget} following by Casino, "
                      f"jumping {jump_string} to reset at z{self.targetZone}. "
                      f"{stack_string}")
            header = (f"{prefix} to z{self.thelloraTarget} following by Casino,"
                      f"Jumping {jump_string},Reset at z{self.targetZone},"
                      f"{stack_string}")
        else:
            status = (f"Casino at z1 followed by non-combined to "
                      f"z{self.thelloraTarget}, jumping {jump_string} to reset "
                      f"at z{self.targetZone}. {stack_string}")
            header = (f"Casino at z1 followed by non-combine to "
                      f"z{self.thelloraTarget},Jumping {jump_string},"
                      f"Reset at z{self.targetZone},{stack_string}")
        ctx.shared.UpdateOutbound("IBM_RunControl_StatusString", status)
        ctx.farm.Logger.OutputHeader(header)

    def SetInitialStackString(self):
        ctx = self._ctx
        if self.ShouldOfflineStack():
            stack_string = ("Stacking: Expecting offline at "
                            f"z{ctx.setting('IBM_Offline_Stack_Zone')}")
        else:
            stack_string = ("Stacking: Expecting online at or after "
                            f"z{ctx.setting('IBM_Online_Melf_Min')}")
            if self.ShouldBlankRestart():
                stack_string += (" with relay blank restart"
                                 if self.RelayBlankOffline
                                 else " with blank restart")
        ctx.shared.UpdateOutbound("IBM_RunControl_StackString", stack_string)

    # --- stack calculations ------------------------------------------------------

    def NeedToStack(self):
        stacks = self._ctx.heroes[58].ReadSBStacks()
        return stacks is not None and stacks < self.GetTargetStacks()

    def GetTargetStacks(self, ignore_haste=False, force_recalc=False):
        if ignore_haste:
            return self.GetTargetStacksForFullRun(True)
        self.UpdateLeftoverHaste(force_recalc)
        stacks_to_generate = self.GetTargetStacksForFullRun() - self.leftoverHaste
        return math.ceil(stacks_to_generate / self.stackConversionRate)

    def UpdateThellora(self, force=False):
        thellora = self._ctx.heroes[139]
        if thellora.UpdateRushTarget() or force:
            self.thelloraTarget = self.GetThelloraTarget(
                thellora.rushCap or 0, self.combining)

    def IsFeatSwap(self):
        return self.zonesPerJumpE > 1

    def GetThelloraTarget(self, base_jump, combine):
        if combine:  # combined jump uses the M jump value
            return base_jump + self.zonesPerJumpM
        return base_jump + 1

    def CheckThelloraBossRecovery(self):
        """Avoid rushing into bosses after a failed run by breaking/making the
        combine. Sets Briv's z1c in the default non-combining case."""
        ctx = self._ctx
        thellora = ctx.heroes[139]
        level_manager = ctx.farm.LevelManager
        if self.combining:
            if self.ThelloraBossAvoidance:
                charges = math.floor(thellora.GetCappedRushCharges())
                target_combining = self.GetThelloraTarget(charges, True)
                if (target_combining < self.thelloraTarget
                        and target_combining % 5 == 0
                        and self.GetThelloraTarget(charges, False) % 5 != 0):
                    level_manager.OverrideLevelByID(58, "z1c", True)
                    ctx.log("Thellora: Broke combine to avoid hitting boss")
        else:
            if self.ThelloraBossAvoidance:
                charges = math.floor(thellora.GetCappedRushCharges())
                target_non_combining = self.GetThelloraTarget(charges, False)
                if (target_non_combining < self.thelloraTarget
                        and target_non_combining % 5 == 0
                        and self.GetThelloraTarget(charges, True) % 5 != 0):
                    ctx.log("Thellora: Attempting to combine to avoid hitting boss")
                    return
            # Standard outcome: no Briv levelling before z1 completes
            level_manager.OverrideLevelByID(58, "z1c", True)

    def GetTargetStacksForFullRun(self, assume_standard_rush=False):
        ctx = self._ctx
        thellora = ctx.heroes[139]
        rush_next = 0 if assume_standard_rush else thellora.rushNext
        if rush_next:
            thellora_target = self.GetThelloraTarget(rush_next, self.combining)
        else:
            thellora_target = self.thelloraTarget
        if self.combining:
            # 1 jump for the combine, 1 for the M-jump after the Casino
            jumps = self._zone(thellora_target + self.zonesPerJumpM).jumpsToFinish + 2
            if (rush_next and self.ThelloraBossAvoidance and self.IsFeatSwap()
                    and self.zonesPerJumpM > self.zonesPerJumpE):
                jumps += 1
                ctx.farm.Logger.AddThelloraCompensationMessage(
                    "GetTargetStacksForFullRun: Added extra jump for combining "
                    "Thellora recovery for a total of: ", jumps)
        elif thellora.inM:
            jumps = self._zone(thellora_target + self.zonesPerJumpM).jumpsToFinish + 1
            if rush_next and self.ThelloraBossAvoidance and self.IsFeatSwap():
                jumps += 1
                ctx.farm.Logger.AddThelloraCompensationMessage(
                    "GetTargetStacksForFullRun: Added extra jump for "
                    "non-combining Thellora recovery for a total of: ", jumps)
        else:
            jumps = self._zone(thellora_target).jumpsToFinish
        return jump_cost(jumps)

    def _zone(self, zone_number):
        zone = self.zones.get(zone_number)
        if zone is None:  # out-of-route zone; treat as end (0 jumps)
            zone = Zone()
            zone.z = zone_number
            zone.jumpsToFinish = 0
            zone.stacksToFinish = 0
        return zone

    def UpdateLeftoverHaste(self, force_recalc=False):
        ctx = self._ctx
        if self.leftoverCalculated and not force_recalc:
            return
        thellora = ctx.heroes[139]
        thellora.rushNext = 0
        calc = self.UpdateLeftoverHaste_Calculate()
        self.leftoverHaste = calc["haste"]
        if thellora.inA:
            # No z1 credit when she is not in M
            target_charges = (thellora.rushCap or 0) + (0 if thellora.inM else 0.2)
            current_charges = thellora.ReadRushAreaCharges()
            remaining_charges = max(0, target_charges - current_charges)
            if calc["partialRun"]:
                zones_remaining = max(
                    0, self.GetStackDepletionZone(calc["zone"],
                                                  calc["jumpsToDepletion"])
                    - calc["zone"])
            else:
                zones_remaining = max(0, self.targetZone - calc["zone"])
            if zones_remaining < remaining_charges * 5:
                thellora.rushNext = math.floor(current_charges
                                               + zones_remaining / 5)
            highest = ctx.memory.ReadHighestZone()
            if highest is not None and highest >= self.thelloraTarget:
                self.leftoverCalculated = True  # post-Thellora; don't redo
        else:
            self.leftoverCalculated = True

    def GetStackDepletionZone(self, zone_number, jumps):
        while jumps > 0:
            current = self._zone(zone_number)
            if current.jumpZone:  # on Q
                next_zone = current.z + self.zonesPerJumpQ
                jumps -= 1
            else:
                next_zone = current.z + self.zonesPerJumpE
                if self.zonesPerJumpE > 1:  # Briv in E costs a jump too
                    jumps -= 1
            zone_number = next_zone
        return zone_number

    def UpdateLeftoverHaste_Calculate(self):
        """Expected leftover haste at run end, plus depletion info."""
        ctx = self._ctx
        calc = {"haste": ctx.heroes[58].ReadHasteStacks() or 0,
                "jumpsToDepletion": 0, "partialRun": False}
        if not ctx.memory.ReadTransitioning():
            calc["zone"] = ctx.memory.ReadCurrentZone() or 0
        else:  # stacks were spent leaving the previous zone
            calc["zone"] = ctx.memory.ReadHighestZone() or 0
        jumps = self._zone(calc["zone"]).jumpsToFinish
        if jumps < 1:
            return calc
        while jumps > 0:
            if calc["haste"] < 50:  # won't jump with <50 stacks
                calc["partialRun"] = True
                calc["jumpsToDepletion"] = (self._zone(calc["zone"]).jumpsToFinish
                                            - jumps)
                return calc
            calc["haste"] = round(calc["haste"] * 0.968)
            jumps -= 1
        return calc

    def EnoughHasteForCurrentRun(self):
        ctx = self._ctx
        if not ctx.memory.ReadTransitioning():
            zone = ctx.memory.ReadCurrentZone() or 0
        else:
            zone = ctx.memory.ReadHighestZone() or 0
        haste = ctx.heroes[58].ReadHasteStacks() or 0
        return haste >= self._zone(zone).stacksToFinish

    # --- offline / blank / relay decisions --------------------------------------------

    def ShouldOfflineStack(self):
        if self.HybridBlankOffline:  # not used with blank offlines
            return False
        if self.cycleForceOffline:  # takes priority over disable
            return True
        if self.cycleDisableOffline:
            return False
        if self.cycleMax == 1:  # hybrid disabled
            return True
        if self.cycleCount >= self.cycleMax:  # hybrid offline
            return True
        return False

    def ExpectingGameRestart(self):
        return self.ShouldOfflineStack() or self.ShouldBlankRestart()

    def ShouldBlankRestart(self):
        return (self.HybridBlankOffline
                and (self.cycleCount >= self.cycleMax or self.cycleForceOffline)
                and (not self.cycleDisableOffline or self.cycleForceOffline))

    def TestForBlankOffline(self, current_zone):
        ctx = self._ctx
        # Once the relay manager starts, we are committed
        if ((self.ShouldBlankRestart() and self.EnoughHasteForCurrentRun())
                or (self.RelayBlankOffline and self.RelayData.IsActive())):
            if current_zone > ctx.setting("IBM_Offline_Stack_Zone", 0):
                self.BlankRestart()
            elif self.RelayBlankOffline and not self.RelayData.HasTriggered():
                if current_zone >= self.RelayData.relayZone:
                    self.RelayData.Start()

    def BlankRestart(self):
        """Restart without stacking (clears memory bloat)."""
        ctx = self._ctx
        memory = ctx.memory
        if ctx.setting("IBM_OffLine_Blank_Stop"):
            self.ToggleAutoProgress(0, False, True)
        start_stacks = ctx.heroes[58].ReadSBStacks() or 0
        offline_start = tick_ms()
        start_zone = memory.ReadCurrentZone() or 0
        ctx.log(f"BlankRestart Entry:z{start_zone}")
        # use_pid so we don't close the relay copy in relay mode
        ctx.farm.GameMaster.CloseIC("BlankRestart", self.RelayBlankOffline)
        if self.RelayBlankOffline:
            ctx.log("BlankRestart() returning game in Relay mode")
            self.RelayData.Release()
            self.ResetCycleCount()
        else:
            sleep_time = ctx.setting("IBM_OffLine_Sleep_Time", 0)
            if sleep_time:
                start = tick_ms()
                while tick_ms() - start < sleep_time:
                    ctx.shared.UpdateOutbound(
                        "LoopString",
                        f"BlankRestart Sleep: {sleep_time - (tick_ms() - start)}")
                    precise_sleep(15)
        ctx.farm.GameMaster.SafetyCheck()
        total_time = tick_ms() - offline_start
        generated = (ctx.heroes[58].ReadSBStacks() or 0) - start_stacks
        return_zone = memory.ReadCurrentZone() or 0
        if return_zone < start_zone:
            ctx.farm.RollBackAction(return_zone)
            ctx.log(f"BlankRestart() Exit Rollback Detected,Start@z{start_zone},"
                    f"End@z{return_zone},{generated},Time:{total_time},"
                    f"OfflineTime:{memory.ReadOfflineTime()},"
                    f"Server:{memory.IBM_GetWebRootFriendly()}")
        else:
            ctx.log(f"BlankRestart() Exit, End@z{return_zone},{generated},"
                    f"Time:{total_time},OfflineTime:{memory.ReadOfflineTime()},"
                    f"Server:{memory.IBM_GetWebRootFriendly()}")
        if ctx.setting("IBM_OffLine_Blank_Stop"):
            self.ToggleAutoProgress(1, False, True)
        ctx.shared.UpdateOutbound(
            "IBM_RunControl_StackString",
            f"Restarted at z{return_zone} in {round(total_time / 1000, 2)}s")
        ctx.farm.PreviousZoneStartTime = tick_ms()

    def TestForSteelBonesStackFarming(self):
        """True on the failure case: out of stacks with enough for a new run
        (forces a restart)."""
        ctx = self._ctx
        memory = ctx.memory
        current_zone = memory.ReadCurrentZone()
        if current_zone is None or current_zone < 0 \
                or current_zone >= self.targetZone:
            return False  # don't test while modron resetting
        stacks = ctx.heroes[58].ReadSBStacks() or 0
        target_stacks = self.GetTargetStacks()
        if stacks < target_stacks:
            should_offline = self.ShouldOfflineStack()
            if should_offline and \
                    current_zone >= ctx.setting("IBM_Offline_Stack_Zone", 0):
                self.StackRestart()
                self.StartAutoProgressSoft()
                return False
            if not should_offline and not self.PostponeStacking(current_zone):
                self.OnlineStacker.Stack()
                return False
        # Out of jumps but enough stacks for a new adventure => restart.
        haste = ctx.heroes[58].ReadHasteStacks() or 0
        highest = memory.ReadHighestZone() or 0
        if (haste < 50 and stacks >= target_stacks
                and highest > self.thelloraTarget
                and highest <= self.targetZone
                and not memory.ReadTransitioning()):
            if self.RelayBlankOffline and self.RelayData.IsActive():
                ctx.log("TestForSteelBonesStackFarming() force restart "
                        "suppressed due to Relay")
            else:
                ctx.log(f"Out of stacks:z{current_zone}")
                ctx.farm.GameMaster.RestartAdventure(
                    "Out of Haste and have Steelbones for next")
                return True
        return False

    def ResetCycleCount(self):
        self.cycleForceOffline = False
        self.cycleCount = 0

    def PostponeStacking(self, current_zone):
        ctx = self._ctx
        if current_zone < ctx.setting("IBM_Offline_Stack_Min", 0):
            return True  # never below the recovery minimum
        haste = ctx.heroes[58].ReadHasteStacks()
        if haste is not None and haste < 50:
            return False  # stack immediately - Briv can't jump
        if current_zone > self.LastSafeStackZone:
            return False  # stack now to avoid resetting before stacking
        if current_zone < ctx.setting("IBM_Online_Melf_Min", 0):
            return True
        if not self._zone(current_zone).stackZone:
            return True
        return False

    def GetLastSafeStackZone(self):
        last_zone = (self.targetZone or 0) - 1
        if last_zone % 5 == 0:  # boss just before reset
            last_zone -= 1
        return last_zone - self.zonesPerJumpQ

    def ShouldAvoidRestack(self, stacks, target_stacks):
        memory = self._ctx.memory
        if stacks >= target_stacks:
            return True
        zone = memory.ReadCurrentZone()
        if zone == 1:  # likely modron has reset
            return True
        if zone is not None and zone < self._ctx.setting("IBM_Offline_Stack_Min", 0):
            return True
        return False

    def StackRestart(self):
        ctx = self._ctx
        memory = ctx.memory
        briv = ctx.heroes[58]
        start_stacks = stacks = briv.ReadSBStacks() or 0
        target_stacks = self.GetTargetStacks(force_recalc=True)
        if self.ShouldAvoidRestack(stacks, target_stacks):
            return
        retry_attempt = 0
        # Hybrid never retries - going offline clears bloat either way
        max_retries = 2 if self.cycleMax == 1 else 0
        offline_start = tick_ms()
        while stacks < target_stacks and retry_attempt <= max_retries:
            self.StackFailRetryAttempt += 1
            retry_attempt += 1
            self.StackFarmSetup()
            zone = memory.ReadCurrentZone()
            if self.targetZone and zone is not None and zone > self.targetZone:
                ctx.shared.UpdateOutbound(
                    "LoopString",
                    "Attempted to offline stack after modron reset - verify settings")
                break
            warn = (f" - Warning: Retry #{self.StackFailRetryAttempt - 1}. "
                    "Check Stack Settings." if self.StackFailRetryAttempt > 1 else "")
            self.offlineSaveTime = ctx.farm.GameMaster.CloseIC(
                f"StackRestart{warn}")
            sleep_time = ctx.setting("IBM_OffLine_Sleep_Time", 0)
            sleep_start = tick_ms()
            while tick_ms() - sleep_start < sleep_time:
                ctx.shared.UpdateOutbound(
                    "LoopString",
                    f"Stack Sleep: {sleep_time - (tick_ms() - sleep_start)}")
                precise_sleep(15)
            ctx.farm.GameMaster.SafetyCheck()
            stacks = briv.ReadSBStacks() or 0
            zone = memory.ReadCurrentZone()
            if zone is not None and zone < ctx.setting("IBM_Offline_Stack_Min", 0):
                ctx.shared.UpdateOutbound("LoopString",
                                          "Stack Sleep: Failed (zone < min)")
                break  # bad save? loaded below stack zone
            ctx.log(f"Offline:{zone},{stacks},"
                    f"Time:{tick_ms() - self.offlineSaveTime},"
                    f"Attempt:{retry_attempt},"
                    f"OfflineTime:{memory.ReadOfflineTime()},"
                    f"Server:{memory.IBM_GetWebRootFriendly()}")
            self.offlineSaveTime = -1  # flags as not active
        ctx.farm.PreviousZoneStartTime = tick_ms()
        generated = (briv.ReadSBStacks() or 0) - start_stacks
        total_time = tick_ms() - offline_start
        attempts = f" using {retry_attempt} attempts" if retry_attempt > 1 else ""
        if retry_attempt > max_retries + 1:
            ctx.shared.UpdateOutbound(
                "LoopString",
                f"Failed to generate target {target_stacks} stacks in "
                f"{max_retries} attempts. Verify settings")
            ctx.shared.UpdateOutbound(
                "IBM_RunControl_StackString",
                f"FAIL: Attempted to stack offline at z{memory.ReadCurrentZone()} "
                f"generating {generated} stacks in "
                f"{round(total_time / 1000, 2)}s{attempts}")
        else:
            ctx.shared.UpdateOutbound(
                "IBM_RunControl_StackString",
                f"Stacking: Completed offline at z{memory.ReadCurrentZone()} "
                f"generating {generated} stacks in "
                f"{round(total_time / 1000, 2)}s{attempts}")

    def StackFarmSetup(self):
        ctx = self._ctx
        if not self.KillCurrentBoss():
            self.FallBackFromBossZone()
        self.KEY_W.key_press()
        self.ToggleAutoProgress(0, False, True)
        ctx.farm.LevelManager.LevelFormation("W", "min")
        self.WaitForTransition(self.KEY_W)
        start_time = tick_ms()
        timeout = 5000
        ctx.shared.UpdateOutbound("LoopString", "Setting stack farm formation")
        while (not self.OnlineStacker.FormationCheckWithFari()
               and tick_ms() - start_time < timeout):
            self.KEY_W.key_press()
            ctx.farm.LevelManager.LevelFormation("W", "min")
            precise_sleep(15)
        if tick_ms() - start_time >= timeout:
            ctx.log(f"FAIL: StackFarmSetup() did not set W formation within "
                    f"{timeout}ms")

    def KillCurrentBoss(self, max_loop_time=25000):
        ctx = self._ctx
        memory = ctx.memory
        current_zone = memory.ReadCurrentZone()
        if current_zone is None or current_zone % 5:
            return True
        start_time = tick_ms()
        ctx.shared.UpdateOutbound("LoopString", "Killing boss before stacking")
        while ((memory.ReadCurrentZone() or 0) % 5 == 0
               and tick_ms() - start_time < max_loop_time):
            self.SetFormation()
            if not memory.ReadQuestRemaining():  # skip boss bag
                self.ToggleAutoProgress(1, False, False)
            precise_sleep(50)
        if tick_ms() - start_time >= max_loop_time:
            return False
        self.WaitForTransition()
        return True

    def WaitForTransition(self, key=None, max_loop_time=5000):
        ctx = self._ctx
        if not ctx.memory.ReadTransitioning():
            return
        start_time = tick_ms()
        if key:
            ctx.input.game_focus()  # set focus once and use _bulk
        while (ctx.memory.ReadTransitioning() == 1
               and tick_ms() - start_time < max_loop_time):
            if key:
                key.key_press_bulk()
            precise_sleep(15)

    def FallBackFromBossZone(self, key=None, max_loop_time=5000):
        ctx = self._ctx
        fell_back = False
        current_zone = ctx.memory.ReadCurrentZone()
        if current_zone is None or current_zone % 5:
            return fell_back
        start_time = tick_ms()
        ctx.shared.UpdateOutbound("LoopString", "Falling back from boss zone")
        while ((ctx.memory.ReadCurrentZone() or 0) % 5 == 0
               and tick_ms() - start_time < max_loop_time):
            self.KEY_LEFT.key_press()
            fell_back = True
            precise_sleep(15)
        self.WaitForTransition(key)
        return fell_back

    def FallBackFromZone(self, max_loop_time=5000):
        ctx = self._ctx
        start_time = tick_ms()
        while (ctx.memory.ReadCurrentZone() == -1
               and tick_ms() - start_time < max_loop_time):
            precise_sleep(15)
        start_time = tick_ms()
        ctx.shared.UpdateOutbound("LoopString", "Falling back from zone...")
        while (not ctx.memory.ReadTransitioning()
               and tick_ms() - start_time < max_loop_time):
            self.KEY_LEFT.key_press()
            precise_sleep(15)  # don't go back multiple zones
        self.WaitForTransition()

    # --- formation control -------------------------------------------------------------

    def SetFormation(self, fast_check=False, use_high_zone=False):
        ctx = self._ctx
        memory = ctx.memory
        if not fast_check:
            self._trust_recent = False
        zone = memory.ReadHighestZone() if use_high_zone \
            else memory.ReadCurrentZone()
        is_e_zone = self.ShouldWalk(zone or 0)
        with ctx.critical:  # 'Thread, NoTimers' - animation skip handling
            bench_return = self.BenchBrivConditions(is_e_zone)
            last_formation = memory.ReadMostRecentFormationFavorite()
            if bench_return and last_formation != 3:  # 3 is E
                self.KEY_E.key_press()
                if bench_return == 2:
                    # Only re-place Briv urgently if we must jump right away
                    if self._zone(memory.ReadHighestZone() or 0).jumpZone:
                        precise_sleep(15)  # avoid instant swap-back
                        start_time = tick_ms()
                        while (memory.ReadFormationTransitionDir() == 4
                               and not ctx.heroes[58].ReadBenched()
                               and tick_ms() - start_time < 1000):
                            precise_sleep(15)
                        self.KEY_Q.key_press_bulk()
                        while (memory.ReadFormationTransitionDir() == 4
                               and tick_ms() - start_time < 1000):
                            precise_sleep(15)
                return
        if self.UnBenchBrivConditions(is_e_zone) and last_formation != 1:  # 1 is Q
            self.KEY_Q.key_press()
            return
        if self._trust_recent and fast_check:
            if last_formation not in (1, 3):
                (self.KEY_E if is_e_zone else self.KEY_Q).key_press()
        else:
            level_manager = ctx.farm.LevelManager
            if not (self.IsCurrentFormation(level_manager.GetFormation("Q"))
                    or self.IsCurrentFormation(level_manager.GetFormation("E"))):
                (self.KEY_E if is_e_zone else self.KEY_Q).key_press()
            else:
                self._trust_recent = True  # confirmed Q or E - normal progression

    def IsCurrentFormation(self, test_formation):
        """Port of g_SF.IsCurrentFormation (SharedFunctions)."""
        if not test_formation:
            return False
        current = self._ctx.memory.GetCurrentFormation()
        if not current or len(current) != len(test_formation):
            return False
        return all(test_formation[i] == current[i] for i in range(len(current)))

    def BenchBrivConditions(self, is_e_zone):
        """0 do not bench, 1 bench, 2 bench for animation override."""
        memory = self._ctx.memory
        if (self.zonesPerJumpE == 1
                and memory.ReadTransitionDirection() == 1
                and memory.ReadFormationTransitionDir() == 4):
            return 2
        return 1 if is_e_zone else 0

    def UnBenchBrivConditions(self, is_e_zone):
        if is_e_zone:
            return False
        if self.zonesPerJumpE > 1:  # no transition checks when feat swapping
            return True
        if self._ctx.memory.ReadFormationTransitionDir() != 4:  # not OffToRight
            return True
        return False

    def ShouldWalk(self, zone):
        return not self._zone(zone).jumpZone

    def GetStandardFormationKey(self, zone):
        return self.KEY_E if self.ShouldWalk(zone) else self.KEY_Q

    def GetStandardFormation(self, zone):
        level_manager = self._ctx.farm.LevelManager
        return level_manager.GetFormation("E" if self.ShouldWalk(zone) else "Q")

    # --- route construction --------------------------------------------------------------

    def LoadRoute(self):
        for zone_number in range(1, (self.targetZone or 0) + 1):
            if zone_number not in self.zones:
                zone = Zone()
                zone.z = zone_number
                self.zones[zone_number] = zone
                self.ProcessRoute(zone)
        # Pre-calculate jumps from every possible end node (targetZone up to
        # targetZone + jump - 1 can be hit when jumping past the reset)
        end_zone = self.targetZone or 0
        while (end_zone < (self.targetZone or 0) + self.zonesPerJumpQ
               and end_zone <= self.zoneCap + 1):
            if end_zone in self.zones:
                self.JumpsRecurse(self.zones[end_zone], 0)
            end_zone += 1

    def JumpsRecurse(self, current_zone, starting_jumps):
        # Note: the AHK original has 'if (inZone.jumpsToFinish:=-1)' - an
        # assignment, so the 'not yet processed' check is always true and
        # every visit overwrites. Ported as-is for identical behaviour.
        # Iterative rather than recursive: a walk-every-zone route makes the
        # chain as long as the target zone, past Python's recursion limit.
        stack = [(current_zone, starting_jumps)]
        while stack:
            zone, jumps = stack.pop()
            for in_zone in zone.incomingZones.values():
                jumps_done = jumps
                if in_zone.jumpZone:
                    jumps_done += 1
                elif self.IsFeatSwap():
                    jumps_done += 1
                in_zone.jumpsToFinish = jumps_done
                in_zone.stacksToFinish = jump_cost(jumps_done)
                stack.append((in_zone, jumps_done))

    def ProcessRoute(self, current_zone):
        ctx = self._ctx
        route_jump = ctx.setting("IBM_Route_Zones_Jump", [])
        route_stack = ctx.setting("IBM_Route_Zones_Stack", [])
        while current_zone.z < (self.targetZone or 0):
            type_index = current_zone.z % 50
            if type_index == 0:
                type_index = 50
            # settings arrays are 1-indexed in AHK
            current_zone.jumpZone = _route_flag(route_jump, type_index)
            current_zone.stackZone = _route_flag(route_stack, type_index)
            if current_zone.jumpZone:
                next_number = current_zone.z + self.zonesPerJumpQ
            else:
                next_number = current_zone.z + self.zonesPerJumpE
            if next_number in self.zones:
                current_zone.nextZone = self.zones[next_number]
                self.zones[next_number].incomingZones[current_zone.z] = current_zone
                break  # joined an existing route
            next_zone = Zone()
            next_zone.z = next_number
            next_zone.incomingZones[current_zone.z] = current_zone
            current_zone.nextZone = next_zone
            self.zones[next_number] = next_zone
            current_zone = next_zone

    def BrivHasThunderStep(self):
        """Feat 2131: 'Gain 20% More Sprint Stacks When Converted'."""
        memory = self._ctx.memory
        slot_q = memory.GetSavedFormationSlotByFavorite(1)
        slot_e = memory.GetSavedFormationSlotByFavorite(3)
        # Note: the AHK original calls a non-existent method for the E check
        # (silently falsy); the evident intent - checking both Q and E - is
        # ported here.
        if memory.HeroHasAnyFeatsSavedInFormation(58, slot_q) \
                or (slot_e is not None
                    and memory.HeroHasAnyFeatsSavedInFormation(58, slot_e)):
            return bool(memory.HeroHasFeatSavedInFormation(58, 2131, slot_q)
                        or memory.HeroHasFeatSavedInFormation(58, 2131, slot_e))
        modron_slot = memory.GetActiveModronFormationSaveSlot()
        if memory.HeroHasAnyFeatsSavedInFormation(58, modron_slot):
            return bool(memory.HeroHasFeatSavedInFormation(58, 2131, modron_slot))
        feats = memory.GetHeroFeats(58)  # might not be saved in formations
        return bool(feats and 2131 in feats)

    # --- autoprogress ------------------------------------------------------------------------

    def ToggleAutoProgress(self, is_toggled=1, force_toggle=False,
                           force_state=False):
        ctx = self._ctx
        with ctx.critical:
            start_time = tick_ms()
            if force_toggle:
                self.KEY_autoProgress.key_press()
            if ctx.memory.ReadAutoProgressToggled() != is_toggled:
                self.KEY_autoProgress.key_press()
            while (ctx.memory.ReadAutoProgressToggled() != is_toggled
                   and force_state and tick_ms() - start_time < 1000):
                self.KEY_autoProgress.key_press_bulk()
                precise_sleep(15)

    def StartAutoProgressSoft(self):
        if self._ctx.memory.ReadAutoProgressToggled() != 1:
            self.KEY_autoProgress.key_press()

    def InitZone(self):
        ctx = self._ctx
        ctx.farm.LevelManager.LevelClickDamage()
        self.StartAutoProgressSoft()
        ctx.farm.PreviousZoneStartTime = tick_ms()


def _route_flag(route_array, type_index):
    """1-indexed AHK settings array access."""
    if not route_array or type_index > len(route_array):
        return False
    return route_array[type_index - 1] == 1


class OnlineStacker:
    """Prototype for the Farideh-ultimate activation methods."""

    def __init__(self, ctx, route_master):
        self._ctx = ctx
        self.RM = route_master
        self.useFaridehUlt = 33 in ctx.farm.LevelManager.savedFormationChamps["W"]
        self.FaridehUltThreshold = 0
        if self.useFaridehUlt:
            ctx.heroes[33]  # ensure the object exists at start-up
            self.FaridehUltThreshold = ctx.setting("IBM_Online_Farideh_Threshold", 0)
        self.useBrivBoost = ctx.setting("IBM_LevelManager_Boost_Use")
        self.BrivBoost = None
        if self.useBrivBoost:
            self.BrivBoost = BrivBoost(ctx,
                                       ctx.setting("IBM_LevelManager_Boost_Multi", 8))

    def InitMemoryReads(self):
        pass

    def FaridehUltCheck(self, activate_fari_ult):
        """Returns the updated activate flag (the AHK ByRef parameter)."""
        return activate_fari_ult

    def Stack(self):
        ctx = self._ctx
        memory = ctx.memory
        briv = ctx.heroes[58]
        target_stacks = self.RM.GetTargetStacks(force_recalc=True)
        briv.InitFastSB()
        stacks = briv.FastReadSBStacks() or 0
        if stacks >= target_stacks:
            return
        start_stacks = stacks
        self.RM.SetFormation()  # correct formation before stopping progress
        self.RM.ToggleAutoProgress(0, False, True)
        key_auto = self.RM.KEY_autoProgress
        start_time = time.perf_counter()
        activate_fari_ult = 0
        if self.useFaridehUlt:
            if (memory.ReadCurrentZone() or 0) < ctx.setting("IBM_Online_Melf_Min", 0):
                # Recovery: a levelled Farideh massively raises the stack zone
                ctx.farm.LevelManager.OverrideLevelByIDLowerToMax(33, "min", 0)
                activate_fari_ult = 0
            else:
                activate_fari_ult = 1
        game_speed = memory.IBM_ReadBaseGameSpeed() or 1
        self.Setup(activate_fari_ult, 15000 / game_speed)
        if activate_fari_ult:
            # After the formation switch so Tatyana's handler is available
            self.InitMemoryReads()
        ctx.shared.UpdateOutbound("LoopString", "Online Stack")
        max_stack_time = 200.0 / game_speed  # seconds (200s at x1, 16s at x12.5)
        if ctx.farm.failedConversionMode:
            max_stack_time *= 5  # probably killing things; allow far more time
        precision_mode = False
        precision_trigger = math.floor(target_stacks * 0.90)
        current_zone = memory.ReadCurrentZone()
        elapsed = 0.0
        critical_held = False
        try:
            while stacks < target_stacks and elapsed < max_stack_time:
                if activate_fari_ult:
                    activate_fari_ult = self.FaridehUltCheck(activate_fari_ult)
                if not (precision_mode or activate_fari_ult):
                    if stacks > precision_trigger:
                        ctx.critical.acquire()
                        critical_held = True
                        ctx.input.game_focus()  # pre-set for the release press
                        precision_mode = True
                    else:
                        time.sleep(0.010)
                elapsed = time.perf_counter() - start_time
                stacks = briv.FastReadSBStacks() or 0
            key_auto.key_press_bulk()  # re-enable autoprogress ASAP
            if elapsed >= max_stack_time:
                if critical_held:
                    ctx.critical.release()
                    critical_held = False
                ctx.farm.GameMaster.RestartAdventure(
                    f"Online stack took too long ({round(elapsed, 1)}s)")
                ctx.farm.GameMaster.SafetyCheck()
                ctx.farm.PreviousZoneStartTime = tick_ms()
                return
            ctx.farm.PreviousZoneStartTime = tick_ms()
            # Jumping straight from stack zone to reset zone behaves oddly
            run_complete = (memory.ReadHighestZone() or 0) >= self.RM.targetZone
            if not run_complete and (memory.ReadQuestRemaining() or 0) > 0:
                ctx.log("Online stack zone not complete - falling back")
                self.RM.FallBackFromZone()
            else:
                self.RM.ToggleAutoProgress(1, False, True)
        finally:
            if critical_held:
                ctx.critical.release()
        generated = stacks - start_stacks
        elapsed_ms = round(elapsed * 1000)
        ctx.shared.UpdateOutbound(
            "IBM_RunControl_StackString",
            f"Stacking: Completed online at z{current_zone} generating "
            f"{generated} stacks in {elapsed_ms}ms")
        ctx.log(f"Online{{z{current_zone} Tar={target_stacks} "
                f"Gen={generated}}},{briv.FastReadSBStacks()},{elapsed_ms}")
        if not run_complete:
            self.RM.SetFormation(use_high_zone=True)  # current zone is complete
            self.RM.WaitForTransition()

    def Setup(self, expect_fari, timeout=1000):
        """Swap to W as the zone completes, fast-levelling W-only champions."""
        ctx = self._ctx
        memory = ctx.memory
        fast_level_list = self.GetFastLevelList()
        quest_node = memory.GameManager.game.gameInstances[0] \
            .ActiveCampaignData.currentArea.QuestRemaining
        quest_addr = quest_node.resolve_address()

        def quest_remaining():
            value = memory.mem.read(quest_addr, "Int") if quest_addr else None
            return value if value is not None else 0

        key_w = self.RM.KEY_W
        self.RM.WaitForTransition()
        end_time = tick_ms() + timeout
        while not memory.ReadAreaActive() and tick_ms() < end_time:
            time.sleep(0.001)
        end_time = tick_ms() + timeout
        quest = quest_remaining()
        for hero in fast_level_list:
            while quest > 0 and tick_ms() < end_time:
                quest = quest_remaining()  # catch completion as closely as possible
            key_w.key_press_bulk()
            loop_count = 0
            while not hero.ReadSelectedInSeat() and loop_count < 12:
                if loop_count:
                    key_w.key_press_bulk()
                loop_count += 1
            if hero.Current.use_modifier_for_fast:
                ctx.farm.LevelManager.SetModifierKey(True)
                hero.Key.key_press_bulk()
                ctx.farm.LevelManager.SetModifierKey(False)
            else:
                hero.Key.key_press_bulk()
        if quest > 0:  # no fast handlers waited for completion
            while quest > 0 and tick_ms() < end_time:
                time.sleep(0.001)
                quest = quest_remaining()
            key_w.key_press_bulk()
        start_time = tick_ms()
        ctx.shared.UpdateOutbound("LoopString", "Setting stack farm formation")
        while (not self.FormationCheckWithFari(expect_fari)
               and tick_ms() - start_time < timeout):
            key_w.key_press()
            ctx.farm.LevelManager.LevelFormation("W", "min", 0)
            time.sleep(0.010)
        if tick_ms() - start_time >= timeout:
            ctx.log(f"FAIL: Online Stack Setup() did not set W formation "
                    f"within {timeout}ms")
            ctx.log(f"DEBUG: Fari Level=[{ctx.heroes[33].ReadLevel()}] "
                    f"Formation={self.DEBUG_FORMATION_STRING()}")
        if self.useBrivBoost:
            self.BrivBoost.Apply()
        ctx.farm.LevelManager.LevelFormation("W", "min")  # apply Boost changes

    def FormationCheckWithFari(self, farideh_required=False):
        """True when the field matches W exactly, except Farideh (33) who is
        ignored - or required present in any slot when farideh_required."""
        ctx = self._ctx
        formation_w = ctx.farm.LevelManager.GetFormation("W")
        current = ctx.memory.GetCurrentFormation()
        if not current:
            return False
        fari_found = False
        for index, champ in enumerate(current):
            if champ == 33:
                fari_found = True
            else:
                expected = formation_w[index] if index < len(formation_w) else None
                if expected != champ and expected != 33:
                    return False
        return not farideh_required or fari_found

    def GetFastLevelList(self):
        ctx = self._ctx
        fast_list = []
        for hero_id in ctx.farm.LevelManager.savedFormationChamps["XW"]:
            hero = ctx.heroes[hero_id]
            if hero.NeedsLevelling():
                hero.Current.use_modifier_for_fast = \
                    hero.GetLevelsRequired() < 100
                fast_list.append(hero)
        return fast_list

    def DEBUG_FORMATION_STRING(self):
        slots = self._ctx.memory.GameManager.game.gameInstances[0] \
            .Controller.formation.slots
        size = slots.size()
        if size is None or size <= 0 or size > 14:
            return "X:[]"
        parts = []
        champ_count = 0
        for index in range(size):
            hero_id = slots[index].hero.child("def").ID.read()
            if hero_id is not None and hero_id > 0:
                champ_count += 1
                parts.append(str(hero_id))
            else:
                parts.append("_")
        return f"{champ_count}:[{';'.join(parts)};]"


class OnlineStackerAttacking(OnlineStacker):
    def __init__(self, ctx, route_master):
        super().__init__(ctx, route_master)
        self._melee_addr = None
        self._ranged_addr = None

    def InitMemoryReads(self):
        formation = self._ctx.memory.GameManager.game.gameInstances[0] \
            .Controller.formation
        self._melee_addr = formation.numAttackingMonstersReached.resolve_address()
        self._ranged_addr = formation.numRangedAttackingMonsters.resolve_address()

    def FaridehUltCheck(self, activate_fari_ult):
        mem = self._ctx.memory.mem
        melee = mem.read(self._melee_addr, "Int") or 0
        ranged = mem.read(self._ranged_addr, "Int") or 0
        if melee + ranged >= self.FaridehUltThreshold:
            # ExitOnceQueued so we don't wait on activation and overstack
            self._ctx.heroes[33].UseUltimate(exit_once_queued=True)
            return 0
        return activate_fari_ult


class OnlineStackerTatyanaReturn(OnlineStacker):
    def __init__(self, ctx, route_master):
        super().__init__(ctx, route_master)
        self._timer_addr = None
        self.FaridehUltThresholdGameTime = 0

    def InitMemoryReads(self):
        # Requires Tatyana fielded and levelled enough for Find a Feast (80)
        self._timer_addr = self._ctx.heroes[97].GetFindAFeastReturnTimerAddress()
        self.FaridehUltThresholdGameTime = self.FaridehUltThreshold / 1000

    def _return_time(self):
        if not self._timer_addr:
            return None
        return self._ctx.memory.mem.read(self._timer_addr, "Double")

    def FaridehUltCheck(self, activate_fari_ult):
        return_time = self._return_time()
        if activate_fari_ult == 1:  # waiting on the timer being available
            if return_time is not None and return_time > 0:
                if return_time < self.FaridehUltThresholdGameTime:
                    self._ctx.heroes[33].UseUltimate(exit_once_queued=True)
                    return 0
                return 2  # primed for a future call
            return 1
        if return_time is not None \
                and return_time < self.FaridehUltThresholdGameTime:
            self._ctx.heroes[33].UseUltimate(exit_once_queued=True)
            return 0
        return activate_fari_ult


class OnlineStackerActiveEnemies(OnlineStacker):
    def __init__(self, ctx, route_master):
        super().__init__(ctx, route_master)
        self._size_addr = None

    def InitMemoryReads(self):
        self._size_addr = self._ctx.memory.GameManager.game.gameInstances[0] \
            .Controller.area.activeMonsters.size_address()

    def FaridehUltCheck(self, activate_fari_ult):
        count = self._ctx.memory.mem.read(self._size_addr, "Int") or 0
        if count >= self.FaridehUltThreshold:
            self._ctx.heroes[33].UseUltimate(exit_once_queued=True)
            return 0
        return activate_fari_ult


class BrivBoost:
    """IC_BrivMaster_BrivBoost_Class - level Briv to survive the stack zone."""

    def __init__(self, ctx, target_multi):
        self._ctx = ctx
        memory = ctx.memory
        self.BuildBrivLevelTable(130, {70: 95, 180: 165, 265: 290, 340: 510,
                                       455: 890, 575: 1560, 695: 2730,
                                       815: 4775, 935: 7800, 1050: 14200,
                                       1170: 24000, 1300: 42500})
        self.ZoneCache = {}
        self.DPSGrowthRateCurve = memory.IBM_ReadDPSGrowthCurve() or []
        if not self.DPSGrowthRateCurve:
            raise RuntimeError(
                "Briv Boost failed to read the DPS growth rate curve at "
                "adventure start. If this persists disable Briv Boost.")
        self.areaAndCampaignMonsterDamageMultiplier = \
            (memory.IBM_ReadAreaMonsterDamageMultiplier() or 1) \
            * (memory.IBM_ReadCampaignMonsterDamageMultiplier() or 1)
        self.monsterBaseDPS = memory.IBM_ReadMonsterBaseDPS() or 0
        self.maxMonsters = 100
        self.overwhelmAdditivePenalty = 0.1
        self.targetMultiplier = target_multi

    def Apply(self):
        ctx = self._ctx
        briv = ctx.heroes[58]
        current_level = briv.ReadLevel() or 0
        target_level = self.GetBrivBoostTargetLevel(
            ctx.memory.ReadHighestZone() or 0, current_level)
        if target_level > current_level:
            ctx.farm.LevelManager.OverrideLevelByIDRaiseToMin(58, "min",
                                                              target_level)
            ctx.log(f"BrivBoost{{C={current_level} T={target_level}}}")

    def GetBrivBoostTargetLevel(self, zone, current_level):
        if zone not in self.ZoneCache:
            self.ZoneCache[zone] = self.GetPreFlamesDamage(zone)
        flames_adjusted = self.ZoneCache[zone] * \
            (2 ** self._ctx.heroes[83].GetNumFlamesCards())
        base_hp = self.GetBrivBaseHPforLevel(current_level)
        max_health = self._ctx.heroes[58].ReadMaxHealth() or base_hp
        briv_hp_multiplier = max_health / base_hp if base_hp else 1
        target = self.GetBrivLevelForBaseHP(flames_adjusted / briv_hp_multiplier)
        return math.ceil(target / 100) * 100  # adjust for x100 levelling

    def GetPreFlamesDamage(self, zone):
        overwhelm = self._ctx.heroes[58].ReadOverwhelm() or 0
        damage = self.GetCurveValue(zone)
        damage *= self.areaAndCampaignMonsterDamageMultiplier
        damage *= self.maxMonsters
        damage *= 1 + max(self.maxMonsters - overwhelm, 0) \
            * self.overwhelmAdditivePenalty
        damage *= self.targetMultiplier
        return damage

    def GetBrivLevelForBaseHP(self, base_hp):
        max_level = 1
        for level, hp in self.BrivLevelTable.items():
            if hp >= base_hp:
                return level
            max_level = level
        return max_level

    def GetBrivBaseHPforLevel(self, briv_level):
        last_hp = 0
        for level, hp in self.BrivLevelTable.items():
            if level <= briv_level:
                last_hp = hp
            else:
                break
        return last_hp

    def GetCurveValue(self, index):
        result = self.monsterBaseDPS
        curve = self.DPSGrowthRateCurve
        for i, point in enumerate(curve):
            if point["level"] > index:
                break
            value = point["value"] or 0
            if i != len(curve) - 1 and index > curve[i + 1]["level"]:
                num = curve[i + 1]["level"] - point["level"]
            else:
                num = index - point["level"]
            result *= value ** num
        return result

    def BuildBrivLevelTable(self, base_hp, upgrade_list):
        hp = base_hp
        self.BrivLevelTable = {1: hp}
        for level, upgrade_hp in upgrade_list.items():
            hp += upgrade_hp
            self.BrivLevelTable[level] = hp
