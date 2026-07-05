"""Settings template, load-merge and save - port of GetSettingsTemplate /
CheckForMissingSettings / CheckForExtraSettings / SaveSettings from
IC_BrivMaster_Home.ahk, plus the route import/export string codec
(compatible with Emmote's routes site strings, e.g. {3zXoa17wA,}).

Booleans are stored as 0/1 numbers, matching what AHK_JSON wrote, so the
settings file stays interchangeable between the AHK and Python versions.
"""

from __future__ import annotations

import json

# RFC 4648 S5 URL-safe alphabet (BASE_64_CHARACTERS in the AHK)
BASE64_CHARACTERS = ("ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                     "abcdefghijklmnopqrstuvwxyz0123456789-_")

_DEFAULT_LEVELS = {
    "7": {"min": 100, "prio": 0, "priolimit": "", "z1": 100},
    "58": {"min": 200, "prio": 3, "priolimit": "", "z1": 200},
    "59": {"min": 70, "prio": 2, "priolimit": "", "z1": 70},
    "75": {"min": 220, "prio": 0, "priolimit": "", "z1": 220},
    "83": {"min": 200, "prio": 4, "priolimit": 100, "z1": 200},
    "91": {"min": 300, "prio": 0, "priolimit": "", "z1": 300},
    "97": {"min": 100, "prio": 4, "priolimit": 100, "z1": 100},
    "99": {"min": 200, "prio": 2, "priolimit": "", "z1": 200},
    "117": {"min": 50, "prio": 0, "priolimit": "", "z1": 50},
    "139": {"min": 1, "prio": 0, "priolimit": "", "z1": 1},
    "145": {"min": 100, "prio": 0, "priolimit": "", "z1": 100},
    "148": {"min": 100, "prio": 2, "priolimit": "", "z1": 100},
    "165": {"min": 200, "prio": 2, "priolimit": "", "z1": 200},
}

_SCAN_CODE_DEFAULTS = {
    "Esc": 1, "F1": 59, "F2": 60, "F3": 61, "F4": 62, "F5": 63, "F6": 64,
    "F7": 65, "F8": 66, "F9": 67, "F10": 68, "F11": 87, "F12": 88,
    "q": 16, "w": 17, "e": 18, "g": 34, "Left": 331, "ClickDmg": 41,
    "LCtrl": 29, "Shift": 42, "Alt": 56,
    "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8, "8": 9,
    "9": 10, "0": 11,
}

_THEME_DEFAULT = {
    "DefaultText": 0xC0C0C0, "WarningText": 0xF18500,
    "SpecialText1": 0x8888FF, "SpecialText2": 0x88FF88,
    "TableText": 0xE0E0E0, "EditText": 0x333333,
    "TableBackground": 0x555555, "WindowBackground": 0x333333,
    "TrafficLightBad": 0xF00000, "TrafficLightGood": 0x00F000,
    "TrafficLightNeutral": 0xFFC000, "DarkMode": 1,
}

_GAME_PROFILE = {"Name": "Profile 1", "Framerate": 600, "Particles": 0,
                 "HRes": 1920, "VRes": 1080, "Fullscreen": 0, "CapFPSinBG": 0,
                 "SaveFeats": 0, "ConsolePortraits": 0, "NarrowHero": 1,
                 "AllHero": 1, "Swap25100": 0}

# Keys whose value is taken/kept wholesale (the AHK object-valued leaves)
_LEAF_OBJECTS = {"IBM_LevelManager_Levels", "IBM_Theme_Current",
                 "IBM_Game_Settings_Option_Set",
                 "IBM_Ellywick_NonGemFarm_Cards", "IBM_Route_Zones_Jump",
                 "IBM_Route_Zones_Stack", "IBM_Scan_Codes"}


def settings_template():
    """All settings with their defaults (GetSettingsTemplate port)."""
    return {
        "IBM_Offline_Stack_Zone": 500, "IBM_Offline_Stack_Min": 300,
        "IBM_Route_Combine": 0, "IBM_Route_Combine_Boss_Avoidance": 1,
        "IBM_LevelManager_Levels": dict(_DEFAULT_LEVELS),
        "IBM_Route_Zones_Jump": [1] * 50, "IBM_Route_Zones_Stack": [1] * 50,
        "IBM_Online_Melf_Min": 349, "IBM_LevelManager_Input_Max": 5,
        "IBM_LevelManager_Boost_Use": 0, "IBM_LevelManager_Boost_Multi": 8,
        "IBM_Route_BrivJump_Q": 4, "IBM_Route_BrivJump_E": 0,
        "IBM_Route_BrivJump_M": 4,
        "IBM_Casino_Target_Base": 3, "IBM_Casino_Redraws_Base": 1,
        "IBM_Casino_MinCards_Base": 0, "IBM_Casino_Front_Row_Threshold": 2,
        "IBM_OffLine_Delay_Time": 15000, "IBM_OffLine_Sleep_Time": 0,
        "IBM_Level_Options_Mod_Key": "Shift", "IBM_Level_Options_Mod_Value": 10,
        "IBM_Route_Offline_Restore_Window": 1, "IBM_OffLine_Freq": 1,
        "IBM_OffLine_Blank": 0, "IBM_OffLine_Blank_Relay": 0,
        "IBM_OffLine_Blank_Relay_Zones": 300,
        "IBM_Level_Options_Suppress_Front": 1, "IBM_Level_Options_Ghost": 1,
        "IBM_Level_Recovery_Softcap": 0,
        "IBM_Format_Date_Display": "yyyy-MM-ddTHH:mm:ss",
        "IBM_Format_Date_File": "yyyyMMddTHHmmss",
        "IBM_Game_Exe": "IdleDragons.exe", "IBM_Game_Path": "",
        "IBM_Game_Launch": "", "IBM_Game_Hide_Launcher": 0,
        "IBM_OffLine_Timeout": 5,
        "IBM_Window_X": 0, "IBM_Window_Y": 900, "IBM_Window_Hide": 0,
        "IBM_Level_Diana_Cheese": 0, "IBM_Allow_Modron_Buff_Off": 0,
        "IBM_Logger_MiniLog": 0, "IBM_Logger_ZoneLog": 0,
        "IBM_Online_Farideh_Threshold": 90, "IBM_Online_Farideh_Condition": 1,
        "IBM_Scan_Codes": dict(_SCAN_CODE_DEFAULTS),
        "IBM_OffLine_Blank_Stop": 0,
        "IBM_Theme_Current": dict(_THEME_DEFAULT),
        "HUB": {
            "IBM_ChestSnatcher_Options_Min_Gem": 500000,
            "IBM_ChestSnatcher_Options_Min_Gold": 500,
            "IBM_ChestSnatcher_Options_Min_Silver": 500,
            "IBM_ChestSnatcher_Options_Min_Buy": 250,
            "IBM_ChestSnatcher_Options_Open_Gold": 0,
            "IBM_ChestSnatcher_Options_Open_Silver": 0,
            "IBM_DailyRewardClaim_Enable": 1,
            "IBM_Game_Settings_Option_Profile": 1,
            "IBM_Game_Settings_Option_Set": [
                dict(_GAME_PROFILE),
                {**_GAME_PROFILE, "Name": "Profile 2"}],
            # Min/Max per card in card-ID order (Knight, Moon, Gem, Fates, Flames)
            "IBM_Ellywick_NonGemFarm_Cards": [0, 0, 4, 5, 0, 0, 0, 1, 0, 0],
            "IBM_Version_Check": 0, "IBM_Offsets_Check": 0,
            "IBM_Offsets_Lock_Pointers": 0,
            "IBM_Offsets_URL": "https://raw.githubusercontent.com/RLee-EN/"
                               "BrivMaster-Imports/refs/heads/main/",
            "IBM_Window_Wide": 0,
        },
    }


def merge_with_template(settings):
    """Add missing keys from the template and drop unknown ones, recursively
    (CheckForMissingSettings + CheckForExtraSettings port). Returns the
    number of changes made."""
    def merge(target, template):
        changes = 0
        for key, default in template.items():
            if key not in target:
                target[key] = default
                changes += 1
            elif isinstance(default, dict) and key not in _LEAF_OBJECTS:
                if not isinstance(target[key], dict):
                    target[key] = default
                    changes += 1
                else:
                    changes += merge(target[key], default)
        for key in [k for k in target if k not in template]:
            del target[key]
            changes += 1
        return changes

    return merge(settings, settings_template())


def save_settings(settings, path):
    """SaveSettings port - tab-indented JSON like AHK_JSON.Dump."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent="\t", sort_keys=True)


# --- route import/export strings ------------------------------------------------

def binary_array_to_base64(values):
    """ConvertBinaryArrayToBase64 port: 6-bit chunks MSB-first, last chunk
    zero-padded. NOT byte-aligned base64 - by design."""
    chunks = [list(values[i:i + 6]) for i in range(0, len(values), 6)]
    if chunks and len(chunks[-1]) < 6:
        chunks[-1].extend([0] * (6 - len(chunks[-1])))
    result = []
    for chunk in chunks:
        dec = 0
        for position, bit in enumerate(reversed(chunk)):
            dec += (1 if bit else 0) * (2 ** position)
        result.append(BASE64_CHARACTERS[dec])
    return "".join(result)


def base64_to_binary_array(text):
    """ConvertBase64ToBinaryArray port. Result is a multiple of 6 bits."""
    bits = []
    for char in text:
        index = BASE64_CHARACTERS.find(char)  # case-sensitive
        if index < 0:
            continue
        bits.extend([1 if index & mask else 0
                     for mask in (0x20, 0x10, 0x08, 0x04, 0x02, 0x01)])
    return bits


def route_export_string(settings):
    """GetRouteExportString port: '{<jump>,<stack>}'."""
    return ("{"
            + binary_array_to_base64(settings.get("IBM_Route_Zones_Jump", []))
            + ","
            + binary_array_to_base64(settings.get("IBM_Route_Zones_Stack", []))
            + "}")


def route_import_string(settings, route_string):
    """ParseRouteImportString port. Either part may be blank to leave that
    half unchanged. Returns (jump_changed, stack_changed)."""
    import re
    jump_changed = stack_changed = False
    match = re.search(r"\{([A-Za-z0-9\-_]*),([A-Za-z0-9\-_]*)\}", route_string)
    if not match:
        return False, False
    if match.group(1):
        bits = base64_to_binary_array(match.group(1))[:50]
        if bits:
            settings["IBM_Route_Zones_Jump"] = bits + [0] * (50 - len(bits))
            jump_changed = True
    if match.group(2):
        bits = base64_to_binary_array(match.group(2))[:50]
        if bits:
            settings["IBM_Route_Zones_Stack"] = bits + [0] * (50 - len(bits))
            stack_changed = True
    return jump_changed, stack_changed
