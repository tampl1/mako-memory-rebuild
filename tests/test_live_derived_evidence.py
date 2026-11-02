import json
import unittest
from pathlib import Path


class LiveDerivedEvidenceTests(unittest.TestCase):
    def test_failed_attempt_preserved_and_live_goal_reached(self):
        root = Path(__file__).resolve().parent.parent
        first = json.loads((root / "artifacts" / "live-derived-g4-evidence.json").read_text())
        live = json.loads((root / "artifacts" / "live-derived-g4-evidence-attempt-02.json").read_text())
        self.assertEqual(first["verdict"]["overall"], "UNKNOWN")
        self.assertEqual(live["verdict"], {"PASS": 13, "FAIL": 0, "SKIP": 0,
                                           "UNKNOWN": 0, "overall": "PASS"})
        self.assertEqual(live["calls"], {"embedding": 3, "rerank": 0, "router": 1,
                                         "total": 4, "limit": 12})
        self.assertEqual(live["model_budget"]["total"], 51)
        self.assertEqual(live["lease"]["status"], "released")
        self.assertLess(live["lease"]["released_at"], live["lease"]["expires_at"])
        self.assertTrue(all(live["checks"].values()))
        self.assertEqual(live["router"]["physical_calls"], 1)
        self.assertEqual(live["router"]["ledger_records"], 1)
        self.assertEqual(live["router"]["dispatches"][0]["content"], "OK")
        self.assertEqual(live["runs"]["R2_replay"]["run_id"], live["runs"]["R2"]["run_id"])
        self.assertIsNone(live["runs"]["R3_crash"].get("run_id"))

    def test_manifest_pins(self):
        root = Path(__file__).resolve().parent.parent
        manifest = json.loads((root / "integration" / "live-derived-g4-manifest.json").read_text())
        self.assertEqual(manifest["calls"]["total_spend"], 51)
        self.assertEqual(manifest["lease"]["status"], "released")
        self.assertEqual(manifest["evidence"]["first_attempt"]["verdict"], "UNKNOWN")
        self.assertEqual(manifest["evidence"]["successful_attempt"]["verdict"], "PASS")


if __name__ == "__main__":
    unittest.main()
