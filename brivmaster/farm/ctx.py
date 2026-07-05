"""Shared runtime context - the AHK globals, made explicit.

AHK name          -> here
g_IBM_Settings    -> ctx.settings (dict)
g_SF.Memory       -> ctx.memory (MemoryFunctions)
g_Heroes          -> ctx.heroes
g_InputManager    -> ctx.input
g_ServerCall      -> ctx.server
g_SharedData      -> ctx.shared
g_IBM             -> ctx.farm (GemFarm; also exposes .Logger/.RouteMaster/...)

'Critical On' / 'Thread, NoTimers' sections become ctx.critical - the only
other thread that sends input is the DialogSwatter, which honours the lock.
"""

from __future__ import annotations

import threading
import time


def tick_ms():
    """A_TickCount equivalent (monotonic milliseconds)."""
    return int(time.monotonic() * 1000)


def precise_sleep(sleep_ms):
    """IBM_Sleep port - accurate short sleep."""
    target = time.perf_counter() + sleep_ms / 1000.0
    while True:
        remaining = target - time.perf_counter()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.015))


def sleep_offset(base_perf_counter, offset_ms):
    """IBM_SleepOffset port - sleep until offset_ms after base_perf_counter
    (a time.perf_counter() value)."""
    target = base_perf_counter + offset_ms / 1000.0
    while True:
        remaining = target - time.perf_counter()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 0.015))


def ahk_time_format(ahk_format, default="%Y%m%dT%H%M%S"):
    """Convert an AHK FormatTime pattern (the IBM_Format_Date_* settings) to
    a strftime pattern. Covers the tokens AHK documents for dates/times."""
    if not ahk_format:
        return default
    result = []
    index = 0
    tokens = [("yyyy", "%Y"), ("yy", "%y"), ("MMMM", "%B"), ("MMM", "%b"),
              ("MM", "%m"), ("M", "%m"), ("dddd", "%A"), ("ddd", "%a"),
              ("dd", "%d"), ("d", "%d"), ("HH", "%H"), ("H", "%H"),
              ("hh", "%I"), ("h", "%I"), ("mm", "%M"), ("m", "%M"),
              ("ss", "%S"), ("s", "%S"), ("tt", "%p"), ("t", "%p")]
    while index < len(ahk_format):
        for token, strf in tokens:
            if ahk_format.startswith(token, index):
                result.append(strf)
                index += len(token)
                break
        else:
            char = ahk_format[index]
            result.append("%%" if char == "%" else char)
            index += 1
    return "".join(result)


class FarmContext:
    def __init__(self):
        self.settings = {}
        self.memory = None
        self.heroes = None
        self.input = None
        self.server = None
        self.shared = None
        self.farm = None
        self.ipc = None
        self.critical = threading.RLock()

    @property
    def logger(self):
        return self.farm.Logger if self.farm is not None else None

    def log(self, message):
        logger = self.logger
        if logger is not None:
            logger.AddMessage(message)
        else:
            print(f"[log] {message}")

    def setting(self, key, default=None):
        value = self.settings.get(key, default)
        return default if value is None else value
