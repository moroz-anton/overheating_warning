import json
import math
import os
from dataclasses import asdict, dataclass, fields
from pathlib import Path

SENSOR_SOURCES = ("auto", "embedded", "lhm_wmi", "acpi")


@dataclass(frozen=True)
class Config:
    threshold_c: float = 90.0
    sustained_seconds: float = 10.0
    recovery_c: float = 85.0
    recovery_seconds: float = 10.0
    poll_seconds: float = 2.0
    repeat_sound_seconds: float = 60.0
    sound: bool = True
    sensor_ids: tuple[str, ...] = ()
    sensor_source: str = "auto"

    def __post_init__(self) -> None:
        if self.sensor_source not in SENSOR_SOURCES:
            raise ValueError(f"sensor_source must be one of: {', '.join(SENSOR_SOURCES)}.")
        for name in (
            "threshold_c", "sustained_seconds", "recovery_c",
            "recovery_seconds", "poll_seconds", "repeat_sound_seconds",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be a finite number.")
            if not math.isfinite(value):
                raise ValueError(f"{name} must be a finite number.")
        if not 0 < self.recovery_c < self.threshold_c <= 120:
            raise ValueError("Require 0 < recovery_c < threshold_c <= 120.")
        if not 0.25 <= self.poll_seconds <= 60:
            raise ValueError("poll_seconds must be between 0.25 and 60.")
        for name in ("sustained_seconds", "recovery_seconds", "repeat_sound_seconds"):
            if not 0 < getattr(self, name) <= 3600:
                raise ValueError(f"{name} must be greater than 0 and at most 3600.")
        if not isinstance(self.sound, bool):
            raise ValueError("sound must be true or false.")
        if not isinstance(self.sensor_ids, tuple) or any(
            not isinstance(value, str) or not value.startswith("/")
            for value in self.sensor_ids
        ):
            raise ValueError("sensor_ids must be an array of sensor identifiers.")


def data_directory() -> Path:
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        raise RuntimeError("LOCALAPPDATA is not set; a Windows user profile is required.")
    return Path(local) / "OverheatingWarning"


def load_config(path: Path) -> Config:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("x", encoding="utf-8") as stream:
                json.dump(asdict(Config()), stream, indent=2)
                stream.write("\n")
        except FileExistsError:
            pass  # Another invocation may have created the default configuration.
    with path.open(encoding="utf-8") as stream:
        values = json.load(stream)
    if not isinstance(values, dict):
        raise ValueError("Configuration must be a JSON object.")
    unknown = values.keys() - {field.name for field in fields(Config)}
    if unknown:
        raise ValueError(f"Unknown configuration keys: {', '.join(sorted(unknown))}")
    if "sensor_ids" in values:
        if not isinstance(values["sensor_ids"], list):
            raise ValueError("sensor_ids must be an array.")
        values["sensor_ids"] = tuple(values["sensor_ids"])
    return Config(**values)
