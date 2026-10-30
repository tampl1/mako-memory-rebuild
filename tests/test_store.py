"""Temporal store semantics: invalidation, as_of, provenance, isolation."""

import tempfile
import unittest
from pathlib import Path

from src.temporal_store import TemporalStore, ValidationError


def ts(m):
    return f"2026-10-31T00:{m:02d}:00+00:00"


class StoreTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.store = TemporalStore(Path(self._tmp.name) / "m.sqlite3")
        self.owner, self.project = "owner-1", "proj"

    def tearDown(self):
        self._tmp.cleanup()

    def _fact(self, version, minute, actor="agent-alpha", session="s1", owner=None):
        return self.store.ingest_event(
            owner_id=owner or self.owner, project=self.project, session=session,
            actor=actor, recipients=[self.owner], kind="fact",
            occurred_at=ts(minute), ingested_at=ts(minute), certainty="explicit",
            payload={"release_target": version})

    def _claim(self, statement, evidence, certainty="explicit", owner=None,
               derived=None, created_at=None):
        return self.store.add_claim(
            owner_id=owner or self.owner, project=self.project, session="s1",
            actor="agent-alpha", certainty=certainty, statement=statement,
            evidence_event_ids=evidence, derived_from_claim_ids=derived,
            created_at=created_at or ts(0))

    def test_invalidation_closes_event_and_dependent_claims(self):
        e1 = self._fact("v1.0", 0)
        c1 = self._claim({"action": "ship", "version": "v1.0"}, [e1["event_id"]],
                         created_at=ts(1))
        dep = self._claim({"action": "notify", "version": "v1.0"},
                          [e1["event_id"]], derived=[c1["claim_id"]], created_at=ts(2))
        other = self._claim({"unrelated": True}, [], created_at=ts(1))
        e2 = self.store.ingest_event(
            owner_id=self.owner, project=self.project, session="s2",
            actor="agent-beta", recipients=[self.owner], kind="correction",
            occurred_at=ts(5), ingested_at=ts(5), certainty="explicit",
            payload={"release_target": "v1.1"},
            supersedes_event_id=e1["event_id"], correction_of=e1["event_id"])
        out = self.store.invalidate_for_correction(e2["event_id"])
        self.assertEqual(out["closed_event"], e1["event_id"])
        self.assertEqual(set(out["invalidated_claim_ids"]),
                         {c1["claim_id"], dep["claim_id"]})
        c1b = self.store.get_claim(c1["claim_id"])
        self.assertIsNotNone(c1b["valid_to"])
        self.assertEqual(c1b["invalid_due_to_event_id"], e2["event_id"])
        self.assertIsNone(self.store.get_claim(other["claim_id"])["valid_to"])
        self.assertIsNotNone(self.store.get_event(e1["event_id"])["valid_to"])
        # immutability of content: digest and payload unchanged by invalidation
        self.assertEqual(e1["source_digest"],
                         self.store.get_event(e1["event_id"])["source_digest"])

    def test_as_of_returns_historical_view(self):
        e1 = self._fact("v1.0", 0)
        c1 = self._claim({"action": "ship", "version": "v1.0"}, [e1["event_id"]],
                         created_at=ts(1))
        e2 = self.store.ingest_event(
            owner_id=self.owner, project=self.project, session="s2",
            actor="agent-beta", recipients=[self.owner], kind="correction",
            occurred_at=ts(5), ingested_at=ts(5), certainty="explicit",
            payload={"release_target": "v1.1"},
            supersedes_event_id=e1["event_id"], correction_of=e1["event_id"])
        self.store.invalidate_for_correction(e2["event_id"])
        c2 = self._claim({"action": "ship", "version": "v1.1"}, [e2["event_id"]],
                         created_at=ts(6))
        hist = self.store.recall(owner_id=self.owner, project=self.project,
                                 query="ship", as_of=ts(3))
        curr = self.store.recall(owner_id=self.owner, project=self.project,
                                 query="ship")
        self.assertEqual([i["ref_id"] for i in hist["items"]], [c1["claim_id"]])
        self.assertEqual([i["ref_id"] for i in curr["items"]], [c2["claim_id"]])

    def test_provenance_links_by_id_and_text_similarity_is_not_evidence(self):
        e1 = self.store.ingest_event(
            owner_id=self.owner, project=self.project, session="s9",
            actor="agent-alpha", recipients=[self.owner], kind="fact",
            occurred_at=ts(2), ingested_at=ts(2), certainty="explicit",
            payload={"note": "ship candidate", "detail": "v9.9"})
        c1 = self._claim({"action": "ship", "version": "v1.0"}, [], created_at=ts(1))
        self.assertEqual(self.store.evidence_for(c1["claim_id"]), [])
        out = self.store.recall(owner_id=self.owner, project=self.project,
                                query="ship")
        item = [i for i in out["items"] if i["ref_id"] == c1["claim_id"]][0]
        self.assertEqual(item["provenance"]["evidence_event_ids"], [])
        # e1 matched by text in raw history but never enters claim provenance
        raw = self.store.raw_history(owner_id=self.owner, project=self.project,
                                     query="ship")
        self.assertIn(e1["event_id"], [i["ref_id"] for i in raw["items"]])

    def test_unknown_author_kept_unknown_and_date_never_invented(self):
        ev = self.store.ingest_event(
            owner_id=self.owner, project=self.project, session="s1",
            actor=None, recipients=[], kind="message",
            occurred_at=ts(1), ingested_at=ts(1), certainty="explicit",
            payload={"body": "hi"})
        self.assertIsNone(ev["actor"])
        with self.assertRaises(ValidationError):
            self.store.ingest_event(
                owner_id=self.owner, project=self.project, session="s1",
                actor="a", recipients=[], kind="fact", occurred_at=None,
                certainty="explicit", payload={})
        out = self.store.raw_history(owner_id=self.owner, project=self.project,
                                     query="hi")
        # the event is retrievable and its author stays unknown — nothing fabricated
        self.assertEqual([i["ref_id"] for i in out["items"]], [ev["event_id"]])
        self.assertIsNone(self.store.get_event(ev["event_id"])["actor"])

    def test_no_duplicate_migration(self):
        first = self.store.apply_migration("mig-001", {"rows": 3}, "tool-0.1")
        second = self.store.apply_migration("mig-001", {"rows": 3}, "tool-0.1")
        self.assertFalse(first["already_applied"])
        self.assertTrue(second["already_applied"])
        import sqlite3
        with self.store._db() as db:
            n = db.execute("SELECT COUNT(*) c FROM migration_records "
                           "WHERE migration_id='mig-001'").fetchone()["c"]
        self.assertEqual(n, 1)

    def test_two_owners_isolated(self):
        e1 = self._fact("v1.0", 0, owner="owner-1")
        self._fact("v8.8", 1, owner="owner-2")
        c1 = self._claim({"action": "ship", "version": "v1.0"}, [e1["event_id"]],
                         owner="owner-1", created_at=ts(2))
        self._claim({"action": "ship", "version": "v8.8"}, [], owner="owner-2",
                    created_at=ts(3))
        out1 = self.store.recall(owner_id="owner-1", project=self.project,
                                 query="ship")
        out2 = self.store.recall(owner_id="owner-2", project=self.project,
                                 query="ship")
        raw1 = self.store.raw_history(owner_id="owner-1", project=self.project,
                                      query="release_target")
        self.assertEqual([i["ref_id"] for i in out1["items"]], [c1["claim_id"]])
        self.assertEqual(len(out2["items"]), 1)
        self.assertNotEqual(out2["items"][0]["ref_id"], c1["claim_id"])
        self.assertNotIn("owner-2", {i["ref_id"] for i in raw1["items"]})
        eids1 = [i["provenance"]["evidence_event_ids"] for i in out1["items"]]
        eids2 = [i["provenance"]["evidence_event_ids"] for i in out2["items"]]
        self.assertIn(e1["event_id"], eids1[0])
        self.assertTrue(all(e1["event_id"] not in ids for ids in eids2))

    def test_negative_control_false_old_lesson_dies_with_fact_change(self):
        e_old = self._fact("v1.0", 0)
        lesson = self._claim({"lesson": "always ship v1.0"},
                             [e_old["event_id"]], certainty="inferred",
                             created_at=ts(1))
        before = self.store.recall(owner_id=self.owner, project=self.project,
                                   query="ship")
        self.assertIn(lesson["claim_id"], [i["ref_id"] for i in before["items"]])
        e_new = self.store.ingest_event(
            owner_id=self.owner, project=self.project, session="s2",
            actor="agent-beta", recipients=[self.owner], kind="correction",
            occurred_at=ts(5), ingested_at=ts(5), certainty="explicit",
            payload={"release_target": "v2.0"},
            supersedes_event_id=e_old["event_id"], correction_of=e_old["event_id"])
        self.store.invalidate_for_correction(e_new["event_id"])
        after = self.store.recall(owner_id=self.owner, project=self.project,
                                  query="ship")
        self.assertNotIn(lesson["claim_id"], [i["ref_id"] for i in after["items"]])
        hist = self.store.recall(owner_id=self.owner, project=self.project,
                                 query="ship", as_of=ts(3))
        self.assertIn(lesson["claim_id"], [i["ref_id"] for i in hist["items"]])
