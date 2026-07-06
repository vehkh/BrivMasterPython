"""Port of IC_BrivMaster_Heroes.ahk - the hero collection and the extended
classes for Briv (58), Ellywick (83), Tatyana (97) and Thellora (139)."""

from __future__ import annotations

from ..memory.gos import NULL_NODE
from .ctx import precise_sleep


class LevelData:
    """The Master/Current levelling data blob each hero carries."""

    __slots__ = ("min", "z1", "z1c", "priority", "priority_limit",
                 "pending_levels", "level", "casino_levelling",
                 "use_modifier_for_fast", "optimistic_level",
                 "optimistic_expiry")

    def __init__(self):
        self.min = 0
        self.z1 = 0
        self.z1c = False
        self.priority = 0
        self.priority_limit = None
        self.pending_levels = 0
        self.level = 0
        self.casino_levelling = 0
        # In-flight press floor: presses sent but possibly not yet visible
        # in memory (Wine input latency). NeedsLevelling honours this so a
        # later worklist does not re-press off a stale read.
        self.optimistic_level = 0
        self.optimistic_expiry = 0
        self.use_modifier_for_fast = False

    def clone(self):
        other = LevelData()
        for name in LevelData.__slots__:
            setattr(other, name, getattr(self, name))
        return other


class Hero:
    """IC_BrivMaster_Hero_Class - one champion."""

    def __init__(self, ctx, hero_id, hero_index):
        self._ctx = ctx
        self.ID = hero_id
        self.HeroIndex = hero_index
        self.Seat = self.ReadChampSeat()
        self.Key = ctx.input.get_key(f"F{self.Seat}") if self.Seat else None
        if self.Key is not None:
            self.Key.tag = self.Seat
        self.lastUpgradeLevel = self.GetLastUpgradeLevel()
        self.Master = LevelData()
        self.Current = LevelData()
        self.inM = self.inQ = self.inW = self.inE = self.inA = False

    # --- memory reads -----------------------------------------------------

    def _hero_node(self):
        if self.HeroIndex is None:  # unowned champion - all reads fail softly
            return NULL_NODE
        return self._ctx.memory.GameManager.game.gameInstances[0] \
            .Controller.userData.HeroHandler.heroes[self.HeroIndex]

    def ReadBenched(self):
        return self._hero_node().Benched.read()

    def ReadChampSeat(self):
        return self._hero_node().child("def").SeatID.read()

    def GetLastUpgradeLevel(self):
        """Highest level requirement among the hero's upgrades."""
        upgrades = self._hero_node().upgradeHandler.upgradesByUpgradeId
        size = upgrades.size()
        if size is None or size < 1 or size > 1000:
            return 0
        max_upgrade_level = 0
        for index in range(size):
            node = upgrades.dict_value_at(index)
            required = node.RequiredLevel.read() if node else None
            if required is not None and required != 9999:  # 9999 = not available
                max_upgrade_level = max(required, max_upgrade_level)
        return max_upgrade_level

    def ReadLevel(self):
        return self._hero_node().level.read()

    def _ultimates_bar(self):
        return self._ctx.memory.GameManager.game.gameInstances[0] \
            .Screen.uiController.ultimatesBar.ultimateItems

    def _find_ultimate_item(self):
        items = self._ultimates_bar()
        size = items.size()
        if size is None:
            return None
        for index in range(size):
            item = items[index]
            if item.hero.child("def").ID.read() == self.ID:
                return item
        return None

    def ReadUltimateCooldown(self):
        item = self._find_ultimate_item()
        return item.ultimateAttack.internalCooldownTimer.read() if item else None

    def _ultimate_fired(self, item):
        """The 'did it activate' check UseUltimate polls; overridden by Elly."""
        cooldown = item.ultimateAttack.internalCooldownTimer.read()
        return cooldown is not None and cooldown > 0

    def UseUltimate(self, max_retries=50, exit_once_queued=False):
        """Press the ultimate hotkey, retrying while the cooldown does not
        register. Returns the number of attempts, or None on failure."""
        item = self._find_ultimate_item()
        if item is None:
            return None
        hotkey = item.HotKey.read()
        if hotkey is None or hotkey == "":
            return None
        if not self._pre_ultimate(item):
            return None
        key = self._ctx.input.get_key(str(hotkey))
        if key is None:
            return None
        key.key_press()
        retry_count = 0
        while not self._ultimate_fired(item) and retry_count < max_retries:
            if item.ultimateAttack.queued.read():
                # queued - just wait on it (counts as 1/10th of a retry)
                retry_count += 1
                if exit_once_queued:
                    return retry_count
                precise_sleep(10)
            else:
                key.key_press()
                retry_count += 10
        return retry_count

    def _pre_ultimate(self, item):
        """Hook for subclasses needing setup before pressing (Elly)."""
        return True

    def ReadMaxHealth(self):
        return self._hero_node().lastMaxHealth.read()

    def ReadOverwhelm(self):
        return self._hero_node().overwhelm.read()

    def ReadFielded(self):
        """In the current on-field formation. Prefer not ReadBenched()."""
        slots = self._ctx.memory.GameManager.game.gameInstances[0] \
            .Controller.formation.slots
        size = slots.size()
        if size is None or size <= 0 or size > 14:
            return False
        for index in range(size):
            if slots[index].hero.child("def").ID.read() == self.ID:
                return True
        return False

    def ReadSelectedInSeat(self):
        """Selected in their seat - may not be placed / levelled."""
        if not self.Seat:
            return False
        return self.ID == self._ctx.memory.GameManager.game.gameInstances[0] \
            .Screen.uiController.bottomBar.heroPanel \
            .activeBoxes[self.Seat - 1].hero.child("def").ID.read()

    def ReadName(self):
        return self._hero_node().child("def").name.read()

    def ReadActiveGameInstanceID(self):
        return self._hero_node().ActiveGameInstanceId_k__BackingField.read()

    def HasCoreSpec(self, spec_id):
        """True if the hero has this specialisation saved in the modron core."""
        slot = self._ctx.memory.GetActiveModronFormationSaveSlot()
        if slot is None or slot < 0:
            return False
        specs = self._ctx.memory.GameManager.game.gameInstances[0] \
            .FormationSaveHandler.formationSavesV2[slot] \
            .Specializations.dict_value(self.ID)
        if specs is None:
            return False
        spec_list = specs.child("List")
        size = spec_list.size()
        if size is None or size <= 0 or size > 5:
            return False
        return any(spec_list[i].read() == spec_id for i in range(size))

    # --- general -------------------------------------------------------------

    def CanUseUltimate(self):
        cooldown = self.ReadUltimateCooldown()
        if cooldown:
            return cooldown <= 0 and not self.ReadBenched()
        return False

    # --- levelling -----------------------------------------------------------

    def Reset(self):
        self.Current = self.Master.clone()

    def ApplyLevelSettings(self, level_settings, saved_formation_champs):
        self.inM = self.ID in saved_formation_champs["M"]
        self.inQ = self.ID in saved_formation_champs["Q"]
        self.inW = self.ID in saved_formation_champs["W"]
        self.inE = self.ID in saved_formation_champs["E"]
        self.inA = self.inM or self.inQ or self.inW or self.inE
        champ = (level_settings or {}).get(str(self.ID)) \
            or (level_settings or {}).get(self.ID)
        master = self.Master = LevelData()
        if champ:
            master.min = champ.get("min", 0) or 0
            master.z1 = champ.get("z1", 0) or 0
            master.priority = champ.get("prio", 0) or 0
            master.priority_limit = champ.get("priolimit") or None
        # else: defaults (level 0 - never level unconfigured champions)
        self.Current = master.clone()

    def GetTargetLevel(self, mode="min"):
        if mode == "z1":
            return self.Current.z1
        if mode == "min":
            return self.Current.min
        return 0

    def NeedsLevelling(self, mode="min"):
        from .ctx import tick_ms
        level = self.ReadLevel()
        level = level if level is not None else 0
        if (self.Current.optimistic_level > level
                and tick_ms() < self.Current.optimistic_expiry):
            level = self.Current.optimistic_level
        self.Current.level = level
        if mode == "z1":
            return self.Current.level < self.Current.z1
        if mode == "min":
            return self.Current.level < self.Current.min
        return False

    def GetPriority(self, mode="min", include_pending=True):
        if mode != "z1":  # priority settings apply to z1 only
            return 0
        expected = self.Current.level + (self.Current.pending_levels
                                         if include_pending else 0)
        if self.Current.priority_limit and expected >= self.Current.priority_limit:
            return 0
        return self.Current.priority

    def CheckZ1cAllowed(self, mode="min"):
        """z1c = 'zone 1 complete' levelling condition."""
        if mode == "z1" and self.Current.z1c:
            memory = self._ctx.memory
            zone = memory.ReadCurrentZone()
            return (zone is not None and zone > 1) \
                or memory.ReadQuestRemaining() == 0
        return True

    def GetLevelsRequired(self, mode="min"):
        """Always includes pending. Does not refresh Current.level."""
        expected = self.Current.level + self.Current.pending_levels
        if mode == "z1":
            return max(self.Current.z1 - expected, 0)
        if mode == "min":
            return max(self.Current.min - expected, 0)
        return 0

    def SetSoftCap(self):
        self.Current.min = self.lastUpgradeLevel

    def OverrideLevel(self, mode, level):
        setattr(self.Current, {"min": "min", "z1": "z1", "z1c": "z1c"}[mode], level)

    def RaisePriorityForFrontRow(self):
        if self.Current.priority <= 0:
            self.Current.priority = 1
            self.Current.priority_limit = 100


class Briv(Hero):
    def __init__(self, ctx, hero_id, hero_index):
        super().__init__(ctx, hero_id, hero_index)
        self.MEMORY_SB_ADDRESS = None

    def Reset(self):
        super().Reset()
        self.MEMORY_SB_ADDRESS = None  # stops rubbish reads pre-InitFastSB()

    def _sb_node(self):
        return self._ctx.memory.GameManager.game.gameInstances[0] \
            .Controller.userData.StatHandler.BrivSteelbonesStacks

    def ReadSBStacks(self):
        return self._sb_node().read()

    def FastReadSBStacks(self):
        """InitFastSB() must have been called first."""
        return self._ctx.memory.mem.read(self.MEMORY_SB_ADDRESS, "Int")

    def ReadHasteStacks(self):
        return self._ctx.memory.GameManager.game.gameInstances[0] \
            .Controller.userData.StatHandler.BrivSprintStacks.read()

    def InitFastSB(self):
        """Pin the Steelbones stat address for spam-reading while stacking."""
        self.MEMORY_SB_ADDRESS = self._sb_node().resolve_address()


class Thellora(Hero):
    STAT_RUSH_TRIGGERED = "thellora_plateaus_of_unicorn_run_has_triggered"
    STAT_AREA_CHARGES = "thellora_plateaus_of_unicorn_run_areas"
    # Default index taken from v645; game-data dependent, cache re-seeks on miss
    _cached_charges_index = 421

    def __init__(self, ctx, hero_id, hero_index):
        super().__init__(ctx, hero_id, hero_index)
        self.rushCap = ctx.memory.IBM_GetCurrentCampaignFavourExponent()
        self.rushNext = 0  # expected next rush zone when in recovery

    def Reset(self):
        super().Reset()
        self.rushNext = 0

    def _server_stats(self):
        return self._ctx.memory.GameManager.game.gameInstances[0] \
            .Controller.userData.StatHandler.ServerStats

    def ReadRushTriggered(self):
        node = self._server_stats().dict_value(self.STAT_RUSH_TRIGGERED)
        return node is not None and node.read() == 1

    def ReadRushAreaCharges(self):
        """Charges stat via a cached dictionary index (the stat dict has ~570
        entries; a scan is slow, and the index rarely moves)."""
        stats = self._server_stats()
        cached = Thellora._cached_charges_index
        if stats.dict_key_at(cached).read() == self.STAT_AREA_CHARGES:
            value = stats.dict_value_at(cached).read()
            return float(value) if value is not None else 0
        size = stats.size()
        if size is None or size < 0 or size > 5000:
            return 0
        # Search forward from the cache first (new stats push it down), then back
        search_order = list(range(cached + 1, size)) + list(range(cached - 1, -1, -1))
        for index in search_order:
            if stats.dict_key_at(index).read() == self.STAT_AREA_CHARGES:
                self._ctx.log(f"ReadRushAreaCharges() CACHE MISS cachedIndex="
                              f"[{cached}] index=[{index}] please report this message")
                Thellora._cached_charges_index = index
                value = stats.dict_value_at(index).read()
                return float(value) if value is not None else 0
        return 0

    def UpdateRushTarget(self):
        """True if the favour read was available and the cap changed."""
        cap = self._ctx.memory.IBM_GetCurrentCampaignFavourExponent()
        if cap and self.rushCap != cap:
            self.rushCap = cap
            return True
        return False

    def GetCappedRushCharges(self):
        cap = self.rushCap if self.rushCap is not None else 0
        return min(self.ReadRushAreaCharges(), cap)


class Ellywick(Hero):
    EFFECT_KEY_DoMT = "ellywick_deck_of_many_things"
    EFFECT_KEY_CotF = "ellywick_call_of_the_feywild"

    def __init__(self, ctx, hero_id, hero_index):
        super().__init__(ctx, hero_id, hero_index)
        self.EFFECT_HANDLER_CARDS = None
        self.MEMORY_COTF_ULT_ACTIVE_ADDRESS = None

    def Reset(self):
        super().Reset()
        self.EFFECT_HANDLER_CARDS = None
        self.MEMORY_COTF_ULT_ACTIVE_ADDRESS = None

    def _effect_key_handler(self):
        return self._hero_node().effects.effectKeysByHashedKeyName

    def _find_parent_handler(self, effect_key):
        handler = self._effect_key_handler()
        size = handler.size()
        if size is None:
            return None
        for index in range(size):
            value = handler.dict_value_at(index)
            if value is None:
                continue
            parent = value.child("List")[0].parentEffectKeyHandler
            if parent.child("def").Key.read() == effect_key:
                return parent
        return None

    def InitDoMTHandler(self):
        parent = self._find_parent_handler(self.EFFECT_KEY_DoMT)
        if parent is not None:
            # Pinned to a raw address - goes stale on reset/restart, as in AHK
            self.EFFECT_HANDLER_CARDS = parent.activeEffectHandlers[0].rebase()

    def InitCotFUltActive(self):
        if self.MEMORY_COTF_ULT_ACTIVE_ADDRESS:
            return True
        parent = self._find_parent_handler(self.EFFECT_KEY_CotF)
        if parent is not None:
            self.MEMORY_COTF_ULT_ACTIVE_ADDRESS = \
                parent.activeEffectHandlers[0].IsUltimateActive.resolve_address()
            return self.MEMORY_COTF_ULT_ACTIVE_ADDRESS is not None
        return False

    def ReadEllywickUltimateActive(self):
        if self.InitCotFUltActive():
            return self._ctx.memory.mem.read(
                self.MEMORY_COTF_ULT_ACTIVE_ADDRESS, "Char")
        return None

    def _pre_ultimate(self, item):
        # Elly's UseUltimate override tracks IsUltimateActive directly - the
        # ultimates-bar cooldown is not accurate enough before a DM reset.
        return self.InitCotFUltActive()

    def _ultimate_fired(self, item):
        return self.ReadEllywickUltimateActive() == 1

    def GetNumCardsOfType(self, card_type):
        """3 is Gem, 5 is Flames."""
        if self.EFFECT_HANDLER_CARDS is None:
            return 0
        cards = self.EFFECT_HANDLER_CARDS.cardsInHand
        size = cards.size()
        if size is None:
            return 0
        return sum(1 for i in range(size)
                   if cards[i].CardType.read() == card_type)

    def ReadNumCards(self):
        if self.EFFECT_HANDLER_CARDS is None:
            return None
        return self.EFFECT_HANDLER_CARDS.cardsInHand.size()

    def GetNumGemCards(self):
        return self.GetNumCardsOfType(3)

    def GetNumFlamesCards(self):
        return self.GetNumCardsOfType(5)


class Tatyana(Hero):
    EFFECT_KEY_FAF = "tatyana_find_a_feast"

    def GetFindAFeastReturnTimerAddress(self):
        """Address of the Find a Feast await-return timer (a Double); the read
        itself stays raw as it is part of the stacking loop."""
        handler = self._hero_node().effects.effectKeysByHashedKeyName
        size = handler.size()
        if size is not None:
            for index in range(size):
                value = handler.dict_value_at(index)
                if value is None:
                    continue
                parent = value.child("List")[0].parentEffectKeyHandler
                if parent.child("def").Key.read() == self.EFFECT_KEY_FAF:
                    return parent.activeEffectHandlers[0] \
                        .awaitReturnTimer.t.resolve_address()
        self._ctx.log("Tatyana: unable to find Find a Feast handler")
        return None


_EXTENDED = {58: Briv, 83: Ellywick, 97: Tatyana, 139: Thellora}


class Heroes:
    """g_Heroes - creates hero objects on first access, like the AHK __Get."""

    def __init__(self, ctx):
        self._ctx = ctx
        self._heroes = {}
        self.initialised = False
        self.IDToIndexMap = {}
        self.Init()

    def Init(self):
        if self.initialised:
            return True
        self.initialised = self.GenerateHeroIDtoHeroIndexMap()
        return self.initialised

    def GenerateHeroIDtoHeroIndexMap(self):
        heroes = self._ctx.memory.GameManager.game.gameInstances[0] \
            .Controller.userData.HeroHandler.heroes
        size = heroes.size()
        if size is None or size <= 0 or size >= 500:
            return False
        id_map = {}
        for index in range(size):
            hero_id = heroes[index].child("def").ID.read()
            if hero_id is not None:
                id_map[hero_id] = index
        self.IDToIndexMap = id_map
        return True

    def __getitem__(self, hero_id):
        if hero_id not in self._heroes:
            if not self.Init():
                return None
            cls = _EXTENDED.get(hero_id, Hero)
            self._heroes[hero_id] = cls(self._ctx, hero_id,
                                        self.IDToIndexMap.get(hero_id))
        return self._heroes[hero_id]

    def has(self, hero_id):
        """AHK hasKey - true only for already-created hero objects."""
        return hero_id in self._heroes

    def InM(self, hero_id):
        return self._heroes[hero_id].inM if hero_id in self._heroes else False

    def InA(self, hero_id):
        return self._heroes[hero_id].inA if hero_id in self._heroes else False

    def ResetAll(self):
        for hero in self._heroes.values():
            hero.Reset()

    def created(self):
        return dict(self._heroes)
