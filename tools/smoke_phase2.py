#!/usr/bin/env python3
"""Passive smoke test for the Phase 2 platform layer.

Verifies scan-code mapping, window discovery, and the server-call plumbing
(compression round-trip, save body, checksum) WITHOUT sending any input or
touching any process - safe to run while a real farm is active.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from brivmaster.platform import window_backend  # noqa: E402
from brivmaster.platform.input import DEFAULT_SCAN_CODES, InputManager  # noqa: E402
from brivmaster.server_call import ServerCall, deflate_b64, inflate_b64  # noqa: E402

FAILURES = []


def check(name, condition, detail=""):
    status = "ok" if condition else "FAIL"
    print(f"  [{status}] {name}{(' - ' + str(detail)) if detail else ''}")
    if not condition:
        FAILURES.append(name)


def main():
    backend = window_backend()

    print("Scan-code -> virtual-key mapping:")
    expected_vk = {"q": 0x51, "w": 0x57, "e": 0x45, "g": 0x47, "Esc": 0x1B,
                   "F1": 0x70, "F12": 0x7B, "1": 0x31, "0": 0x30,
                   "LCtrl": 0xA2, "Shift": 0xA0, "Left": 0x25}
    for key, vk_expected in expected_vk.items():
        vk = backend.vk_from_scancode(DEFAULT_SCAN_CODES[key])
        check(f"vk({key})", vk == vk_expected,
              f"got 0x{vk:X}, expected 0x{vk_expected:X}")

    print("Key object construction (no input sent):")
    manager = InputManager(hwnd_provider=lambda: 0)
    key_q = manager.get_key("q")
    check("q lparam_down", key_q.lparam_down == 0x10 << 16,
          hex(key_q.lparam_down))
    check("q lparam_up", key_q.lparam_up == (0xC0000001 | 0x10 << 16),
          hex(key_q.lparam_up))
    key_left = manager.get_key("Left")
    check("Left (extended) vk", key_left.vk == 0x25, hex(key_left.vk))
    check("unknown key returns None", manager.get_key("nosuchkey") is None)

    print("Window discovery (passive):")
    windows = backend.find_windows_by_exe("IdleDragons.exe")
    print(f"  game windows found: {windows}")
    if windows:
        hwnd, pid = windows[0]
        check("window_exists", backend.window_exists(hwnd))
        check("window_pid round-trip", backend.window_pid(hwnd) == pid)
        check("get_process_name", backend.get_process_name(pid),
              backend.get_process_name(pid))
    else:
        print("  (game not running or mid-restart - skipped)")

    print("Server-call plumbing (no network):")
    text = '{"stats":{"briv_steelbones_stacks":0,"briv_sprint_stacks":12345}}'
    packed = deflate_b64(text)
    check("deflate/inflate round-trip", inflate_b64(packed) == text)
    check("md5 salt checksum",
          ServerCall.MD5Save("test") ==
          __import__("hashlib").md5(
              b"testsomethingpoliticallycorrect").hexdigest())
    boundary = ServerCall.GetBoundryHeader()
    check("boundary format",
          boundary.startswith("BestHTTP_HTTPMultiPartForm_")
          and len(boundary) == len("BestHTTP_HTTPMultiPartForm_") + 8,
          boundary)
    call = ServerCall()
    call.userID, call.userHash = 123, "f" * 32
    body = call.GetSaveFromJSON(text, boundary)
    check("save body parts",
          body.count(f"--{boundary}\r\n") == 11
          and body.endswith(f"--{boundary}--\r\n"))
    check("save body has compressed details",
          f"name=\"details_compressed\"" in body and packed in body)

    print()
    if FAILURES:
        print(f"RESULT: {len(FAILURES)} FAILURE(S): {FAILURES}")
        return 1
    print("RESULT: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
