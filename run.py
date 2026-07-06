#!/usr/bin/env python3
"""Convenience launcher for BrivMasterPython.

Short aliases for the `python -m brivmaster.<x>` commands:

    python run.py home        # Home GUI     (= python -m brivmaster.home)
    python run.py farm        # gem farm     (= python -m brivmaster.run_farm)
    python run.py monitor     # run monitor  (= python -m brivmaster.monitor)
    python run.py probe       # memory probe (= python tools/probe.py)
    python run.py setup       # env check    (= python setup_check.py)

Extra arguments pass straight through, e.g.:
    python run.py farm --dry-run
    python run.py probe --wait 60
    python run.py setup --check

On Windows you can also just double-click run.bat (launches the Home GUI).
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# command -> argv passed to this same Python interpreter
COMMANDS = {
    "home": ["-m", "brivmaster.home"],
    "farm": ["-m", "brivmaster.run_farm"],
    "run": ["-m", "brivmaster.run_farm"],          # alias of farm
    "monitor": ["-m", "brivmaster.monitor"],
    "relay": ["-m", "brivmaster.relay"],
    "probe": [os.path.join("tools", "probe.py")],
    "input-probe": [os.path.join("tools", "input_probe.py")],
    "setup": ["setup_check.py"],
}
VISIBLE = [name for name in COMMANDS if name != "run"]


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help", "help"):
        print(__doc__)
        print("commands:", ", ".join(VISIBLE))
        return 0 if args else 1
    name, rest = args[0], args[1:]
    if name not in COMMANDS:
        print(f"unknown command: {name}\n")
        print(__doc__)
        print("commands:", ", ".join(VISIBLE))
        return 2
    # Run from this directory so package/offsets auto-location behaves the
    # same no matter where run.py was invoked from.
    return subprocess.call([sys.executable] + COMMANDS[name] + rest, cwd=HERE)


if __name__ == "__main__":
    sys.exit(main())
