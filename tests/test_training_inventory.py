import json
import unittest
from pathlib import Path

from training.build_inventory import FIELDS


class TrainingInventoryTests(unittest.TestCase):
    def test_inventory_has_required_fields_and_unknowns(self):
        root = Path(__file__).resolve().parent.parent
        report = json.loads((root / "docs" / "training" / "fleet-training-register.json").read_text())
        self.assertEqual(report["status"], "awaiting_inventory")
        self.assertGreaterEqual(len(report["records"]), 7)
        for record in report["records"]:
            for field in FIELDS:
                self.assertIn(field, record)
        twin = next(r for r in report["records"] if r["track"] == "twin-sft-lora")
        self.assertTrue(twin["artifact_digest"].startswith("sha256:61e340b1"))
        self.assertTrue(twin["tokenizer_digest"].startswith("sha256:be756060"))
        zvuk = next(r for r in report["records"] if r["track"] == "zvuk-text-finetune-artifact")
        self.assertEqual(zvuk["state"], "unknown")
        self.assertIn("not asserted as trained weights", zvuk["blocker"])
        self.assertGreaterEqual(len(report["missing_platforms"]), 4)


if __name__ == "__main__":
    unittest.main()
