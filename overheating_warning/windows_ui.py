import ctypes
import logging
import queue
import threading
import time
import winsound
from pathlib import Path

import win32api
import win32con
import win32gui

from .config import Config
from .monitor import Status, monitor


TRAY_MESSAGE = win32con.WM_APP + 1
MENU_TEST = 1
MENU_CONFIG = 2
MENU_LOG = 3
MENU_EXIT = 4
MENU_DIAGNOSE = 5
MSGFLT_ALLOW = 1
CLASS_NAME = "OverheatingWarningOverlay"


class WindowsUI:
    def __init__(self, config: Config, config_path: Path, log_path: Path) -> None:
        self.config = config
        self.config_path = config_path
        self.log_path = log_path
        self._running = True
        self._closed = False
        self._visible = False
        self._tray_added = False
        self._test_until = 0.0
        self._last_sound = float("-inf")
        self._alert_kind = ""
        self._title = ""
        self._body = ""
        self._status = Status(None, False, "Waiting for the first CPU sensor reading.")
        self._updates: queue.Queue[Status] = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._fonts: list[int] = []
        self._taskbar_created = win32gui.RegisterWindowMessage("TaskbarCreated")
        self._icon = win32gui.LoadIcon(0, win32con.IDI_WARNING)
        instance = win32api.GetModuleHandle(None)
        window_class = win32gui.WNDCLASS()
        window_class.hInstance = instance
        window_class.lpszClassName = CLASS_NAME
        window_class.lpfnWndProc = self._window_proc
        self._class_atom = win32gui.RegisterClass(window_class)
        self.hwnd = win32gui.CreateWindowEx(
            win32con.WS_EX_TOPMOST | win32con.WS_EX_TOOLWINDOW
            | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_LAYERED
            | win32con.WS_EX_TRANSPARENT,
            self._class_atom, "CPU Temperature Warning", win32con.WS_POPUP,
            0, 0, 480, 160, 0, 0, instance, None,
        )
        try:
            # When elevated, UIPI would otherwise drop Explorer's tray and restart messages.
            allow_message = ctypes.windll.user32.ChangeWindowMessageFilterEx
            allow_message.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
            for allowed in (TRAY_MESSAGE, self._taskbar_created, win32con.WM_COMMAND):
                allow_message(self.hwnd, allowed, MSGFLT_ALLOW, None)
            win32gui.SetLayeredWindowAttributes(self.hwnd, 0, 242, win32con.LWA_ALPHA)
            dpi = ctypes.windll.user32.GetDpiForWindow(self.hwnd)
            self._scale = dpi / 96.0
            for size, weight in ((23, 700), (17, 400)):
                font = win32gui.LOGFONT()
                font.lfFaceName = "Segoe UI"
                font.lfHeight = -round(size * self._scale)
                font.lfWeight = weight
                self._fonts.append(win32gui.CreateFontIndirect(font))
            self._add_tray()
        except Exception:
            self.close()
            raise

    def _add_tray(self) -> None:
        win32gui.Shell_NotifyIcon(win32gui.NIM_ADD, (
            self.hwnd, 0,
            win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP,
            TRAY_MESSAGE, self._icon, "CPU monitor: waiting for sensors",
        ))
        self._tray_added = True

    def _window_proc(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        try:
            if message == win32con.WM_PAINT:
                self._paint(hwnd)
                return 0
            if message == win32con.WM_MOUSEACTIVATE:
                return win32con.MA_NOACTIVATE
            if message == win32con.WM_NCHITTEST:
                return win32con.HTTRANSPARENT
            if message == TRAY_MESSAGE:
                if lparam == win32con.WM_RBUTTONUP:
                    self._menu(hwnd)
                elif lparam == win32con.WM_LBUTTONDBLCLK:
                    self.test_warning()
                return 0
            if message == self._taskbar_created:
                self._add_tray()
                return 0
            if message == win32con.WM_COMMAND:
                command = win32api.LOWORD(wparam)
                if command == MENU_EXIT:
                    self._running = False
                elif command == MENU_TEST:
                    self.test_warning()
                elif command == MENU_DIAGNOSE:
                    from .startup import launch_args
                    import subprocess

                    args = launch_args()
                    win32api.ShellExecute(
                        hwnd, "open", args[0],
                        subprocess.list2cmdline([*args[1:], "--list-sensors", "--no-elevate"]),
                        None, win32con.SW_SHOWNORMAL,
                    )
                elif command in (MENU_CONFIG, MENU_LOG):
                    path = self.config_path if command == MENU_CONFIG else self.log_path
                    win32api.ShellExecute(hwnd, "open", str(path), None, None, win32con.SW_SHOWNORMAL)
                return 0
            if message == win32con.WM_CLOSE:
                self._running = False
                return 0
            if message == win32con.WM_DESTROY:
                self._running = False
                return 0
        except Exception:
            logging.exception("Windows UI event failed")
            self._running = False
            # The native callback cannot propagate Python exceptions to the event loop.
            ctypes.windll.user32.MessageBoxW(
                None, "CPU monitor UI failed. Check monitor.log and restart the app.",
                "Overheating Warning", 0x10,
            )
            return 0
        return win32gui.DefWindowProc(hwnd, message, wparam, lparam)

    def _paint(self, hwnd: int) -> None:
        dc, paint = win32gui.BeginPaint(hwnd)
        brush = win32gui.CreateSolidBrush(win32api.RGB(43, 22, 22))
        old_font = None
        try:
            rect = win32gui.GetClientRect(hwnd)
            win32gui.FillRect(dc, rect, brush)
            if not self._fonts:
                return
            margin = round(18 * self._scale)
            win32gui.SetBkMode(dc, win32con.TRANSPARENT)
            win32gui.SetTextColor(dc, win32api.RGB(255, 195, 86))
            old_font = win32gui.SelectObject(dc, self._fonts[0])
            win32gui.DrawText(
                dc, self._title, -1,
                (margin, margin, rect[2] - margin, margin + round(34 * self._scale)),
                win32con.DT_LEFT | win32con.DT_SINGLELINE | win32con.DT_NOPREFIX,
            )
            win32gui.SelectObject(dc, self._fonts[1])
            win32gui.SetTextColor(dc, win32api.RGB(255, 255, 255))
            win32gui.DrawText(
                dc, self._body, -1,
                (margin, margin + round(42 * self._scale), rect[2] - margin, rect[3] - margin),
                win32con.DT_LEFT | win32con.DT_WORDBREAK | win32con.DT_NOPREFIX,
            )
        finally:
            if old_font is not None:
                win32gui.SelectObject(dc, old_font)
            win32gui.DeleteObject(brush)
            win32gui.EndPaint(hwnd, paint)

    def _menu(self, hwnd: int) -> None:
        menu = win32gui.CreatePopupMenu()
        try:
            for command, label in (
                (MENU_TEST, "Test warning (10 seconds)"),
                (MENU_DIAGNOSE, "Diagnose sensors"),
                (MENU_CONFIG, "Open configuration (restart to apply)"),
                (MENU_LOG, "Open log"),
                (MENU_EXIT, "Exit"),
            ):
                win32gui.AppendMenu(menu, win32con.MF_STRING, command, label)
            # Only an explicit tray click may activate the window for a native menu.
            win32gui.SetForegroundWindow(hwnd)
            x, y = win32gui.GetCursorPos()
            win32gui.TrackPopupMenu(
                menu, win32con.TPM_RIGHTBUTTON, x, y, 0, hwnd, None,
            )
            win32gui.PostMessage(hwnd, win32con.WM_NULL, 0, 0)
        finally:
            win32gui.DestroyMenu(menu)

    def test_warning(self) -> None:
        self._test_until = time.monotonic() + 10
        logging.info("Showing 10-second test warning")

    def _render(self, now: float) -> None:
        status = self._status
        if status.error:
            title = "CPU MONITOR UNAVAILABLE"
            body = "Temperature is unknown. Use the tray menu:\nDiagnose sensors / Open log."
            kind = "error"
            tooltip = "CPU monitor unavailable - use Diagnose sensors in the tray menu"
        elif status.overheating:
            title = f"CPU TOO HOT: {status.temperature:.1f} C"
            body = "Save your game and quit now. Let the laptop cool.\nTime before a hang cannot be predicted."
            kind = "hot"
            tooltip = title
        else:
            title = body = kind = ""
            tooltip = f"CPU: {status.temperature:.1f} C - monitoring"
            if status.source:
                tooltip += f" ({status.source})"
        # A test must never obscure a genuine heat warning or a monitoring failure.
        if not kind and now < self._test_until:
            title = "TEST: CPU TEMPERATURE WARNING"
            body = "This is a test, not an overheating alert.\nThe warning does not take focus or intercept clicks."
            kind = "test"
        if kind:
            self._title, self._body = title, body
            foreground = win32gui.GetForegroundWindow()
            display = win32api.MonitorFromWindow(foreground, win32con.MONITOR_DEFAULTTOPRIMARY)
            left, top, right, bottom = win32api.GetMonitorInfo(display)["Work"]
            width = min(round(500 * self._scale), right - left)
            height = min(round(165 * self._scale), bottom - top)
            win32gui.SetWindowPos(
                self.hwnd, win32con.HWND_TOPMOST,
                right - width - round(20 * self._scale), top + round(20 * self._scale),
                width, height, win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW,
            )
            win32gui.InvalidateRect(self.hwnd, None, False)
            self._visible = True
            if self.config.sound and (
                kind != self._alert_kind or now - self._last_sound >= self.config.repeat_sound_seconds
            ):
                winsound.PlaySound(
                    "SystemExclamation",
                    winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT,
                )
                self._last_sound = now
        elif self._visible:
            win32gui.ShowWindow(self.hwnd, win32con.SW_HIDE)
            self._visible = False
        self._alert_kind = kind
        win32gui.Shell_NotifyIcon(win32gui.NIM_MODIFY, (
            self.hwnd, 0, win32gui.NIF_TIP, TRAY_MESSAGE, self._icon, tooltip[:127],
        ))

    def run(self, test_only: bool = False) -> None:
        if test_only:
            self._status = Status(50.0, False)
            self.test_warning()
        else:
            self._thread = threading.Thread(
                target=monitor, args=(self.config, self._updates, self._stop),
                name="cpu-temperature", daemon=True,
            )
            self._thread.start()
        next_render = 0.0
        last_update = time.monotonic()
        stalled = False
        try:
            while self._running:
                win32gui.PumpWaitingMessages()
                now = time.monotonic()
                try:
                    self._status = self._updates.get_nowait()
                    last_update = now
                    stalled = False
                    next_render = 0
                except queue.Empty:
                    pass
                if not test_only and not stalled and now - last_update > max(
                    10.0, self.config.poll_seconds * 3,
                ):
                    logging.error("No new CPU reading received; the sensor worker may be stalled")
                    self._status = Status(
                        None, self._status.overheating,
                        "No new CPU reading received. Check the log and restart the app.",
                    )
                    stalled = True
                    next_render = 0
                if test_only and now >= self._test_until:
                    break
                if now >= next_render:
                    self._render(now)
                    next_render = now + 0.5
                time.sleep(0.05)
        finally:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
            if self._thread.is_alive():
                logging.warning("Sensor thread is still busy during shutdown")
        if self._tray_added:
            win32gui.Shell_NotifyIcon(win32gui.NIM_DELETE, (self.hwnd, 0))
        win32gui.DestroyWindow(self.hwnd)
        for font in self._fonts:
            win32gui.DeleteObject(font)
        win32gui.UnregisterClass(self._class_atom, win32api.GetModuleHandle(None))
