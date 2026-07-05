"""Port of IC_BrivMaster_SharedData_Class (IC_BrivMaster_SharedFunctions.ahk)
plus the settings-file loading.

In the AHK original this object is exported over COM to the Home GUI; here it
is a plain object for now - the socket IPC that replaces COM arrives with the
Home GUI port (phase 4). The farm reads/writes it identically either way.
"""

from __future__ import annotations

import json
import os


class SharedData:
    def __init__(self, ctx, settings_path):
        self._ctx = ctx
        self.SettingsPath = settings_path
        self.BossesHitThisRun = 0
        self.TotalBossesHit = 0
        self.TotalRollBacks = 0
        self.BadAutoProgress = 0
        self.IBM_RunControl_DisableOffline = False
        self.IBM_RunControl_ForceOffline = False
        self.IBM_ProcessSwap = False
        self.IBM_RunControl_CycleString = ""
        self.IBM_RunControl_StatusString = ""
        self.IBM_RunControl_StackString = ""
        self.IBM_BuyChests = False
        self.RunLogResetNumber = 0
        self.RunLog = ""
        self.LoopString = ""
        self.LastCloseReason = ""
        self.IBM_OutboundDirty = False

    def Init(self):
        self.UpdateSettingsFromFile()
        self.IBM_OutboundDirty = False

    def UpdateSettingsFromFile(self):
        """Load settings from the GUI settings file into ctx.settings.
        Note: the file was written by AHK_JSON, so booleans are stored as
        numbers already - Python truthiness handles them unchanged."""
        try:
            with open(self.SettingsPath, "r", encoding="utf-8-sig") as f:
                settings = json.load(f)
        except (OSError, ValueError):
            return False
        for key, value in settings.items():
            if key != "HUB":  # do not load hub-only settings
                self._ctx.settings[key] = value
        return True

    def UpdateOutbound(self, key, value):
        if getattr(self, key, None) != value:
            setattr(self, key, value)
            self.IBM_OutboundDirty = True

    def ResetRunStats(self):
        self.BossesHitThisRun = 0
        self.TotalBossesHit = 0
        self.TotalRollBacks = 0
        self.BadAutoProgress = 0
        self.IBM_OutboundDirty = True

    def UpdateOutbound_Increment(self, key):
        setattr(self, key, (getattr(self, key, 0) or 0) + 1)
        self.IBM_OutboundDirty = True


def default_settings_path():
    """The settings file: our own copy if present, else the AHK install's
    (sibling BrivMaster directory during the port)."""
    package_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))  # .../PyBrivMaster
    candidates = [
        os.path.join(package_root, "IC_BrivMaster_Settings.json"),
        os.path.join(os.path.dirname(package_root), "BrivMaster",
                     "IC_BrivMaster_Settings.json"),
    ]
    for path in candidates:
        if os.path.isfile(path):
            return path
    return candidates[-1]
