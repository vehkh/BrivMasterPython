"""Platform-specific layers (window control, key injection, process launch).

Windows (win32.py) is the reference implementation, ported 1:1 from the AHK
original. Linux/X11 backend (x11.py) uses python-xlib for window discovery
and XSendEvent for key injection.
"""

import sys


def window_backend():
    """The window/input/process module for the current platform."""
    if sys.platform == "win32":
        from . import win32
        return win32
    elif sys.platform.startswith("linux"):
        from . import x11
        return x11
    raise NotImplementedError(
        f"No platform backend for {sys.platform} yet")
