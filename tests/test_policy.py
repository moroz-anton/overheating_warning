import unittest

from overheating_warning.config import Config
from overheating_warning.policy import HeatPolicy


class HeatPolicyTests(unittest.TestCase):
    def setUp(self):
        self.policy = HeatPolicy(Config())

    def heat(self):
        for now in range(0, 11, 2):
            decision = self.policy.sample(91, now)
        return decision

    def test_exact_threshold_does_not_trigger(self):
        for now in range(0, 31, 2):
            self.assertFalse(self.policy.sample(90, now).overheating)

    def test_requires_full_continuous_ten_seconds(self):
        for now in range(0, 10, 2):
            self.assertFalse(self.policy.sample(91, now).overheating)
        self.assertTrue(self.policy.sample(91, 10).started)
        self.assertFalse(self.policy.sample(92, 12).started)

    def test_a_single_cool_sample_resets_hot_duration(self):
        self.policy.sample(92, 0)
        self.policy.sample(92, 2)
        self.policy.sample(90, 4)
        for now in range(6, 16, 2):
            self.assertFalse(self.policy.sample(92, now).overheating)
        self.assertTrue(self.policy.sample(92, 16).started)

    def test_recovery_requires_ten_seconds_at_or_below_85(self):
        self.heat()
        for now in range(12, 22, 2):
            self.assertTrue(self.policy.sample(85, now).overheating)
        self.assertTrue(self.policy.sample(85, 22).recovered)

    def test_recovery_hysteresis_and_rearming(self):
        self.heat()
        for now in range(12, 24, 2):
            self.assertTrue(self.policy.sample(88, now).overheating)
        for now in range(24, 36, 2):
            self.policy.sample(84, now)
        self.assertFalse(self.policy.overheating)
        for now in range(36, 48, 2):
            result = self.policy.sample(94, now)
        self.assertTrue(result.started)

    def test_interrupted_recovery_restarts_timer(self):
        self.heat()
        self.policy.sample(84, 12)
        self.policy.sample(84, 14)
        self.policy.sample(86, 16)
        for now in range(18, 28, 2):
            self.assertTrue(self.policy.sample(85, now).overheating)
        self.assertTrue(self.policy.sample(85, 28).recovered)

    def test_missing_data_does_not_count_as_hot_or_cool(self):
        self.policy.sample(92, 0)
        self.policy.unavailable()
        self.assertFalse(self.policy.sample(92, 100).overheating)
        self.policy = HeatPolicy(Config())
        self.heat()
        self.policy.sample(80, 12)
        self.policy.unavailable()
        self.assertTrue(self.policy.sample(80, 100).overheating)

    def test_sleep_or_clock_reversal_resets_evidence(self):
        for next_time in (100, -1):
            policy = HeatPolicy(Config())
            policy.sample(92, 0)
            policy.sample(92, 2)
            self.assertFalse(policy.sample(92, next_time).overheating)

    def test_invalid_temperatures_are_rejected(self):
        for value in (float("nan"), float("inf"), -1, 0, 126):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.policy.sample(value, 0)


if __name__ == "__main__":
    unittest.main()
