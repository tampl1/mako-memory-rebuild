"""Contract conformance: every stored record validates against its schema."""

import json
import unittest
from pathlib import Path

from src.temporal_store import TemporalStore, load_schema
from src.validation import validate

CONTRACTS = Path(__file__).resolve().parent.parent / "contracts"


class ContractShapeTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.store = TemporalStore(Path(self._tmp.name) / "m.sqlite3")

    def tearDown(self):
        self._tmp.cleanup()

    def test_contract_files_are_valid_json_schema_subset(self):
        for name in ("source-event", "claim", "evidence-link",
                     "context-snapshot", "migration-record"):
            schema = json.loads((CONTRACTS / f"{name}.schema.json").read_text())
            self.assertEqual(schema["title"], name)
            self.assertTrue(schema["required"])

    def test_event_and_claim_roundtrip_validates(self):
        ev = self.store.ingest_event(
            owner_id="o", project="p", session="s", actor="a", recipients=[],
            kind="fact", occurred_at="2026-10-31T00:00:00+00:00",
            certainty="explicit", payload={"k": "v"})
        validate({k: v for k, v in ev.items() if k != "already_present"},
                 load_schema("source-event.schema.json"))
        cl = self.store.add_claim(
            owner_id="o", project="p", session="s", actor="a",
            certainty="explicit", statement={"action": "ship"},
            evidence_event_ids=[ev["event_id"]])
        validate({k: v for k, v in cl.items() if k != "already_present"},
                 load_schema("claim.schema.json"))
        links = self.store.evidence_for(cl["claim_id"])
        validate({"link_id": "ln_x12345678", "claim_id": cl["claim_id"],
                  "event_id": ev["event_id"], "role": links[0]["role"],
                  "created_at": cl["created_at"], "note": None},
                 load_schema("evidence-link.schema.json"))


if __name__ == "__main__":
    unittest.main()
