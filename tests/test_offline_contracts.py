import tempfile
import unittest

from child_runner import main as child_main
from live_runner import event, planned
from offline_replay import main as replay_main
from src.live_retrieval import CallBudget, OllamaLiveRetriever
from src.pilot import Pilot
from src.safety import AccessGrants, EvidenceError


class OfflineEmbeddingRetriever(OllamaLiveRetriever):
    def _embed(self, text):
        return [1.0, 0.0]

    def verify_runtime(self, mode="raw"):
        return True


class OfflineContractTests(unittest.TestCase):
    def test_saved_receipt_replay_uses_no_models(self):
        self.assertEqual(replay_main(), 0)

    def test_release_key_value_and_missing_field_fails_before_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            pilot = Pilot(tmp)
            owner = "owner-live"
            grants = AccessGrants()
            grants.allow(owner, "live-synth", "bge-m3/1024", 1)
            retriever = OfflineEmbeddingRetriever(
                pilot.memory, grants, owner, "live-synth", "http://unused", CallBudget())
            valid = event(pilot.memory, owner, "fact", 0, {"release_target": "v1.2"})
            out = retriever.retrieve("release_target")
            self.assertIn("release_target", out["items"][0]["text"])
            self.assertIn("v1.2", out["items"][0]["text"])
            self.assertEqual(out["items"][0]["fact_id"], valid["event_id"])
            event(pilot.memory, owner, "fact", 1, {"note": "version is absent"})
            with self.assertRaises(EvidenceError):
                planned(retriever, "release_target")
            self.assertEqual(pilot.executor.executions, [])

    def test_child_context_contract_on_real_core(self):
        self.assertEqual(child_main(), 0)


if __name__ == "__main__":
    unittest.main()
