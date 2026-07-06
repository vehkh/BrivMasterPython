"""Offsets check/download - port of CheckOffsetVersions/DownloadOffsets from
IC_BrivMaster_Home.ahk.

GitHub layout (IBM_Offsets_URL):
    IC_Offsets_Header_P<platform>.csv   'importVer,importRev,pointerVer,pointerRev'
    IC_Offsets_Data_P<platform>.zlib    base64 of a zlib stream of JSON:
        {"Imports": {"<Name>": "<generated AHK>", ...}, "Pointers": {...}}

Platform IDs: 11 Steam (and 18 CNE, treated as Steam), 21 Epic Games Store.
"""

from __future__ import annotations

import json
import os

from ..server_call import ServerCall, inflate_b64

PLATFORM_NAMES = {11: "Steam", 18: "CNE (as Steam)", 21: "Epic Games Store"}


def platform_name(platform_id):
    return PLATFORM_NAMES.get(platform_id, f"Unknown ({platform_id})")


def resolve_platform(memory, override=None):
    """Platform from the override, the game, or the offsets file metadata;
    18 is treated as 11."""
    platform_id = override or memory.ReadPlatform()
    if not platform_id:
        # The in-game platform pointer is not always readable (e.g. right
        # after a restart); the offsets file records what it was built for.
        try:
            platform_id = int(memory.Versions.get("Platform") or 0) or None
        except (AttributeError, TypeError, ValueError):
            platform_id = None
    if platform_id == 18:
        platform_id = 11
    return platform_id


def fetch_header(settings, platform_id):
    """Returns {'imports': 'ver rev', 'pointers': 'ver rev'} or None."""
    url = (settings.get("HUB", {}).get("IBM_Offsets_URL", "")
           + f"IC_Offsets_Header_P{platform_id}.csv")
    raw = ServerCall().BasicServerCallRaw(url)
    if not raw:
        return None
    parts = [part.strip() for part in raw.split(",")]
    if len(parts) < 4:
        return None
    return {"imports": f"{parts[0]} {parts[1]}",
            "pointers": f"{parts[2]} {parts[3]}",
            "imports_version": parts[0], "pointers_version": parts[2]}


def check_versions(memory, settings, platform_override=None):
    """CheckOffsetVersions port - returns a dict for display."""
    if not memory.IsAttached:
        # Attach on demand so the check works without a running farm
        exe = settings.get("IBM_Game_Exe", "IdleDragons.exe")
        memory.AttachToReadyInstance(exe, wait_s=0)
    result = {
        "game": None, "platform": None,
        "current_imports": memory.GetImportsVersion(),
        "current_pointers": (f"{memory.Versions['Pointer_Version_Major']}"
                             f"{memory.Versions['Pointer_Version_Minor']} "
                             f"{memory.Versions['Pointer_Revision']}"),
        "github_imports": None, "github_pointers": None,
    }
    game_major = memory.ReadBaseGameVersion()
    if game_major:
        result["game"] = f"{game_major}{memory.IBM_ReadGameVersionMinor() or ''}"
    platform_id = resolve_platform(memory, platform_override)
    if not platform_id:
        result["error"] = ("Unable to read the platform ID from the game - "
                           "pass one explicitly (11 Steam / 21 EGS)")
        return result
    result["platform"] = platform_name(platform_id)
    header = fetch_header(settings, platform_id)
    if header is None:
        result["error"] = "Unable to read offset header"
        return result
    result["github_imports"] = header["imports"]
    result["github_pointers"] = header["pointers"]
    return result


def download_offsets(memory, settings, offsets_dir, platform_override=None,
                     lock_pointers=None):
    """DownloadOffsets port. Writes IC_*_Import.ahk + IC_Offsets.json into
    offsets_dir. lock_pointers ('Imports only'): keep the existing pointer
    data, update only the import version fields. Returns a status string."""
    platform_id = resolve_platform(memory, platform_override)
    if not platform_id:
        return ("FAILED: unable to read the platform ID from the game - "
                "pass one explicitly (11 Steam / 21 EGS)")
    if lock_pointers is None:
        lock_pointers = bool(settings.get("HUB", {})
                             .get("IBM_Offsets_Lock_Pointers"))
    url = (settings.get("HUB", {}).get("IBM_Offsets_URL", "")
           + f"IC_Offsets_Data_P{platform_id}.zlib")
    raw = ServerCall().BasicServerCallRaw(url)
    if not raw:
        return "FAILED: unable to read offset data"
    offset_json = inflate_b64(raw.strip())
    if not offset_json:
        return "FAILED: offset data did not decompress"
    try:
        offset_data = json.loads(offset_json)
    except ValueError:
        return "FAILED: offset data is not valid JSON"
    os.makedirs(offsets_dir, exist_ok=True)
    for import_name, import_string in offset_data.get("Imports", {}).items():
        path = os.path.join(offsets_dir, f"IC_{import_name}_Import.ahk")
        with open(path, "w", encoding="utf-8") as f:
            f.write(import_string)
    pointers_path = os.path.join(offsets_dir, "IC_Offsets.json")
    new_pointers = offset_data.get("Pointers", {})
    warning = ""
    if lock_pointers:
        try:
            with open(pointers_path, "r", encoding="utf-8-sig") as f:
                existing = json.load(f)
        except (OSError, ValueError):
            existing = {}
        for key in ("Import_Version_Major", "Import_Version_Minor",
                    "Import_Revision"):
            existing[key] = new_pointers.get(key)
        if existing.get("Platform") != new_pointers.get("Platform"):
            warning = (" WARNING: 'Imports only' selected but downloaded "
                       f"platform ({new_pointers.get('Platform')}) differs "
                       f"from existing ({existing.get('Platform')}) - review!")
        to_write = existing
    else:
        to_write = new_pointers
    with open(pointers_path, "w", encoding="utf-8") as f:
        json.dump(to_write, f, indent="\t")
    return (f"Download complete for {platform_name(platform_id)} "
            f"(imports {new_pointers.get('Import_Version_Major')}"
            f"{new_pointers.get('Import_Version_Minor')} "
            f"{new_pointers.get('Import_Revision')}"
            f"{', pointers preserved' if lock_pointers else ''}). "
            "Restart Home and the farm to use the new offsets." + warning)
