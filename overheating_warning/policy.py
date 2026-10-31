import math
from dataclasses import dataclass

from .config import Config


@dataclass(frozen=True)
class Decision:
    overheating: bool
    started: bool = False
    recovered: bool = False


class HeatPolicy:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.overheating = False
        self._hot_since: float | None = None
        self._cool_since: float | None = None
        self._last_sample: float | None = None

    def unavailable(self) -> None:
        self._hot_since = None
        self._cool_since = None
        self._last_sample = None

    def sample(self, temperature: float, now: float) -> Decision:
        if not math.isfinite(temperature) or not 0 < temperature <= 125:
            raise ValueError("CPU temperature must be finite and in (0, 125] Celsius.")
        if not math.isfinite(now):
            raise ValueError("Sample time must be finite.")
        if self._last_sample is not None and (
            now < self._last_sample or now - self._last_sample > self.config.poll_seconds * 2.5
        ):
            self.unavailable()
        self._last_sample = now
        if not self.overheating:
            if temperature > self.config.threshold_c:
                if self._hot_since is None:
                    self._hot_since = now
                if now - self._hot_since >= self.config.sustained_seconds:
                    self.overheating = True
                    self._hot_since = None
                    return Decision(True, started=True)
            else:
                self._hot_since = None
        else:
            if temperature <= self.config.recovery_c:
                if self._cool_since is None:
                    self._cool_since = now
                if now - self._cool_since >= self.config.recovery_seconds:
                    self.overheating = False
                    self._cool_since = None
                    return Decision(False, recovered=True)
            else:
                self._cool_since = None
        return Decision(self.overheating)
