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
