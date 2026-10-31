import logging
import queue
import threading
import time
from dataclasses import dataclass

from .config import Config
from .policy import HeatPolicy
from .sensors import SensorError, create_reader


@dataclass(frozen=True)
class Status:
    temperature: float | None
    overheating: bool
    error: str | None = None
    source: str | None = None


def monitor(config: Config, updates: queue.Queue[Status], stop: threading.Event) -> None:
    import pythoncom

    initialized = False
    reader = None
    try:
        pythoncom.CoInitialize()
        initialized = True
        reader = create_reader(config.sensor_source, config.sensor_ids)
        policy = HeatPolicy(config)
        last_error = None
        while not stop.is_set():
            started = time.monotonic()
            try:
                temperature = max(reading.celsius for reading in reader.read())
                decision = policy.sample(temperature, time.monotonic())
                if last_error is not None:
                    logging.info("CPU sensor monitoring recovered")
                last_error = None
                if decision.started:
                    logging.warning("Sustained CPU overheating: %.1f C", temperature)
                elif decision.recovered:
                    logging.info("CPU recovered: %.1f C", temperature)
                status = Status(temperature, decision.overheating, source=reader.label)
            except SensorError as error:
                policy.unavailable()
                message = str(error)
                if message != last_error:
                    logging.exception("CPU monitoring unavailable: %s", message)
                last_error = message
                status = Status(None, policy.overheating, message)
            # Keep memory bounded even if the UI is temporarily busy.
            if updates.full():
                try:
                    updates.get_nowait()
                except queue.Empty:
                    pass
            updates.put_nowait(status)
            stop.wait(max(0.0, config.poll_seconds - (time.monotonic() - started)))
    except Exception:
        logging.exception("Temperature worker stopped unexpectedly")
        status = Status(None, False, "Monitor stopped unexpectedly. Restart it and check the log.")
        if updates.full():
            try:
                updates.get_nowait()
            except queue.Empty:
                pass
        updates.put_nowait(status)
    finally:
        if reader is not None:
            reader.close()
        if initialized:
            pythoncom.CoUninitialize()
