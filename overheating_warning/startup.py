import ctypes
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape


TASK_NAME = "OverheatingWarning"
# Older versions registered a per-user Run entry; it is removed on install/uninstall.
LEGACY_VALUE_NAME = "OverheatingWarning"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def launch_args() -> list[str]:
    """Absolute windowless command for this app (frozen exe or source checkout)."""
    executable = Path(sys.executable).resolve()
    if getattr(sys, "frozen", False):
        return [str(executable)]
    pythonw = executable.with_name("pythonw.exe")
    if not pythonw.is_file():
        raise RuntimeError(f"Windowless Python interpreter not found: {pythonw}")
    launcher = Path(__file__).resolve().parent.parent / "run.py"
    if not launcher.is_file():
        raise RuntimeError("Source startup requires run.py; use the executable instead.")
    return [str(pythonw), str(launcher)]


def current_user() -> str:
    import win32api
    import win32con

    return win32api.GetUserNameEx(win32con.NameSamCompatible)


def task_xml(args: list[str], user: str, run_level: str = "HighestAvailable") -> str:
    command, arguments = args[0], subprocess.list2cmdline(args[1:])
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Shows a non-intrusive warning when the CPU stays too hot.</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <UserId>{escape(user)}</UserId>
      <Delay>PT10S</Delay>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>{run_level}</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>false</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>5</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(command)}</Command>
      <Arguments>{escape(arguments)}</Arguments>
    </Exec>
  </Actions>
</Task>
"""


def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["schtasks", *args], capture_output=True, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def register_task(
    name: str, args: list[str], user: str, run_level: str = "HighestAvailable",
) -> None:
    handle, path = tempfile.mkstemp(suffix=".xml")
    os.close(handle)
    try:
        Path(path).write_text(task_xml(args, user, run_level), encoding="utf-16")
        result = _schtasks("/Create", "/TN", name, "/XML", path, "/F")
    finally:
        os.unlink(path)
    if result.returncode != 0:
        raise RuntimeError(f"Could not create scheduled task: {(result.stderr or result.stdout).strip()}")


def delete_task(name: str) -> None:
    if _schtasks("/Query", "/TN", name).returncode != 0:
        return
    result = _schtasks("/Delete", "/TN", name, "/F")
    if result.returncode != 0:
        raise RuntimeError(f"Could not delete scheduled task: {(result.stderr or result.stdout).strip()}")


def _remove_legacy_run_entry() -> None:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, LEGACY_VALUE_NAME)
    except FileNotFoundError:
        return


def install_startup() -> None:
    """Start elevated at sign-in without a UAC prompt. Requires administrator rights."""
    if not is_admin():
        raise PermissionError("Administrator rights are required to install startup.")
    register_task(TASK_NAME, launch_args(), current_user())
    _remove_legacy_run_entry()


def remove_startup() -> None:
    _remove_legacy_run_entry()
    delete_task(TASK_NAME)
