"""Platform-specific layers (window control, key injection, process launch).

Windows (win32.py) is the reference implementation, ported 1:1 from the AHK
original. A Linux/X11 backend implementing the same module interface is
planned (see the Linux track task); everything above this package must stay
platform-agnostic.
"""

import sys


def window_backend():
    """The window/input/process module for the current platform."""
    if sys.platform == "win32":
        from . import win32
        return win32
    raise NotImplementedError(
        "No platform backend for this OS yet (Linux/X11 backend is on the "
        "Linux track task)")
