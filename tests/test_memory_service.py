import unittest
from unittest.mock import patch

from memory_service_runner import run_lifecycle
from src.memory_service import MemoryService


class MemoryServiceTests(unittest.TestCase):
    def test_full_lifecycle_and_negative_controls(self):
        report = run_lifecycle()
        self.assertEqual(report["verdict"]["overall"], "PASS")
        self.assertEqual(report["verdict"]["FAIL"], 0)
        self.assertTrue(all(report["checks"].values()))
        self.assertEqual(report["counters"]["effect"], 0)
        self.assertEqual(report["counters"]["ACK"], 2)
        self.assertEqual(report["counters"]["deterministic_model_calls"], 4)
        self.assertEqual(report["counters"]["live_model_calls"], 0)
        self.assertEqual(report["run_ids"]["v2"], report["run_ids"]["v2_replay"])
        self.assertEqual(
            report["negative_errors"]["generation_mismatch"], "DependencyUnavailable")
        self.assertEqual(report["negative_errors"]["missing_source"], "DependencyUnavailable")

    def test_unrelated_failures_in_real_negative_paths_are_not_accepted(self):
        original = MemoryService.execute
        cases = (
            (":mismatch", "generation_mismatch_rejected"),
            (":missing", "missing_source_rejected"),
        )
        for suffix, check in cases:
            with self.subTest(suffix=suffix):
                target = suffix

                def injected(service, version, *, suffix=""):
                    if suffix == target:
                        raise RuntimeError("INJECTED unrelated failure in real negative path")
                    return original(service, version, suffix=suffix)

                with patch.object(MemoryService, "execute", new=injected):
                    report = run_lifecycle()
                self.assertEqual(report["verdict"]["overall"], "FAIL")
                self.assertFalse(report["checks"][check])
                self.assertEqual(report["negative_errors"][check.removesuffix("_rejected")],
                                 "RuntimeError")


if __name__ == "__main__":
    unittest.main()
