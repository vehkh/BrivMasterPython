"""Port of IC_BrivMaster_LevelManager.ahk - champion levelling."""

from __future__ import annotations

import math

from .ctx import precise_sleep, tick_ms


class LevelManager:
    def __init__(self, ctx):
        """Processes all formations once - changing a target level requires a
        script restart, as in the original."""
        self._ctx = ctx
        memory = ctx.memory
        self.levelingDone = {}
        self.savedFormations = {}
        self.savedFormationChamps = {"A": {}, "XW": {}}
        self.currentWorkList = None
        self.ExtractFormation(memory.GetSavedFormationSlotByFavorite(1), "Q")
        self.ExtractFormation(memory.GetSavedFormationSlotByFavorite(3), "E")
        self.ExtractFormation(memory.GetActiveModronFormationSaveSlot(), "M")
        # W must be last so W-only champions are identified easily
        self.ExtractFormation(memory.GetSavedFormationSlotByFavorite(2), "W")
        self.ProcessFormation(ctx.setting("IBM_LevelManager_Levels", {}))
        self.SetZ1C()
        self.ResetLevellingDone()
        self.maxKeyPresses = ctx.setting("IBM_LevelManager_Input_Max", 6)
        self.KEY_ClickDmg = ctx.input.get_key("ClickDmg")
        self.ExtactFrontColumn()
        self.ghostLevellingHeroes = []
        if ctx.setting("IBM_Level_Options_Ghost"):
            self.CreateGhostFormation()
        mod_key = ctx.setting("IBM_Level_Options_Mod_Key", "Ctrl")
        self.KEY_Modifier = ctx.input.get_key(
            "LCtrl" if mod_key == "Ctrl" else mod_key)
        self.modifierLevelUpAmount = ctx.setting("IBM_Level_Options_Mod_Value", 25)
        # The click-damage widget only reflects the Ctrl multiplier, so the
        # memory confirmation in SetModifierKey is meaningless for other
        # modifiers (e.g. Shift/x10, the reliable choice under Wine, where
        # the game does not see a virtual Ctrl at all).
        self.modifierConfirmable = (mod_key == "Ctrl")
        self.clickDamageTargetFinal = 0
        self.clickDamageTargetRush = 0

    def SetZ1C(self):
        """z1c = zone 1 complete levelling restrictions (do not change at
        run time)."""
        heroes = self._ctx.heroes
        if heroes.InM(139):  # Thellora in M: avoid Melf completing z1 too fast
            if heroes.InM(59):
                heroes[59].Master.z1c = True
                heroes[59].Reset()
        else:  # Casino on z1: stop Briv being levelled
            heroes[58].Master.z1c = True
            heroes[58].Reset()

    def LevelFormation(self, formation_index, mode="min", allowed_time=10000,
                       force_priority=False, suppress_by_id=None,
                       wait_for_gold=False):
        if self.levelingDone.get(formation_index, {}).get(mode):
            return
        self.CreateWorklist(formation_index, mode, suppress_by_id, wait_for_gold)
        self.LevelWorklist(allowed_time, force_priority, wait_for_gold)

    def LevelWorklist(self, allowed_time=0, force_priority=False,
                      wait_for_gold=False):
        if self.currentWorkList is None:
            return
        start_time = tick_ms()
        while True:
            worklist = self.currentWorkList
            if worklist.Done() or (force_priority and worklist.IsPriorityDone()):
                break
            wait_for_gold = worklist.Level(self.maxKeyPresses, wait_for_gold,
                                           force_priority)
            if tick_ms() - start_time > allowed_time:
                break

    def CreateWorklist(self, formation_index, mode, suppress_by_id,
                       wait_for_gold):
        if formation_index == "" or formation_index is None:
            champion_ids = self._ctx.memory.IBM_GetCurrentFormationChampions() or {}
        else:
            champion_ids = self.savedFormationChamps.get(formation_index, {})
        pending = 0
        self.currentWorkList = WorkList(self._ctx, self, mode, formation_index)
        for champ_id in champion_ids:
            pending += self.currentWorkList.AddChamp(champ_id, suppress_by_id,
                                                     wait_for_gold)
        if pending == 0 and formation_index:
            heroes = self._ctx.heroes
            floors_active = any(
                heroes[champ_id] is not None
                and tick_ms() < heroes[champ_id].Current.optimistic_expiry
                for champ_id in champion_ids)
            if not floors_active:
                self.levelingDone.setdefault(formation_index, {})[mode] = True

    def GetClickDamageTargetLevel(self):
        memory = self._ctx.memory
        if memory.ReadCurrentZone() == 1:
            return self.clickDamageTargetRush  # meet Thellora's rush target
        highest = memory.ReadHighestZone() or 0
        return min(self.clickDamageTargetFinal,
                   highest + self._ctx.farm.RouteMaster.zonesPerJumpQ * 2)

    def LevelClickDamage(self, timeout=500):
        memory = self._ctx.memory
        start_time = tick_ms()
        click_target = self.GetClickDamageTargetLevel()
        while True:
            level = memory.ReadClickLevel()
            if level is None or level >= click_target:
                break
            if (memory.IBM_ReadClickLevelUpAllowed() or 0) <= 0:
                break
            if tick_ms() - start_time >= timeout:
                break
            self.KEY_ClickDmg.key_press()

    def SetupFailedConversion(self):
        self.OverrideMinToSoftCap()
        self.ResetLevellingDone()
        self._ctx.log("SetupFailedConversion() Triggered")

    def OverrideMinToSoftCap(self):
        for hero in self._ctx.heroes.created().values():
            hero.SetSoftCap()

    def ResetLevellingDone(self):
        for index in ("Q", "W", "E", "M"):
            self.levelingDone[index] = {"min": False, "z1": False}
        if self._ctx.setting("IBM_Level_Options_Ghost"):
            self.levelingDone["GHOST"] = {"min": False, "z1": False}

    def ExtractFormation(self, slot, index):
        """Extracts the formation and its champion set in one go."""
        memory = self._ctx.memory
        self.savedFormations[index] = []
        self.savedFormationChamps[index] = {}
        if slot is None or slot < 0:
            return
        save = memory.GameManager.game.gameInstances[0] \
            .FormationSaveHandler.formationSavesV2[slot]
        size = save.Formation.size()
        if size is None or size <= 0 or size > 500:
            return
        for position in range(size):
            champ_id = save.Formation[position].read()
            self.savedFormations[index].append(champ_id)
            if champ_id is not None and champ_id != -1:
                self.savedFormationChamps[index][champ_id] = True
                if index == "W" and champ_id not in self.savedFormationChamps["A"]:
                    self.savedFormationChamps["XW"][champ_id] = True  # eXclusive W
                self.savedFormationChamps["A"][champ_id] = True

    def GetFormation(self, index):
        return self.savedFormations.get(index)

    def CreateGhostFormation(self):
        """M plus champions whose seats M does not use - candidates for ghost
        levelling during the Casino."""
        heroes = self._ctx.heroes
        self.savedFormationChamps["GHOST"] = {}
        self.ghostLevellingHeroes = []
        seat_list = {}
        for hero_id in self.savedFormationChamps["M"]:
            self.savedFormationChamps["GHOST"][hero_id] = True
            seat_list[heroes[hero_id].Seat] = True
        for hero_id in self.savedFormationChamps["A"]:
            if (hero_id not in self.savedFormationChamps["GHOST"]
                    and not seat_list.get(heroes[hero_id].Seat)):
                self.savedFormationChamps["GHOST"][hero_id] = True
                self.ghostLevellingHeroes.append(heroes[hero_id])

    def ExtactFrontColumn(self):
        """Front-row M champions except Briv - candidates for levelling
        suppression so Briv takes all the hits."""
        front_size = self._ctx.memory.IBM_GetFrontColumnSize() or 0
        self.frontColumnChampionsMNoBriv = []
        formation_m = self.savedFormations.get("M", [])
        for position in range(min(front_size, len(formation_m))):
            hero_id = formation_m[position]
            if hero_id != 58 and hero_id is not None and hero_id != -1:
                self.frontColumnChampionsMNoBriv.append(hero_id)

    def SetupFirstZoneFrontRow(self):
        ctx = self._ctx
        if ctx.setting("IBM_Level_Options_Suppress_Front"):
            front_row = []
            for hero_id in self.frontColumnChampionsMNoBriv:
                hero = ctx.heroes[hero_id]
                levels_required = hero.GetTargetLevel()
                if levels_required <= 100 - self.modifierLevelUpAmount:
                    # e.g. 50 at x25 -> 2 modifier presses
                    hero.Current.casino_levelling = math.ceil(
                        levels_required / self.modifierLevelUpAmount)
                else:
                    hero.Current.casino_levelling = 0  # x100 levelling
                front_row.append(hero)
                self.OverrideLevelByIDLowerToMax(hero_id, "z1", 0)
                self.OverrideLevelByIDLowerToMax(hero_id, "min", 0)
            return front_row
        # Otherwise raise priority so they are placed before attacks start
        for hero_id in self.frontColumnChampionsMNoBriv:
            self.RaisePriorityForFrontRow(hero_id)
        return []

    def SetupFirstZoneGhost(self):
        """Confirm which ghost candidates are actually seated."""
        ghost_list = []
        for hero in self.ghostLevellingHeroes:
            if hero.ReadSelectedInSeat():
                levels_required = hero.GetTargetLevel()
                if levels_required <= 100 - self.modifierLevelUpAmount:
                    hero.Current.casino_levelling = math.ceil(
                        levels_required / self.modifierLevelUpAmount)
                else:
                    hero.Current.casino_levelling = 0
                ghost_list.append(hero)
        return ghost_list

    def OverrideLevelByID(self, hero_id, mode, level):
        if self._ctx.heroes.has(hero_id):
            self._ctx.heroes[hero_id].OverrideLevel(mode, level)

    def ResetLevelByID(self, hero_id):
        if self._ctx.heroes.has(hero_id):
            self._ctx.heroes[hero_id].Reset()
            self.ResetLevellingDone()

    def OverrideLevelByIDRaiseToMin(self, hero_id, mode, level):
        if self._ctx.heroes.has(hero_id):
            hero = self._ctx.heroes[hero_id]
            if getattr(hero.Current, mode) < level:
                setattr(hero.Current, mode, level)
                self.ResetLevellingDone()  # might need further levelling

    def OverrideLevelByIDLowerToMax(self, hero_id, mode, level):
        if self._ctx.heroes.has(hero_id):
            hero = self._ctx.heroes[hero_id]
            if getattr(hero.Current, mode) > level:
                setattr(hero.Current, mode, level)

    def RaisePriorityForFrontRow(self, hero_id):
        if self._ctx.heroes.has(hero_id):
            self._ctx.heroes[hero_id].RaisePriorityForFrontRow()

    def Reset(self):
        ctx = self._ctx
        self.ResetLevellingDone()
        ctx.heroes.ResetAll()
        route_master = ctx.farm.RouteMaster
        self.clickDamageTargetFinal = route_master.targetZone
        if ctx.heroes[139].inM:
            self.clickDamageTargetRush = route_master.thelloraTarget
        else:
            self.clickDamageTargetRush = (route_master.thelloraTarget
                                          + route_master.zonesPerJumpQ * 2)

    def ProcessFormation(self, level_settings):
        for hero_id in self.savedFormationChamps["A"]:
            self._ctx.heroes[hero_id].ApplyLevelSettings(
                level_settings, self.savedFormationChamps)

    def SetModifierKey(self, use_modifier):
        """Returns True when memory confirms the level-up amount changed.
        Callers must skip modifier presses on False: an unregistered
        modifier turns every x25 press into x100 and overshoots the caps
        (seen on Linux, where key injection can miss a busy window)."""
        memory = self._ctx.memory
        if not self.modifierConfirmable:
            if not use_modifier:
                self.KEY_Modifier.release_bulk()
                precise_sleep(60)
                return True
            # No widget feedback for this modifier (it only tracks Ctrl).
            # Confirm with a sacrificial click-damage press instead: click
            # levels in the same x10/x100 steps, has no specialisation
            # levels, and gets levelled by the script anyway - so a probe
            # that lands as x100 costs nothing, while a champion press
            # with a missed modifier overshoots a Feat-Guard cap for the
            # whole run.
            self._ctx.input.game_focus()
            self.KEY_Modifier.press_bulk()
            precise_sleep(60)
            if (memory.IBM_ReadClickLevelUpAllowed() or 0) <= 0:
                # Click at its cap - cannot probe; trust after a settle
                precise_sleep(120)
                return True
            before = memory.ReadClickLevel()
            if before is None:
                precise_sleep(120)
                return True
            self.KEY_ClickDmg.key_press_bulk()
            start_time = tick_ms()
            while tick_ms() - start_time < 500:
                level = memory.ReadClickLevel()
                if level is not None and level != before:
                    if level - before == self.modifierLevelUpAmount:
                        return True  # game sees the modifier
                    break  # landed as x100: modifier missed
                precise_sleep(5)
            self.KEY_Modifier.release_bulk()
            return False
        if use_modifier:
            self.KEY_Modifier.press_bulk()
            start_time = tick_ms()
            # Allow up to 100ms for the keypress to apply, to avoid deadlock
            while (memory.IBM_ClickDamageLevelAmount() != self.modifierLevelUpAmount
                   and tick_ms() - start_time < 100):
                precise_sleep(1)
            confirmed = (memory.IBM_ClickDamageLevelAmount()
                         == self.modifierLevelUpAmount)
            if not confirmed:  # do not leave a half-registered modifier down
                self.KEY_Modifier.release_bulk()
            return confirmed
        self.KEY_Modifier.release_bulk()
        start_time = tick_ms()
        while (memory.IBM_ClickDamageLevelAmount() == self.modifierLevelUpAmount
               and tick_ms() - start_time < 100):
            precise_sleep(1)
        return memory.IBM_ClickDamageLevelAmount() != self.modifierLevelUpAmount


class WorkList:
    """IC_BrivMaster_LevelManager_WorkList_Class - one levelling job."""

    def __init__(self, ctx, level_manager, mode, formation_index=None):
        self._ctx = ctx
        self.champs = {}
        self.parent = level_manager
        self.mode = mode
        self.formation_index = formation_index
        self.minPriority = 0
        self.maxPriority = 0

    def Level(self, max_key_presses, wait_for_gold, force_priority):
        """Returns the updated wait_for_gold (the AHK ByRef parameter)."""
        ctx = self._ctx
        key_list_100 = []
        key_list_10 = []  # the modifier list - can be x10 or x25
        favorite = {"Q": 1, "W": 2, "E": 3}.get(self.formation_index)
        if favorite is not None and \
                ctx.memory.ReadMostRecentFormationFavorite() != favorite:
            return wait_for_gold  # swap still in flight - press next pass
        self.GetKeyList(max_key_presses, key_list_100, key_list_10,
                        force_priority)
        if not key_list_100 and not key_list_10:
            return wait_for_gold  # z1c can leave nothing to do this iteration
        if wait_for_gold:  # start-of-run calls only
            self.WaitForAreaActive()
            ctx.farm.Logger.SetActiveStartTime()
            ctx.input.game_focus()  # as close to the input as possible
            first_key = key_list_100[0] if key_list_100 else key_list_10[0]
            wait_for_gold = not self.WaitForFirstGold(first_key.tag)
        else:
            ctx.input.game_focus()
        with ctx.critical:  # no other input senders while levelling
            for key in key_list_100:
                key.key_press_bulk()
            if key_list_10:
                # Skip the x25 presses entirely if the modifier did not
                # register - they would land as x100 and overshoot the
                # caps. UpdateLevels() clears pending, so the next loop
                # iteration simply retries them.
                if self.parent.SetModifierKey(True):
                    for key in key_list_10:
                        key.key_press_bulk()
                self.parent.SetModifierKey(False)
        self.UpdateLevels()
        return wait_for_gold

    def WaitForAreaActive(self):
        start_time = tick_ms()
        while (not self._ctx.memory.ReadAreaActive()
               and tick_ms() - start_time < 10000):
            pass

    def WaitForFirstGold(self, check_seat):
        """First 8 bytes of the gold quad suffice: x==0 iff the quad is 0."""
        memory = self._ctx.memory
        start_time = tick_ms()
        gold = memory.IBM_ReadGoldFirst8BytesBySeat(check_seat)
        while not gold and tick_ms() - start_time < 10000:
            gold = memory.IBM_ReadGoldFirst8BytesBySeat(check_seat)
        return bool(gold and gold > 0)

    def GetKeyList(self, max_key_presses, key_list_100, key_list_10,
                   force_priority):
        def scan(key_list, occupied, threshold_check):
            cur_priority = self.maxPriority
            while cur_priority >= self.minPriority and \
                    (len(key_list) + occupied < max_key_presses
                     or (force_priority and cur_priority > 0)):
                champ_list = self.GetChampsAtPriority(cur_priority)
                while champ_list and \
                        (len(key_list) + occupied < max_key_presses
                         or (force_priority and cur_priority > 0)):
                    for champ_id in list(champ_list):
                        champion = self.champs[champ_id]
                        levels_required = champion.GetLevelsRequired(self.mode)
                        step = threshold_check(champ_list, champ_id, champion,
                                               levels_required, key_list,
                                               cur_priority)
                        if step == "break":
                            break
                    else:
                        continue
                    break
                cur_priority -= 1

        def check_100(champ_list, champ_id, champion, levels_required,
                      key_list, cur_priority):
            if levels_required > 0 and not champion.ReadSelectedInSeat():
                # Seat shows another champion (shared seat / formation swap
                # in flight): pressing now would level the wrong champion.
                champ_list.pop(champ_id, None)
                return None
            if levels_required >= 200:  # keep - needs more than one press
                key_list.append(champion.Key)
                champion.Current.pending_levels += 100
                if champion.GetPriority(self.mode, True) != cur_priority:
                    champ_list.pop(champ_id, None)
                if len(key_list) >= max_key_presses and \
                        (not force_priority or cur_priority <= 0):
                    return "break"
            elif levels_required >= 100:  # single press left
                key_list.append(champion.Key)
                champion.Current.pending_levels += 100
                champ_list.pop(champ_id, None)
                if len(key_list) >= max_key_presses and \
                        (not force_priority or cur_priority <= 0):
                    return "break"
            else:
                champ_list.pop(champ_id, None)
            return None

        modifier_amount = self.parent.modifierLevelUpAmount

        def check_10(champ_list, champ_id, champion, levels_required,
                     key_list, cur_priority):
            if levels_required > 0 and not champion.ReadSelectedInSeat():
                champ_list.pop(champ_id, None)
                return None
            # z1c being dynamic means a champion can appear here for x10
            # after being ignored for x100
            if levels_required >= 100:
                champ_list.pop(champ_id, None)
                return None
            occupied = k100_count
            if levels_required > modifier_amount:
                key_list.append(champion.Key)
                champion.Current.pending_levels += modifier_amount
                if champion.GetPriority(self.mode, True) != cur_priority:
                    champ_list.pop(champ_id, None)
                if len(key_list) + occupied >= max_key_presses and \
                        (not force_priority or cur_priority <= 0):
                    return "break"
            elif levels_required == modifier_amount:
                key_list.append(champion.Key)
                champion.Current.pending_levels += modifier_amount
                champ_list.pop(champ_id, None)
                if len(key_list) + occupied >= max_key_presses and \
                        (not force_priority or cur_priority <= 0):
                    return "break"
            else:
                # Remainder smaller than one modifier press: stop UNDER the
                # target rather than crossing it (a Feat-Guard cap breach
                # is permanent; a few missing levels are not). With the AHK
                # x25 this never triggered - targets were multiples of 25.
                # Exception: a champion still at level 0 is not placed at
                # all (e.g. Thellora with min=1) - placement outweighs
                # overshooting a tiny target, so cross like the AHK did.
                if (levels_required > 0
                        and champion.Current.level
                        + champion.Current.pending_levels <= 0):
                    key_list.append(champion.Key)
                    champion.Current.pending_levels += modifier_amount
                champ_list.pop(champ_id, None)
            return None

        scan(key_list_100, 0, check_100)
        # The modifier key cycle counts as a key press as well
        k100_count = len(key_list_100) + 1
        scan(key_list_10, k100_count, check_10)

    def GetChampsAtPriority(self, cur_priority):
        return {champ_id: True for champ_id, champ in self.champs.items()
                if champ.GetPriority(self.mode, True) == cur_priority
                and champ.CheckZ1cAllowed(self.mode)}

    def UpdateLevels(self):
        # Give the game a moment to apply the presses before re-reading:
        # a stale level read here makes the next iteration press again,
        # overshooting the target by a full press (seen under Wine, where
        # input-to-game latency is higher than with Windows PostMessage).
        pending = {champ_id: champ for champ_id, champ in self.champs.items()
                   if champ.Current.pending_levels}
        # Floor for cross-worklist reads: another worklist (z1 vs min) may
        # re-read this hero before these presses reach game memory and
        # would press again - the floor makes NeedsLevelling see at least
        # what has already been sent, for a few seconds.
        for champ in pending.values():
            expected = champ.Current.level + champ.Current.pending_levels
            if expected > champ.Current.optimistic_level:
                champ.Current.optimistic_level = expected
            champ.Current.optimistic_expiry = tick_ms() + 3000
        deadline = tick_ms() + 800
        while pending and tick_ms() < deadline:
            for champ_id in list(pending):
                champ = pending[champ_id]
                level = champ.ReadLevel()
                if level is not None and level >= (champ.Current.level
                                                   + champ.Current.pending_levels):
                    del pending[champ_id]
            if pending:
                precise_sleep(5)
        # Champions still pending after the timeout: assume the presses WILL
        # land rather than pressing again off a stale read. A lost press
        # self-heals (the next LevelFormation pass re-reads and retries);
        # an extra press overshoots past the Feat-Guard cap permanently.
        for champ_id, champ in pending.items():
            expected = champ.Current.level + champ.Current.pending_levels
            champ.Current.level = expected
            champ.Current.pending_levels = 0
            target = champ.Current.z1 if self.mode == "z1" else champ.Current.min
            if expected >= target:
                del self.champs[champ_id]
        for champ_id in list(self.champs):
            if champ_id in pending:
                continue  # handled optimistically above
            self.champs[champ_id].Current.pending_levels = 0
            if not self.champs[champ_id].NeedsLevelling(self.mode):
                del self.champs[champ_id]

    def IsPriorityDone(self):
        return not any(
            champion.GetPriority(self.mode, False) > 0
            and champion.CheckZ1cAllowed(self.mode)
            for champion in self.champs.values())

    def Done(self):
        return not self.champs

    def AddChamp(self, hero_id, suppress_by_id, start_of_run):
        heroes = self._ctx.heroes
        hero = heroes[hero_id]
        if hero is None or hero.Key is None:  # no data
            return 0
        if hero.NeedsLevelling(self.mode):
            # Can't check seat selection at the very start - game still loading
            if start_of_run or hero.ReadSelectedInSeat():
                if not (suppress_by_id and hero_id in suppress_by_id):
                    self.champs[hero_id] = hero
                    self.UpdatePriorityMinMax(hero.GetPriority(self.mode, False))
            return 1
        return 0

    def UpdatePriorityMinMax(self, current):
        if current < self.minPriority:
            self.minPriority = current
        if current > self.maxPriority:
            self.maxPriority = current
