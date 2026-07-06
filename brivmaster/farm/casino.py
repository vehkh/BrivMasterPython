"""Port of IC_BrivMaster_Functions.ahk (minus the Logger):
EllywickCasino, DialogSwatter, DianaCheese."""

from __future__ import annotations

import datetime
import threading
import time

from .ctx import precise_sleep, sleep_offset, tick_ms


class EllywickCasino:
    TIMEOUT_BASE = 100000   # ms of game time: 10s at x10 speed
    ULT_DELAY = 3750        # half of the 7500ms duration of Elly's ult

    def __init__(self, ctx):
        self._ctx = ctx
        if ctx.heroes[139].inM:
            # Ghost levelling only applies with Thellora in M (Briv present
            # for the full Casino)
            self.ghostLevelling = ctx.setting("IBM_Level_Options_Ghost")
            self.levelFormation = "min"
        else:
            self.ghostLevelling = False
            self.levelFormation = "z1"
        self.Reset()

    def Reset(self):
        ctx = self._ctx
        self.Complete = False
        self.Redraws = 0
        self.UsedUlt = False
        self.MaxRedraws = ctx.setting("IBM_Casino_Redraws_Base", 2)
        self.GemCardsNeeded = ctx.setting("IBM_Casino_Target_Base", 3)
        self.MinCards = ctx.setting("IBM_Casino_MinCards_Base", 0)
        self.lockedFrontColumnChamps = []
        self.DeferredDMUlt = 0
        self.DMUltDelay = 0

    def Casino(self):
        """Returns True when UnlockHeroes() still needs calling."""
        ctx = self._ctx
        memory = ctx.memory
        heroes = ctx.heroes
        level_manager = ctx.farm.LevelManager
        elly = heroes[83]
        if elly.ReadBenched():
            ctx.log(f"No Elly{{z{memory.ReadCurrentZone()}}}")
            return len(self.lockedFrontColumnChamps) > 0

        if self.ghostLevelling:
            ghost_heroes = level_manager.SetupFirstZoneGhost()
            ghost_formation_levelled = False
            next_ghost = ghost_heroes.pop(0) if ghost_heroes else None
        else:
            ghost_heroes = []
            ghost_formation_levelled = True
            next_ghost = None
        locked = self.lockedFrontColumnChamps
        next_front = locked.pop(0) if locked else None
        modifier_pre_press = False
        unlock_threshold = ctx.setting("IBM_Casino_Front_Row_Threshold", 2)
        melee_addr = memory.GameManager.game.gameInstances[0] \
            .Controller.formation.numAttackingMonstersReached.resolve_address()

        def melee_count():
            value = memory.mem.read(melee_addr, "Int") if melee_addr else None
            return value if value is not None else 0

        elly.InitDoMTHandler()
        elly.InitCotFUltActive()  # may fail below level 200; recovers itself
        game_speed = memory.IBM_ReadBaseGameSpeed() or 1
        self.DMUltDelay = (self.ULT_DELAY / game_speed) / 1000.0  # seconds
        zone_incomplete = heroes[139].inM  # M-jump check only with Thellora
        start_time = time.perf_counter()
        timeout = start_time + (self.TIMEOUT_BASE / game_speed) / 1000.0
        last_loop_end = start_time

        while last_loop_end < timeout:
            if self.DeferredDMUlt and last_loop_end > self.DeferredDMUlt:
                self.UseDMUlt()
            if self.UsedUlt and not elly.ReadEllywickUltimateActive():
                self.UsedUlt = False  # ultimate completed
            num_cards = elly.ReadNumCards()
            num_gem_cards = elly.GetNumGemCards()
            if num_cards is None:
                break  # abort - memory reads unavailable
            if num_gem_cards is None:
                # AHK compares "" < n as true; a failed gem-card read must
                # not crash the Casino - treat as no gem cards yet
                num_gem_cards = 0
            if num_cards < self.MinCards or num_gem_cards < self.GemCardsNeeded:
                if self.MaxRedraws - self.Redraws > 0:
                    if not self.UsedUlt and self.ShouldRedraw(num_cards,
                                                              num_gem_cards):
                        self.UseEllywickUlt()
                elif self.MinCards == 0 or (not self.UsedUlt
                                            and num_cards >= self.MinCards):
                    # waiting for the ult to resolve to count correctly
                    break
            else:
                break

            levelling_done = False
            if next_front:
                # Level while the formation is engaged so the champion is NOT
                # placed - saves time without interfering with Briv
                if next_front.Current.casino_levelling:
                    if not modifier_pre_press:
                        level_manager.SetModifierKey(True)
                        modifier_pre_press = True
                    if melee_count() >= unlock_threshold:
                        for _ in range(next_front.Current.casino_levelling):
                            next_front.Key.key_press_bulk()
                        level_manager.SetModifierKey(False)
                        modifier_pre_press = False
                        level_manager.ResetLevelByID(next_front.ID)
                        next_front = locked.pop(0) if locked else None
                        levelling_done = True
                elif melee_count() >= unlock_threshold:
                    next_front.Key.key_press_bulk()
                    level_manager.ResetLevelByID(next_front.ID)
                    next_front = locked.pop(0) if locked else None
                    levelling_done = True
            elif (not ghost_formation_levelled and next_ghost
                  and not memory.IsCurrentFormationFull()):
                if next_ghost.Current.casino_levelling:
                    if not modifier_pre_press:
                        level_manager.SetModifierKey(True)
                        modifier_pre_press = True
                    if melee_count() >= unlock_threshold:
                        for _ in range(next_ghost.Current.casino_levelling):
                            next_ghost.Key.key_press_bulk()
                        level_manager.SetModifierKey(False)
                        modifier_pre_press = False
                        next_ghost = ghost_heroes.pop(0) if ghost_heroes else None
                        levelling_done = True
                elif melee_count() >= unlock_threshold:
                    next_ghost.Key.key_press_bulk()
                    next_ghost = ghost_heroes.pop(0) if ghost_heroes else None
                    levelling_done = True
            elif not ghost_formation_levelled:
                # Suppress Farideh (33) so her levelling can be blocked during
                # online stacking recovery
                level_manager.LevelFormation("GHOST", self.levelFormation,
                                             suppress_by_id=[33])
                ghost_formation_levelled = True
                levelling_done = True
            if not levelling_done:
                level_manager.LevelWorklist()
            if (zone_incomplete and (memory.ReadCurrentZone() or 0) > 1
                    and memory.ReadQuestRemaining() == 0):
                zone_incomplete = False
            sleep_offset(last_loop_end, 10)
            last_loop_end = time.perf_counter()

        if modifier_pre_press:  # set but no levelling opportunity found
            level_manager.SetModifierKey(False)
        ctx.log(f"Casino{{z{memory.ReadCurrentZone()} "
                f"T={round((last_loop_end - start_time) * 1000)} "
                f"R={self.Redraws} SB={heroes[58].ReadSBStacks()}}}")
        if zone_incomplete:
            ctx.log("Post-Casino wait for zone completion remaining="
                    f"[{memory.ReadQuestRemaining()}]")
            while zone_incomplete and last_loop_end < timeout:
                precise_sleep(10)
                zone_incomplete = memory.ReadQuestRemaining() != 0
                last_loop_end = time.perf_counter()
        if next_front:  # re-add for unlocking
            locked.append(next_front)
            return True
        return False

    def UnlockHeroes(self, level_formation=None):
        level_manager = self._ctx.farm.LevelManager
        for hero in self.lockedFrontColumnChamps:
            level_manager.ResetLevelByID(hero.ID)
        if level_formation:
            level_manager.LevelFormation("M", level_formation)

    def ShouldRedraw(self, num_cards, num_gem_cards):
        if num_cards == 5:
            return True
        if num_cards == 0:
            return False
        return (5 - num_cards) < (self.GemCardsNeeded - num_gem_cards)

    def UseEllywickUlt(self):
        ctx = self._ctx
        elly = ctx.heroes[83]
        dungeon_master = ctx.heroes[99]
        if elly.CanUseUltimate():
            self.UsedUlt = True  # assumed
            retry_count = elly.UseUltimate(50)
            if retry_count is None or retry_count > 50:
                ctx.log(f"Casino Elly (Level=[{elly.ReadLevel()}] "
                        f"Benched=[{elly.ReadBenched()}]) failed to activate "
                        f"with retryCount=[{retry_count}]")
                self.UsedUlt = False
            else:
                self.DeferredDMUlt = time.perf_counter() + self.DMUltDelay
                self.Redraws += 1
        elif dungeon_master.CanUseUltimate():
            # Somehow Elly's ult isn't ready but DM's is - try using it
            self.UseDMUlt()
        else:
            ctx.log(f"Casino Elly (Level=[{elly.ReadLevel()}] "
                    f"Benched=[{elly.ReadBenched()}]) Ult not available and "
                    f"DM (Level=[{dungeon_master.ReadLevel()}]) Ult not "
                    f"available - lowered max rerolls to [{self.Redraws}]")
            self.MaxRedraws = self.Redraws  # this Casino is busted; move on

    def UseDMUlt(self):
        ctx = self._ctx
        dungeon_master = ctx.heroes[99]
        if dungeon_master.CanUseUltimate():
            retry_count = dungeon_master.UseUltimate(50)
            if retry_count is None or retry_count > 50:
                ctx.log(f"Casino DM (Level=[{dungeon_master.ReadLevel()}] "
                        f"Benched=[{dungeon_master.ReadBenched()}]) failed "
                        f"to activate with retryCount=[{retry_count}]")
        self.DeferredDMUlt = 0  # reset in all cases


class DialogSwatter:
    """Swats the welcome-back dialog at game start. The AHK version uses a
    SetTimer; here a small thread polling every 100ms, honouring ctx.critical
    so it never interleaves with levelling input."""

    def __init__(self, ctx):
        self._ctx = ctx
        self._key_esc = ctx.input.get_key("Esc")
        self._deadline = 0
        self._thread = None
        self._stop = threading.Event()

    def Start(self):
        self._deadline = tick_ms() + 3000  # 3s should be enough
        if self._thread is None or not self._thread.is_alive():
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, daemon=True,
                                            name="DialogSwatter")
            self._thread.start()

    def Stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set() and tick_ms() <= self._deadline:
            try:
                if self._ctx.memory.ReadWelcomeBackActive():
                    with self._ctx.critical:
                        self._key_esc.key_press()
            except Exception:  # noqa: BLE001 - reads race game restarts
                pass
            time.sleep(0.1)


class DianaCheese:
    """Diana can give excess chests after the daily reset (12:00 CNE/Pacific
    time). The AHK version reconstructs the Pacific timezone from the Windows
    registry; zoneinfo does the same job here."""

    def __init__(self, ctx=None):
        self._tz = None
        try:
            from zoneinfo import ZoneInfo
            self._tz = ZoneInfo("America/Los_Angeles")
        except Exception:  # noqa: BLE001 - no tzdata on this box
            self._tz = None

    def GetCNETime(self):
        """Hours with minutes as a fraction, e.g. 23.95 = 23:57."""
        if self._tz is not None:
            now = datetime.datetime.now(self._tz)
        else:
            # Fallback: US Pacific from UTC with the post-2007 US DST rule
            utc = datetime.datetime.now(datetime.timezone.utc)
            year = utc.year
            def nth_sunday(month, n):
                date = datetime.date(year, month, 1)
                first_sunday = 1 + (6 - date.weekday()) % 7
                return datetime.date(year, month, first_sunday + 7 * (n - 1))
            dst_start = datetime.datetime.combine(
                nth_sunday(3, 2), datetime.time(10), datetime.timezone.utc)
            dst_end = datetime.datetime.combine(
                nth_sunday(11, 1), datetime.time(9), datetime.timezone.utc)
            offset = -7 if dst_start <= utc < dst_end else -8
            now = utc + datetime.timedelta(hours=offset)
        return now.hour + now.minute / 60

    def InWindow(self):
        server_time = self.GetCNETime()
        # 11:57 to 12:30; reset is at 12:00 CNE (Pacific) time
        return server_time is not None and 11.95 < server_time < 12.5
