# Overheating Warning

A Python Windows 10/11 CPU temperature monitor for gaming laptops (built for an
AORUS / Gigabyte laptop, but works on any Intel or AMD laptop). When the CPU stays
above **90 °C for 10 seconds**, it shows a Steam/Ubisoft-style warning in the corner
of the screen and plays a sound. The warning is **click-through and never takes
focus**, so the game keeps running and you can save and quit. It lives in the
system tray and can start automatically when you sign in.

It does not close games, control fans, throttle the CPU, or shut down Windows.

## How it reads the CPU temperature

The executable bundles [LibreHardwareMonitorLib](https://github.com/LibreHardwareMonitor/LibreHardwareMonitor)
(MPL-2.0, pinned to v0.9.6, SHA-256 verified at build time) and reads the CPU's own
digital thermal sensors (package + every core). This uses the same sensors as
HWiNFO and Gigabyte Control Center. It is **independent of the laptop brand** and
supports the Intel Core / Core Ultra and AMD Ryzen CPUs used in AORUS laptops.
Reading these sensors needs two things:

1. **The PawnIO driver.** This is a one-time install of the signed driver that
   Libre Hardware Monitor uses. Run this in a terminal:
   ```powershell
   winget install namazso.PawnIO
   ```
   You can also get the installer from <https://pawnio.eu>.
2. **Administrator rights.** The app asks for UAC approval when started manually.
   The startup task (below) runs it elevated at sign-in without a prompt.

Sources, in the `sensor_source` setting:

| Value | Source |
|-------|--------|
| `auto` (default) | Built-in library, then a running Libre Hardware Monitor app (WMI) as fallback |
| `embedded` | Built-in library only |
| `lhm_wmi` | Only a separately running Libre Hardware Monitor app |
| `acpi` | Windows ACPI thermal zones. **Approximate**, opt-in only: on many laptops they are missing, lag, or are not the CPU die temperature |

## 1. Check your laptop first (one minute)

```powershell
.\OverheatingWarning.exe --list-sensors
```

Approve the UAC prompt. A report appears, also saved to
`%LOCALAPPDATA%\OverheatingWarning\diagnostics.txt`. It shows your laptop model,
CPU, admin/PawnIO status, and every CPU temperature found. You want:

```
READY: monitoring will use built-in LibreHardwareMonitorLib; hottest CPU sensor now 54.0 C.
```

Compare the values with Gigabyte Control Center's CPU temperature. Start a game
and check that they rise. If you see **NOT READY**, the report says why. Usually
PawnIO is missing or UAC was declined. The tray menu also has
**Diagnose sensors**.

Then test the warning **inside your game** (start the game first):

```powershell
.\OverheatingWarning.exe --test-overlay
```

A 10-second test banner appears in the top-right of the monitor the game is on.

## Warning behavior and settings

`%LOCALAPPDATA%\OverheatingWarning\config.json` (created on first run):

```json
{
  "threshold_c": 90.0,
  "sustained_seconds": 10.0,
  "recovery_c": 85.0,
  "recovery_seconds": 10.0,
  "poll_seconds": 2.0,
  "repeat_sound_seconds": 60.0,
  "sound": true,
  "sensor_ids": [],
  "sensor_source": "auto"
}
```

- The warning starts when the **hottest CPU sensor** stays **above 90 °C for
  10 continuous seconds**, so brief spikes are ignored. It reads
  "CPU TOO HOT: 93.5 C - Save your game and quit now".
- It stays visible until the CPU is at or below 85 °C for 10 seconds. The sound
  repeats every 60 seconds while it is hot.
- If the temperature cannot be read, a **CPU MONITOR UNAVAILABLE** banner is
  shown. A missing reading is never treated as "cool".
- `sensor_ids` can restrict monitoring to exact identifiers from `--list-sensors`,
  for example `["/intelcpu/0/temperature/18"]` (CPU Package). Leave it empty to use
  all CPU temperatures. Intel "Distance to TjMax" values are ignored automatically.
- Exit from the tray icon and restart after editing the file. Invalid settings
  produce an error rather than silently falling back to defaults.
- The log is in `%LOCALAPPDATA%\OverheatingWarning\monitor.log`.

## 2. Build the executable

Requires 64-bit Python 3.11+ on Windows. In PowerShell from this folder:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[build]"
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

`build.ps1` downloads and verifies the pinned LibreHardwareMonitorLib into
`vendor\lhm` (`tools\fetch_lhm.py`). It then runs the tests and produces a single
`dist\OverheatingWarning.exe` containing Python, pywin32, pythonnet, and the
sensor library. Copy that one file wherever you like, for example
`C:\Tools\OverheatingWarning\`. The .NET Framework 4.8 it uses is built into
Windows 10/11. Some antivirus products flag unsigned one-file executables.

To run from source instead: `.\.venv\Scripts\python.exe tools\fetch_lhm.py`, then
`.\.venv\Scripts\python.exe run.py [--list-sensors | --test-overlay]`.

## 3. Start automatically at sign-in

Put the executable in its permanent location first, then run:

```powershell
& 'C:\Tools\OverheatingWarning\OverheatingWarning.exe' --install-startup
```

Approve the UAC prompt once. This creates a Task Scheduler task named
**OverheatingWarning** for your user. The task:

- starts 10 seconds after sign-in, with highest privileges, so there is no UAC
  prompt at each login;
- keeps running on battery;
- has no time limit.

To remove it:

```powershell
& 'C:\Tools\OverheatingWarning\OverheatingWarning.exe' --remove-startup
```

Removing it does not stop an instance that is already running; use **Exit** in
the tray menu. Only one instance runs per session.

## Command-line options

| Option | Purpose |
|--------|---------|
| *(none)* | Run the tray monitor (asks for elevation if needed) |
| `--list-sensors` | Diagnose temperature sources and print/save the report |
| `--test-overlay` | Show a 10-second test warning, no sensors needed |
| `--install-startup` / `--remove-startup` | Manage the sign-in task |
| `--no-elevate` | Never show a UAC prompt; the built-in source then cannot read the CPU |

## Limitations

- **Game display mode.** The warning is a normal topmost window, so it shows over
  windowed, borderless, and most modern "fullscreen" games. Windows 10/11
  fullscreen optimizations make those behave like borderless. Some games use
  true *exclusive* fullscreen, and nothing can be drawn over them without
  injecting into the game, which this tool deliberately avoids because of
  anti-cheat. Use `--test-overlay` in each game. If it is hidden, switch the game
  to borderless. The sound alert plays regardless.
- **Timing.** A warning cannot guarantee one minute before a hang. If your laptop
  hangs soon after reaching 90 °C, lower `threshold_c` (for example 87) or
  `sustained_seconds`. Frequent overheating usually means blocked vents, dust, or
  dried thermal paste. Gigabyte Control Center's fan profiles (e.g. "Gaming" or
  "Turbo" mode) also help.
- **Verification status.** This version was built and tested on an Intel Core
  Ultra 7 265H laptop without PawnIO or admin rights. There the bundled library
  loaded and detected the CPU and all its sensors, and the tool correctly
  reported that it could not read temperatures and showed the "unavailable"
  warning. Readings with PawnIO and elevation, and on your specific AORUS model,
  have not been tested; check them with step 1 above.
