import unittest
from run_matrix import accepted


class MatrixTests(unittest.TestCase):
    def test_acceptance_requires_collection_normal_mode_and_full_interval(self):
        good = dict(status='completed', performance_mode_verified=True,
                    device_failure=False, collector_exit_code=0,
                    capture_status='completed; includes startup')
        frames = [dict(p99_target_met=True, first_s=30.01, last_s=179.9)]
        self.assertTrue(accepted(good, frames))
        for change in (dict(status='device_failed'), dict(forced_cleanup=True),
                       dict(device_failure=True), dict(performance_mode_verified=False),
                       dict(collector_exit_code=1), dict(capture_status='failed')):
            with self.subTest(change=change):
                self.assertFalse(accepted(good | change, frames))
        self.assertFalse(accepted(good, []))
        self.assertFalse(accepted(good, [frames[0] | dict(p99_target_met=False)]))
        self.assertFalse(accepted(good, [frames[0] | dict(last_s=60)]))
        self.assertFalse(accepted(good, [frames[0] | dict(first_s=120)]))
