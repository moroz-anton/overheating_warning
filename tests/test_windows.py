from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from overheating_warning.config import Config
from overheating_warning.monitor import Status, monitor
from overheating_warning.sensors import Reading, SensorError
from overheating_warning import startup


@unittest.skipUnless(sys.platform == "win32", "Windows APIs required")
class WindowsTests(unittest.TestCase):
    def test_source_launch_uses_absolute_windowless_interpreter_and_launcher(self):
        args = startup.launch_args()
        self.assertTrue(args[0].endswith("pythonw.exe"))
        self.assertTrue(args[1].endswith("run.py"))

    def test_frozen_launch_uses_executable_only(self):
        with patch.object(sys, "frozen", True, create=True), patch.object(
            sys, "executable", r"C:\My Tools\OverheatingWarning.exe",
        ):
            self.assertEqual(startup.launch_args(), [r"C:\My Tools\OverheatingWarning.exe"])

    def test_task_xml_is_elevated_battery_safe_and_escaped(self):
        import xml.etree.ElementTree as ET

        xml = startup.task_xml([r"C:\A&B\pythonw.exe", r"C:\My Tools\run.py"], r"PC\me")
        root = ET.fromstring(xml.split("?>", 1)[1])
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        self.assertEqual(root.find(".//t:RunLevel", ns).text, "HighestAvailable")
        self.assertEqual(root.find(".//t:DisallowStartIfOnBatteries", ns).text, "false")
        self.assertEqual(root.find(".//t:StopIfGoingOnBatteries", ns).text, "false")
        self.assertEqual(root.find(".//t:ExecutionTimeLimit", ns).text, "PT0S")
        self.assertEqual(root.find(".//t:Command", ns).text, r"C:\A&B\pythonw.exe")
        self.assertEqual(root.find(".//t:Arguments", ns).text, '"C:\\My Tools\\run.py"')
        self.assertEqual(root.find(".//t:LogonTrigger/t:UserId", ns).text, r"PC\me")

    def test_task_scheduler_accepts_generated_task(self):
        # A least-privilege variant can be registered without elevation, validating the XML.
        name = "OverheatingWarning-Test-" + uuid4().hex
        try:
            startup.register_task(name, startup.launch_args(), startup.current_user(), "LeastPrivilege")
            self.assertEqual(startup._schtasks("/Query", "/TN", name).returncode, 0)
        finally:
            startup.delete_task(name)
        self.assertNotEqual(startup._schtasks("/Query", "/TN", name).returncode, 0)

    def test_install_startup_requires_admin(self):
        with patch.object(startup, "is_admin", return_value=False), self.assertRaises(PermissionError):
            startup.install_startup()

    def test_worker_publishes_hottest_cpu_and_reports_sensor_failure(self):
        for result in (
            [Reading("/intelcpu/0/temperature/0", "Package", 91),
             Reading("/intelcpu/0/temperature/1", "Core", 95)],
            SensorError("Sensor provider offline"),
        ):
            updates = queue.Queue(maxsize=1)
            stop = threading.Event()
            with patch("overheating_warning.monitor.create_reader") as reader, patch.object(
                stop, "wait", side_effect=lambda _: stop.set(),
            ):
                if isinstance(result, SensorError):
                    reader.return_value.read.side_effect = result
                    with self.assertLogs(level="ERROR"):
                        monitor(Config(), updates, stop)
                else:
                    reader.return_value.read.return_value = result
                    monitor(Config(), updates, stop)
            status = updates.get_nowait()
            if isinstance(result, SensorError):
                self.assertIsNone(status.temperature)
                self.assertEqual(status.error, str(result))
            else:
                self.assertEqual(status.temperature, 95)

    def test_real_native_overlay_does_not_activate_and_is_click_through(self):
        import win32con
        import win32gui

        from overheating_warning.windows_ui import WindowsUI

        with tempfile.TemporaryDirectory() as directory:
            ui = WindowsUI(Config(sound=False), Path(directory) / "config.json", Path(directory) / "log")
            try:
                foreground = win32gui.GetForegroundWindow()
                ui._status = Status(94, True)
                ui._render(time.monotonic())
                win32gui.PumpWaitingMessages()
                self.assertTrue(win32gui.IsWindowVisible(ui.hwnd))
                self.assertEqual(win32gui.GetForegroundWindow(), foreground)
                styles = win32gui.GetWindowLong(ui.hwnd, win32con.GWL_EXSTYLE)
                for style in (
                    win32con.WS_EX_NOACTIVATE, win32con.WS_EX_TRANSPARENT,
                    win32con.WS_EX_TOPMOST, win32con.WS_EX_LAYERED,
                ):
                    self.assertTrue(styles & style)
                self.assertIn("94.0", ui._title)
                ui.test_warning()
                ui._render(time.monotonic())
                self.assertIn("CPU TOO HOT", ui._title)
                ui._status = Status(None, True, "Sensor lost")
                ui._render(time.monotonic())
                self.assertIn("UNAVAILABLE", ui._title)
                ui._status = Status(70, False)
                ui._test_until = 0
                ui._render(time.monotonic())
                self.assertFalse(win32gui.IsWindowVisible(ui.hwnd))
            finally:
                ui.close()

    def test_cli_help(self):
        result = subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parents[1] / "run.py"), "--help"],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--list-sensors", result.stdout)

    def test_stalled_sensor_worker_shows_unavailable_instead_of_old_safe_reading(self):
        from overheating_warning.windows_ui import WindowsUI

        with tempfile.TemporaryDirectory() as directory:
            ui = WindowsUI(Config(sound=False), Path(directory) / "config.json", Path(directory) / "log")
            ui._status = Status(60, False)
            rendered = []

            def render(now):
                rendered.append(ui._status)
                if ui._status.error:
                    ui._running = False

            with patch("overheating_warning.windows_ui.threading.Thread") as worker, patch(
                "overheating_warning.windows_ui.time.monotonic", side_effect=[0, 0, 11],
            ), patch("overheating_warning.windows_ui.time.sleep"), patch.object(
                ui, "_render", side_effect=render,
            ), self.assertLogs(level="ERROR"):
                worker.return_value.is_alive.return_value = False
                ui.run()
            self.assertEqual(rendered[0].temperature, 60)
            self.assertIsNone(rendered[-1].temperature)
            self.assertIn("No new CPU reading", rendered[-1].error)
