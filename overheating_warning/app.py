import argparse
import ctypes
import logging
from logging.handlers import RotatingFileHandler
import subprocess
import sys

from .config import data_directory, load_config
from .startup import install_startup, is_admin, launch_args, remove_startup

ERROR_CANCELLED = 1223


def _message(text: str, error: bool = False) -> None:
    if sys.stdout is not None:
        print(text, file=sys.stderr if error else sys.stdout)
    else:
        ctypes.windll.user32.MessageBoxW(
            None, text, "Overheating Warning", 0x10 if error else 0x40,
        )


def run_elevated(extra: list[str], wait: bool) -> int | None:
    """Relaunch this app with a UAC prompt. Returns None if the user declines."""
    import pywintypes
    import win32con
    import win32event
    import win32process
    from win32com.shell import shell, shellcon

    args = launch_args()
    try:
        info = shell.ShellExecuteEx(
            fMask=shellcon.SEE_MASK_NOCLOSEPROCESS, lpVerb="runas", lpFile=args[0],
            lpParameters=subprocess.list2cmdline([*args[1:], *extra]),
            nShow=win32con.SW_SHOWNORMAL,
        )
    except pywintypes.error as error:
        if error.winerror == ERROR_CANCELLED:
            return None
        raise
    handle = info["hProcess"]
    try:
        if not wait:
            return 0
        win32event.WaitForSingleObject(handle, win32event.INFINITE)
        return win32process.GetExitCodeProcess(handle)
    finally:
        handle.Close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Non-activating Windows CPU temperature warnings.")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--list-sensors", action="store_true", help="Diagnose CPU temperature sources and exit.")
    actions.add_argument("--test-overlay", action="store_true", help="Show a 10-second test without reading sensors.")
    actions.add_argument("--install-startup", action="store_true", help="Start (elevated) at this user's Windows sign-in.")
    actions.add_argument("--remove-startup", action="store_true", help="Remove the startup task.")
    parser.add_argument("--no-elevate", action="store_true", help="Never show a UAC prompt; CPU sensors may be unreadable.")
    args = parser.parse_args()
    if sys.platform != "win32":
        _message("Overheating Warning requires Windows 10 or 11.", error=True)
        return 1
    try:
        directory = data_directory()
        directory.mkdir(parents=True, exist_ok=True)
        log_path = directory / "monitor.log"
        handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        logging.basicConfig(
            level=logging.INFO, handlers=[handler],
            format="%(asctime)s %(levelname)s %(threadName)s %(message)s",
            force=True,
        )
        admin = is_admin()
        if args.install_startup or args.remove_startup:
            flag = "--install-startup" if args.install_startup else "--remove-startup"
            if not admin:
                code = run_elevated([flag], wait=True)
                if code is None:
                    _message("Administrator approval is required to change startup.", error=True)
                    return 1
                return code
            if args.install_startup:
                install_startup()
                _message(
                    "Startup enabled: the monitor will start elevated at sign-in without a UAC prompt.\n"
                    "Keep the executable in its current location."
                )
            else:
                remove_startup()
                _message("Startup disabled.")
            return 0
        config_path = directory / "config.json"
        config = load_config(config_path)
        needs_admin = config.sensor_source in ("auto", "embedded") and not args.test_overlay
        if needs_admin and not admin and not args.no_elevate:
            extra = ["--list-sensors"] if args.list_sensors else []
            if run_elevated([*extra, "--no-elevate"], wait=args.list_sensors) is not None:
                return 0
            logging.warning("UAC elevation declined; continuing without administrator rights")
        if args.list_sensors:
            import pythoncom

            from .diagnostics import report

            pythoncom.CoInitialize()
            try:
                text, ready = report(config)
            finally:
                pythoncom.CoUninitialize()
            report_path = directory / "diagnostics.txt"
            report_path.write_text(text + "\n", encoding="utf-8")
            _message(f"{text}\n\nSaved to {report_path}", error=not ready)
            return 0 if ready else 2

        import win32api
        import win32event

        # A local named mutex prevents duplicate tray icons and warning sounds.
        mutex = win32event.CreateMutex(None, False, r"Local\OverheatingWarning")
        try:
            if win32api.GetLastError() == 183:
                _message("Overheating Warning is already running. Use its system tray menu.")
                return 0
            set_dpi = ctypes.windll.user32.SetProcessDpiAwarenessContext
            set_dpi.argtypes = [ctypes.c_void_p]
            set_dpi.restype = ctypes.c_bool
            if not set_dpi(ctypes.c_void_p(-4)):
                logging.info("DPI awareness was already set or per-monitor V2 is unavailable")
            from .windows_ui import WindowsUI

            logging.info(
                "Starting CPU monitor (test_only=%s, admin=%s, source=%s)",
                args.test_overlay, admin, config.sensor_source,
            )
            WindowsUI(config, config_path, log_path).run(test_only=args.test_overlay)
            logging.info("CPU monitor exited")
        finally:
            win32api.CloseHandle(mutex)
        return 0
    except Exception as error:
        logging.exception("Overheating Warning failed")
        _message(f"Overheating Warning failed:\n{error}\n\nCheck %LOCALAPPDATA%\\OverheatingWarning\\monitor.log.", error=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
