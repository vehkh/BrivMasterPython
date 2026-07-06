"""Game settings profiles - port of GameSettingsCheck / SettingCheck /
ForcedSettingCheck / GetSettingsFileLocation from IC_BrivMaster_Home.ahk.

Two user-defined profiles (framerate, particles, resolution, ...) are kept
in HUB.IBM_Game_Settings_Option_Set. Briv Master never changes the game's
settings on its own: it periodically CHECKS the game's own file
(<game path>\\IdleDragons_Data\\StreamingAssets\\localSettings.json) against
the selected profile and reports differences; 'Set Now' applies the profile,
which the game only picks up when started - so writing is refused while it
runs.

The AHK version needed special raw-boolean JSON handling (AHK has no bool
type); Python's json reads/writes real true/false, so that machinery has no
counterpart here.
"""

from __future__ import annotations

import json
import os
import time

from ..platform import window_backend

# (game file key, profile key, is boolean)
SETTING_MAP = [
    ("TargetFramerate", "Framerate", False),
    ("PercentOfParticlesSpawned", "Particles", False),
    ("resolution_x", "HRes", False),
    ("resolution_y", "VRes", False),
    ("resolution_fullscreen", "Fullscreen", True),
    ("ReduceFramerateWhenNotInFocus", "CapFPSinBG", True),
    ("FormationSaveIncludeFeatsCheck", "SaveFeats", True),
    ("UseConsolePortraits", "ConsolePortraits", True),
    ("ShowAllHeroBoxes", "AllHero", True),
    # All hero boxes must be visible for the script; at higher resolutions
    # narrow boxes aren't needed for that, so it is checked, not forced
    ("NarrowHeroBoxes", "NarrowHero", True),
]
FORCED_SETTINGS = [("LevelupAmountIndex", 3)]  # always x100 levelling


class GameSettingsProfiles:
    CHECK_INTERVAL_MS = 3600000  # hourly
    FIRST_CHECK_DELAY_MS = 60000

    def __init__(self, ctx):
        self._ctx = ctx
        self._win = window_backend()
        self.file_location = None
        self.status = ""
        self.status_level = "DefaultText"   # traffic light name, as in AHK
        self.detail = ""                    # the per-setting diff lines
        self.next_check = (time.monotonic() * 1000
                           + self.FIRST_CHECK_DELAY_MS)

    # --- plumbing ------------------------------------------------------------

    def _find_settings_file(self):
        game_path = self._ctx.setting("IBM_Game_Path", "") or ""
        candidate = os.path.join(game_path, "IdleDragons_Data",
                                 "StreamingAssets", "localSettings.json")
        if os.path.isfile(candidate):
            self.file_location = candidate
        return self.file_location

    def is_game_closed(self):
        exe = self._ctx.setting("IBM_Game_Exe", "IdleDragons.exe")
        if self._win.find_window_by_exe(exe):
            return False
        # Isolated-display mode: the window is on another X display -
        # check the process so 'Set Now' cannot write under a running game
        if os.environ.get("BRIVMASTER_DISPLAY"):
            return not self._win.find_pids(exe)
        return True

    def _profile(self):
        hub = self._ctx.settings.get("HUB", {})
        index = int(hub.get("IBM_Game_Settings_Option_Profile", 1) or 1)
        profiles = hub.get("IBM_Game_Settings_Option_Set") or []
        if 1 <= index <= len(profiles):
            return index, profiles[index - 1]
        return index, {}

    # --- the check -----------------------------------------------------------

    def tick(self):
        """Hourly background check, driven from the hub Update()."""
        if time.monotonic() * 1000 >= self.next_check:
            self.check()

    def check(self, change=False):
        """GameSettingsCheck port. change=True is 'Set Now'. Returns the
        status string (also kept on self for the GUI)."""
        self.next_check = time.monotonic() * 1000 + self.CHECK_INTERVAL_MS
        check_time = time.strftime("(%H:%M)")
        if not self._find_settings_file():
            return self._set_status(
                f"{check_time} Unable to open game settings",
                "TrafficLightBad", "")
        try:
            with open(self.file_location, "r", encoding="utf-8-sig") as f:
                game_settings = json.load(f)
        except (OSError, ValueError) as err:
            return self._set_status(
                f"{check_time} Game settings unreadable: {err}",
                "TrafficLightBad", "")
        index, profile = self._profile()
        name = profile.get("Name", f"Profile {index}")
        changes = []

        def note(key, expected, actual):
            changes.append(f"{key} - Expected: {expected} Actual: {actual}")

        for cne_name, ibm_name, is_boolean in SETTING_MAP:
            target = profile.get(ibm_name)
            if is_boolean:
                target = bool(target)
            actual = game_settings.get(cne_name)
            if actual != target:
                note(cne_name, target, actual)
                if change:
                    game_settings[cne_name] = target
        # Hotkey swap special case: only checked when the option is on
        if profile.get("Swap25100"):
            hotkeys = game_settings.get("HotKeys") or {}
            level25 = hotkeys.get("hero_level_25") or []
            if level25 != ["LeftControl"]:
                note("HotKeys.hero_level_25", "LeftControl",
                     f"{level25} ({len(level25)} items)")
                if change:
                    hotkeys["hero_level_25"] = ["LeftControl"]
            level100 = hotkeys.get("hero_level_100") or []
            if sorted(level100) != ["LeftControl", "LeftShift"]:
                note("HotKeys.hero_level_100", "LeftShift and LeftControl",
                     f"{level100} ({len(level100)} items)")
                if change:
                    hotkeys["hero_level_100"] = ["LeftShift", "LeftControl"]
            if change:
                game_settings["HotKeys"] = hotkeys
        for cne_name, value in FORCED_SETTINGS:
            if game_settings.get(cne_name) != value:
                note(cne_name, value, game_settings.get(cne_name))
                if change:
                    game_settings[cne_name] = value

        detail = "\n".join(changes)
        count = len(changes)
        plural = "difference" if count == 1 else "differences"
        if not count:
            return self._set_status(f"{check_time} IC and {name} match",
                                    "DefaultText", detail)
        if not change:
            return self._set_status(
                f"{check_time} IC and {name} have {count} {plural}",
                "TrafficLightNeutral", detail)
        if not self.is_game_closed():
            return self._set_status(
                f"{check_time} IC and {name} have {count} {plural} - game "
                "settings cannot be changed whilst Idle Champions is running",
                "TrafficLightNeutral", detail)
        try:
            with open(self.file_location, "w", encoding="utf-8") as f:
                json.dump(game_settings, f, indent="\t")
        except OSError as err:
            return self._set_status(
                f"{check_time} Failed to write game settings: {err}",
                "TrafficLightBad", detail)
        applied = "1 change" if count == 1 else f"{count} changes"
        return self._set_status(
            f"{check_time} IC and {name} aligned with {applied}",
            "TrafficLightGood", detail)

    def _set_status(self, status, level, detail):
        self.status = status
        self.status_level = level
        self.detail = detail
        return status
