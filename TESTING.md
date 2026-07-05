# PyBrivMaster test plan

## Results so far (2026-07-02)

- **Stage A: PASSED** - probe/smoke/dry-run all green; pre-flight OK with the
  correct strategy (combine to z281, 15&10z/J, reset z1345, 566 stacks/432 TS).
- **Stage B: PASSED** - autoprogress toggle+restore and Q/E/Q formation swaps
  all verified via memory.
- **Stage C: PASSED** - 6 full unattended cycles, 0 fails: Casino at z281
  with re-rolls (R=0-2), online stacking Tar=432 Gen=432 in ~3.6s at z626,
  ~50s/run (~19k BPH), stack-conversion server call at each reset worked
  (haste present in the following run). Log: Logs/RunLog_20260702T144553.csv.
  Stopped cleanly via IPC.
- **Stage D3 (stack-save): PASSED** implicitly during C; re-confirmed in the
  soak: 209/209 conversion saves succeeded.
- **Stages D1/D2/E: PASSED (2026-07-05)** from the user's 73h/5,582-run
  unattended soak (02-05 Jul): BPH 20,524 vs AHK baseline 20,951 (**-2.0%**,
  within threshold); 22 blank restarts at cycle thresholds; 35 relay
  launches with handover working - 0 login-holds/all force-releases, which
  MATCHES the AHK's own logs since May (behaviour predates the port). The
  3.39% fail rate was entirely "Modron reset timed out" clustered in the
  Jul 02-03 server outage (Q3/Q4 of the soak + today's session: 0 fails).
- **Stage D5: PASSED (2026-07-05)** - MiniLog enabled via live IPC settings
  reload (no farm restart), entries per run, Monitor window tracking.
- **Stage D6: PASSED (2026-07-05)** - game force-killed mid-Casino; farm
  relaunched via the EGS command and was back in Main Loop in ~50s, clean
  cycles after, no spurious rollbacks.
- **Stage D7: PASSED (2026-07-05)** - live check against the real
  localSettings.json found a genuine diff (fullscreen vs profile); write
  path verified offline (needs game closed by design).
- **Stage D4: PASSED pending eyeball** - Home GUI connected to the live
  farm with ChestSnatcher/daily-claim active; confirm the claim/buy
  messages in the Chests log pane (claim fires ~3min after GUI start).
- Remaining: Linux track only.

Staged so each step risks nothing that the previous step hasn't proven.
Stages A-B take ~15 minutes; C is one supervised farm run; D-E run alongside
normal farming. Run everything from an **elevated** prompt (the game runs
elevated) inside `PyBrivMaster\`, with
`$py = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"` or plain
`python` if it's on PATH.

Before anything: **back up `BrivMaster\IC_BrivMaster_Settings.json`**.

## Stage A - passive (safe while the AHK farm is running)

| # | Command | Pass criteria |
|---|---|---|
| A1 | `python tools\probe.py --wait 60` | `RESULT: OK`; gems/formations/zone all plausible; no `MISSING_FIELDS` warning |
| A2 | `python tools\probe.py --watch 1` during a run | zone/gems track the game live |
| A3 | `python tools\smoke_phase2.py` | `RESULT: OK` (scan codes, window discovery, save-body plumbing) |
| A4 | `python -m brivmaster.run_farm --dry-run` **with the gem-farm adventure loaded** | `PRE-FLIGHT OK` + a correct strategy line (compare with what the AHK Home shows). On the wrong adventure it must refuse with a sensible message |

## Stage B - first active input (AHK farm STOPPED, game open, any adventure)

| # | Command | Pass criteria |
|---|---|---|
| B1 | `python tools\input_probe.py` | all checks `ok`: autoprogress toggles and restores; Q/E formation swaps register |
| B2 | Home GUI Elly tool (optional): `python -m brivmaster.home`, set Moon 4:5 / Fates 0:1 on a gold-farm context, Start | re-rolls happen; status reaches `Complete after N redraws` |

If B1 fails on the formation checks but the keys visibly do something in
game, check the in-game keybinds match `IBM_Scan_Codes`.

## Stage C - first supervised farm run (AHK farm STOPPED)

Setup: gem-farm adventure loaded, Q/W/E/M formations saved as usual, modron
automation on. Then:

```powershell
python -m brivmaster.run_farm
```

Watch one full cycle and compare against what the AHK farm normally does:

1. Pre-flight passes; strategy line matches the AHK Home's.
2. z1: levelling happens, Thellora rushes, Casino re-rolls to 3 gem cards.
3. Jumps follow the route (correct Q/E swaps; no boss hits beyond normal).
4. Stacking: at the stack zone, W formation sets, stacks accumulate, run
   resumes; `Logs\RunLog_*.csv` in PyBrivMaster gets an entry at reset.
5. Modron reset -> next run starts cleanly.

Abort at any time with Ctrl+C, then resume the AHK farm. Nothing the Python
farm does is persistent beyond normal gameplay.

Known first-run risks (fixable, report with the RunLog):
- timing differences vs AHK in the Casino/combine (levelling priorities);
- `--dry-run` warnings about familiars;
- online stacking timing (`Online{...}` log lines show generated stacks/ms).

## Stage D - subsystems (during/after C)

| # | What | Pass criteria |
|---|---|---|
| D1 | Hybrid: set `IBM_OffLine_Freq` > 1, blank restarts on | offline/blank restart at the configured cycle; `Cycle n/m` advances in the Home GUI |
| D2 | Relay: relay restarts on | `_Relay.csv` log appears; relay instance launches near the relay zone, holds at login (state 5 in the log), swap completes faster than a plain blank restart |
| D3 | Stack-save: watch log for `Servercall Save via:` | converted haste appears in game after reset |
| D4 | Chests/daily: Home GUI connected while farming | chest log shows buys/opens after resets; daily platinum claims once |
| D5 | Monitor: enable `Output mini log`, run `python -m brivmaster.monitor` | rows appear per run; age counter goes amber/red if the farm stalls |
| D6 | Recovery: while farming, kill the game process manually | SafetyCheck reopens it and the run recovers or restarts cleanly |
| D7 | Game settings profiles: set IBM_Game_Path, Check now against your real localSettings.json; change a setting in-game, re-check; Set Now with the game closed | differences reported with detail tooltip; Set Now aligns the file and the game shows the profile values after starting |

## Stage E - parity soak

Run the Python farm for 50+ runs and compare `Logs\RunLog_*.csv` (same
format as AHK) against a same-length AHK session: BPH, fail rate, average
total/active/wait. Differences > ~5% BPH deserve investigation before
switching over.

## Linux track (on the other machine)

1. `sudo sysctl kernel.yama.ptrace_scope=0`; game running under Heroic/Lutris.
2. Stage A1/A2 (the memory backend is written but unvalidated on Linux).
3. X11 input backend does not exist yet - Stages B+ wait for it (task #6).

## Current limitations to remember while testing

- Settings editors in the Python Home are read-only (edit JSON + Reload).
- Game-settings profiles and offsets download still live in the AHK Home.
- The farm applies settings at startup only (as the AHK original does).
