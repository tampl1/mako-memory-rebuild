"""Pilot scenario orchestration. stdlib + real CORE runtime/store only."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .temporal_store import TemporalStore
from .adapters import (
    POLICY_REVISION, FactVerifyingVerifier, PermissiveVerifier, StaticPolicyGate,
    StoreMemoryContext, SyntheticModel, SyntheticToolExecutor, SyntheticTransport,
    decide,
)
from core_v1_contract import Binding, Principal
from core_v1_runtime import CoreRuntime, VerticalRequest
from core_v1_store import SQLiteRunStore

MODEL_A = "syn-model-a"
MODEL_B = "syn-model-b"
TOOL = "ship_release"


class ProcessDeath(BaseException):
    """Simulates a process kill; not a CoreError so the runtime cannot
    convert or persist it — the lease simply stays held until it expires."""


class CrashStore(SQLiteRunStore):
    """Dies on the transition that commits a chosen checkpoint stage."""

    def __init__(self, path, clock, die_on_stage):
        super().__init__(path, clock=clock)
        self.die_on_stage = die_on_stage
        self.dead = True

    async def transition(self, lease, expected_revision, state, checkpoint,
                         result, observations):
        for obs in observations:
            if obs.details.get("stage") == self.die_on_stage:
                raise ProcessDeath()
        return await super().transition(lease, expected_revision, state,
                                        checkpoint, result, observations)


def make_binding(owner_id, identity_id="agent-alpha", attempt=1):
    principal = Principal(owner_id=owner_id, tenant_id="tenant-synth",
                          identity_id=identity_id, connection_id="conn-synth-1",
                          generation=1, key_thumbprint="synth-thumb")
    return Binding(principal=principal, source_message_id="msg-synth",
                   source_delivery_id="dlv-synth", source_attempt=attempt)


def make_request(query, tool=TOOL, arguments=None):
    return VerticalRequest(query=query, mode="auto", selected_model=None,
                           policy_revision=POLICY_REVISION, tool_name=tool,
                           tool_arguments=arguments or {"version": "?", "fact_ref": "?"})


class Clock:
    def __init__(self, start=None):
        self.now = start if start is not None else __import__("time").time()

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class Pilot:
    """Memory lives in TemporalStore; CORE runs live in a separate
    SQLiteRunStore file. The crash test swaps in CrashStore over the same
    runs file, then a fresh runtime on the plain store resumes."""

    def __init__(self, workdir):
        import time as _time
        self.workdir = Path(workdir)
        self.memory = TemporalStore(self.workdir / "memory.sqlite3")
        self.runs_path = str(self.workdir / "runs.sqlite3")
        self.clock = Clock()
        self.epoch = _time.time()
        self.executor = SyntheticToolExecutor()
        self.transport = SyntheticTransport()
        self.policy = StaticPolicyGate()
        self.base = datetime(2026, 10, 31, tzinfo=timezone.utc)
        self.evidence = {"phases": []}
        self._last_fact_event = {}

    def _ts(self, minutes):
        return (self.base + timedelta(minutes=minutes)).isoformat()

    def _deadline(self):
        return datetime.fromtimestamp(self.epoch + 3600, timezone.utc).isoformat()

    def _phase(self, name, **data):
        self.evidence["phases"].append({"phase": name, **data})

    # ------------------------------------------------------------- seeding
    def seed(self, owner="owner-1", project="synth-proj"):
        m = self.memory
        e0 = m.ingest_event(
            owner_id=owner, project=project, session="sess-boot",
            actor="agent-beta", recipients=[owner], kind="fact",
            occurred_at=self._ts(0), ingested_at=self._ts(0), certainty="explicit",
            payload={"lesson": "registry pinned: always ship v1.2"})
        e1 = m.ingest_event(
            owner_id=owner, project=project, session="sess-plan",
            actor="agent-alpha", recipients=["agent-beta", owner], kind="fact",
            occurred_at=self._ts(10), ingested_at=self._ts(10), certainty="explicit",
            payload={"release_target": "v1.2", "note": "plan of record"})
        c_neg = m.add_claim(
            owner_id=owner, project=project, session="sess-plan",
            actor="agent-beta", certainty="inferred",
            statement={"lesson": "always ship v1.2", "reason": "registry pinned"},
            evidence_event_ids=[e0["event_id"], e1["event_id"]],
            model_versions={MODEL_A: "syn-1.0"}, created_at=self._ts(11))
        c1 = m.add_claim(
            owner_id=owner, project=project, session="sess-plan",
            actor="agent-alpha", certainty="explicit",
            statement={"action": "ship", "version": "v1.2"},
            evidence_event_ids=[e1["event_id"]], created_at=self._ts(12))
        self._last_fact_event[owner] = e1["event_id"]
        self._phase("seed", events=[e0["event_id"], e1["event_id"]],
                    claims=[c_neg["claim_id"], c1["claim_id"]])
        return {"e0": e0, "e1": e1, "c_neg": c_neg, "c1": c1}

    def correct_fact(self, owner, project, new_version, at_minutes, actor="agent-beta"):
        prev_id = self._last_fact_event[owner]
        e2 = self.memory.ingest_event(
            owner_id=owner, project=project, session="sess-change",
            actor=actor, recipients=[owner], kind="correction",
            occurred_at=self._ts(at_minutes), ingested_at=self._ts(at_minutes),
            certainty="explicit",
            payload={"release_target": new_version, "note": "requirement changed"},
            supersedes_event_id=prev_id, correction_of=prev_id)
        inv = self.memory.invalidate_for_correction(e2["event_id"])
        c2 = self.memory.add_claim(
            owner_id=owner, project=project, session="sess-change",
            actor=actor, certainty="explicit",
            statement={"action": "ship", "version": new_version},
            evidence_event_ids=[e2["event_id"]],
            model_versions={MODEL_A: "syn-1.0"}, created_at=self._ts(at_minutes))
        self._last_fact_event[owner] = e2["event_id"]
        self._phase("correct", correcting_event=e2["event_id"],
                    closed_event=inv["closed_event"],
                    invalidated=inv["invalidated_claim_ids"], new_claim=c2["claim_id"])
        return {"e2": e2, "invalidated": inv, "c2": c2}

    # ---------------------------------------------------------------- CORE
    def _run_store(self):
        return SQLiteRunStore(self.runs_path, clock=self.clock)

    def _planned_arguments(self, owner, project, query, memory_mode, store=None):
        """The decision the runtime's model WILL reach, computed from the same
        deterministic recall + decide the runtime uses. Supplies
        request.tool_arguments (CORE executes caller-supplied arguments; the
        verifier then proves model decision == executed effect)."""
        store = store or self.memory
        if memory_mode == "none":
            items = []
        elif memory_mode == "raw_history":
            items = store.raw_history(owner_id=owner, project=project,
                                      query=query)["items"]
        else:
            items = store.recall(owner_id=owner, project=project,
                                 query=query)["items"]
        decision = decide(items, query)
        return {"action": decision["action"],
                "arguments": {"version": decision["version"],
                              "fact_ref": decision["fact_ref"]}}

    def run_core(self, label, owner, project, query, model_slug, memory_mode,
                 request_key=None, tool_arguments=None, crash_stage=None,
                 expected_fact_id=None, verifier_cls=None, memory_context=None,
                 binding=None, deadline_at=None):
        request_key = request_key or (
            f"{label}:{owner}:{hashlib.sha256(query.encode()).hexdigest()[:12]}")
        expected_fact_id = (expected_fact_id if expected_fact_id is not None
                            else self._last_fact_event.get(owner))
        verifier_cls = verifier_cls or FactVerifyingVerifier
        if tool_arguments is None:
            planned = self._planned_arguments(owner, project, query, memory_mode)
            tool, tool_arguments = planned["action"], planned["arguments"]
        else:
            tool = TOOL
        store = (CrashStore(self.runs_path, clock=self.clock,
                            die_on_stage=crash_stage)
                 if crash_stage else self._run_store())
        model = SyntheticModel(model_slug)
        memory = memory_context or StoreMemoryContext(self.memory, owner, project, memory_mode)
        verifier = verifier_cls(expected_fact_id)
        binding = binding or make_binding(owner)
        request = make_request(query, tool=tool, arguments=tool_arguments)
        runtime = CoreRuntime(store=store, model=model, memory=memory,
                              policy=self.policy, tools=self.executor,
                              transport=self.transport, verifier=verifier)
        try:
            run = asyncio.run(runtime.execute(binding, request_key, request,
                                              deadline_at or self._deadline(), holder=label))
            crashed = False
        except ProcessDeath:
            run, crashed = None, True
        result = {
            "label": label, "crashed_after_tool": crashed,
            "model_slug": model_slug, "memory_mode": memory_mode,
            "model_calls": model.calls, "memory_recalls": memory.recalls,
            "tool_executions_total": len(self.executor.executions),
            "request_key": request_key,
        }
        if run is not None:
            result.update({"run_id": run.run_id, "state": run.state,
                           "revision": run.revision, "result": run.result,
                           "checkpoint_stages": sorted(run.checkpoint.keys())})
        self._phase("core_run", **{k: v for k, v in result.items() if k != "result"})
        return result

    def flush(self, owner, run_id, binding=None, deadline_at=None):
        binding = binding or make_binding(owner)
        runtime = CoreRuntime(store=self._run_store(), model=SyntheticModel(MODEL_A),
                              memory=StoreMemoryContext(self.memory, owner, "synth-proj", "none"),
                              policy=self.policy, tools=self.executor,
                              transport=self.transport,
                              verifier=PermissiveVerifier(None))
        receipt = asyncio.run(runtime.flush_result(
            binding.principal, run_id, deadline_at or self._deadline()))
        self._phase("flush", run_id=run_id, deliveries=len(self.transport.deliveries))
        return receipt

    # ------------------------------------------------------------ scenario
    def run_pilot(self):
        import time as _time
        owner, project = "owner-1", "synth-proj"
        seeded = self.seed(owner, project)

        r1 = self.run_core("R1-decide", owner, project, "what should we ship?",
                           MODEL_A, "temporal_recall",
                           expected_fact_id=seeded["e1"]["event_id"])
        self.flush(owner, r1["run_id"])

        corr = self.correct_fact(owner, project, "v1.3", at_minutes=20)

        hist = self.memory.recall(owner_id=owner, project=project,
                                  query="what should we ship?", as_of=self._ts(15))
        curr = self.memory.recall(owner_id=owner, project=project,
                                  query="what should we ship?")
        self._phase("as_of", historical_valid=[i["ref_id"] for i in hist["items"]],
                    current_valid=[i["ref_id"] for i in curr["items"]])

        r2 = self.run_core("R2-decide-after-change", owner, project,
                           "what should we ship?", MODEL_A, "temporal_recall",
                           expected_fact_id=corr["e2"]["event_id"])
        self.evidence["receipt_fact_links"] = {
            "R1_tool_receipt_fact_ref": r1["result"]["tool_receipt"]["fact_ref"],
            "R2_tool_receipt_fact_ref": r2["result"]["tool_receipt"]["fact_ref"],
            "old_fact_event": seeded["e1"]["event_id"],
            "new_fact_event": corr["e2"]["event_id"],
        }

        # crash after the tool effect commits; restart with a brand-new runtime
        t_before = _time.time()
        r3_key = f"R3-crash:{owner}:fixedkey"
        r3 = self.run_core("R3-crash", owner, project, "what should we ship?",
                           MODEL_A, "temporal_recall", request_key=r3_key,
                           crash_stage="tool_confirmed",
                           expected_fact_id=corr["e2"]["event_id"])
        self.clock.advance(40)  # original lease (30s) is dead; deadline is far ahead
        r3r = self.run_core("R3-restart", owner, project, "what should we ship?",
                            MODEL_A, "temporal_recall", request_key=r3_key,
                            expected_fact_id=corr["e2"]["event_id"])
        self.evidence["restart"] = {
            "crashed": r3["crashed_after_tool"],
            "executions_after_crash": r3["tool_executions_total"],
            "executions_after_restart": r3r["tool_executions_total"],
            "restart_state": r3r.get("state"),
            "restart_model_calls": r3r["model_calls"],
            "restart_memory_recalls": r3r["memory_recalls"],
            "checkpoint_stages_after_restart": r3r.get("checkpoint_stages"),
        }

        before = self.memory.get_event(corr["e2"]["event_id"])["source_digest"]
        r4 = self.run_core("R4-model-swap", owner, project, "what should we ship?",
                           MODEL_B, "temporal_recall",
                           expected_fact_id=corr["e2"]["event_id"])
        after = self.memory.get_event(corr["e2"]["event_id"])["source_digest"]
        self.evidence["model_swap"] = {
            "data_digest_unchanged": before == after,
            "R1_model_receipt": r1["result"]["model_receipt"]["model"],
            "R4_model_receipt": r4["result"]["model_receipt"]["model"],
            "R4_action_version": r4["result"]["tool_receipt"]["version"],
        }
        self.evidence["runs"] = {"R1": r1, "R2": r2, "R3": r3, "R3_restart": r3r,
                                 "R4": r4}
        self.evidence["wall_seconds"] = round(_time.time() - t_before, 3)
        return self.evidence

    # ------------------------------------------------------------ comparison
    def run_comparison(self, workdir):
        owner, project = "owner-cmp", "synth-proj"
        store = TemporalStore(Path(workdir) / "cmp-memory.sqlite3")
        base = datetime(2026, 10, 31, tzinfo=timezone.utc)

        def ts(minutes):
            return (base + timedelta(minutes=minutes)).isoformat()

        e1 = store.ingest_event(owner_id=owner, project=project, session="s1",
                                actor="agent-alpha", recipients=[owner], kind="fact",
                                occurred_at=ts(0), ingested_at=ts(0), certainty="explicit",
                                payload={"release_target": "v2.0"})
        store.add_claim(owner_id=owner, project=project, session="s1",
                        actor="agent-alpha", certainty="explicit",
                        statement={"action": "ship", "version": "v2.0"},
                        evidence_event_ids=[e1["event_id"]], created_at=ts(1))
        e2 = store.ingest_event(owner_id=owner, project=project, session="s2",
                                actor="agent-beta", recipients=[owner], kind="correction",
                                occurred_at=ts(5), ingested_at=ts(5), certainty="explicit",
                                payload={"release_target": "v2.1"},
                                supersedes_event_id=e1["event_id"],
                                correction_of=e1["event_id"])
        store.invalidate_for_correction(e2["event_id"])
        store.add_claim(owner_id=owner, project=project, session="s2",
                        actor="agent-beta", certainty="explicit",
                        statement={"action": "ship", "version": "v2.1"},
                        evidence_event_ids=[e2["event_id"]], created_at=ts(6))
        query = "what should we ship? check release_target"
        expected = {"action": "ship_release", "version": "v2.1",
                    "fact_ref": e2["event_id"]}
        conditions = {}
        for mode in ("none", "raw_history", "temporal_recall"):
            verifier_cls = PermissiveVerifier if mode == "none" else FactVerifyingVerifier
            planned = self._planned_arguments(owner, project, query, mode, store=store)
            rt = CoreRuntime(
                store=SQLiteRunStore(str(Path(workdir) / f"cmp-runs-{mode}.sqlite3")),
                model=(model := SyntheticModel(MODEL_A)),
                memory=StoreMemoryContext(store, owner, project, mode),
                policy=self.policy, tools=SyntheticToolExecutor(),
                transport=SyntheticTransport(), verifier=verifier_cls(e2["event_id"]))
            request = make_request(query, tool=planned["action"],
                                   arguments=planned["arguments"])
            run = asyncio.run(rt.execute(make_binding(owner), f"cmp:{mode}",
                                         request, self._deadline(), holder=f"cmp-{mode}"))
            answer = run.result["answer"]
            conditions[mode] = {
                "action": answer["action"], "version": answer["version"],
                "fact_ref": answer["fact_ref"], "basis": answer.get("basis"),
                "memory_receipt": run.checkpoint["memory"]["retrieval_receipt"],
                "tool_receipt_fact_ref": run.result["tool_receipt"]["fact_ref"],
                "correct": (answer["version"] == expected["version"]
                            and answer["fact_ref"] == expected["fact_ref"]),
            }
        self._phase("comparison", expected=expected,
                    **{f"condition_{k}": {"version": v["version"],
                                          "correct": v["correct"]}
                       for k, v in conditions.items()})
        return {"expected": expected, "conditions": conditions}
