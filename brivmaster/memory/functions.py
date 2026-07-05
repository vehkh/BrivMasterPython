"""Port of IC_BrivMaster_MemoryFunctions_Class (+ the _IBM_MM manager glue)
from IC_BrivMaster_Memory.ahk.

Method names are kept identical to the AHK original so the rest of the port
(RouteMaster, LevelManager, ...) stays mechanically comparable against the
reference implementation. Failed reads return None (the AHK '""').
"""

from __future__ import annotations

import json
import os

from . import backend
from .gos import GosNode, GosTemplate, RootContext, quad_to_exponent
from .imports_parser import parse_import_file

MODULE_NAME = "mono-2.0-bdwgc.dll"

_ROOTS = (
    # (json key,        import file,                      root alias inside the import)
    ("IdleGameManager", "IC_IdleGameManager_Import.ahk", "IdleGameManager"),
    ("GameSettings",    "IC_GameSettings_Import.ahk",    "CrusadersGame.GameSettings"),
    ("EngineSettings",  "IC_EngineSettings_Import.ahk",  "UnityGameEngine.Core.EngineSettings"),
)


class OffsetsError(Exception):
    pass


class MemoryFunctions:
    def __init__(self, offsets_file):
        """offsets_file: path to IC_Offsets.json; the generated import files
        are expected alongside it, as in the AHK layout."""
        self.offsets_file = offsets_file
        offsets_dir = os.path.dirname(os.path.abspath(offsets_file))
        try:
            with open(offsets_file, "r", encoding="utf-8-sig") as f:
                pointers = json.load(f)
        except (OSError, ValueError) as err:
            raise OffsetsError(
                f"Offset data not found or invalid ({offsets_file}): {err}. "
                "Review the BM Game tab / download offsets.") from err

        # All the version information is stored in the pointer JSON file
        self.Versions = {key: pointers.get(key) for key in (
            "Import_Revision", "Import_Version_Major", "Import_Version_Minor",
            "Platform", "Pointer_Revision", "Pointer_Version_Major",
            "Pointer_Version_Minor")}

        self.mem = None
        self._roots = {}       # name -> (template, context, module_address)
        self.import_warnings = {}

        for name, import_file, alias in _ROOTS:
            pointer_def = pointers.get(name)
            if pointer_def is None:
                raise OffsetsError(f"Pointer '{name}' missing from {offsets_file}")
            static_offset = int(pointer_def.get("staticOffset") or 0)
            module_address = int(pointer_def["moduleAddress"])
            structure_offsets = [int(o) for o in pointer_def["moduleOffset"]]
            template = GosTemplate(name, structure_offsets)
            import_path = os.path.join(offsets_dir, import_file)
            if os.path.isfile(import_path):
                warnings = parse_import_file(import_path, template, alias,
                                             static_offset)
                if warnings:
                    self.import_warnings[name] = warnings
            else:
                self.import_warnings[name] = [f"import file missing: {import_path}"]
            self._roots[name] = (template, RootContext(), module_address)

        # Formation caches (as in the AHK class)
        self.FavoriteFormations = {}
        self.LastFormationSavesVersion = {}
        self.SlotFormations = {}

    # --- attachment (the _IBM_MM part) -----------------------------------

    def OpenProcessReader(self, exe_name="IdleDragons.exe", pid=None):
        """Attach to the game and refresh the pointer base addresses.
        Returns True when attached."""
        if self.mem is not None:
            self.mem.close()
        self.mem = backend.attach_to(exe_name, pid)
        return self.RefreshBases()

    def AttachToReadyInstance(self, exe_name="IdleDragons.exe", wait_s=45):
        """Attach preferring a fully-loaded game instance. There can be
        several game processes (relay restarts hold a second instance at the
        login screen) and the farm restarts the game regularly - scan all
        candidates until one is in-game, or the wait budget runs out.
        Returns 'ready', 'attached' (fallback) or None."""
        import time
        deadline = time.monotonic() + wait_s
        attached = False
        while True:
            for pid in backend.native_backend().find_pids(exe_name):
                if not self.OpenProcessReader(exe_name, pid):
                    continue
                attached = True
                if self.ReadGameStarted() and self.ReadUserIsInited():
                    return "ready"
            if time.monotonic() >= deadline:
                return "attached" if attached else None
            time.sleep(2)

    def RefreshBases(self):
        """Re-resolve the module base (call after a game restart)."""
        module_base = self.mem.module_base(MODULE_NAME) if self.mem else -1
        for template, ctx, module_address in self._roots.values():
            if module_base and module_base > 0:
                ctx.mem = self.mem
                ctx.base_address = module_base + module_address
            else:
                ctx.mem = None
                ctx.base_address = None
        return module_base is not None and module_base > 0

    @property
    def IsAttached(self):
        return self.mem is not None and self.mem.attached and self.mem.is_running()

    def _root(self, name):
        template, ctx, _ = self._roots[name]
        return GosNode(template, ctx, template.rel_offsets)

    def module_offset_for(self, name):
        """The module-relative offset of a pointer root (needed by the relay
        helper, which resolves a fresh module base in the new process)."""
        return self._roots[name][2]

    @property
    def GameManager(self):
        return self._root("IdleGameManager")

    @property
    def GameSettings(self):
        return self._root("GameSettings")

    @property
    def EngineSettings(self):
        return self._root("EngineSettings")

    # --- version info -----------------------------------------------------

    def GetImportsVersion(self):
        return (f"{self.Versions['Import_Version_Major']}"
                f"{self.Versions['Import_Version_Minor']} "
                f"{self.Versions['Import_Revision']}")  # '639 A', '639.1 B'

    # --- simple reads -------------------------------------------------------

    def ReadBaseGameVersion(self):
        return self.GameSettings.MobileClientVersion.read()

    def ReadGameStarted(self):
        return self.GameManager.game.gameStarted.read()

    def ReadResetting(self):
        return self._instance0().ResetHandler.Resetting.read()

    def ReadTransitioning(self):
        return self._instance0().Controller.areaTransitioner.IsTransitioning_k__BackingField.read()

    def ReadTransitionDirection(self):
        # 0 = static (instant), 1 = right, 2 = left, 3 = JumpDown, 4 = FallDown
        return self._instance0().Controller.areaTransitioner.transitionDirection.read()

    def ReadFormationTransitionDir(self):
        # 0 = OnFromLeft, 1 = OnFromRight, 2 = OnFromTop,
        # 3 = OffToLeft, 4 = OffToRight, 5 = OffToBottom
        return self._instance0().Controller.formation.transitionDir.read()

    def ReadAreaActive(self):
        return self._instance0().Controller.area.Active.read()

    def ReadUserIsInited(self):
        return self._instance0().Controller.userData.inited.read()

    def ReadIsSplashVideoActive(self):
        return self.GameManager.game.loadingScreen.SplashScreen.IsActive_k__BackingField.read()

    def ReadClickLevel(self):
        return self._instance0().ClickLevel.read()

    def ReadUserID(self):
        return self.GameSettings.UserID.read()

    def ReadUserHash(self):
        return self.GameSettings.Hash.read()

    def ReadInstanceID(self):
        return self.GameSettings._instance.instanceID.read()

    def ReadWebRoot(self):
        return self.EngineSettings.WebRoot.read()

    def ReadPlatform(self):
        return self.GameSettings.Platform.read()

    def ReadGems(self):
        return self._instance0().Controller.userData.redRubies.read()

    def ReadCurrentObjID(self):
        return self._instance0().ActiveCampaignData.currentObjective.ID.read()

    def ReadQuestRemaining(self):
        return self._instance0().ActiveCampaignData.currentArea.QuestRemaining.read()

    def ReadCurrentZone(self):
        return self._instance0().ActiveCampaignData.currentAreaID.read()

    def ReadHighestZone(self):
        return self._instance0().ActiveCampaignData.highestAvailableAreaID.read()

    def ReadActiveGameInstance(self):
        return self._instance0().Controller.userData.ActiveUserGameInstance.read()

    def _instance0(self):
        return self.GameManager.game.gameInstances[0]

    # --- modron -------------------------------------------------------------

    def GetActiveModronFormation(self):
        """Formation array of the formation used in the currently active modron."""
        slot = self.GetActiveModronFormationSaveSlot()
        if slot is not None and slot >= 0:
            return self.GetFormationSaveBySlot(slot)
        return None

    def GetActiveModronFormationSaveSlot(self):
        favorite = "M"  # (M)odron
        version = self._instance0().FormationSaveHandler.formationSavesV2.version()
        if (self.FavoriteFormations.get(favorite) is not None
                and version == self.LastFormationSavesVersion.get(favorite)):
            return self.FavoriteFormations[favorite]
        save_id = self.GetModronFormationsSaveIDByFormationCampaignID(
            self.ReadFormationCampaignID())
        saves_size = self.ReadFormationSavesSize()
        if saves_size is None or saves_size <= 0 or saves_size > 500:
            return None
        for slot in range(saves_size):
            if self.ReadFormationSaveIDBySlot(slot) == save_id:
                return slot
        return -1

    def GetModronFormationsSaveIDByFormationCampaignID(self, formation_campaign_id):
        modron_slot = self.GetCurrentModronSaveSlot()
        if modron_slot is None or formation_campaign_id is None:
            return None
        node = self._instance0().Controller.userData.ModronHandler \
            .modronSaves[modron_slot].FormationSaves.dict_value(formation_campaign_id)
        return node.read() if node is not None else None

    def GetCurrentModronSaveSlot(self):
        active_instance = self.ReadActiveGameInstance()
        modron_saves = self._instance0().Controller.userData.ModronHandler.modronSaves
        size = modron_saves.size()
        if size is None or size <= 0 or size > 20:
            return None
        for index in range(size):
            if modron_saves[index].InstanceID.read() == active_instance:
                return index
        return None

    def GetModronResetArea(self):
        return self.GetCoreTargetAreaByInstance(self.ReadActiveGameInstance())

    def GetCoreTargetAreaByInstance(self, instance_id=1):
        modron_saves = self._instance0().Controller.userData.ModronHandler.modronSaves
        size = modron_saves.size()
        if size is None or size <= 0 or size > 50000:
            return None
        for index in range(size):
            if modron_saves[index].InstanceID.read() == instance_id:
                return modron_saves[index].targetArea.read()
        return -1

    def _modron_toggle(self, index):
        slot = self.GetCurrentModronSaveSlot()
        if slot is None:
            return None
        node = self._instance0().Controller.userData.ModronHandler \
            .modronSaves[slot].TogglePreferences.dict_value(index)
        return node.read() if node is not None else None

    def ReadModronAutoFormation(self):
        return self._modron_toggle(0)

    def ReadModronAutoReset(self):
        return self._modron_toggle(1)

    def ReadModronAutoBuffs(self):
        return self._modron_toggle(2)

    # --- combat / area --------------------------------------------------------

    def ReadNumAttackingMonstersReached(self):
        return self._instance0().Controller.formation.numAttackingMonstersReached.read()

    def ReadNumRangedAttackingMonsters(self):
        return self._instance0().Controller.formation.numRangedAttackingMonsters.read()

    def ReadActiveMonstersCount(self):
        return self._instance0().Controller.area.activeMonsters.size()

    # --- formations -------------------------------------------------------------

    def ReadFormationCampaignID(self):
        return self._instance0().FormationSaveHandler.FormationCampaignID.read()

    def ReadFormationSaveIDBySlot(self, slot=0):
        return self._instance0().FormationSaveHandler.formationSavesV2[slot].SaveID.read()

    def ReadFormationSavesSize(self):
        return self._instance0().FormationSaveHandler.formationSavesV2.size()

    def ReadFormationFavoriteIDBySlot(self, slot=0):
        # 0 = not a favorite, 1 = save slot 1 (Q), 2 = (W), 3 = (E)
        return self._instance0().FormationSaveHandler.formationSavesV2[slot].Favorite.read()

    def GetFormationSaveBySlot(self, slot=0, ignore_empty_slots=False):
        """Champion IDs saved in a formation slot; -1 marks an empty seat."""
        save = self._instance0().FormationSaveHandler.formationSavesV2[slot]
        current_version = save.Formation.version()
        cache_key = f"slot{slot}"
        if (current_version is not None
                and current_version == self.LastFormationSavesVersion.get(cache_key)
                and self.SlotFormations.get(cache_key) is not None):
            formation = self.SlotFormations[cache_key]
            if ignore_empty_slots:
                return [champ for champ in formation if champ != -1]
            return list(formation)
        size = save.Formation.size()
        if size is None or size <= 0 or size > 20:
            return None
        formation = []
        for index in range(size):
            champ_id = save.Formation[index].read()
            if champ_id is None:
                return None
            formation.append(champ_id)
        self.LastFormationSavesVersion[cache_key] = current_version
        self.SlotFormations[cache_key] = list(formation)
        if ignore_empty_slots:
            return [champ for champ in formation if champ != -1]
        return formation

    def GetSavedFormationSlotByFavorite(self, favorite=1):
        saves_size = self.ReadFormationSavesSize()
        if saves_size is None or saves_size <= 0 or saves_size > 500:
            return None
        for slot in range(saves_size):
            if self.ReadFormationFavoriteIDBySlot(slot) == favorite:
                return slot
        return None

    def ReadMostRecentFormationFavorite(self):
        # Note: updates even if the formation swap fails - not reliable
        return self._instance0().FormationSaveHandler.mostRecentFormation.Favorite.read()

    def GetFormationByFavorite(self, favorite=0):
        version = self._instance0().FormationSaveHandler.formationSavesV2.version()
        if (self.FavoriteFormations.get(favorite) is not None
                and version == self.LastFormationSavesVersion.get(favorite)):
            return self.FavoriteFormations[favorite]
        slot = self.GetSavedFormationSlotByFavorite(favorite)
        if slot is None:
            return None
        formation = self.GetFormationSaveBySlot(slot)
        self.FavoriteFormations[favorite] = list(formation) if formation else formation
        self.LastFormationSavesVersion[favorite] = version
        return formation

    def GetCurrentFormation(self):
        """Current on-field formation; empty seats are -1."""
        size = self._instance0().Controller.formation.slots.size()
        if size is None or size <= 0 or size > 14:
            return None
        formation = []
        for slot in range(size):
            hero_id = self.ReadChampIDBySlot(slot)
            formation.append(hero_id if hero_id is not None and hero_id > 0 else -1)
        return formation

    def ReadChampIDBySlot(self, slot=0):
        # 'def' is a Python keyword, hence the explicit child() accessor
        return self._instance0().Controller.formation.slots[slot] \
            .hero.child("def").ID.read()

    # --- chests / patron / feats ----------------------------------------------

    def ReadChestCountByID(self, chest_id):
        node = self._instance0().Controller.userData.ChestHandler \
            .chestCounts.dict_value(chest_id)
        return node.read() if node is not None else None

    def ReadPatronID(self):
        patron_field = self._instance0().PatronHandler.ActivePatron_k__BackingField
        pointer = patron_field.read()
        if pointer is None or pointer == 0:
            return pointer
        patron_id = patron_field.ID.read()
        if patron_id is None or patron_id < 0 or patron_id > 100:
            return None
        return patron_id

    def HeroHasFeatSavedInFormation(self, hero_id=58, feat_id=2131, formation_slot=1):
        feats = self._instance0().FormationSaveHandler \
            .formationSavesV2[formation_slot].Feats.dict_value(hero_id)
        if feats is None:
            return None
        feat_list = feats.child("List")
        size = feat_list.size()
        if size is None:
            return None
        if size <= 0 or size > 10:
            return False
        for index in range(size):
            if feat_list[index].read() == feat_id:
                return True
        return False

    def HeroHasAnyFeatsSavedInFormation(self, hero_id=58, formation_slot=1):
        feats = self._instance0().FormationSaveHandler \
            .formationSavesV2[formation_slot].Feats.dict_value(hero_id)
        if feats is None:
            return None
        size = feats.child("List").size()
        if size is None:
            return None
        return 0 < size <= 10

    def GetHeroFeats(self, hero_id):
        if hero_id is None or hero_id < 1:
            return None
        slots = self._instance0().Controller.userData.FeatHandler \
            .heroFeatSlots.dict_value(hero_id)
        if slots is None:
            return None
        feat_list = slots.child("List")
        size = feat_list.size()
        if size is None or size < 0 or size > 10:
            return None
        return [feat_list[index].ID.read() for index in range(size)]

    # --- IBM helpers -------------------------------------------------------------

    def IBM_GetWebRootFriendly(self):
        web_root = self.ReadWebRoot()
        return web_root if web_root else "Unable to read WebRoot"

    def IBM_ReadGameVersionMinor(self):
        # If the game is 636.2, return '.2'. Often empty.
        return self.GameSettings.VersionPostFix.read()

    def IBM_IsBuffActive(self, buff_name):
        """Is a (Gem Hunter) potion buff active."""
        buffs = self._instance0().BuffHandler.activeBuffs
        size = buffs.size()
        if size is None or size < 0 or size > 1000:
            return False
        for index in range(size):
            if buffs[index].Name.read() == buff_name:
                return True
        return False

    def IBM_ReadBaseGameSpeed(self):
        """Game speed without the area transition multiplier Diana applies."""
        multiplier = self._instance0().areaTransitionTimeScaleMultiplier.read()
        if not multiplier:
            multiplier = 1  # so we don't divide by zero
        time_scale = self.GameManager.TimeScale.read()
        if time_scale is None:
            return None
        return time_scale / multiplier

    def IBM_ReadCurrentZoneMonsterHealthExponent(self):
        """e.g. 85.90308999 for 8e85."""
        parts = self._instance0().ActiveCampaignData.currentArea.Health.read_quad_parts()
        if parts is None:
            return None
        return quad_to_exponent(*parts)

    def IBM_GetCurrentCampaignFavourExponent(self):
        """Current favour as a base-10 exponent, processed from the raw IEEE 754
        double to avoid precision limits (e.g. 306.6 for 4e306)."""
        currency_id = self._instance0().ActiveCampaignData.AdventureDef \
            ._campaignDef.ResetCurrencyID.read()
        if currency_id is None:
            return None
        reset_defs = self._instance0().Controller.userData \
            .ResetCurrencyHandler.ResetCurrencyDefs
        cached = getattr(self, "_favour_index_cache", None)
        index = None
        if cached is not None and reset_defs[cached].ID.read() == currency_id:
            index = cached
        else:
            self._favour_index_cache = None
            size = reset_defs.size()
            if size is None or size < 0 or size > 500:
                return None
            for i in range(size):
                if reset_defs[i].ID.read() == currency_id:
                    index = self._favour_index_cache = i
                    break
        if index is None:
            return None
        bits = reset_defs[index].CurrentAmount.read("Int64")
        if bits is None:
            return None
        bits &= 0xFFFFFFFFFFFFFFFF
        sign = -1 if (bits >> 63) else 1
        exponent = ((bits & 0x7FF0000000000000) >> 52) - 1023  # IEEE 754 double
        mantissa = (bits & 0x000FFFFFFFFFFFFF) / 0x000FFFFFFFFFFFFF
        import math
        try:
            favour_exp = exponent * math.log10(2) + math.log10(sign * (1 + mantissa))
        except ValueError:
            return None
        return math.floor(favour_exp)

    def IBM_ReadAreaMonsterDamageMultiplier(self):
        return self._instance0().ActiveCampaignData.currentArea.AreaDef \
            .MonsterDamageMultiplier.read()

    def IBM_ReadCampaignMonsterDamageMultiplier(self):
        return self._instance0().ActiveCampaignData.currentRules.MonsterDamageModifier.read()

    def IBM_ReadMonsterBaseDPS(self):
        return self._instance0().ActiveCampaignData.currentRules.monsterbaseStats.BaseDPS.read()

    def IBM_ReadDPSGrowthCurve(self):
        curve = self._instance0().ActiveCampaignData.currentRules \
            .monsterbaseStats.DPSGrowthRateCurve
        size = curve.size()
        if size is None:
            return None
        data = []
        for index in range(size):
            level = curve.dict_key_at(index).read()
            if level is None:
                continue
            value_node = curve.dict_value(level)
            data.append({"level": level,
                         "value": value_node.read() if value_node else None})
        return data

    def IBM_ReadGoldFirst8BytesBySeat(self, seat):
        return self._instance0().Screen.uiController.bottomBar.heroPanel \
            .activeBoxes[seat - 1].lastGold.read("Int64")

    def IBM_IsCurrentFormationEmpty(self):
        slots = self._instance0().Controller.formation.slots
        size = slots.size()
        if size is None or size <= 0 or size > 12:
            return True  # assume an invalid read means empty
        for slot in range(size):
            hero_id = self.ReadChampIDBySlot(slot)
            if hero_id is not None and hero_id > 0:
                return False
        return True

    def IsCurrentFormationFull(self):
        slots = self._instance0().Controller.formation.slots
        size = slots.size()
        if size is None:
            return False
        for slot in range(size):
            if self.ReadChampIDBySlot(slot) is None:
                return False
        return True

    def IBM_ClickDamageLevelAmount(self):
        # Base amount set per levelling selection: always 1/10/25/100
        return self._instance0().Screen.uiController.bottomBar.heroPanel \
            .clickDamageBox.levelUpAmount.read()

    def IBM_GetFrontColumnSize(self):
        slots = self._instance0().Controller.formation.slots
        size = slots.size()
        if size is None:
            return None
        front_count = 0
        for slot in range(size):
            if slots[slot].SlotDef.Column.read() == 0:
                front_count += 1
        return front_count

    def IBM_ReadIsInstanceDirty(self):
        # Dirty = unsaved data
        return self._instance0().isDirty.read()

    def IBM_ReadCurrentSave(self):
        # Pointer to the current save; non-zero whilst the game is saving
        return self._instance0().Controller.userData.SaveHandler.currentSave.read()

    def IBM_ReadIsGameUserLoaded(self):
        return self.GameManager.game.gameUser.Loaded.read()

    def IBM_ReadClickLevelUpAllowed(self):
        value = self._instance0().Screen.uiController.bottomBar.heroPanel \
            .clickDamageBox.maxLevelUpAllowed.read()
        return 1 if value is None else value

    def IBM_ReadLastSave(self):
        return self._instance0().Controller.userData.SaveHandler.lastUserDataSaveTime.read()

    def IBM_GetCurrentFormationChampions(self):
        """Champions on the field without positioning data: {hero_id: True}."""
        size = self._instance0().Controller.formation.slots.size()
        if size is None or size <= 0 or size > 12:
            return None
        champs = {}
        for slot in range(size):
            hero_id = self.ReadChampIDBySlot(slot)
            if hero_id is not None and hero_id > 0:
                champs[hero_id] = True
        return champs

    def IBM_GetFormationFieldFamiliarCountBySlot(self, slot):
        familiars = self._instance0().FormationSaveHandler \
            .formationSavesV2[slot].Familiars.dict_value("Clicks")
        if familiars is None:
            return None
        familiar_list = familiars.child("List")
        size = familiar_list.size()
        if size is None or size < 0 or size > 10:
            return None
        count = 0
        for index in range(size):
            # Negative numbers store gaps in the familiar layout
            value = familiar_list[index].read()
            if value is not None and value >= 0:
                count += 1
        return count

    def IBM_GetActiveGameInstanceID(self):
        # Instance ID 1-4, NOT the index in the gameInstances collection
        return self._instance0().InstanceUserData_k__BackingField.InstanceId.read()

    def ReadOfflineTime(self):
        return self._instance0().OfflineHandler.OfflineTimeRequested_k__BackingField.read()

    def ReadOfflineDone(self):
        handler_state = self._instance0().OfflineHandler.CurrentState_k__BackingField.read()
        stop_reason = self._instance0().OfflineHandler.CurrentStopReason_k__BackingField.read()
        return handler_state == 0 and stop_reason is not None

    def ReadResetsTotal(self):
        return self._instance0().Controller.userData.StatHandler.Resets.read()

    def ReadResetsCount(self):
        return self._instance0().ResetsSinceLastManual.read()

    def ReadAutoProgressToggled(self):
        return self._instance0().Screen.uiController.topBar.objectiveProgressBox \
            .areaBar.autoProgressButton.toggled.read()

    def ReadWelcomeBackActive(self):
        return self._instance0().Screen.uiController.notificationManager \
            .notificationDisplay.welcomeBackNotification.Active.read()
