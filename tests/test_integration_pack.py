import json
import unittest
from pathlib import Path


class IntegrationPackTests(unittest.TestCase):
    def test_memory_bridge_contract(self):
        root = Path(__file__).resolve().parent.parent
        data = json.loads((root / "integration" / "memory-bridge-manifest.json").read_text())
        self.assertEqual(data["source_sha"], "2d63b1abe26b02c996a4a8de06777e0624124f34")
        for field in ("owner", "tenant", "project", "grant_revision", "embedding_generation"):
            self.assertIn(field, data["pinned_context_fields"])
        self.assertIsNone(data["comparison_contract"]["expected_improvement"])

    def test_history_shadow_has_no_content_and_rollback(self):
        root = Path(__file__).resolve().parent.parent
        data = json.loads((root / "integration" / "history-shadow-manifest.json").read_text())
        self.assertEqual(data["counts"]["source_records_read"], 3)
        self.assertEqual(data["counts"]["shadow_records"], 3)
        self.assertEqual(data["counts"]["content_bytes_copied"], 0)
        self.assertIn("original wire", data["rollback"])

    def test_weight_audit_does_not_promote_guard_v2(self):
        root = Path(__file__).resolve().parent.parent
        data = json.loads((root / "integration" / "quality-audit.json").read_text())
        guard = next(t for t in data["tracks"] if t["track"] == "guard-v2")
        self.assertEqual(guard["state"], "unknown_not_promoted")
        twin = next(t for t in data["tracks"] if t["track"] == "twin-sft-adapter-v4")
        self.assertEqual(twin["retention_eval"], "missing")


if __name__ == "__main__":
    unittest.main()
