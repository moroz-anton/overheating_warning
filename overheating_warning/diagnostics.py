import winreg

from .config import Config
from .sensors import SOURCES, SensorError, create_reader
from .startup import is_admin


def pawnio_installed() -> bool:
    for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\PawnIO",
                0, winreg.KEY_READ | view,
            ):
                return True
        except OSError:
            continue
    return False


def _system_lines() -> list[str]:
    try:
        import win32com.client

        service = win32com.client.GetObject(r"winmgmts:\\.\root\cimv2")
        system = next(iter(service.ExecQuery("SELECT Manufacturer, Model FROM Win32_ComputerSystem")))
        cpus = [cpu.Name.strip() for cpu in service.ExecQuery("SELECT Name FROM Win32_Processor")]
        return [f"Computer: {system.Manufacturer} {system.Model}", f"CPU: {', '.join(cpus)}"]
    except Exception as error:
        return [f"System information unavailable: {error}"]


def report(config: Config) -> tuple[str, bool]:
    """Return a human-readable sensor report and whether monitoring will work."""
    lines = _system_lines()
    lines.append(f"Running as administrator: {'yes' if is_admin() else 'NO'}")
    lines.append(f"PawnIO driver installed: {'yes' if pawnio_installed() else 'NO'}")
    lines.append(f"Configured sensor_source: {config.sensor_source}")
    for name in SOURCES:
        reader = SOURCES[name](config.sensor_ids)
        try:
            readings = reader.read()
            lines.append(f"\n[{reader.label}] OK")
            lines.extend(f"  {r.identifier} | {r.name} | {r.celsius:.1f} C" for r in readings)
        except SensorError as error:
            lines.append(f"\n[{reader.label}] unavailable: {error}")
        finally:
            reader.close()
    reader = create_reader(config.sensor_source, config.sensor_ids)
    try:
        hottest = max(r.celsius for r in reader.read())
        lines.append(
            f"\nREADY: monitoring will use {reader.label}; hottest CPU sensor now {hottest:.1f} C."
        )
        ready = True
    except SensorError:
        lines.append(
            "\nNOT READY: no usable CPU temperature for the configured source.\n"
            "Fix: install PawnIO (winget install namazso.PawnIO) and run this as administrator."
        )
        ready = False
    finally:
        reader.close()
    return "\n".join(lines), ready
