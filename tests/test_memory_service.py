import unittest

from memory_service_runner import run_lifecycle


class MemoryServiceTests(unittest.TestCase):
    def test_full_lifecycle_and_negative_controls(self):
        report = run_lifecycle()
        self.assertEqual(report["verdict"]["overall"], "PASS")
        self.assertEqual(report["verdict"]["FAIL"], 0)
        self.assertTrue(all(report["checks"].values()))
        self.assertEqual(report["counters"]["effect"], 0)
        self.assertEqual(report["counters"]["ACK"], 2)
        self.assertEqual(report["counters"]["live_model_calls"], 0)
        self.assertEqual(report["run_ids"]["v2"], report["run_ids"]["v2_replay"])


if __name__ == "__main__":
    unittest.main()
