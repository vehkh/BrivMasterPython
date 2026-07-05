"""Win32 window/input/process primitives, mirroring the AHK commands the
original uses (WinExist/WinGet/WinActivate/ControlFocus/SendMessage/Run/
Process). ctypes-only; no external dependencies.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import subprocess

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_shell32 = ctypes.WinDLL("shell32", use_last_error=True)

_user32.SendMessageTimeoutW.restype = ctypes.c_void_p
_user32.SendMessageTimeoutW.argtypes = [wt.HWND, ctypes.c_uint,
                                        ctypes.c_void_p, ctypes.c_void_p,
                                        ctypes.c_uint, ctypes.c_uint,
                                        ctypes.POINTER(ctypes.c_void_p)]
_user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
_kernel32.OpenProcess.restype = wt.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD,
                                                 wt.LPWSTR,
                                                 ctypes.POINTER(wt.DWORD)]

WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSCOMMAND = 0x0112
SC_CLOSE = 0xF060
SMTO_ABORTIFHUNG = 0x0002  # what AHK's SendMessage uses


# --- processes ---------------------------------------------------------------

def find_pids(exe_name):
    """All PIDs whose executable name matches (case-insensitive)."""
    from ..memory.backend import WindowsProcessMemory
    return WindowsProcessMemory.find_pids(exe_name)


def get_process_name(pid):
    """Executable name (no path) for a PID, or None (GetProcessName port)."""
    if not pid:
        return None
    # PROCESS_QUERY_LIMITED_INFORMATION
    handle = _kernel32.OpenProcess(0x1000, False, int(pid))
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(2048)
        size = wt.DWORD(2048)
        if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return None
    finally:
        _kernel32.CloseHandle(handle)


def process_exists(exe_name):
    pids = find_pids(exe_name)
    return pids[0] if pids else 0


def terminate_process(pid):
    """TerminateProcess port: True if a handle could be acquired and the
    terminate was sent. Does not check that the process actually exited."""
    handle = _kernel32.OpenProcess(0x0001, False, int(pid))  # PROCESS_TERMINATE
    if not handle:
        return False
    _kernel32.TerminateProcess(handle, 0)
    _kernel32.CloseHandle(handle)
    return True


def set_priority_realtime(pid):
    """Raise a process to Realtime priority (needs admin; Windows silently
    grants High otherwise - same behaviour as the AHK Process command)."""
    # PROCESS_SET_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION
    handle = _kernel32.OpenProcess(0x0200 | 0x1000, False, int(pid))
    if not handle:
        return False
    ok = bool(_kernel32.SetPriorityClass(handle, 0x100))  # REALTIME_PRIORITY_CLASS
    _kernel32.CloseHandle(handle)
    return ok


class ShellExecuteInfoW(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("fMask", wt.ULONG), ("hwnd", wt.HWND),
                ("lpVerb", wt.LPCWSTR), ("lpFile", wt.LPCWSTR),
                ("lpParameters", wt.LPCWSTR), ("lpDirectory", wt.LPCWSTR),
                ("nShow", ctypes.c_int), ("hInstApp", wt.HINSTANCE),
                ("lpIDList", ctypes.c_void_p), ("lpClass", wt.LPCWSTR),
                ("hkeyClass", ctypes.c_void_p), ("dwHotKey", wt.DWORD),
                ("hIconOrMonitor", wt.HANDLE), ("hProcess", wt.HANDLE)]


def launch(command, hide=False):
    """Port of the AHK Run command for the game launch string. Handles both
    plain executable paths ('C:\\...\\IdleDragons.exe') and URIs / launcher
    commands (EGS 'com.epicgames.launcher://...').

    Returns the PID of the started process, or 0 when only a shell action was
    triggered (URIs) - the caller then discovers the game PID by scanning,
    exactly as the AHK original does."""
    command = command.strip()
    show = 0 if hide else 1  # SW_HIDE / SW_SHOWNORMAL

    if "://" in command.split(" ", 1)[0]:
        # URI - hand to the shell, no meaningful PID comes back
        info = ShellExecuteInfoW()
        info.cbSize = ctypes.sizeof(info)
        info.fMask = 0x00000040  # SEE_MASK_NOCLOSEPROCESS
        info.lpVerb = "open"
        info.lpFile = command
        info.nShow = show
        if not _shell32.ShellExecuteExW(ctypes.byref(info)):
            raise OSError(f"ShellExecuteEx failed for: {command}")
        pid = 0
        if info.hProcess:
            pid = _kernel32.GetProcessId(info.hProcess) or 0
            _kernel32.CloseHandle(info.hProcess)
        return pid

    creationflags = 0x08000000 if hide else 0  # CREATE_NO_WINDOW
    startupinfo = None
    if hide:
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
    process = subprocess.Popen(command, creationflags=creationflags,
                               startupinfo=startupinfo, close_fds=True)
    return process.pid


# --- windows -------------------------------------------------------------------

def _enum_windows():
    hwnds = []
    EnumWindowsProc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def callback(hwnd, _lparam):
        hwnds.append(hwnd)
        return True

    _user32.EnumWindows(EnumWindowsProc(callback), 0)
    return hwnds


def window_pid(hwnd):
    pid = wt.DWORD(0)
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def find_window_by_pid(pid):
    """First top-level visible window of a process (WinExist ahk_pid)."""
    for hwnd in _enum_windows():
        if window_pid(hwnd) == pid and _user32.IsWindowVisible(hwnd):
            return hwnd
    return 0


def find_windows_by_exe(exe_name):
    """All top-level visible windows whose process matches the exe name
    (WinGet List ahk_exe). Returns [(hwnd, pid), ...]."""
    pids = set(find_pids(exe_name))
    result = []
    for hwnd in _enum_windows():
        pid = window_pid(hwnd)
        if pid in pids and _user32.IsWindowVisible(hwnd):
            result.append((hwnd, pid))
    return result


def find_window_by_exe(exe_name):
    windows = find_windows_by_exe(exe_name)
    return windows[0][0] if windows else 0


def window_exists(hwnd):
    return bool(hwnd) and bool(_user32.IsWindow(hwnd))


def get_active_window():
    return _user32.GetForegroundWindow()


def activate_window(hwnd):
    """WinActivate port."""
    if not hwnd:
        return
    _user32.ShowWindow(hwnd, 9)  # SW_RESTORE - in case minimised
    _user32.SetForegroundWindow(hwnd)


def control_focus(hwnd):
    """ControlFocus port: give the window keyboard focus without (necessarily)
    activating it, via thread input attachment."""
    if not hwnd:
        return False
    target_thread = _user32.GetWindowThreadProcessId(hwnd, None)
    current_thread = _kernel32.GetCurrentThreadId()
    if target_thread == current_thread:
        _user32.SetFocus(hwnd)
        return True
    if not _user32.AttachThreadInput(current_thread, target_thread, True):
        return False
    try:
        _user32.SetFocus(hwnd)
    finally:
        _user32.AttachThreadInput(current_thread, target_thread, False)
    return True


def send_message(hwnd, msg, wparam, lparam, timeout_ms=5000):
    """SendMessage port (SendMessageTimeout with SMTO_ABORTIFHUNG, as AHK)."""
    result = ctypes.c_void_p(0)
    ok = _user32.SendMessageTimeoutW(hwnd, msg, ctypes.c_void_p(wparam),
                                     ctypes.c_void_p(lparam),
                                     SMTO_ABORTIFHUNG, timeout_ms,
                                     ctypes.byref(result))
    return bool(ok)


def request_window_close(hwnd, timeout_ms=10000):
    """The polite close the AHK CloseIC uses: WM_SYSCOMMAND / SC_CLOSE."""
    return send_message(hwnd, WM_SYSCOMMAND, SC_CLOSE, 0, timeout_ms)


# --- keys ------------------------------------------------------------------------

def vk_from_scancode(scan_code):
    """Virtual-key code for a scan code (GetKeyVK port).

    AHK marks extended keys with bit 8 (e.g. 0x14B = Left arrow);
    MapVirtualKeyW's MAPVK_VSC_TO_VK_EX (3) wants the 0xE0 prefix form."""
    query = (0xE000 | (scan_code & 0xFF)) if scan_code & 0x100 else scan_code
    return _user32.MapVirtualKeyW(query, 3)


def send_key_down(hwnd, vk, lparam, timeout_ms=1000):
    return send_message(hwnd, WM_KEYDOWN, vk, lparam, timeout_ms)


def send_key_up(hwnd, vk, lparam, timeout_ms=3000):
    return send_message(hwnd, WM_KEYUP, vk, lparam, timeout_ms)
