"""X11 window/input/process primitives for Linux (Wine/Proton game).

Mirrors the win32.py interface so the farm code remains platform-agnostic.
Uses python-xlib for window discovery/control and XSendEvent for key injection.
Fallback to XTEST via the `record` extension if XSendEvent doesn't register keys.

Key concepts:
- Wine windows appear as normal X11 windows; find by WM_CLASS, _NET_WM_PID
- X keycodes differ from Windows scan codes; map via keysyms
- XSendEvent sends synthetic events to a specific window (no focus steal)
- XTEST (python-xlib record module) synthesizes global events to the focused window
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Optional

try:
    from Xlib import X, XK, display, protocol
    from Xlib.ext import record
    HAS_XLIB = True
    # Build keysym map only if xlib is available
    _SCAN_TO_KEYSYM = {
        # Numbers
        11: XK.XK_0, 2: XK.XK_1, 3: XK.XK_2, 4: XK.XK_3, 5: XK.XK_4,
        6: XK.XK_5, 7: XK.XK_6, 8: XK.XK_7, 9: XK.XK_8, 10: XK.XK_9,
        # Letters (QWERTY)
        16: XK.XK_q, 17: XK.XK_w, 18: XK.XK_e, 34: XK.XK_g,
        # Modifiers
        29: XK.XK_Control_L,      # LCtrl
        42: XK.XK_Shift_L,        # Shift
        56: XK.XK_Alt_L,          # Alt
        # Function keys
        59: XK.XK_F1, 60: XK.XK_F2, 61: XK.XK_F3, 62: XK.XK_F4,
        63: XK.XK_F5, 64: XK.XK_F6, 65: XK.XK_F7, 66: XK.XK_F8,
        67: XK.XK_F9, 68: XK.XK_F10, 87: XK.XK_F11, 88: XK.XK_F12,
        # Special
        1: XK.XK_Escape,          # Esc
        41: XK.XK_grave,          # ClickDmg (grave/backtick, farm uses this for damage)
        331: XK.XK_Left,          # Left arrow
    }
except ImportError:
    HAS_XLIB = False
    _SCAN_TO_KEYSYM = {}

try:
    from pynput.keyboard import Controller as KeyboardController, Key
    HAS_PYNPUT = True
    _keyboard_controller = KeyboardController()
except ImportError:
    HAS_PYNPUT = False
    _keyboard_controller = None

from ..memory.backend import LinuxProcessMemory

# Map Windows scan codes -> key names (for keyboard module)
# Covers QWERTY defaults from DEFAULT_SCAN_CODES.
_SCAN_TO_KEY_NAME = {
    # Numbers
    11: "0", 2: "1", 3: "2", 4: "3", 5: "4",
    6: "5", 7: "6", 8: "7", 9: "8", 10: "9",
    # Letters (QWERTY)
    16: "q", 17: "w", 18: "e", 34: "g",
    # Modifiers
    29: "ctrl", 42: "shift", 56: "alt",
    # Function keys
    59: "f1", 60: "f2", 61: "f3", 62: "f4",
    63: "f5", 64: "f6", 65: "f7", 66: "f8",
    67: "f9", 68: "f10", 87: "f11", 88: "f12",
    # Special
    1: "esc", 41: "`", 331: "left",
}

# XSendEvent state flags and masks (only if Xlib available)
if HAS_XLIB:
    _KEY_PRESS_MASK = X.KeyPressMask
    _KEY_RELEASE_MASK = X.KeyReleaseMask
else:
    _KEY_PRESS_MASK = None
    _KEY_RELEASE_MASK = None


# --- Initialization & setup ---

def _ensure_xlib():
    """Raise if python-xlib is not available."""
    if not HAS_XLIB:
        raise RuntimeError(
            "python-xlib is required for X11 backend. Install: pip install python-xlib")


def _get_display():
    """Cached X11 display connection."""
    if not hasattr(_get_display, '_disp'):
        _ensure_xlib()
        try:
            _get_display._disp = display.Display()
        except Exception as e:
            raise RuntimeError(f"Failed to connect to X11 display: {e}")
    return _get_display._disp


def _get_screen():
    """Current screen (usually screen 0)."""
    return _get_display().screen()


# --- Processes ---

def find_pids(exe_name):
    """All PIDs whose executable name matches (case-insensitive)."""
    return LinuxProcessMemory.find_pids(exe_name)


def get_process_name(pid):
    """Executable name (no path) for a PID, or None."""
    if not pid:
        return None
    try:
        # Read the symlink /proc/<pid>/exe to get the executable path
        path = os.readlink(f"/proc/{pid}/exe")
        return os.path.basename(path)
    except (OSError, FileNotFoundError):
        # Fall back to /proc/<pid>/comm (truncated to 15 chars)
        try:
            with open(f"/proc/{pid}/comm", "r") as f:
                return f.read().strip()
        except (OSError, FileNotFoundError):
            return None


def terminate_process(pid):
    """Send SIGKILL to a process. Returns True if the signal was sent."""
    try:
        os.kill(int(pid), 9)  # SIGKILL
        return True
    except (OSError, ProcessLookupError):
        return False


def set_priority_realtime(pid):
    """No-op on Linux (would need root/capabilities). AHK silently grants High
    if admin is not available; we match by doing nothing (the process will run
    at normal priority, same as the fallback)."""
    return True


# Idle Champions' EGS app ID (constant across all installs)
_IC_EGS_APP_ID = "40cb42e38c0b4a14a1bb133eb3291572"

# Heroic's bundled legendary binary, in install-method order
_LEGENDARY_CANDIDATES = (
    "/usr/lib64/heroic/resources/app.asar.unpacked/build/bin/x64/linux/legendary",
    "/usr/lib/heroic/resources/app.asar.unpacked/build/bin/x64/linux/legendary",
    "/opt/Heroic/resources/app.asar.unpacked/build/bin/x64/linux/legendary",
    os.path.expanduser("~/.var/app/com.heroicgameslauncher.hgl/config"
                       "/heroic/tools/legendary/legendary"),
)


def _find_legendary(command):
    """Locate a legendary binary: the configured command if it is one,
    otherwise the first existing Heroic-bundled candidate."""
    first_word = command.split(" ", 1)[0] if command else ""
    if os.path.basename(first_word).startswith("legendary") \
            and os.path.exists(first_word):
        return first_word
    for candidate in _LEGENDARY_CANDIDATES:
        if os.path.exists(candidate):
            return candidate
    return None


def launch(command, hide=False):
    """Port of the AHK Run command for game launch on Linux.

    Prefers Heroic's legendary CLI (handles EGS authentication); falls back
    to running the configured command directly.

    Returns PID if we have it, or 0 if discovery by scanning is needed.
    """
    command = (command or "").strip()

    legendary = _find_legendary(command)
    if legendary:
        try:
            env = os.environ.copy()
            config = os.path.expanduser(
                "~/.config/heroic/legendaryConfig/legendary")
            if os.path.isdir(config):
                env["LEGENDARY_CONFIG_PATH"] = config
            args = [legendary, "launch", _IC_EGS_APP_ID, "--language", "en"]
            if os.path.exists("/usr/bin/wine"):
                args += ["--wine", "/usr/bin/wine"]
            subprocess.Popen(args, env=env, close_fds=True,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return 0  # Let the PID scanner find the game process
        except Exception:
            pass

    if not command:
        return 0

    # A URI handler (e.g. heroic://launch/...)
    if "://" in command.split(" ", 1)[0]:
        try:
            subprocess.Popen(["xdg-open", command], close_fds=True)
        except Exception:
            pass
        return 0

    # Direct executable (e.g. a wine wrapper script), else a shell command
    try:
        process = subprocess.Popen(command, shell=True, close_fds=True,
                                   stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        return process.pid
    except Exception:
        return 0


# --- Windows (X11) ---

def _window_has_pid(window, target_pid):
    """Check if a window's _NET_WM_PID property matches the target."""
    try:
        prop = window.get_full_property(
            _get_display().get_atom("_NET_WM_PID"), 0)
        if prop and prop.value:
            return prop.value[0] == target_pid
    except Exception:
        pass
    return False


def _window_matches_class(window, class_name):
    """Check if a window's WM_CLASS matches (case-insensitive)."""
    try:
        # WM_CLASS is usually "instance\0class\0"
        wm_class = window.get_wm_class()
        if wm_class:
            # wm_class is (instance, class); check both
            return (class_name.lower() in (wm_class[0].lower(), wm_class[1].lower()))
    except Exception:
        pass
    return False


def _is_visible(window):
    """Check if a window is mapped and visible.

    Wine windows may not always report IsViewable even when displayed,
    so we're lenient: a window exists if we can query it without error."""
    try:
        window.get_attributes()
        return True
    except Exception:
        return False


def _get_window_tree(root=None):
    """Recursively list all windows under root (default screen root)."""
    if root is None:
        root = _get_screen().root
    windows = [root]
    try:
        tree = root.query_tree()
        for child in tree.children:
            windows.extend(_get_window_tree(child))
    except Exception:
        pass
    return windows


def _candidate_windows():
    """Top-level application windows via the EWMH _NET_CLIENT_LIST root
    property - one X round-trip instead of a full recursive tree walk
    (this runs on the GUI timer and per key batch, so speed matters).
    Falls back to the tree walk if the WM doesn't provide the list."""
    try:
        dpy = _get_display()
        root = _get_screen().root
        prop = root.get_full_property(dpy.get_atom("_NET_CLIENT_LIST"),
                                      X.AnyPropertyType)
        if prop and prop.value:
            return [dpy.create_resource_object("window", wid)
                    for wid in prop.value]
    except Exception:
        pass
    return _get_window_tree()


def window_pid(hwnd):
    """Get the PID of a window (read _NET_WM_PID)."""
    try:
        prop = hwnd.get_full_property(
            _get_display().get_atom("_NET_WM_PID"), 0)
        if prop and prop.value:
            return prop.value[0]
    except Exception:
        pass
    return 0


def find_window_by_pid(pid):
    """First visible window matching the PID."""
    for window in _candidate_windows():
        if _window_has_pid(window, pid) and _is_visible(window):
            return window
    return None


def find_windows_by_exe(exe_name):
    """All visible windows whose process name matches the exe name.
    Viewable (mapped) windows are listed first so callers focusing/typing
    get the live window, not a dying one from before a game restart.
    Returns [(window, pid), ...]."""
    pids = {int(p) for p in find_pids(exe_name)}
    if not pids:
        return []
    viewable, other = [], []
    for window in _candidate_windows():
        pid = window_pid(window)
        if pid not in pids:
            continue
        try:
            mapped = window.get_attributes().map_state == X.IsViewable
        except Exception:
            continue
        (viewable if mapped else other).append((window, pid))
    return viewable + other


def find_window_by_exe(exe_name):
    """First visible window whose process name matches."""
    windows = find_windows_by_exe(exe_name)
    return windows[0][0] if windows else None


def window_exists(hwnd):
    """Check if a window still exists and is valid."""
    if not hwnd:
        return False
    try:
        hwnd.get_attributes()
        return True
    except Exception:
        return False


def get_active_window():
    """Get the currently focused window (_NET_ACTIVE_WINDOW)."""
    try:
        root = _get_screen().root
        prop = root.get_full_property(
            _get_display().get_atom("_NET_ACTIVE_WINDOW"), 0)
        if prop and prop.value:
            return _get_display().get_window(prop.value[0])
    except Exception:
        pass
    return None


def activate_window(hwnd):
    """Activate (raise and focus) a window via _NET_ACTIVE_WINDOW."""
    if not hwnd or not window_exists(hwnd):
        return
    try:
        # Wine windows often don't support all X11 operations
        # Try to activate but don't fail if it doesn't work
        control_focus(hwnd)
    except Exception:
        pass


def control_focus(hwnd):
    """Give a window keyboard focus (without necessarily raising it)."""
    if not hwnd:
        return False
    try:
        # Focusing an unmapped/destroyed window raises async BadMatch
        # errors (flooding stderr) and does nothing - check map_state first
        # (stale hwnds are common right after the farm restarts the game).
        attrs = hwnd.get_attributes()
        if attrs.map_state != X.IsViewable:
            return False
        hwnd.set_input_focus(X.RevertToPointerRoot, 0)  # timestamp=0 (current)
        _get_display().sync()
        return True
    except Exception:
        # X11 operation may fail on Wine - that's OK, farm can proceed
        return True


def request_window_close(hwnd, timeout_ms=10000):
    """Send WM_DELETE_WINDOW to request a polite close (preserved by Wine as WM_CLOSE)."""
    if not hwnd or not window_exists(hwnd):
        return False
    try:
        # Check if window supports WM_DELETE_WINDOW
        wm_protocols = _get_display().get_atom("WM_PROTOCOLS")
        wm_delete = _get_display().get_atom("WM_DELETE_WINDOW")

        prop = hwnd.get_full_property(wm_protocols, 0)
        if prop and wm_delete in (prop.value or []):
            # Window accepts WM_DELETE_WINDOW; send it
            event = protocol.event.ClientMessage(
                window=hwnd, client_type=wm_protocols,
                data=(32, [wm_delete, 0, 0, 0, 0]))
            hwnd.send_event(event)
            _get_display().sync()
            return True
    except Exception:
        pass
    # Fall back to destroying the window (less polite but works)
    try:
        hwnd.destroy()
        return True
    except Exception:
        return False


# --- Keys ---

def vk_from_scancode(scan_code):
    """Convert Windows scan code to an X11 keycode (via keysym).

    This is a simplified mapping - the full mapping would need to account for
    different keyboard layouts. For QWERTY (the default), this covers the
    important keys from IBM_Scan_Codes."""
    keysym = _SCAN_TO_KEYSYM.get(scan_code)
    if keysym is None:
        # Unknown scan code; try returning it as-is (will likely fail)
        return scan_code
    try:
        dpy = _get_display()
        keycode = dpy.keysym_to_keycode(keysym)
        return keycode if keycode else scan_code
    except Exception:
        return scan_code


def send_key_down(hwnd, vk, lparam, timeout_ms=1000):
    """Inject a key-down event using pynput's keyboard controller."""
    if not HAS_PYNPUT:
        return False
    try:
        # Extract scan code from lparam (packed as sc<<16 in input.py, bits 16-24)
        scan_code = (lparam >> 16) & 0x1FF if lparam else 0
        key_name = _SCAN_TO_KEY_NAME.get(scan_code)
        if not key_name:
            return False

        # pynput sends to focused window - no need to activate
        # (activating causes X11 errors on Wine)
        key = _get_pynput_key(key_name)
        if key:
            _keyboard_controller.press(key)
            return True
        return False
    except Exception:
        return False


def send_key_up(hwnd, vk, lparam, timeout_ms=3000):
    """Inject a key-up event using pynput."""
    if not HAS_PYNPUT:
        return False
    try:
        # Extract scan code from lparam (bits 16-24)
        scan_code = (lparam >> 16) & 0x1FF if lparam else 0
        key_name = _SCAN_TO_KEY_NAME.get(scan_code)
        if not key_name:
            return False

        key = _get_pynput_key(key_name)
        if key:
            _keyboard_controller.release(key)
            return True
        return False
    except Exception:
        return False


def _get_pynput_key(key_name):
    """Convert key name to pynput Key object."""
    if not HAS_PYNPUT:
        return None

    # Map key names to pynput Key objects
    key_map = {
        "ctrl": Key.ctrl,
        "shift": Key.shift,
        "alt": Key.alt,
        "esc": Key.esc,
        "left": Key.left,
        "f1": Key.f1, "f2": Key.f2, "f3": Key.f3, "f4": Key.f4,
        "f5": Key.f5, "f6": Key.f6, "f7": Key.f7, "f8": Key.f8,
        "f9": Key.f9, "f10": Key.f10, "f11": Key.f11, "f12": Key.f12,
    }

    if key_name in key_map:
        return key_map[key_name]
    # For regular characters, return as-is (pynput accepts strings)
    return key_name
