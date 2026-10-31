from types import SimpleNamespace
import unittest

from unittest.mock import patch

from overheating_warning.sensors import (
    AcpiThermalZones, AutoSensor, EmbeddedLibreHardwareMonitor, Reading, Row,
    SensorError, cpu_readings, create_reader,
)


def sensor(identifier, value, name="CPU Package"):
    return SimpleNamespace(Identifier=identifier, Name=name, Value=value)


class SensorTests(unittest.TestCase):
    def test_reads_cpu_cores_but_not_gpu_or_motherboard(self):
        readings = cpu_readings([
            sensor("/intelcpu/0/temperature/0", 91.5),
            sensor("/intelcpu/0/temperature/1", 94),
            sensor("/gpu-nvidia/0/temperature/0", 105),
            sensor("/lpc/nct/temperature/0", 110),
        ])
        self.assertEqual(len(readings), 2)
        self.assertEqual(max(item.celsius for item in readings), 94)

    def test_amd_cpu_is_supported(self):
        self.assertEqual(cpu_readings([sensor("/amdcpu/0/temperature/0", 93)])[0].celsius, 93)

    def test_missing_cpu_is_an_error(self):
        with self.assertRaises(SensorError):
            cpu_readings([sensor("/gpu-nvidia/0/temperature/0", 92)])

    def test_invalid_cpu_values_are_never_reported_as_safe(self):
        for value in (None, True, float("nan"), float("inf"), 0, -5, 200, "bad"):
            with self.subTest(value=value), self.assertRaises(SensorError):
                cpu_readings([sensor("/intelcpu/0/temperature/0", value)])

    def test_explicit_sensor_selection_and_missing_selected_sensor(self):
        identifier = "/customcpu/0/temperature/0"
        result = cpu_readings([sensor(identifier, 92)], (identifier,))
        self.assertEqual(result[0].celsius, 92)
        with self.assertRaises(SensorError):
            cpu_readings([sensor(identifier, 92)], (identifier, "/missing/0"))

    def test_distance_to_tjmax_is_not_a_temperature(self):
        readings = cpu_readings([
            sensor("/intelcpu/0/temperature/0", 92, "CPU Package"),
            sensor("/intelcpu/0/temperature/19", 0, "P-Core #1 Distance to TjMax"),
        ])
        self.assertEqual([r.name for r in readings], ["CPU Package"])

    def test_unreadable_automatic_sensors_are_skipped_but_selected_ones_fail(self):
        rows = [sensor("/intelcpu/0/temperature/0", None, "Core Max"),
                sensor("/intelcpu/0/temperature/18", 88, "CPU Package")]
        self.assertEqual(cpu_readings(rows)[0].celsius, 88)
        with self.assertRaises(SensorError):
            cpu_readings(rows, ("/intelcpu/0/temperature/0",))


class SourceTests(unittest.TestCase):
    def test_embedded_without_driver_reports_hint(self):
        reader = EmbeddedLibreHardwareMonitor()
        with patch.object(reader, "rows", return_value=[
            Row("/intelcpu/0/temperature/18", "CPU Package", None),
        ]), self.assertRaisesRegex(SensorError, "PawnIO"):
            reader.read()

    def test_auto_falls_back_and_reports_all_errors(self):
        class Fake:
            def __init__(self, label, result):
                self.label, self.result, self.closed = label, result, False

            def read(self):
                if isinstance(self.result, Exception):
                    raise self.result
                return self.result

            def close(self):
                self.closed = True

        good = [Reading("/intelcpu/0/temperature/0", "Package", 91)]
        first, second = Fake("a", SensorError("no driver")), Fake("b", good)
        auto = AutoSensor([first, second])
        self.assertEqual(auto.read(), good)
        self.assertEqual(auto.label, "b")
        second.result = SensorError("app closed")
        with self.assertRaises(SensorError) as caught:
            auto.read()
        self.assertIn("no driver", str(caught.exception))
        self.assertIn("app closed", str(caught.exception))
        self.assertEqual(auto.label, "none")
        auto.close()
        self.assertTrue(first.closed and second.closed)

    def test_auto_never_uses_acpi_but_explicit_acpi_converts_kelvin(self):
        labels = [r.label for r in create_reader("auto").readers]
        self.assertNotIn(AcpiThermalZones.label, labels)
        acpi = AcpiThermalZones()
        zone = SimpleNamespace(Name=r"\_TZ.CPUZ", HighPrecisionTemperature=3631)
        with patch.object(acpi, "_rows", return_value=[zone]):
            reading = acpi.read()[0]
        self.assertAlmostEqual(reading.celsius, 89.95, places=2)
        self.assertEqual(reading.identifier, "/acpi/_TZ.CPUZ")
