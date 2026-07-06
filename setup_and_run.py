#!/usr/bin/env python3
"""
BrivMaster Python Farm - Universal Setup & Launch Script
Works on Windows, Linux, and Mac
"""

import os
import sys
import platform
import subprocess
import json
from pathlib import Path

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


def print_header(text):
    print(f"\n{Colors.BLUE}{'='*70}{Colors.NC}")
    print(f"{Colors.BLUE}{text:^70}{Colors.NC}")
    print(f"{Colors.BLUE}{'='*70}{Colors.NC}\n")


def print_step(num, total, text):
    print(f"{Colors.BLUE}[{num}/{total}]{Colors.NC} {text}...", end=" ", flush=True)


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
            result = subprocess.run(cmd, shell=shell, capture_output=True, text=True, timeout=timeout)
            return result.returncode == 0, result.stdout.strip()
        else:
            result = subprocess.run(cmd, shell=shell, timeout=timeout)
            return result.returncode == 0
    except subprocess.TimeoutExpired:
        return False
    except Exception as e:
        return False


def check_python():
    """Verify Python version and architecture"""
    print_step(1, 8, "Checking Python")

    version_info = sys.version_info
    if version_info.major < 3 or (version_info.major == 3 and version_info.minor < 10):
        print_error(f"Python 3.10+ required (found {version_info.major}.{version_info.minor})")
        return False

    if sys.maxsize <= 2**32:
        print_error("64-bit Python required")
        return False

    arch = "64-bit" if sys.maxsize > 2**32 else "32-bit"
    print_ok(f"Python {version_info.major}.{version_info.minor} ({arch})")
    return True


def install_dependencies():
    """Install required Python packages"""
    print_step(2, 8, "Checking dependencies")

    required_packages = {
        'Xlib': 'python-xlib',
        'pynput': 'pynput',
        'PySide6': 'PySide6',
    }

    missing = []
    for module, package in required_packages.items():
        try:
            __import__(module)
        except ImportError:
            missing.append(package)

    if missing:
        print(f"Installing {', '.join(missing)}...", flush=True)
        cmd = [sys.executable, "-m", "pip", "install", "-q"] + missing
        if not run_command(cmd, timeout=600):
            print_error(f"Failed to install packages: {', '.join(missing)}")
            return False
        print_ok("Packages installed")
    else:
        print_ok("All packages installed")

    return True


def check_ptrace_permission():
    """Check and set ptrace permission (Linux only)"""
    os_type = get_os()

    if os_type != "linux":
        print_step(3, 8, "Checking ptrace permission")
        print_ok("Not required on this OS")
        return True

    print_step(3, 8, "Checking ptrace permission")

    try:
        with open("/proc/sys/kernel/yama/ptrace_scope", "r") as f:
            scope = f.read().strip()

        if scope == "0":
            print_ok("ptrace_scope = 0")
            return True
        else:
            print("Setting ptrace_scope to 0...", flush=True)
            # Try to set it
            cmd = "echo 0 | sudo tee /proc/sys/kernel/yama/ptrace_scope > /dev/null 2>&1"
            if run_command(cmd, shell=True):
                print_ok("ptrace_scope set to 0")
                return True
            else:
                print_warning("Could not set ptrace_scope automatically")
                print("  Run this manually: sudo sysctl kernel.yama.ptrace_scope=0")
                return True  # Non-critical, continue anyway
    except Exception as e:
        print_warning(f"Could not check ptrace_scope: {e}")
        return True


def check_heroic(os_type):
    """Verify Heroic installation"""
    print_step(4, 8, "Checking Heroic installation")

    if os_type == "windows":
        # On Windows, Heroic might be in AppData or Program Files
        possible_paths = [
            Path(os.getenv('PROGRAMFILES', '') + r'\Heroic Game Launcher'),
            Path(os.getenv('LOCALAPPDATA', '') + r'\Heroic'),
        ]
        # Just check if we can import the backend without explicit path
        print_ok("Windows (legendary not needed)")
        return True

    elif os_type == "linux":
        legendary_path = "/usr/lib64/heroic/resources/app.asar.unpacked/build/bin/x64/linux/legendary"
        if Path(legendary_path).exists():
            print_ok("Heroic found")
            return True
        else:
            print_error(f"Heroic not found at {legendary_path}")
            print("  Install from: https://heroicgameslauncher.com")
            print("  Then launch Idle Champions once from Heroic GUI")
            return False

    elif os_type == "macos":
        # On Mac, check common installation paths
        possible_paths = [
            Path.home() / "Applications" / "Heroic.app",
            Path("/Applications/Heroic.app"),
        ]
        for path in possible_paths:
            if path.exists():
                print_ok("Heroic found")
                return True

        print_warning("Heroic not found in standard locations")
        print("  Install from: https://heroicgameslauncher.com")
        return True  # Non-critical, continue

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
    print_step(5, 8, "Checking game installation")

    if os_type == "windows":
        # Windows: check in Heroic directory
        possible_paths = [
            Path(os.path.expanduser("~")) / "Games" / "Heroic" / "IdleChampions" / "IdleDragons.exe",
            Path.home() / "Games" / "IdleChampions" / "IdleDragons.exe",
        ]
        for path in possible_paths:
            if path.exists():
                print_ok(f"Game found")
                return True

        print_warning("Game not found in standard locations")
        print("  Make sure Idle Champions is installed via Heroic")
        return True  # Non-critical

    elif os_type == "linux":
        game_path = Path.home() / "Games" / "Heroic" / "IdleChampions" / "IdleDragons.exe"
        if game_path.exists():
            print_ok("Game found")
            return True
        else:
            print_error(f"Game not found at {game_path}")
            print("  Install via Heroic launcher")
            return False

    elif os_type == "macos":
        possible_paths = [
            Path.home() / "Games" / "IdleChampions" / "IdleDragons.exe",
            Path("/Applications/IdleChampions/IdleDragons.exe"),
        ]
        for path in possible_paths:
            if path.exists():
                print_ok("Game found")
                return True

        print_warning("Game not found in standard locations")
        return True  # Non-critical

    return True


def check_offsets():
    """Verify offsets are available"""
    print_step(6, 8, "Checking offsets")

    offsets_file = Path("../BrivMaster/Offsets/IC_Offsets.json")

    if not offsets_file.exists():
        # Check local Offsets directory
        offsets_file = Path("Offsets/IC_Offsets.json")
        if not offsets_file.exists():
            print_error(f"Offsets not found")
            print("  Copy from Windows BrivMaster install or download via Home GUI")
            return False

    try:
        with open(offsets_file) as f:
            data = json.load(f)
            if "Offsets" in data:
                print_ok("Offsets valid")
                return True
    except:
        print_error("Offsets file is invalid")
        return False

    print_ok("Offsets found")
    return True


def configure_game_path(os_type):
    """Auto-detect and set IBM_Game_Path and platform-specific IBM_Game_Launch"""
    print_step(7, 8, "Configuring game paths")

    game_path = find_game_path(os_type)
    if not game_path:
        print_warning("Could not auto-detect game path")
        return True  # Non-critical

    try:
        settings_file = Path("../BrivMaster/IC_BrivMaster_Settings.json")
        if not settings_file.exists():
            print_ok("Settings file not found, using defaults")
            return True

        with open(settings_file, "r", encoding="utf-8") as f:
            settings = json.load(f)

        changed = False

        # Update IBM_Game_Path
        if settings.get("IBM_Game_Path") != game_path:
            settings["IBM_Game_Path"] = game_path
            changed = True

        # Update IBM_Game_Launch for this platform
        if os_type == "linux":
            launch_cmd = "/usr/lib64/heroic/resources/app.asar.unpacked/build/bin/x64/linux/legendary"
            if settings.get("IBM_Game_Launch") != launch_cmd:
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
                json.dump(settings, f, indent=2)
            print_ok(f"Settings updated for {os_type.capitalize()}")
        else:
            print_ok("Settings already configured")

        return True
    except Exception as e:
        print_warning(f"Could not configure settings: {e}")
        return True  # Non-critical


def validate_setup():
    """Quick validation that everything works"""
    print_step(8, 8, "Validating environment")

    # Test memory module import
    try:
        from brivmaster.memory.functions import MemoryFunctions
        from brivmaster.platform import window_backend
        print_ok("Environment validated")
        return True
    except Exception as e:
        print_error(f"Validation failed: {e}")
        return False


def launch_farm():
    """Launch the farm"""
    print_header("Starting BrivMaster Farm")

    print("Settings: ../BrivMaster/IC_BrivMaster_Settings.json")
    print("Offsets:  ../BrivMaster/Offsets/IC_Offsets.json")
    print("")
    print("The farm will:")
    print("  • Run cycles automatically")
    print("  • Auto-restart the game every X runs")
    print("  • Log results to Logs/RunLog_*.csv")
    print("  • Continue until stopped (Ctrl+C)")
    print("")

    os_type = get_os()
    if os_type == "linux":
        print(f"{Colors.YELLOW}Make sure the game is loaded in the gem-farm adventure!{Colors.NC}")

    input("Press ENTER to start the farm...")
    print("")

    # Launch the farm
    cmd = [sys.executable, "-m", "brivmaster.run_farm"]
    subprocess.run(cmd)


def main():
    """Main setup flow"""
    print_header("BrivMaster Python Farm - Universal Setup & Launch")

    os_type = get_os()

    if os_type == "unknown":
        print_error(f"Unsupported OS: {platform.system()}")
        return 1

    print(f"Detected OS: {platform.system()}\n")

    # Run all checks
    checks = [
        ("Python version", check_python),
        ("Dependencies", install_dependencies),
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
