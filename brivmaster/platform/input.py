"""Port of IC_BrivMaster_InputManager_Class (IC_BrivMaster_SharedFunctions.ahk).

Keys are delivered as WM_KEYDOWN/WM_KEYUP messages straight to the game
window (not global keystrokes), with the scan code packed into lParam - the
game does not need to be the foreground window, though the original calls
ControlFocus before non-bulk sends and so do we.

The window backend is injected (brivmaster.platform.window_backend()), so a
future X11 implementation slots in without changing this module.

Default scan codes match the QWERTY defaults of IBM_Scan_Codes; the settings
file can override them for other layouts.
"""

from __future__ import annotations

from . import window_backend

DEFAULT_SCAN_CODES = {
    "0": 11, "1": 2, "2": 3, "3": 4, "4": 5, "5": 6, "6": 7, "7": 8,
    "8": 9, "9": 10,
    "Alt": 56, "ClickDmg": 41, "Esc": 1, "LCtrl": 29, "Shift": 42,
    "Left": 331,
    "e": 18, "g": 34, "q": 16, "w": 17,
    "F1": 59, "F2": 60, "F3": 61, "F4": 62, "F5": 63, "F6": 64, "F7": 65,
    "F8": 66, "F9": 67, "F10": 68, "F11": 87, "F12": 88,
}


class Key:
    """One mapped key: virtual-key code plus down/up lParams
    (IC_BrivMaster_InputManager_Key_Class)."""

    __slots__ = ("key", "vk", "lparam_down", "lparam_up", "tag", "_manager")

    def __init__(self, manager, key, scan_code):
        self._manager = manager
        self.key = key
        self.vk = manager.backend.vk_from_scancode(scan_code)
        packed = scan_code << 16
        self.lparam_down = packed          # 0x0 | sc<<16
        self.lparam_up = 0xC0000001 | packed
        # Arbitrary tracking info, e.g. the associated seat for F-keys
        self.tag = None

    def _hwnd(self):
        return self._manager.hwnd_provider()

    def press(self):
        """Hold a key and do not release."""
        hwnd = self._hwnd()
        self._manager.backend.control_focus(hwnd)
        self._manager.backend.send_key_down(hwnd, self.vk, self.lparam_down)

    def release(self):
        hwnd = self._hwnd()
        self._manager.backend.control_focus(hwnd)
        self._manager.backend.send_key_up(hwnd, self.vk, self.lparam_up)

    def key_press(self):
        """Press then release."""
        hwnd = self._hwnd()
        self._manager.backend.control_focus(hwnd)
        self._manager.backend.send_key_down(hwnd, self.vk, self.lparam_down)
        self._manager.backend.send_key_up(hwnd, self.vk, self.lparam_up)

    # The _bulk versions skip ControlFocus and are for code sending a lot of
    # input together (levelling); that code calls game_focus() once itself.

    def press_bulk(self):
        self._manager.backend.send_key_down(self._hwnd(), self.vk,
                                            self.lparam_down)

    def release_bulk(self):
        self._manager.backend.send_key_up(self._hwnd(), self.vk,
                                          self.lparam_up)

    def key_press_bulk(self):
        hwnd = self._hwnd()
        self._manager.backend.send_key_down(hwnd, self.vk, self.lparam_down)
        self._manager.backend.send_key_up(hwnd, self.vk, self.lparam_up)


class InputManager:
    def __init__(self, hwnd_provider, scan_codes=None, logger=None,
                 backend=None):
        """hwnd_provider: zero-arg callable returning the current game window
        handle (the GameMaster owns and refreshes it across restarts)."""
        self.hwnd_provider = hwnd_provider
        self.scan_codes = dict(DEFAULT_SCAN_CODES)
        if scan_codes:
            self.scan_codes.update(scan_codes)
        self.logger = logger
        self.backend = backend if backend is not None else window_backend()
        self.key_list = {}

    def add_key(self, key):
        if key not in self.key_list:
            scan_code = self.scan_codes.get(key)
            if scan_code is None:
                if self.logger:
                    self.logger.AddMessage(
                        f"InputManager: No scancode for key=[{key}]")
                return
            self.key_list[key] = Key(self, key, scan_code)

    def get_key(self, key):
        if key not in self.key_list:
            self.add_key(key)
        return self.key_list.get(key)

    def game_focus(self):
        """Refocus the game window (needed after IC loses focus)."""
        self.backend.control_focus(self.hwnd_provider())
