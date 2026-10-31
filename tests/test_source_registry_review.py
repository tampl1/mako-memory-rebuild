import json
import unittest
from pathlib import Path

from training.review_sources import main


class SourceRegistryReviewTests(unittest.TestCase):
    def test_review_and_frozen_eval_plan(self):
        self.assertEqual(main(), 1)
        root = Path(__file__).resolve().parent.parent
        report = json.loads((root / "artifacts" / "source-registry-review-evidence.json").read_text())
        plan = json.loads((root / "training" / "frozen-eval-plan.json").read_text())
        self.assertFalse(report["all_checks_passed"])
        self.assertFalse(report["consumer_compatible"])
        self.assertTrue(report["checks"]["separate_eval_train_rights"])
        self.assertFalse(report["checks"]["sft_gate_requires_trainable_base"])
        self.assertEqual(report["split_counts"], {"train": 8, "validation": 2, "test": 2})
        self.assertIn("primevul", report["metadata_only_quarantine"])
        self.assertEqual(report["registry"]["commit"],
                         "fd28a9ae57456268ea906f01cbfa0095a4afb14a")
        self.assertEqual(report["wycheproof"]["test_groups"], 288)
        self.assertTrue(report["wycheproof"]["all_sha256_and_group_counts_match"])
        self.assertIn("train-possible/both gate must require non-null trainable_base for actual SFT",
                      report["consumer_required_fields"])
        self.assertEqual(report["external_training_inputs"], 0)
        self.assertEqual(len(plan["untouched_eval"]["record_ids"]), 4)
        self.assertEqual(plan["gpu_lease"]["status"], "not_requested")
        self.assertEqual(plan["status"], "prepared_not_run")


if __name__ == "__main__":
    unittest.main()
