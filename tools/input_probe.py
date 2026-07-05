#!/usr/bin/env python3
"""Minimal ACTIVE input test - sends a handful of keys to the game and
verifies each via memory reads. Run this ONLY when the AHK farm is stopped
and you are OK with the game receiving input (any adventure is fine).

Sequence (about 5 seconds):
  1. press G twice (autoprogress toggle + restore), verifying the flag flips
  2. press Q, verify ReadMostRecentFormationFavorite() == 1
  3. press E, verify it becomes 3
  4. press Q again to leave the game on the farming formation

Usage: python tools/input_probe.py [--offsets PATH] [--exe NAME]
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brivmaster.memory.functions import MemoryFunctions  # noqa: E402
from brivmaster.platform import window_backend  # noqa: E402
from brivmaster.platform.input import InputManager  # noqa: E402
from probe import default_offsets_path  # noqa: E402


def wait_for(read, expected, timeout_s=2.0):
    deadline = time.monotonic() + timeout_s
    value = read()
    while value != expected and time.monotonic() < deadline:
        time.sleep(0.05)
        value = read()
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--offsets", default=default_offsets_path())
    parser.add_argument("--exe", default="IdleDragons.exe")
    args = parser.parse_args()

    print("!!! This sends keys to the game. The AHK farm must be STOPPED. "
          "Ctrl+C now to abort; starting in 5s...")
    time.sleep(5)
    memory = MemoryFunctions(args.offsets)
    if memory.AttachToReadyInstance(args.exe, wait_s=15) != "ready":
        print("FAILED: no fully-loaded game instance to attach to.")
        return 1
    win = window_backend()
    manager = InputManager(lambda: win.find_window_by_exe(args.exe))
    failures = 0

    initial_auto = memory.ReadAutoProgressToggled()
    print(f"Autoprogress before: {initial_auto}")
    manager.get_key("g").key_press()
    flipped = wait_for(memory.ReadAutoProgressToggled,
                       0 if initial_auto else 1)
    ok = flipped != initial_auto
    print(f"  G press -> {flipped}  [{'ok' if ok else 'FAIL'}]")
    failures += 0 if ok else 1
    manager.get_key("g").key_press()  # restore
    restored = wait_for(memory.ReadAutoProgressToggled, initial_auto)
    print(f"  G restore -> {restored}  "
          f"[{'ok' if restored == initial_auto else 'FAIL'}]")
    failures += 0 if restored == initial_auto else 1

    for key_name, favorite in (("q", 1), ("e", 3), ("q", 1)):
        manager.get_key(key_name).key_press()
        result = wait_for(memory.ReadMostRecentFormationFavorite, favorite)
        ok = result == favorite
        print(f"  {key_name.upper()} press -> mostRecentFavorite={result} "
              f"(expected {favorite})  [{'ok' if ok else 'FAIL'}]")
        failures += 0 if ok else 1
        time.sleep(0.5)

    print(f"\nRESULT: {'OK' if not failures else f'{failures} FAILURE(S)'}")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
