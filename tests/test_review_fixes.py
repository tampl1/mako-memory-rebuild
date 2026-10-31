"""Regression tests for the 31.10 delivery review."""

import tempfile
import unittest
from pathlib import Path

from minimax_runner import run_controls
from src.safety import (
    AccessGrants, EmbeddingGenerationMismatch, EvidenceError,
    GrantRevisionMismatch, SafeReader, validate_decision,
)
from src.temporal_store import TemporalStore


class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = TemporalStore(Path(self._tmp.name) / "m.sqlite3")
        event = self.store.ingest_event(
            owner_id="o", project="p", session="s", actor="a", recipients=[],
            kind="fact", occurred_at="2026-10-31T00:00:00+00:00",
            ingested_at="2026-10-31T00:00:00+00:00", certainty="explicit",
            payload={"release_target": "v1.0"})
        self.store.add_claim(
            owner_id="o", project="p", session="s", actor="a", certainty="explicit",
            statement={"action": "ship", "version": "v1.0"},
            evidence_event_ids=[event["event_id"]], created_at="2026-10-31T00:01:00+00:00")
        self.event = event
        self.grants = AccessGrants()
        self.grants.allow("o", "p", "gen-1", revision=1)
        self.reader = SafeReader(self.store, self.grants, "gen-1", grant_revision=1)

    def tearDown(self):
        self._tmp.cleanup()

    def _retrieved(self):
        return self.reader.read(owner_id="o", project="p", query="ship",
                                mode="temporal_recall", limit=2)

    def test_valid_decision_uses_exact_structured_version(self):
        retrieved = self._retrieved()
        item = retrieved["items"][0]
        self.assertEqual(item["structured"]["version"], "v1.0")
        self.assertTrue(validate_decision(
            {"version": "v1.0", "fact_ref": self.event["event_id"]}, retrieved))

    def test_missing_empty_wrong_partial_foreign_versions_fail(self):
        retrieved = self._retrieved()
        fact_ref = self.event["event_id"]
        bad = [
            {"fact_ref": fact_ref},
            {"version": "", "fact_ref": fact_ref},
            {"version": None, "fact_ref": fact_ref},
            {"version": 1.0, "fact_ref": fact_ref},
            {"version": "v1", "fact_ref": fact_ref},
            {"version": "v9.9", "fact_ref": fact_ref},
        ]
        for decision in bad:
            with self.subTest(decision=decision), self.assertRaises(EvidenceError):
                validate_decision(decision, retrieved)

    def test_grant_revision_and_embedding_generation_are_independent(self):
        self.grants.allow("o", "p", "gen-1", revision=2)
        with self.assertRaises(GrantRevisionMismatch):
            self._retrieved()
        self.grants.allow("o", "p", "gen-2", revision=1)
        with self.assertRaises(EmbeddingGenerationMismatch):
            self._retrieved()

    def test_exact_minimax_controls_and_action_gate(self):
        report = run_controls()
        self.assertTrue(report["all_checks_passed"])
        self.assertEqual(report["manifest_files_verified"], 21)
        self.assertEqual(report["controls"]["T15"]["recorded_effect"], 0)
        self.assertEqual(report["controls"]["T14"]["admitted_call"], 0)
        self.assertEqual(report["controls"]["T17"]["admitted_call"], 1)
        self.assertEqual(report["controls"]["T17"]["recorded_effect"], 1)


if __name__ == "__main__":
    unittest.main()
