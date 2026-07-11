#!/usr/bin/env python3
"""PyBrivMaster environment check & dependency installer.

Run with the Python you intend to use for Briv Master:

    python setup_check.py            # check + install what's missing
    python setup_check.py --check    # report only, install nothing

Verifies: Python version (3.10+) and bitness (64-bit required), pip,
required libraries (installs missing ones), that the brivmaster package
imports, and that the offsets/settings files are findable. Safe to re-run
any time; touches nothing but pip packages.
"""

from __future__ import annotations

import argparse
import importlib
import os
import platform
import struct
import subprocess
import sys

MIN_VERSION = (3, 10)
RECOMMENDED = "3.12"

# (pip name, import name, why, required?)
REQUIREMENTS = [
    ("PySide6", "PySide6", "Home GUI and Monitor windows", True),
]
WINDOWS_EXTRAS = [
    ("tzdata", "tzdata", "IANA timezone data for the Diana daily-reset "
                         "window (optional; a built-in fallback exists)", False),
]
LINUX_MAC_EXTRAS = [
    ("python-xlib", "Xlib", "X11 window discovery/control (farm input)", True),
    ("pynput", "pynput", "key injection to the game window", True),
]

PASS, FAIL, WARN = "[ ok ]", "[FAIL]", "[warn]"
failures = []
warnings = []


def report(status, message):
    print(f"  {status} {message}")
    if status == FAIL:
        failures.append(message)
    elif status == WARN:
        warnings.append(message)


def check_python():
    print("Python interpreter:")
    version = ".".join(map(str, sys.version_info[:3]))
    if sys.version_info >= MIN_VERSION:
        report(PASS, f"version {version} (minimum "
                     f"{'.'.join(map(str, MIN_VERSION))}, "
                     f"recommended {RECOMMENDED}+)")
    else:
        report(FAIL, f"version {version} is too old - install Python "
                     f"{RECOMMENDED} or newer from python.org")
    bits = struct.calcsize("P") * 8
    if bits == 64:
        report(PASS, "64-bit build")
    else:
        report(FAIL, f"{bits}-bit Python cannot read the 64-bit game - "
                     "install the 64-bit build")
    if sys.platform == "win32" and "WindowsApps" in sys.executable:
        report(WARN, "this looks like the Microsoft Store Python - the "
                     "python.org build is recommended")
    print(f"         interpreter: {sys.executable}")


def check_pip():
    print("pip:")
    try:
        import pip  # noqa: F401
        report(PASS, f"available (pip {pip.__version__})")
        return True
    except ImportError:
        pass
    try:
        subprocess.run([sys.executable, "-m", "ensurepip", "--upgrade"],
                       check=True, capture_output=True)
        report(PASS, "bootstrapped via ensurepip")
        return True
    except (subprocess.CalledProcessError, OSError):
        report(FAIL, "pip is missing and ensurepip failed - reinstall Python "
                     "with pip enabled")
        return False


def install(pip_name):
    # --break-system-packages: PEP 668 distros refuse pip installs outside a
    # venv; --user keeps it contained to the account either way
    for extra_args in ([], ["--user"], ["--user", "--break-system-packages"]):
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", pip_name,
             *extra_args], capture_output=True, text=True)
        if result.returncode == 0:
            return True
    print(f"         pip output: {result.stderr.strip()[-400:]}")
    return False


def check_requirements(do_install, have_pip):
    print("Required libraries:")
    requirements = list(REQUIREMENTS)
    if sys.platform == "win32":
        requirements += WINDOWS_EXTRAS
    else:
        requirements += LINUX_MAC_EXTRAS
    for pip_name, import_name, why, required in requirements:
        try:
            module = importlib.import_module(import_name)
            version = getattr(module, "__version__", "")
            report(PASS, f"{pip_name} {version} ({why})")
            continue
        except ImportError:
            pass
        if not do_install:
            report(FAIL if required else WARN,
                   f"{pip_name} missing ({why}) - run without --check to install")
            continue
        if not have_pip:
            report(FAIL if required else WARN,
                   f"{pip_name} missing and pip unavailable")
            continue
        print(f"         installing {pip_name}...")
        if install(pip_name):
            try:
                importlib.import_module(import_name)
                report(PASS, f"{pip_name} installed ({why})")
            except ImportError:
                report(FAIL if required else WARN,
                       f"{pip_name} installed but does not import - "
                       "restart the terminal and re-run this script")
        else:
            report(FAIL if required else WARN,
                   f"could not install {pip_name} - check network/permissions "
                   f"or run: {sys.executable} -m pip install {pip_name}")


def check_package():
    print("Briv Master package:")
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    modules = ["brivmaster.memory.functions", "brivmaster.farm.gem_farm",
               "brivmaster.server_call", "brivmaster.ipc",
               "brivmaster.relay", "brivmaster.run_farm"]
    for name in modules:
        try:
            importlib.import_module(name)
            report(PASS, name)
        except Exception as err:  # noqa: BLE001 - report whatever broke
            report(FAIL, f"{name}: {type(err).__name__}: {err}")
    for name in ("brivmaster.home.gui", "brivmaster.monitor"):
        try:
            importlib.import_module(name)
            report(PASS, f"{name} (GUI)")
        except Exception as err:  # noqa: BLE001
            report(WARN, f"{name}: {type(err).__name__}: {err} "
                         "(farm still works; GUI/Monitor won't)")


def check_data_files():
    print("Data files:")
    from brivmaster.farm.shared_data import default_settings_path
    from brivmaster.run_farm import default_offsets_path
    offsets = default_offsets_path()
    if os.path.isfile(offsets):
        report(PASS, f"offsets: {offsets}")
        for import_file in ("IC_IdleGameManager_Import.ahk",
                            "IC_GameSettings_Import.ahk",
                            "IC_EngineSettings_Import.ahk"):
            path = os.path.join(os.path.dirname(offsets), import_file)
            if not os.path.isfile(path):
                report(WARN, f"import file missing next to offsets: "
                             f"{import_file} (download offsets via the Home "
                             "GUI, BM Game tab)")
    else:
        report(WARN, f"offsets not found (looked at {offsets}) - copy your "
                     "platform's Offsets folder here or download via the "
                     "Home GUI")
    settings = default_settings_path()
    if os.path.isfile(settings):
        report(PASS, f"settings: {settings}")
    else:
        report(WARN, f"settings not found (looked at {settings}) - copy "
                     "IC_BrivMaster_Settings.json from your AHK install, or "
                     "the Home GUI will create defaults on first Save")


def platform_notes():
    if sys.platform == "win32":
        print("Windows notes:")
        print("         If the game runs elevated (admin), run Briv Master "
              "from an elevated prompt too.")
    elif sys.platform.startswith("linux"):
        print("Linux notes:")
        try:
            with open("/proc/sys/kernel/yama/ptrace_scope") as f:
                scope = f.read().strip()
        except OSError:
            scope = "0"  # no Yama LSM
        if scope == "0":
            print("         [ ok ] ptrace_scope = 0 (memory reads allowed)")
        else:
            report(WARN, f"ptrace_scope = {scope} - memory reads will fail; "
                         "run: sudo sysctl kernel.yama.ptrace_scope=0")
        if not os.environ.get("DISPLAY"):
            report(WARN, "no $DISPLAY - the farm needs an X server (Xorg or "
                         "XWayland); see setup_and_run.py for guidance")
        elif os.environ.get("XDG_SESSION_TYPE") == "wayland":
            print("         Wayland session: keys reach the game only while "
                  "its window is focused;")
            print("         the farm re-focuses it per key batch (KWin D-Bus "
                  "on KDE). To farm in the")
            print("         background, use BRIVMASTER_DISPLAY (see "
                  "SETTINGS_BY_PLATFORM.md).")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true",
                        help="report only; do not install anything")
    args = parser.parse_args()
    print(f"PyBrivMaster setup check - {platform.system()} "
          f"{platform.release()}, Python {platform.python_version()}\n")
    check_python()
    if failures:  # wrong interpreter - nothing else is meaningful
        print(f"\nRESULT: FAILED - fix the interpreter first: {failures}")
        return 1
    have_pip = check_pip()
    check_requirements(not args.check, have_pip)
    check_package()
    check_data_files()
    platform_notes()
    print()
    if failures:
        print(f"RESULT: {len(failures)} problem(s): {failures}")
        return 1
    if warnings:
        print(f"RESULT: OK with {len(warnings)} warning(s) - see [warn] "
              "lines above")
    else:
        print("RESULT: OK - everything ready")
    probe = "tools\\probe.py" if sys.platform == "win32" else "tools/probe.py"
    print(f"Next: python {probe} --wait 60   (game running; validates "
          "memory reads)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
