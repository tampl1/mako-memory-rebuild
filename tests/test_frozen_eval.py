import json
import tempfile
import unittest
from pathlib import Path

from frozen_eval import main as frozen_main
from shadow_migration import main as shadow_main
from src.temporal_store import TemporalStore
from live_runner import event


class FrozenEvaluationTests(unittest.TestCase):
    def test_twenty_scenarios_and_shadow_rollback(self):
        self.assertEqual(frozen_main(), 0)
        self.assertEqual(shadow_main(), 0)
        root = Path(__file__).resolve().parent.parent
        evidence = json.loads((root / "artifacts" / "frozen-evaluation-evidence.json").read_text())
        self.assertEqual(evidence["totals"]["observations"]["admitted_call"], 0)
        self.assertEqual(evidence["results"]["13-injection"]["raw"]["observations"]["admitted_call"], 0)
        self.assertEqual(evidence["results"]["01-current_fact"]["no-memory"]["goal_outcome"],
                         "NOT_DELIVERED")
        self.assertTrue(evidence["results"]["04-two_arrival_orders"]["raw"]["actual"]["order_independent"])
        self.assertTrue(evidence["results"]["05-model_swap"]["raw"]["actual"]["model_receipts_differ"])
        self.assertFalse(evidence["all_goals_delivered"])

    def test_raw_history_excludes_invalidated_events_now(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TemporalStore(Path(tmp) / "m.sqlite3")
            old = event(store, "owner-live", "fact", 0, {"release_target": "v1.0"})
            new = event(store, "owner-live", "correction", 10,
                        {"release_target": "v2.0"}, old["event_id"], actor="b")
            store.invalidate_for_correction(new["event_id"])
            out = store.raw_history(owner_id="owner-live", project="live-synth",
                                    query="release_target")
            self.assertNotIn(old["event_id"], [i["ref_id"] for i in out["items"]])
            historical = store.raw_history(owner_id="owner-live", project="live-synth",
                                           query="release_target", as_of="2026-10-31T00:05:00+00:00")
            self.assertIn(old["event_id"], [i["ref_id"] for i in historical["items"]])


if __name__ == "__main__":
    unittest.main()
