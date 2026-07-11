#!/usr/bin/env python3
"""
BrivMaster Python Farm - Universal Setup & Launch Script
Works on Windows, Linux, and Mac
"""

import os
import sys
import platform
import shutil
import subprocess
import json
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


# Color codes for terminal output
class Colors:
    BLUE = '\033[0;34m'
    GREEN = '\033[0;32m'
    RED = '\033[0;31m'
    YELLOW = '\033[1;33m'
    NC = '\033[0m'  # No Color

    @staticmethod
    def disable_on_windows():
        """Disable colors on Windows if not supporting ANSI"""
        if platform.system() == "Windows" and not sys.stdout.isatty():
            Colors.BLUE = Colors.GREEN = Colors.RED = Colors.YELLOW = Colors.NC = ''


Colors.disable_on_windows()

TOTAL_STEPS = 9
_current_step = 0


def print_header(text):
    print(f"\n{Colors.BLUE}{'='*70}{Colors.NC}")
    print(f"{Colors.BLUE}{text:^70}{Colors.NC}")
    print(f"{Colors.BLUE}{'='*70}{Colors.NC}\n")


def print_step(text):
    global _current_step
    _current_step += 1
    print(f"{Colors.BLUE}[{_current_step}/{TOTAL_STEPS}]{Colors.NC} {text}...",
          end=" ", flush=True)


def print_ok(text=""):
    if text:
        print(f"{Colors.GREEN}✓{Colors.NC} {text}")
    else:
        print(f"{Colors.GREEN}✓{Colors.NC}")


def print_error(text):
    print(f"\n{Colors.RED}✗ {text}{Colors.NC}")


def print_warning(text):
    print(f"{Colors.YELLOW}⚠{Colors.NC} {text}")


def get_os():
    """Detect operating system"""
    system = platform.system()
    if system == "Windows":
        return "windows"
    elif system == "Linux":
        return "linux"
    elif system == "Darwin":
        return "macos"
    else:
        return "unknown"


def run_command(cmd, shell=False, capture=False, timeout=30):
    """Run a shell command safely"""
    try:
        if capture:
            result = subprocess.run(cmd, shell=shell, capture_output=True,
                                    text=True, timeout=timeout)
            return result.returncode == 0, result.stdout.strip()
        else:
            result = subprocess.run(cmd, shell=shell, timeout=timeout)
            return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False if not capture else (False, "")
    except Exception:
        return False if not capture else (False, "")


def check_python():
    """Verify Python version and architecture"""
    print_step("Checking Python")

    version_info = sys.version_info
    if version_info.major < 3 or (version_info.major == 3 and version_info.minor < 10):
        print_error(f"Python 3.10+ required (found {version_info.major}.{version_info.minor})")
        return False

    if sys.maxsize <= 2**32:
        print_error("64-bit Python required (the game is 64-bit)")
        return False

    print_ok(f"Python {version_info.major}.{version_info.minor} (64-bit)")
    return True


# Distro package names for the pip fallback advice
_DISTRO_PACKAGES = {
    "dnf": "sudo dnf install python3-xlib python3-pynput python3-pyside6",
    "apt": "sudo apt install python3-xlib python3-pynput python3-pyside6",
    "pacman": "sudo pacman -S python-xlib python-pynput pyside6",
    "zypper": "sudo zypper install python3-xlib python3-pynput python3-pyside6",
}


def _pip_available():
    ok, _ = run_command([sys.executable, "-m", "pip", "--version"], capture=True)
    if ok:
        return True
    # pip missing entirely (minimal distro installs) - try to bootstrap it
    print("bootstrapping pip...", end=" ", flush=True)
    run_command([sys.executable, "-m", "ensurepip", "--upgrade"], timeout=120)
    ok, _ = run_command([sys.executable, "-m", "pip", "--version"], capture=True)
    return ok


def _pip_install(packages):
    """pip install with fallbacks for PEP 668 'externally managed' distros."""
    base = [sys.executable, "-m", "pip", "install", "-q"]
    for extra in ([], ["--user"], ["--user", "--break-system-packages"]):
        if run_command(base + extra + packages, timeout=600):
            return True
    return False


def _distro_install_hint():
    for tool, cmd in _DISTRO_PACKAGES.items():
        if shutil.which(tool):
            return cmd
    return _DISTRO_PACKAGES["apt"]


def install_dependencies():
    """Install required Python packages"""
    print_step("Checking dependencies")

    os_type = get_os()
    # (module, pip package, required?) - X/input libs only matter off-Windows;
    # PySide6 is only needed for the Home GUI and Monitor, the farm is
    # standard library + platform backend.
    wanted = []
    if os_type in ("linux", "macos"):
        wanted += [("Xlib", "python-xlib", True), ("pynput", "pynput", True)]
    wanted += [("PySide6", "PySide6", False)]

    missing_required, missing_optional = [], []
    for module, package, required in wanted:
        try:
            __import__(module)
        except ImportError:
            (missing_required if required else missing_optional).append(package)

    if not missing_required and not missing_optional:
        print_ok("All packages installed")
        return True

    to_install = missing_required + missing_optional
    print(f"installing {', '.join(to_install)}...", end=" ", flush=True)
    if not _pip_available():
        print_error("pip is not available and could not be bootstrapped")
        print(f"  Install via your distro instead: {_distro_install_hint()}")
        return not missing_required
    if _pip_install(to_install):
        print_ok("Packages installed")
        return True
    # Retry just the required set before giving up
    if missing_required and _pip_install(missing_required):
        print_ok("Required packages installed")
        if missing_optional:
            print_warning(f"Optional packages failed ({', '.join(missing_optional)}) "
                          "- the Home GUI/Monitor need them, the farm does not")
        return True
    if missing_required:
        print_error(f"Failed to install: {', '.join(missing_required)}")
        print("  Your distro may block pip (PEP 668). Alternatives:")
        print(f"    {_distro_install_hint()}")
        print(f"  or a venv: {sys.executable} -m venv .venv && "
              ". .venv/bin/activate && pip install " + " ".join(to_install))
        return False
    print_warning(f"Optional packages failed ({', '.join(missing_optional)}) "
                  "- the Home GUI/Monitor need them, the farm does not")
    return True


def check_display_stack():
    """Linux/Mac: an X connection (Xorg or XWayland) is mandatory - window
    discovery and key injection go through it. Also detect the compositor,
    since Wayland changes how window activation works."""
    print_step("Checking display server")

    os_type = get_os()
    if os_type == "windows":
        print_ok("Not required on Windows")
        return True

    display = os.environ.get("DISPLAY")
    if not display:
        print_error("No $DISPLAY - the farm needs an X server (Xorg or XWayland)")
        if os.environ.get("WAYLAND_DISPLAY"):
            print("  You are on Wayland without XWayland. Enable XWayland in your")
            print("  compositor (it is on by default in KDE/GNOME), or run the game")
            print("  on an isolated X server (see SETTINGS_BY_PLATFORM.md /")
            print("  BRIVMASTER_DISPLAY, e.g. with Xephyr or Xvfb).")
        else:
            print("  Headless/SSH session? Start one with Xvfb and point the farm")
            print("  at it:  Xvfb :9 &  then  BRIVMASTER_DISPLAY=:9 python run.py farm")
            print("  (install Xvfb via your distro, e.g. dnf/apt install xorg-x11-server-Xvfb / xvfb)")
        return False

    # Verify the display actually accepts connections and supports XTEST
    # (key injection uses it; Xorg and XWayland both ship it).
    try:
        from Xlib import display as xdisplay
        from Xlib.ext import xtest  # noqa: F401
        dpy = xdisplay.Display()
        has_xtest = dpy.query_extension("XTEST") is not None
        dpy.close()
        if not has_xtest:
            print_error(f"X server on {display} has no XTEST extension")
            print("  Key injection cannot work on this display.")
            return False
    except ImportError:
        print_warning(f"$DISPLAY={display} (python-xlib not importable yet - "
                      "re-run setup if dependency install just happened)")
        return True
    except Exception as err:
        print_error(f"Cannot connect to X display {display}: {err}")
        print("  If you are on Wayland, make sure XWayland is enabled.")
        return False

    session = os.environ.get("XDG_SESSION_TYPE", "")
    if session == "wayland":
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").upper()
        if "KDE" in desktop or shutil.which("kwin_wayland"):
            if shutil.which("busctl"):
                print_ok(f"XWayland on {display} (KDE Plasma - KWin D-Bus "
                         "activation available)")
            else:
                print_warning(f"XWayland on {display}, KDE detected but no "
                              "'busctl' - install systemd tools, or window "
                              "activation (and thus key injection) may fail")
        else:
            print_warning(f"XWayland on {display} ({desktop or 'unknown compositor'})")
            print("  On non-KDE Wayland compositors the farm may be unable to focus")
            print("  the game window, and keys only reach a focused window. If")
            print("  levelling does nothing, run the game on an isolated display")
            print("  (BRIVMASTER_DISPLAY - see SETTINGS_BY_PLATFORM.md) or use an")
            print("  X11 session.")
    else:
        print_ok(f"X11 session on {display}")
    return True


def check_ptrace_permission():
    """Check and set ptrace permission (Linux only)"""
    print_step("Checking ptrace permission")

    if get_os() != "linux":
        print_ok("Not required on this OS")
        return True

    try:
        with open("/proc/sys/kernel/yama/ptrace_scope", "r") as f:
            scope = f.read().strip()

        if scope == "0":
            print_ok("ptrace_scope = 0")
            return True
        print("setting ptrace_scope to 0 (needs sudo)...", flush=True)
        cmd = "echo 0 | sudo tee /proc/sys/kernel/yama/ptrace_scope > /dev/null"
        if run_command(cmd, shell=True, timeout=120):
            print_ok("ptrace_scope set to 0 (until reboot)")
            print("  To make it permanent: "
                  "echo 'kernel.yama.ptrace_scope = 0' | "
                  "sudo tee /etc/sysctl.d/99-brivmaster.conf")
            return True
        print_warning("Could not set ptrace_scope automatically")
        print("  Run this manually: sudo sysctl kernel.yama.ptrace_scope=0")
        print("  Without it, reading game memory will fail.")
        return True  # Non-critical here; the probe/farm will report clearly
    except FileNotFoundError:
        print_ok("No Yama LSM on this kernel (nothing to configure)")
        return True
    except Exception as e:
        print_warning(f"Could not check ptrace_scope: {e}")
        return True


# Where Heroic's bundled legendary CLI lives, by install method
# (mirrors brivmaster/platform/x11.py)
_LEGENDARY_CANDIDATES = (
    "/usr/lib64/heroic/resources/app.asar.unpacked/build/bin/x64/linux/legendary",
    "/usr/lib/heroic/resources/app.asar.unpacked/build/bin/x64/linux/legendary",
    "/opt/Heroic/resources/app.asar.unpacked/build/bin/x64/linux/legendary",
    str(Path.home() / ".var/app/com.heroicgameslauncher.hgl/config"
                      "/heroic/tools/legendary/legendary"),
)


def find_legendary():
    for path in _LEGENDARY_CANDIDATES:
        if Path(path).exists():
            return path
    return shutil.which("legendary")


def check_heroic(os_type):
    """Verify Heroic installation (used to launch/restart the game)"""
    print_step("Checking Heroic installation")

    if os_type == "windows":
        print_ok("Windows (legendary not needed)")
        return True

    if os_type == "linux":
        legendary = find_legendary()
        if legendary:
            print_ok(f"Heroic/legendary found")
            return True
        print_warning("Heroic/legendary not found (rpm, deb, /opt and flatpak "
                      "paths checked)")
        print("  Install from: https://heroicgameslauncher.com and launch")
        print("  Idle Champions once from the Heroic GUI.")
        print("  Without it the farm cannot auto-(re)start the game - you can")
        print("  still farm if you launch the game yourself.")
        return True  # farm works with a manually launched game

    if os_type == "macos":
        for path in (Path.home() / "Applications" / "Heroic.app",
                     Path("/Applications/Heroic.app")):
            if path.exists():
                print_ok("Heroic found")
                return True
        print_warning("Heroic not found in standard locations")
        print("  Install from: https://heroicgameslauncher.com")
        return True

    return True


def find_game_path(os_type):
    """Find game installation path and return it"""
    if os_type == "windows":
        possible = [
            Path(os.path.expanduser("~")) / "Games" / "Heroic" / "IdleChampions",
            Path.home() / "Games" / "IdleChampions",
        ]
    elif os_type == "linux":
        possible = [
            Path.home() / "Games" / "Heroic" / "IdleChampions",
            Path.home() / "Games" / "IdleChampions",
        ]
    elif os_type == "macos":
        possible = [
            Path.home() / "Games" / "IdleChampions",
            Path("/Applications/IdleChampions"),
        ]
    else:
        return None

    for path in possible:
        if (path / "IdleDragons_Data").exists():
            return str(path)
    return None


def check_game_installation(os_type):
    """Verify Idle Champions installation"""
    print_step("Checking game installation")

    game_path = find_game_path(os_type)
    if game_path:
        print_ok("Game found")
        return True
    print_warning("Game not found in standard locations")
    print("  Install Idle Champions via Heroic, or set IBM_Game_Path in the")
    print("  settings (Home GUI, BM Game tab) if it lives elsewhere.")
    return True  # Non-critical: a running game is found by process name


def offsets_candidates():
    """Everywhere the farm itself looks for IC_Offsets.json (both name
    cases - Linux filesystems are case-sensitive)."""
    return [
        SCRIPT_DIR / "Offsets" / "IC_Offsets.json",
        SCRIPT_DIR / "offsets" / "IC_Offsets.json",
        SCRIPT_DIR.parent / "BrivMaster" / "Offsets" / "IC_Offsets.json",
    ]


def check_offsets():
    """Verify offsets are available"""
    print_step("Checking offsets")

    for offsets_file in offsets_candidates():
        if not offsets_file.exists():
            continue
        try:
            with open(offsets_file, encoding="utf-8-sig") as f:
                json.load(f)
            print_ok(f"Offsets valid ({offsets_file.parent.name}/)")
            return True
        except Exception:
            print_error(f"Offsets file is invalid: {offsets_file}")
            return False

    print_error("Offsets not found")
    print("  Looked in: " + ", ".join(str(p.parent) for p in offsets_candidates()))
    print("  Download them via the Home GUI (BM Game tab), copy them from a")
    print("  Windows BrivMaster install, or fetch your platform's files from")
    print("  https://github.com/RLee-EN/BrivMaster-Imports")
    return False


def settings_candidates():
    """Where the farm looks for settings (mirrors shared_data.py)."""
    return [
        SCRIPT_DIR / "IC_BrivMaster_Settings.json",
        SCRIPT_DIR.parent / "BrivMaster" / "IC_BrivMaster_Settings.json",
    ]


def configure_game_path(os_type):
    """Auto-detect and set IBM_Game_Path and platform-specific IBM_Game_Launch"""
    print_step("Configuring game paths")

    settings_file = next((p for p in settings_candidates() if p.exists()), None)
    if settings_file is None:
        print_ok("No settings file yet - the Home GUI creates one on Save "
                 f"({settings_candidates()[-1].parent}/)")
        return True

    game_path = find_game_path(os_type)
    if not game_path:
        print_warning("Could not auto-detect game path (settings left as-is)")
        return True

    try:
        with open(settings_file, "r", encoding="utf-8-sig") as f:
            settings = json.load(f)

        changed = False
        if settings.get("IBM_Game_Path") != game_path:
            settings["IBM_Game_Path"] = game_path
            changed = True

        if os_type == "linux":
            launch_cmd = find_legendary()
            if launch_cmd and settings.get("IBM_Game_Launch") != launch_cmd:
                settings["IBM_Game_Launch"] = launch_cmd
                changed = True
        elif os_type == "macos":
            launch_cmd = "/Applications/Heroic.app/Contents/MacOS/Heroic"
            if settings.get("IBM_Game_Launch") != launch_cmd:
                settings["IBM_Game_Launch"] = launch_cmd
                changed = True
        # Windows settings stay as-is (they come with the settings file)

        if changed:
            with open(settings_file, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent="\t", sort_keys=True)
            print_ok(f"Settings updated for {os_type.capitalize()}")
        else:
            print_ok("Settings already configured")
        return True
    except Exception as e:
        print_warning(f"Could not configure settings: {e}")
        return True  # Non-critical


def validate_setup():
    """Quick validation that everything works"""
    print_step("Validating environment")

    try:
        from brivmaster.memory.functions import MemoryFunctions  # noqa: F401
        from brivmaster.platform import window_backend
        window_backend()  # resolves the platform backend and its imports
        print_ok("Environment validated")
        return True
    except Exception as e:
        print_error(f"Validation failed: {e}")
        return False


def launch_farm():
    """Launch the farm"""
    print_header("Starting BrivMaster Farm")

    print("The farm will:")
    print("  • Run cycles automatically")
    print("  • Auto-restart the game every X runs")
    print("  • Log results to Logs/RunLog_*.csv")
    print("  • Continue until stopped (Ctrl+C)")
    print("")

    os_type = get_os()
    if os_type in ("linux", "macos"):
        print(f"{Colors.YELLOW}Make sure the game is loaded in the gem-farm "
              f"adventure!{Colors.NC}")
        if os.environ.get("XDG_SESSION_TYPE") == "wayland":
            print(f"{Colors.YELLOW}Wayland: the farm keeps the game window "
                  f"focused while it sends keys.{Colors.NC}")

    input("Press ENTER to start the farm...")
    print("")

    cmd = [sys.executable, "-m", "brivmaster.run_farm"]
    subprocess.run(cmd, cwd=SCRIPT_DIR)


def main():
    """Main setup flow"""
    print_header("BrivMaster Python Farm - Universal Setup & Launch")

    os.chdir(SCRIPT_DIR)  # relative paths (offsets/settings) anchor here
    os_type = get_os()

    if os_type == "unknown":
        print_error(f"Unsupported OS: {platform.system()}")
        return 1

    print(f"Detected OS: {platform.system()}\n")

    checks = [
        ("Python version", check_python),
        ("Dependencies", install_dependencies),
        ("Display server", check_display_stack),
        ("Ptrace permission", check_ptrace_permission),
        ("Heroic launcher", lambda: check_heroic(os_type)),
        ("Game installation", lambda: check_game_installation(os_type)),
        ("Offsets", check_offsets),
        ("Game path", lambda: configure_game_path(os_type)),
        ("Environment", validate_setup),
    ]

    for name, check_func in checks:
        try:
            if not check_func():
                print_error(f"Setup failed at: {name}")
                return 1
        except Exception as e:
            print_error(f"Error checking {name}: {e}")
            return 1

    print_header("All Checks Passed! Ready to Farm")

    try:
        launch_farm()
    except KeyboardInterrupt:
        print("\n\nFarm stopped by user")
    except Exception as e:
        print_error(f"Farm error: {e}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
