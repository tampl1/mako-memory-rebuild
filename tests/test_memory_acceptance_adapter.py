import unittest
from unittest.mock import patch

from memory_acceptance_adapter import run_acceptance
from src.memory_service import MemoryService


class MemoryAcceptanceTests(unittest.TestCase):
    def test_acceptance_and_negative_fixtures(self):
        report = run_acceptance()
        self.assertEqual(report["verdict"]["overall"], "PASS")
        self.assertTrue(all(report["checks"].values()))
        self.assertEqual(report["counters"]["ack"], 1)
        self.assertEqual(report["counters"]["effects"], 0)
        self.assertEqual(report["counters"]["live_model_calls"], 0)
        self.assertEqual(report["negative_errors"]["generation_mismatch"], "DependencyUnavailable")
        self.assertEqual(report["negative_errors"]["missing_source"], "DependencyUnavailable")
        self.assertEqual(report["decisions"]["historical"]["as_of"], "2026-10-31T00:05:00+00:00")
        self.assertIsNone(report["decisions"]["latest"]["as_of"])
        self.assertEqual(report["result"]["run_id"], report["replay"]["run_id"])

    def test_unrelated_failures_make_acceptance_fail(self):
        original = MemoryService.execute
        for suffix, check in ((":mismatch", "generation_mismatch_ready_and_typed"),
                              (":missing", "missing_source_ready_and_typed")):
            with self.subTest(suffix=suffix):
                target = suffix

                def injected(service, version, *, suffix=""):
                    if suffix == target:
                        raise RuntimeError("INJECTED unrelated failure")
                    return original(service, version, suffix=suffix)

                with patch.object(MemoryService, "execute", new=injected):
                    report = run_acceptance()
                self.assertEqual(report["verdict"]["overall"], "FAIL")
                self.assertFalse(report["checks"][check])


if __name__ == "__main__":
    unittest.main()
