import asyncio
import json
import tempfile
import unittest

from src.adapters import POLICY_REVISION
from core_v1_contract import ModelResult
from src.child_context import freeze_child_context
from src.decision_consumer import FrozenDecisionContext
from src.final_answer import (
    FinalAnswerMemoryContext, SourceVersionVerifier, make_agent, make_request,
    model_input_digest,
)
from src.pilot import Pilot, make_binding
from src.safety import AccessGrants, SafeReader


class ContextInspectingModel:
    def __init__(self):
        self.calls = 0
        self.seen_items = []

    async def generate(self, request):
        self.calls += 1
        context = json.loads(request.messages[1]["content"])["retrieved_context"]
        self.seen_items = list(context["items"])
        item = self.seen_items[0]
        answer = {"source": item["fact_id"], "version": item["structured"]["version"]}
        return ModelResult(
            message={"role": "assistant", "content": json.dumps(answer)},
            receipt={"model": "offline-context-inspecting-model",
                     "operation_key": request.operation_key},
        )


class FinalAnswerTests(unittest.TestCase):
    def test_frozen_v2_reaches_model_and_replay_has_no_new_calls(self):
        with tempfile.TemporaryDirectory(prefix="g4-final-answer-") as tmp:
            pilot = Pilot(tmp)
            store = pilot.memory
            owner = "owner-fixture"
            project = "g4-memory-consumer"
            grants = AccessGrants()
            grants.allow(owner, project, "bge-m3/1024", 7)
            old = store.ingest_event(
                owner_id=owner, project=project, session="final", actor="a",
                recipients=[owner], kind="fact", occurred_at="2026-10-31T00:00:00Z",
                ingested_at="2026-10-31T00:00:00Z", certainty="explicit",
                payload={"release_target": "v1.0"})
            new = store.ingest_event(
                owner_id=owner, project=project, session="final", actor="b",
                recipients=[owner], kind="correction", occurred_at="2026-10-31T00:10:00Z",
                ingested_at="2026-10-31T00:10:00Z", certainty="explicit",
                payload={"release_target": "v2.0"}, supersedes_event_id=old["event_id"],
                correction_of=old["event_id"])
            store.invalidate_for_correction(new["event_id"])
            store.add_claim(
                owner_id=owner, project=project, session="final", actor="b",
                certainty="explicit", statement={"action": "ship", "version": "v2.0"},
                evidence_event_ids=[new["event_id"]], created_at="2026-10-31T00:10:00Z")
            retrieved = SafeReader(store, grants, "bge-m3/1024", 7).read(
                owner_id=owner, project=project, query="what should we ship?",
                mode="temporal_recall", limit=1)
            child = freeze_child_context(
                parent_run_id="final-v2", as_of=None, owner=owner, tenant="tenant-synth",
                project=project, grant_revision=7, embedding_generation="bge-m3/1024",
                retrieved=retrieved)
            binding = make_binding(owner)
            model = ContextInspectingModel()
            memory = FinalAnswerMemoryContext(FrozenDecisionContext(child, store, grants))
            agent = make_agent(
                store=pilot._run_store(), model=model, memory=memory,
                policy=pilot.policy, tools=pilot.executor, transport=pilot.transport,
                verifier=SourceVersionVerifier(new["event_id"], "v2.0"))
            run = asyncio.run(agent.execute(
                binding, "final-v2-new", make_request("what should we ship?", POLICY_REVISION),
                pilot._deadline(), holder="final-v2"))
            self.assertEqual(run.state, "completed")
            self.assertEqual(model.calls, 1)
            self.assertEqual(model.seen_items[0]["structured"]["version"], "v2.0")
            self.assertEqual(child.grant_revision, 7)
            self.assertEqual(binding.principal.generation, 1)
            self.assertEqual(run.result["tool_receipts"], [])
            answer = json.loads(run.result["answer"]["content"])
            self.assertEqual(answer, {"source": new["event_id"], "version": "v2.0"})
            self.assertEqual(len(model_input_digest(run.checkpoint["messages"])), 64)

            replay_model = ContextInspectingModel()
            replay_memory = FinalAnswerMemoryContext(FrozenDecisionContext(child, store, grants))
            replay = make_agent(
                store=pilot._run_store(), model=replay_model, memory=replay_memory,
                policy=pilot.policy, tools=pilot.executor, transport=pilot.transport,
                verifier=SourceVersionVerifier(new["event_id"], "v2.0"))
            replayed = asyncio.run(replay.execute(
                binding, "final-v2-new", make_request("what should we ship?", POLICY_REVISION),
                pilot._deadline(), holder="final-v2-replay"))
            self.assertEqual(replayed.run_id, run.run_id)
            self.assertEqual(replay_model.calls, 0)
            self.assertEqual(replay_memory.recalls, 0)
            self.assertEqual(replayed.result["tool_receipts"], [])
            self.assertTrue(pilot.flush(owner, run.run_id)["acknowledged"])


if __name__ == "__main__":
    unittest.main()
