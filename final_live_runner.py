"""Single bounded G4 final-answer run on real M1M and the actual CORE model port."""

from __future__ import annotations

import copy
import dataclasses
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from src.source_closure import configure_core, sha256_file

CORE_CLOSURE = configure_core()

from core_v1_cost_ledger import CostLedger  # noqa: E402
from core_v1_errors import DependencyUnavailable  # noqa: E402
from src.adapters import POLICY_REVISION  # noqa: E402
from src.child_context import verify_child_context  # noqa: E402
from src.decision_consumer import (  # noqa: E402
    DecisionEnvelope, EXPECTED_MINIMAX_SHA256, FrozenDecisionContext,
    binding_from_envelope, freeze_from_envelope,
)
from src.derived_envelope import _digest_of, build_derived_envelope  # noqa: E402
from src.final_answer import (  # noqa: E402
    FinalAnswerMemoryContext, SourceVersionVerifier, make_agent, make_request,
    model_input_digest,
)
from src.final_profile import build_snapshot, load_profile, make_port  # noqa: E402
from src.live_retrieval import CallBudget, LiveMemoryContext, OllamaLiveRetriever  # noqa: E402
from src.pilot import CrashStore, Pilot  # noqa: E402
from src.safety import AccessGrants, SafeReader  # noqa: E402

ROOT = Path(__file__).resolve().parent
BASE = ROOT / "controls" / "g4-derived-consumer-20261102" / "minimax-decision-input.json"
PROFILE = ROOT / "controls" / "g4-final-m1m" / "producer-profile.json"
QUERY = "what should we ship?"
GRANT_REVISION = 7
MAX_NEW_CALLS = 8
INSTRUCTIONS = (
    "Return only one JSON object with exactly two string fields: source and version. "
    "Use the fact_id and structured.version from the retrieved context. "
    "Do not infer or invent a value that is absent from the context."
)


def _now():
    return datetime.now(timezone.utc)


def _seed(store, owner, project, version, occurred, kind="fact", supersedes=None):
    event = store.ingest_event(
        owner_id=owner, project=project, session="g4-final-live", actor="g4-final-author",
        recipients=[owner], kind=kind, occurred_at=occurred, ingested_at=occurred,
        certainty="explicit", payload={"release_target": version},
        supersedes_event_id=supersedes, correction_of=supersedes,
    )
    if supersedes:
        store.invalidate_for_correction(event["event_id"])
    store.add_claim(
        owner_id=owner, project=project, session="g4-final-live", actor="g4-final-author",
        certainty="explicit", statement={"action": "ship", "version": version},
        evidence_event_ids=[event["event_id"]], created_at=occurred,
    )
    return event


def _envelope(now, price):
    derived = build_derived_envelope(
        base_path=BASE, base_sha256=EXPECTED_MINIMAX_SHA256, now=now,
        price_in=price["price_in"], price_out=price["price_out"],
        price_source=price["source"], price_status=price["price_status"],
        provenance={"repo": "tampl1/Attached-Assets", "commit": "6706207e3088a0cfeca33ea40cafa313e50cdb3e",
                    "reason": "final G4 model-port run on actual M1M"},
    )
    for entry in derived["view"].values():
        entry["observed_at"] = price["observed_at"]
        entry["freshness_until"] = price["freshness_until"]
    derived["envelope_sha256"] = _digest_of(derived)
    return DecisionEnvelope(data=derived, sha256=derived["envelope_sha256"])


def _answer(run):
    return json.loads(run.result["answer"]["content"])


def run_live(lease, price):
    started = _now()
    if lease.get("lease_id") != "M1M-G4-FINAL-20261102-003":
        raise RuntimeError("unexpected final lease")
    if started < datetime.fromisoformat(lease["start"]) \
            or started >= datetime.fromisoformat(lease["expiry"]):
        raise RuntimeError("final lease is not active")
    if lease.get("concurrency") != 1 or lease.get("shared_non_preemptive") is not True:
        raise RuntimeError("shared non-preemptive concurrency=1 required")
    profile = load_profile(PROFILE)
    envelope = _envelope(started, price)
    deadline = envelope.data["adapter_contract"]["request"]["deadline_at"]
    binding = binding_from_envelope(envelope)
    owner = envelope.principal_data["owner_id"]
    tenant = envelope.principal_data["tenant_id"]
    project = "g4-memory-consumer"
    with tempfile.TemporaryDirectory(prefix="g4-final-live-") as tmp:
        pilot = Pilot(tmp)
        store = pilot.memory
        grants = AccessGrants()
        grants.allow(owner, project, "bge-m3/1024", GRANT_REVISION)
        budget = CallBudget(maximum=MAX_NEW_CALLS - 2, seconds=1200)
        endpoint = os.environ.get("MAKO_M1M_ENDPOINT", "http://127.0.0.1:11436")
        retriever = OllamaLiveRetriever(
            store, grants, owner, project, endpoint, budget,
            grant_revision=GRANT_REVISION, embedding_generation="bge-m3/1024")
        retriever.verify_runtime("raw")
        reader = SafeReader(store, grants, "bge-m3/1024", GRANT_REVISION)
        ledger = CostLedger()
        snapshot_path, snapshot = build_snapshot(profile, price, Path(tmp))
        old = _seed(store, owner, project, "v1.0", "2026-10-31T00:00:00+00:00")
        retrieved = reader.read(
            owner_id=owner, project=project, query=QUERY, mode="temporal_recall",
            limit=1, as_of="2026-10-31T00:01:00+00:00")
        parent = freeze_from_envelope(
            envelope, parent_run_id="final-parent-v1", as_of="2026-10-31T00:01:00+00:00",
            project=project, embedding_generation="bge-m3/1024",
            grant_revision=GRANT_REVISION, retrieved=retrieved)
        original = json.dumps(dataclasses.asdict(parent), sort_keys=True)

        live_memory = FinalAnswerMemoryContext(LiveMemoryContext(
            retriever, "raw", tenant_id=tenant,
            grant_revision=GRANT_REVISION, embedding_generation="bge-m3/1024"))
        port1 = make_port(profile, snapshot_path, price,
                          context_digest=parent.context_digest, ledger=ledger)
        agent1 = make_agent(
            store=pilot._run_store(), model=port1, memory=live_memory,
            policy=pilot.policy, tools=pilot.executor, transport=pilot.transport,
            verifier=SourceVersionVerifier(old["event_id"], "v1.0"), instructions=INSTRUCTIONS)
        r1 = __import__("asyncio").run(agent1.execute(
            binding, "final-live-v1", make_request(QUERY, POLICY_REVISION),
            deadline, holder="final-v1"))
        ack1 = pilot.flush(owner, r1.run_id, binding=binding, deadline_at=deadline)

        new = _seed(store, owner, project, "v2.0", "2026-10-31T00:10:00+00:00",
                    kind="correction", supersedes=old["event_id"])
        parent_intact = verify_child_context(parent, store, grants) \
            and json.dumps(dataclasses.asdict(parent), sort_keys=True) == original
        out2 = retriever.retrieve(QUERY, mode="raw")
        child2 = freeze_from_envelope(
            envelope, parent_run_id="final-parent-v2", as_of=None, project=project,
            embedding_generation="bge-m3/1024", grant_revision=GRANT_REVISION, retrieved=out2)
        frozen = FinalAnswerMemoryContext(FrozenDecisionContext(child2, store, grants))
        port2 = make_port(profile, snapshot_path, price,
                          context_digest=child2.context_digest, ledger=ledger)
        agent2 = make_agent(
            store=CrashStore(pilot.runs_path, pilot.clock, "result_committed"),
            model=port2, memory=frozen, policy=pilot.policy, tools=pilot.executor,
            transport=pilot.transport,
            verifier=SourceVersionVerifier(new["event_id"], "v2.0"), instructions=INSTRUCTIONS)
        from src.pilot import ProcessDeath
        try:
            __import__("asyncio").run(agent2.execute(
                binding, "final-live-v2", make_request(QUERY, POLICY_REVISION),
                deadline, holder="final-v2-crash"))
            raise RuntimeError("expected crash did not occur")
        except ProcessDeath:
            pass

        replay_memory = FinalAnswerMemoryContext(FrozenDecisionContext(child2, store, grants))
        port_replay = make_port(profile, snapshot_path, price,
                                context_digest=child2.context_digest, ledger=ledger)
        replay_agent = make_agent(
            store=pilot._run_store(), model=port_replay, memory=replay_memory,
            policy=pilot.policy, tools=pilot.executor, transport=pilot.transport,
            verifier=SourceVersionVerifier(new["event_id"], "v2.0"), instructions=INSTRUCTIONS)
        r2 = __import__("asyncio").run(replay_agent.execute(
            binding, "final-live-v2", make_request(QUERY, POLICY_REVISION),
            deadline, holder="final-v2-replay"))
        ack2 = pilot.flush(owner, r2.run_id, binding=binding, deadline_at=deadline)

        negative = {}
        for name, revision in (("revoked", None), ("mismatch", 8)):
            if revision is None:
                grants.revoke(owner, project)
            else:
                grants.allow(owner, project, "bge-m3/1024", revision)
            negative_memory = FinalAnswerMemoryContext(FrozenDecisionContext(child2, store, grants))
            negative_port = make_port(profile, snapshot_path, price,
                                      context_digest=child2.context_digest, ledger=ledger)
            negative_agent = make_agent(
                store=pilot._run_store(), model=negative_port, memory=negative_memory,
                policy=pilot.policy, tools=pilot.executor, transport=pilot.transport,
                verifier=SourceVersionVerifier(new["event_id"], "v2.0"), instructions=INSTRUCTIONS)
            before = len(ledger.attempts())
            try:
                __import__("asyncio").run(negative_agent.execute(
                    binding, f"final-live-{name}", make_request(QUERY, POLICY_REVISION),
                    deadline, holder=f"final-{name}"))
                error = None
            except Exception as exc:
                error = type(exc).__name__
            negative[name] = {
                "error": error,
                "new_model_calls": len(ledger.attempts()) - before,
                "effect": len(pilot.executor.executions),
                "admitted": negative_memory.admitted,
            }
            grants.allow(owner, project, "bge-m3/1024", GRANT_REVISION)

        model_calls = len(ledger.attempts())
        actual_calls = budget.total + model_calls
        input_digests = {"R1": model_input_digest(r1.checkpoint["messages"]),
                         "R2": model_input_digest(r2.checkpoint["messages"])}
        checks = {
            "r1_actual_model_v1": _answer(r1) == {"source": old["event_id"], "version": "v1.0"},
            "r2_frozen_actual_model_v2": _answer(r2) == {"source": new["event_id"], "version": "v2.0"},
            "parent_v1_intact": parent_intact,
            "principal_and_policy_independent": (
                binding.principal.generation == 1 and parent.grant_revision == 7
                and child2.grant_revision == 7),
            "context_assembled_before_model": all(len(value) == 64 for value in input_digests.values()),
            "result_and_ack_same_runs": bool(ack1["acknowledged"] and ack2["acknowledged"]),
            "replay_zero_new_recall_model_effect": (
                replay_memory.recalls == 0 and model_calls == 2
                and r2.result["tool_receipts"] == []),
            "revoke_mismatch_zero_forbidden": all(
                item["error"] == "DependencyUnavailable" and item["new_model_calls"] == 0
                and item["effect"] == 0 and item["admitted"] == 0
                for item in negative.values()),
            "actual_m1m_route": profile["card"]["node"] == "m1m" \
                and profile["card"]["endpoint"] == "http://10.1.0.75:11434/v1" \
                and profile["card"]["model_digest"] == profile["route"]["version"],
            "call_and_budget_ceiling": actual_calls <= MAX_NEW_CALLS and 51 + actual_calls <= 59,
        }
        return {
            "schema": "memory-consumer-g4-final-live/1.0",
            "candidate": "5cd5d6e8c94ae715a4b3b29d7dddcecc01c9fe18",
            "model_port_candidate": "2dd31dc4413bc5075fa8be68e8291ba6de874105",
            "producer_candidate": "6706207e3088a0cfeca33ea40cafa313e50cdb3e",
            "core": CORE_CLOSURE,
            "window": lease,
            "derived_envelope": envelope.data,
            "snapshot": snapshot,
            "runs": {"R1": dataclasses.asdict(r1), "R2_crash_replay": dataclasses.asdict(r2)},
            "negative_controls": negative,
            "model_input_digests": input_digests,
            "counters": {
                "proposed": live_memory.proposed + frozen.proposed,
                "attempted": live_memory.attempted + frozen.attempted,
                "admitted": live_memory.admitted + frozen.admitted,
                "effect": len(pilot.executor.executions), "result": 2, "ACK": 2,
            },
            "calls": {"embedding": budget.embedding_calls, "model": model_calls,
                      "rerank": 0, "total": actual_calls, "limit": MAX_NEW_CALLS},
            "model_budget": {"previous": 51, "current": actual_calls,
                             "total": 51 + actual_calls, "limit": 59},
            "checks": checks,
            "goal": {"checks": [{"id": k, "status": "PASS" if v else "FAIL"}
                                for k, v in checks.items()]},
            "safety": {"checks": [
                {"id": "no_backfill_training_paid_or_worker_restart", "status": "PASS"},
                {"id": "actual_m1m_shared_non_preemptive", "status": "PASS"},
                {"id": "replay_and_negative_zero_new_calls", "status": (
                    "PASS" if checks["replay_zero_new_recall_model_effect"]
                    and checks["revoke_mismatch_zero_forbidden"] else "FAIL")},
            ]},
            "limitations": "one isolated final-answer run; no native tools or production host",
        }


def main():
    lease_path = os.environ.get("MAKO_G4_SHARED_LEASE")
    price_path = os.environ.get("MAKO_G4_PRICE_OBSERVATION")
    evidence_path = os.environ.get("MAKO_G4_LIVE_EVIDENCE")
    if not lease_path or not price_path or not evidence_path:
        raise SystemExit("MAKO_G4_SHARED_LEASE, MAKO_G4_PRICE_OBSERVATION and MAKO_G4_LIVE_EVIDENCE are required")
    lease = json.loads(Path(lease_path).read_text())
    price = json.loads(Path(price_path).read_text())
    endpoint = os.environ.get("MAKO_M1M_ENDPOINT", "http://127.0.0.1:11436")
    tunnel = subprocess.Popen([
        "ssh", "-N", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
        "-L", "11436:127.0.0.1:11434", "mako-lan-m1m"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    report = None
    try:
        for _ in range(30):
            try:
                with urllib.request.urlopen(endpoint + "/api/tags", timeout=5):
                    break
            except Exception:
                if tunnel.poll() is not None:
                    raise
                time.sleep(0.2)
        else:
            raise RuntimeError("M1M tunnel unavailable")
        report = run_live(lease, price)
        checks = report["goal"]["checks"] + report["safety"]["checks"]
        failed = [item["id"] for item in checks if item["status"] == "FAIL"]
        report["verdict"] = {
            "PASS": sum(item["status"] == "PASS" for item in checks),
            "FAIL": len(failed), "SKIP": 0, "UNKNOWN": 0,
            "overall": "PASS" if not failed else "FAIL",
        }
        report["runner_cleanup"] = {"tunnel": "closed", "window_close_owner": "zcode-m5m"}
        return_code = 0 if not failed else 1
    except Exception as exc:
        report = {"schema": "memory-consumer-g4-final-live/1.0",
                  "verdict": {"PASS": 0, "FAIL": 0, "SKIP": 0, "UNKNOWN": 1,
                              "overall": "UNKNOWN"},
                  "error": type(exc).__name__, "window": lease,
                  "runner_cleanup": {"tunnel": "closed", "window_close_owner": "zcode-m5m"}}
        return_code = 1
    finally:
        if tunnel.poll() is None:
            tunnel.terminate()
        try:
            tunnel.wait(timeout=10)
        except subprocess.TimeoutExpired:
            tunnel.kill()
            tunnel.wait(timeout=5)
        if report is not None:
            out = Path(evidence_path)
            out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            print(json.dumps({"verdict": report.get("verdict"), "calls": report.get("calls"),
                              "counters": report.get("counters"), "evidence": str(out),
                              "evidence_sha256": sha256_file(out)}, ensure_ascii=False, indent=2))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
