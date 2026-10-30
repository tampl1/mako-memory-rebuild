"""CORE integration tests: real CoreRuntime + SQLiteRunStore on temp files."""

import json
import tempfile
import unittest
from pathlib import Path

from src.pilot import (
    MODEL_A, MODEL_B, Pilot, ProcessDeath,
)


class PilotCoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.pilot = Pilot(self._tmp.name)
        self.owner, self.project = "owner-1", "synth-proj"

    def tearDown(self):
        self._tmp.cleanup()

    def _seed_and_change(self):
        seeded = self.pilot.seed(self.owner, self.project)
        r1 = self.pilot.run_core(
            "T-R1", self.owner, self.project, "what should we ship?",
            MODEL_A, "temporal_recall",
            tool_arguments={"version": "v1.2", "fact_ref": seeded["e1"]["event_id"]},
            expected_fact_id=seeded["e1"]["event_id"])
        corr = self.pilot.correct_fact(self.owner, self.project, "v1.3", at_minutes=20)
        return seeded, r1, corr

    def test_receipt_links_new_fact_version_not_old(self):
        seeded, r1, corr = self._seed_and_change()
        self.assertEqual(r1["state"], "completed")
        self.assertEqual(r1["result"]["tool_receipt"]["fact_ref"],
                         seeded["e1"]["event_id"])
        r2 = self.pilot.run_core(
            "T-R2", self.owner, self.project, "what should we ship?",
            MODEL_A, "temporal_recall",
            tool_arguments={"version": "v1.3", "fact_ref": corr["e2"]["event_id"]},
            expected_fact_id=corr["e2"]["event_id"])
        self.assertEqual(r2["state"], "completed")
        self.assertEqual(r2["result"]["tool_receipt"]["fact_ref"],
                         corr["e2"]["event_id"])
        self.assertNotEqual(r2["result"]["tool_receipt"]["fact_ref"],
                            seeded["e1"]["event_id"])
        self.assertEqual(r2["result"]["tool_receipt"]["version"], "v1.3")

    def test_restart_no_duplicate_effect(self):
        seeded, r1, corr = self._seed_and_change()
        key = "T-R3:owner-1:k1"
        r3 = self.pilot.run_core(
            "T-R3", self.owner, self.project, "what should we ship?",
            MODEL_A, "temporal_recall", request_key=key,
            crash_stage="tool_confirmed",
            tool_arguments={"version": "v1.3", "fact_ref": corr["e2"]["event_id"]},
            expected_fact_id=corr["e2"]["event_id"])
        self.assertTrue(r3["crashed_after_tool"])
        execs_after_crash = r3["tool_executions_total"]
        self.pilot.clock.advance(40)
        r3r = self.pilot.run_core(
            "T-R3r", self.owner, self.project, "what should we ship?",
            MODEL_A, "temporal_recall", request_key=key,
            tool_arguments={"version": "v1.3", "fact_ref": corr["e2"]["event_id"]},
            expected_fact_id=corr["e2"]["event_id"])
        self.assertEqual(r3r["state"], "completed")
        self.assertEqual(r3r["tool_executions_total"], execs_after_crash,
                         "tool must not re-execute after restart")
        self.assertEqual(r3r["model_calls"], 0,
                         "checkpoint preserved: model stage not re-run")
        self.assertEqual(r3r["memory_recalls"], 0,
                         "checkpoint preserved: memory stage not re-run")
        self.assertEqual(len(self.pilot.executor.ledger),
                         execs_after_crash, "effect ledger has exactly one entry")
        self.assertEqual(r3r["result"]["tool_receipt"]["version"], "v1.3")

    def test_model_swap_changes_receipt_not_data(self):
        seeded, r1, corr = self._seed_and_change()
        digest_before = self.pilot.memory.get_event(corr["e2"]["event_id"])["source_digest"]
        claims_before = json.dumps(
            self.pilot.memory.get_claim(corr["c2"]["claim_id"])["statement"],
            sort_keys=True)
        r4 = self.pilot.run_core(
            "T-R4", self.owner, self.project, "what should we ship?",
            MODEL_B, "temporal_recall",
            tool_arguments={"version": "v1.3", "fact_ref": corr["e2"]["event_id"]},
            expected_fact_id=corr["e2"]["event_id"])
        self.assertEqual(r4["state"], "completed")
        self.assertEqual(r1["result"]["model_receipt"]["model"], MODEL_A)
        self.assertEqual(r4["result"]["model_receipt"]["model"], MODEL_B)
        self.assertNotEqual(r1["result"]["model_receipt"]["input_digest"],
                            r4["result"]["model_receipt"]["input_digest"])
        self.assertEqual(
            self.pilot.memory.get_event(corr["e2"]["event_id"])["source_digest"],
            digest_before, "stored data unchanged by model swap")
        self.assertEqual(json.dumps(
            self.pilot.memory.get_claim(corr["c2"]["claim_id"])["statement"],
            sort_keys=True), claims_before)

    def test_comparison_conditions(self):
        out = self.pilot.run_comparison(self._tmp.name)
        conds = out["conditions"]
        exp = out["expected"]
        self.assertFalse(conds["none"]["correct"],
                         "no-memory control must be wrong")
        self.assertEqual(conds["none"]["version"], "v1.2")
        self.assertIsNone(conds["none"]["fact_ref"])
        self.assertTrue(conds["raw_history"]["correct"],
                        "raw-history baseline is real, not a strawman")
        self.assertEqual(conds["raw_history"]["fact_ref"], exp["fact_ref"])
        self.assertTrue(conds["temporal_recall"]["correct"])
        self.assertEqual(conds["temporal_recall"]["fact_ref"], exp["fact_ref"])
        self.assertLessEqual(
            conds["temporal_recall"]["memory_receipt"]["returned"],
            conds["raw_history"]["memory_receipt"]["scanned"])

    def test_flush_delivers_durable_result(self):
        seeded = self.pilot.seed(self.owner, self.project)
        r1 = self.pilot.run_core(
            "T-RF", self.owner, self.project, "what should we ship?",
            MODEL_A, "temporal_recall",
            tool_arguments={"version": "v1.2", "fact_ref": seeded["e1"]["event_id"]},
            expected_fact_id=seeded["e1"]["event_id"])
        receipt = self.pilot.flush(self.owner, r1["run_id"])
        self.assertTrue(receipt["acknowledged"])
        self.assertEqual(len(self.pilot.transport.deliveries), 1)


if __name__ == "__main__":
    unittest.main()
