# Platform-Specific Settings

When copying `IC_BrivMaster_Settings.json` from Windows to another platform, you **must update these settings** for your OS:

## Windows Settings
```json
{
  "IBM_Game_Exe": "IdleDragons.exe",
  "IBM_Game_Launch": "C:\\Temp\\legendary.exe launch \"Idle Champions of the Forgotten Realms\" --skip-version-check",
  "IBM_Game_Hide_Launcher": 0
}
```

## Linux Settings
```json
{
  "IBM_Game_Exe": "IdleDragons.exe",
  "IBM_Game_Launch": "/usr/lib64/heroic/resources/app.asar.unpacked/build/bin/x64/linux/legendary",
  "IBM_Game_Hide_Launcher": 0,
  "IBM_Level_Options_Mod_Key": "Shift",
  "IBM_Level_Options_Mod_Value": 10
}
```

**Note:** The launch command is auto-handled by the farm's X11 backend with proper app ID and Wine configuration.

**IMPORTANT - levelling modifier on Linux:** the game under Wine does not
see a virtual **Ctrl** key at all (a Wine raw-input quirk), so the default
Ctrl/x25 fine-levelling silently degrades to x100 presses and overshoots
your level caps past specialisation choices. **Shift/x10 works reliably** -
set `IBM_Level_Options_Mod_Key` to `"Shift"` and `IBM_Level_Options_Mod_Value`
to `10` as above. Targets that are not multiples of 10 stop just under the
cap (safe direction).

**IMPORTANT - apply your game-settings profile:** a fresh Heroic install
runs the game at 60fps / 720p / full particles / hero boxes hidden, which
makes key presses take over a second to register and breaks levelling
accuracy. Open the Home GUI, check the Game Settings profile, close the
game, and click **Set Now** once. (Profile: 600fps, particles 0, all hero
boxes shown, no background FPS cap.)

## Mac Settings
```json
{
  "IBM_Game_Exe": "IdleDragons.exe",
  "IBM_Game_Launch": "/Applications/Heroic.app/Contents/MacOS/Heroic",
  "IBM_Game_Hide_Launcher": 0
}
```

## Other Settings (Platform-Independent)
These are the same on all platforms - no changes needed:
```json
{
  "IBM_Route_Combine": 281,
  "IBM_Stack_Modron_Freq": 1345,
  "IBM_Offline_Freq": 1,
  "IBM_Feat_Guard": 1,
  "IBM_Favour_Limit": 1,
  "IBM_Max_Thellora_Stacks": 15,
  ... (all other settings are platform-agnostic)
}
```

---

## How to Update Settings

### Option A: Edit JSON Manually
1. Open `IC_BrivMaster_Settings.json` in a text editor
2. Find `IBM_Game_Launch` and `IBM_Game_Exe`
3. Replace with the values for your platform above
4. Save

### Option B: Use Home GUI
```bash
python3 -m brivmaster.home
```
1. Go to the **Settings** tab
2. Find the "Game" section
3. Update `IBM_Game_Exe` and `IBM_Game_Launch`
4. Click **Save Settings**

### Option C: Command Line
```bash
# View current settings
cat ../BrivMaster/IC_BrivMaster_Settings.json | grep -E "IBM_Game_Launch|IBM_Game_Exe"

# Edit with your editor
nano ../BrivMaster/IC_BrivMaster_Settings.json
```

---

## Testing After Update

After updating settings, test with:

```bash
# Validate configuration
python3 -m brivmaster.run_farm --dry-run

# Or just run the farm
python3 -m brivmaster.run_farm
```

If the game launches properly, you're good!

---

## Troubleshooting

### "Game won't launch automatically"
- Verify `IBM_Game_Launch` path exists on your system
- Try launching the game manually first from Heroic
- Check path separators (Windows: `\`, Linux/Mac: `/`)

### "Farm can't find game executable"
- Verify `IBM_Game_Exe` is correct (`IdleDragons.exe` on all platforms)
- Make sure game is installed in the expected location

### Game launches but farm doesn't start
- Make sure you have the correct offsets for your game version
- Check ptrace permission on Linux: `cat /proc/sys/kernel/yama/ptrace_scope` (should be 0)

---

## For New Users: Just Use setup_and_run.py

If you're setting up from scratch on a new platform, just use:

```bash
python3 setup_and_run.py
```

The universal setup script handles platform detection and uses sensible defaults for you. **No manual settings adjustment needed!**

---

## For Developers: How Auto-Detection Works

The farm backend auto-detects the platform:

```python
# In brivmaster/platform/__init__.py
if sys.platform.startswith('linux'):
    return x11.X11Backend()  # Uses hardcoded Heroic legendary
elif sys.platform == 'win32':
    return win32.Win32Backend()  # Uses IBM_Game_Launch setting
elif sys.platform == 'darwin':
    return x11.X11Backend()  # Uses hardcoded Mac launch
```

So even if `IBM_Game_Launch` is wrong, the farm can still work if the game is running. The launch command is only used if the game isn't already open.
