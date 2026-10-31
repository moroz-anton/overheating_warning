import logging
import math
from dataclasses import dataclass
from pathlib import Path
import sys
import threading
from typing import Iterable, Protocol


CPU_PREFIXES = ("/intelcpu/", "/amdcpu/")
# Intel "Distance to TjMax" sensors are headroom values, not temperatures.
RELATIVE_NAME_MARKERS = ("distance to tjmax",)
DRIVER_HINT = (
    "Run Overheating Warning as administrator and install the PawnIO driver "
    "(winget install namazso.PawnIO, or https://pawnio.eu)."
)


class SensorError(RuntimeError):
    pass


class SensorRow(Protocol):
    Identifier: str
    Name: str
    Value: object


@dataclass(frozen=True)
class Row:
    Identifier: str
    Name: str
    Value: object


@dataclass(frozen=True)
class Reading:
    identifier: str
    name: str
    celsius: float


def _celsius(identifier: str, raw: object) -> float:
    try:
        if raw is None or isinstance(raw, bool):
            raise ValueError("missing or boolean value")
        value = float(str(raw))
        if not math.isfinite(value) or not 0 < value <= 125:
            raise ValueError("outside plausible CPU temperature range")
    except (ValueError, TypeError) as error:
        raise SensorError(
            f"Invalid CPU temperature from {identifier}: {raw!r} ({error})"
        ) from error
    return value


def cpu_readings(
    rows: Iterable[SensorRow], sensor_ids: tuple[str, ...] = (),
    prefixes: tuple[str, ...] = CPU_PREFIXES,
) -> list[Reading]:
    readings = []
    seen = set()
    for row in rows:
        identifier = str(row.Identifier)
        name = str(row.Name)
        if sensor_ids:
            selected = identifier in sensor_ids
        else:
            selected = identifier.startswith(prefixes) and not any(
                marker in name.lower() for marker in RELATIVE_NAME_MARKERS
            )
        if not selected:
            continue
        seen.add(identifier)
        # Unreadable automatic sensors (e.g. a core without driver access) are skipped;
        # if nothing usable remains the caller still gets an error, never a safe value.
        if row.Value is None and not sensor_ids:
            continue
        readings.append(Reading(identifier, name, _celsius(identifier, row.Value)))
    missing = set(sensor_ids) - seen
    if missing:
        raise SensorError(f"Configured sensors are missing: {', '.join(sorted(missing))}")
    if not readings:
        raise SensorError("No CPU temperature values are available.")
    return readings


def lhm_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS")) / "lhm"
    return Path(__file__).resolve().parent.parent / "vendor" / "lhm"


_clr_lock = threading.Lock()
_clr_loaded = False


def _load_library(directory: Path) -> None:
    global _clr_loaded
    with _clr_lock:
        if _clr_loaded:
            return
        library = directory / "LibreHardwareMonitorLib.dll"
        if not library.is_file():
            raise SensorError(
                f"Bundled sensor library not found at {library}. "
                "Run 'python tools\\fetch_lhm.py' and rebuild."
            )
        import pythonnet

        pythonnet.load("netfx")
        import clr

        # pythonnet resolves dependent assemblies from sys.path.
        if str(directory) not in sys.path:
            sys.path.append(str(directory))
        clr.AddReference(str(library))
        _clr_loaded = True


class EmbeddedLibreHardwareMonitor:
    """Reads CPU sensors in-process with the bundled LibreHardwareMonitorLib."""

    label = "built-in LibreHardwareMonitorLib"

    def __init__(self, sensor_ids: tuple[str, ...] = ()) -> None:
        self.sensor_ids = sensor_ids
        self._computer = None

    def rows(self) -> list[Row]:
        try:
            if self._computer is None:
                _load_library(lhm_directory())
                from LibreHardwareMonitor.Hardware import Computer

                computer = Computer()
                computer.IsCpuEnabled = True
                computer.Open()
                self._computer = computer
                logging.info("Opened built-in LibreHardwareMonitorLib")
            from LibreHardwareMonitor.Hardware import HardwareType, SensorType

            rows = []
            for hardware in self._computer.Hardware:
                if hardware.HardwareType != HardwareType.Cpu:
                    continue
                hardware.Update()
                for sensor in hardware.Sensors:
                    if sensor.SensorType == SensorType.Temperature:
                        value = sensor.Value
                        rows.append(Row(
                            str(sensor.Identifier), str(sensor.Name),
                            None if value is None else float(value),
                        ))
            return rows
        except SensorError:
            raise
        except Exception as error:
            self.close()
            raise SensorError(f"Built-in sensor library failed: {error}") from error

    def read(self) -> list[Reading]:
        rows = self.rows()
        if not rows:
            raise SensorError("No supported CPU was detected by LibreHardwareMonitorLib.")
        if all(row.Value is None for row in rows):
            raise SensorError(f"CPU detected but its temperature is unreadable. {DRIVER_HINT}")
        return cpu_readings(rows, self.sensor_ids)

    def close(self) -> None:
        computer, self._computer = self._computer, None
        if computer is not None:
            try:
                computer.Close()
            except Exception:
                logging.exception("Closing LibreHardwareMonitorLib failed")


class _WmiReader:
    namespace = ""
    query = ""

    def __init__(self) -> None:
        self._service = None

    def _rows(self) -> list[object]:
        import pywintypes
        import win32com.client

        try:
            if self._service is None:
                self._service = win32com.client.GetObject(
                    "winmgmts:{impersonationLevel=impersonate}!\\\\.\\" + self.namespace
                )
            return list(self._service.ExecQuery(self.query, "WQL", 0x30))
        except pywintypes.com_error as error:
            self._service = None
            raise SensorError(
                f"WMI query in {self.namespace} failed (provider not running or not supported)."
            ) from error

    def close(self) -> None:
        self._service = None


class LibreHardwareMonitorWmi(_WmiReader):
    """Reads a separately running Libre Hardware Monitor app through WMI."""

    label = "Libre Hardware Monitor app (WMI)"
    namespace = r"root\LibreHardwareMonitor"
    query = "SELECT Identifier, Name, Value FROM Sensor WHERE SensorType = 'Temperature'"

    def __init__(self, sensor_ids: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.sensor_ids = sensor_ids

    def read(self) -> list[Reading]:
        try:
            return cpu_readings(self._rows(), self.sensor_ids)
        except SensorError as error:
            raise SensorError(
                f"{error} The Libre Hardware Monitor app is not running or has no CPU data."
            ) from error


class AcpiThermalZones(_WmiReader):
    """Firmware ACPI thermal zones. Opt-in only: they may not track the CPU die."""

    label = "ACPI thermal zones (approximate)"
    namespace = r"root\cimv2"
    query = (
        "SELECT Name, HighPrecisionTemperature "
        "FROM Win32_PerfFormattedData_Counters_ThermalZoneInformation"
    )

    def __init__(self, sensor_ids: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.sensor_ids = sensor_ids

    def read(self) -> list[Reading]:
        rows = []
        for zone in self._rows():
            tenths_kelvin = zone.HighPrecisionTemperature
            value = None if tenths_kelvin in (None, 0) else int(tenths_kelvin) / 10 - 273.15
            rows.append(Row("/acpi/" + str(zone.Name).split("\\")[-1], str(zone.Name), value))
        return cpu_readings(rows, self.sensor_ids, prefixes=("/acpi/",))


class AutoSensor:
    """Uses the first source that currently provides CPU temperatures."""

    def __init__(self, readers: list) -> None:
        self.readers = readers
        self.active = None

    @property
    def label(self) -> str:
        return self.active.label if self.active is not None else "none"

    def read(self) -> list[Reading]:
        errors = []
        for reader in self.readers:
            try:
                readings = reader.read()
            except SensorError as error:
                errors.append(f"{reader.label}: {error}")
                continue
            if self.active is not reader:
                logging.info("Using CPU temperature source: %s", reader.label)
                self.active = reader
            return readings
        self.active = None
        raise SensorError("\n".join(errors))

    def close(self) -> None:
        for reader in self.readers:
            reader.close()


SOURCES = {
    "embedded": EmbeddedLibreHardwareMonitor,
    "lhm_wmi": LibreHardwareMonitorWmi,
    "acpi": AcpiThermalZones,
}


def create_reader(source: str, sensor_ids: tuple[str, ...] = ()) -> AutoSensor:
    names = ("embedded", "lhm_wmi") if source == "auto" else (source,)
    return AutoSensor([SOURCES[name](sensor_ids) for name in names])
