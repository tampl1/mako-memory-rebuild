import os
import unittest
from unittest.mock import patch

from consumer_runner import CONTROLS, run_consumer
from src.decision_consumer import EXPECTED_MINIMAX_SHA256, load_decision_envelope
from src.source_closure import SourceClosureError, configure_core


class DecisionConsumerTests(unittest.TestCase):
    def test_exact_minimax_envelope_and_principal_generation(self):
        envelope = load_decision_envelope(CONTROLS / "minimax-decision-input.json")
        self.assertEqual(envelope.sha256, EXPECTED_MINIMAX_SHA256)
        self.assertEqual(envelope.principal_data["generation"], 1)
        self.assertEqual(envelope.principal_data["owner_id"], "owner-fixture")
        self.assertEqual(envelope.principal_data["tenant_id"], "tenant-fixture")

    def test_consumer_v1_v2_and_negative_controls(self):
        report = run_consumer()
        self.assertEqual(report["verdict"]["overall"], "PASS")
        self.assertEqual(report["verdict"]["FAIL"], 0)
        self.assertEqual(report["counters"]["effect"], 2)
        self.assertEqual(report["counters"]["result"], 2)
        self.assertEqual(report["counters"]["ACK"], 2)
        self.assertEqual(report["counters"]["external_calls"], 0)
        self.assertEqual(report["model_budget"]["previously_spent"], 47)
        self.assertEqual(report["model_budget"]["current_run"], 0)
        self.assertEqual(report["model_budget"]["remaining"], 17)
        for name in ("revoked", "foreign", "missing", "tampered", "embedding_generation"):
            self.assertEqual(report["negative_controls"][name]["effect"], 0, name)
            self.assertEqual(report["negative_controls"][name]["admitted"], 0, name)

    def test_core_directory_and_commit_have_no_fallback(self):
        with patch.dict(os.environ, {"MAKO_CORE_DIR": "", "MAKO_CORE_GIT_HEAD": ""}):
            with self.assertRaises(SourceClosureError):
                configure_core()


if __name__ == "__main__":
    unittest.main()
