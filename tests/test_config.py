import json
from pathlib import Path
import tempfile
import unittest

from overheating_warning.config import Config, load_config


class ConfigTests(unittest.TestCase):
    def test_creates_default_and_roundtrips(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "config.json"
            self.assertEqual(load_config(path), Config())
            self.assertEqual(load_config(path), Config())

    def test_invalid_values_rejected(self):
        cases = (
            {"threshold_c": "90"}, {"threshold_c": True}, {"threshold_c": float("nan")},
            {"threshold_c": 85}, {"recovery_c": 91}, {"poll_seconds": 0},
            {"sustained_seconds": -1}, {"sound": 1}, {"repeat_sound_seconds": 0},
            {"sensor_ids": ("bad",)}, {"recovery_seconds": float("inf")},
            {"sensor_source": "hwinfo"},
        )
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                Config(**values)

    def test_bad_json_unknown_keys_and_wrong_shape_fail_explicitly(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            for content in ('{', '[]', '{"threshhold_c": 90}', '{"sensor_ids": "x"}'):
                path.write_text(content, encoding="utf-8")
                with self.subTest(content=content), self.assertRaises(ValueError):
                    load_config(path)

    def test_selected_sensors_load_as_tuple(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"sensor_ids": ["/intelcpu/0/temperature/0"]}))
            self.assertEqual(load_config(path).sensor_ids, ("/intelcpu/0/temperature/0",))
