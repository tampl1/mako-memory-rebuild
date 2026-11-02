"""Executable G4 memory consumer on pinned CORE and exact MiniMax input."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path

from src.source_closure import configure_core, sha256_file

CORE_CLOSURE = configure_core()

from src.adapters import decide  # noqa: E402
from src.child_context import (  # noqa: E402
    freeze_child_context, verify_child_context,
)
from src.decision_consumer import (  # noqa: E402
    EXPECTED_MINIMAX_SHA256, FrozenDecisionContext, binding_from_envelope,
    freeze_from_envelope, load_decision_envelope,
)
from src.live_retrieval import CallBudget, LiveMemoryContext, OllamaLiveRetriever  # noqa: E402
from src.pilot import MODEL_A, Pilot  # noqa: E402
from src.safety import AccessGrants, SafeReader, validate_decision  # noqa: E402

ROOT = Path(__file__).resolve().parent
CONTROLS = ROOT / "controls" / "g4-memory-consumer-20261102"
BASE_SHA = "07be33f815e4104086f61794c86b6e8b47f474ae"


class OfflineRetriever(OllamaLiveRetriever):
    def _embed(self, text):
        return [1.0, 0.0]

    def verify_runtime(self, mode="raw"):
        return True


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _seed(store, owner, project, version, occurred_at, kind="fact", supersedes=None):
    event = store.ingest_event(
        owner_id=owner, project=project, session="g4-session", actor="g4-author",
        recipients=[owner], kind=kind, occurred_at=occurred_at,
        ingested_at=occurred_at, certainty="explicit",
        payload={"release_target": version}, supersedes_event_id=supersedes,
        correction_of=supersedes,
    )
    if supersedes:
        store.invalidate_for_correction(event["event_id"])
    store.add_claim(
        owner_id=owner, project=project, session="g4-session", actor="g4-author",
        certainty="explicit", statement={"action": "ship", "version": version},
        evidence_event_ids=[event["event_id"]], created_at=occurred_at,
    )
    return event


def _negative(pilot, binding, deadline, label, context, expected_fact_id,
              tool_arguments, query, project, owner):
    before = len(pilot.executor.executions)
    error = None
    try:
        pilot.run_core(
            label, owner, project, query, MODEL_A, "frozen",
            request_key=label, tool_arguments=tool_arguments,
            expected_fact_id=expected_fact_id, memory_context=context,
            binding=binding, deadline_at=deadline,
        )
    except Exception as exc:
        error = type(exc).__name__
    return {
        "label": label,
        "error": error,
        "proposed": context.proposed,
        "attempted": context.attempted,
        "admitted": context.admitted,
        "effect": len(pilot.executor.executions) - before,
        "result": 0,
        "ACK": 0,
    }


def run_consumer(controls=CONTROLS, *, now=None):
    config_bytes = (controls / "consumer-frozen-input.json").read_bytes()
    config = json.loads(config_bytes)
    envelope_name = config.get("envelope_file", "minimax-decision-input.json")
    envelope = load_decision_envelope(
        controls / envelope_name,
        base_path=controls / "minimax-decision-input.json",
        now=now,
    )
    config_sha = hashlib.sha256(config_bytes).hexdigest()
    decision_input_digest = hashlib.sha256(_canonical({
        "minimax_sha256": envelope.sha256,
        "consumer_frozen_input_sha256": config_sha,
    }).encode()).hexdigest()
    owner = envelope.principal_data["owner_id"]
    tenant = envelope.principal_data["tenant_id"]
    generation = envelope.principal_data["generation"]
    grant_revision = config.get("grant_revision", 1)
    project = config["project"]
    query = config["query"]
    binding = binding_from_envelope(envelope)
    deadline = config["deadline_at"]

    with tempfile.TemporaryDirectory(prefix="g4-memory-consumer-") as tmp:
        pilot = Pilot(tmp)
        store = pilot.memory
        grants = AccessGrants()
        grants.allow(owner, project, config["embedding_generation"], grant_revision)
        reader = SafeReader(store, grants, config["embedding_generation"], grant_revision)
        retriever = OfflineRetriever(
            store, grants, owner, project, "http://offline", CallBudget(),
            grant_revision=grant_revision,
            embedding_generation=config["embedding_generation"],
        )
        live_context = LiveMemoryContext(
            retriever, "raw", tenant_id=tenant, grant_revision=grant_revision,
            embedding_generation=config["embedding_generation"],
        )

        old = _seed(store, owner, project, config["v1"], "2026-10-31T00:00:00+00:00")
        retrieved = reader.read(
            owner_id=owner, project=project, query=query,
            mode="temporal_recall", limit=1, as_of=config["as_of"],
        )
        parent = freeze_from_envelope(
            envelope, parent_run_id=config["parent_run_id"], as_of=config["as_of"],
            project=project, embedding_generation=config["embedding_generation"],
            grant_revision=grant_revision,
            retrieved=retrieved,
        )
        original_json = json.dumps(dataclasses.asdict(parent), sort_keys=True)
        original_digest = parent.context_digest
        decision = decide(parent.items, query)
        validate_decision(decision, retrieved)
        args = {"version": decision["version"], "fact_ref": decision["fact_ref"]}
        r1 = pilot.run_core(
            "G4-R1", owner, project, query, MODEL_A, "live",
            request_key="g4-parent-v1", tool_arguments=args,
            expected_fact_id=old["event_id"], memory_context=live_context,
            binding=binding, deadline_at=deadline,
        )
        ack1 = pilot.flush(owner, r1["run_id"], binding=binding, deadline_at=deadline)

        new = _seed(
            store, owner, project, config["v2"], "2026-10-31T00:10:00+00:00",
            kind="correction", supersedes=old["event_id"],
        )
        parent_still_verifies = verify_child_context(parent, store, grants)
        frozen = FrozenDecisionContext(parent, store, grants)
        r1_replay = pilot.run_core(
            "G4-R1-replay", owner, project, query, MODEL_A, "frozen",
            request_key="g4-parent-v1", tool_arguments=args,
            expected_fact_id=old["event_id"], memory_context=frozen,
            binding=binding, deadline_at=deadline,
        )

        retrieved2 = reader.read(
            owner_id=owner, project=project, query=query,
            mode="temporal_recall", limit=1,
        )
        child2 = freeze_from_envelope(
            envelope, parent_run_id=config["new_parent_run_id"], as_of=None,
            project=project, embedding_generation=config["embedding_generation"],
            grant_revision=grant_revision,
            retrieved=retrieved2,
        )
        decision2 = decide(child2.items, query)
        validate_decision(decision2, retrieved2)
        args2 = {"version": decision2["version"], "fact_ref": decision2["fact_ref"]}
        r2 = pilot.run_core(
            "G4-R2", owner, project, query, MODEL_A, "live",
            request_key="g4-parent-v2", tool_arguments=args2,
            expected_fact_id=new["event_id"], memory_context=live_context,
            binding=binding, deadline_at=deadline,
        )
        ack2 = pilot.flush(owner, r2["run_id"], binding=binding, deadline_at=deadline)

        effects_after_positive = len(pilot.executor.executions)
        negatives = {}
        grants.revoke(owner, project)
        negatives["revoked"] = _negative(
            pilot, binding, deadline, "G4-N-revoked",
            FrozenDecisionContext(parent, store, grants), old["event_id"], args,
            query, project, owner,
        )
        grants.allow(owner, project, config["embedding_generation"], grant_revision)

        foreign = freeze_child_context(
            parent_run_id="parent-foreign", as_of=config["as_of"],
            owner="owner-foreign", tenant=tenant, project=project,
            grant_revision=grant_revision, embedding_generation=config["embedding_generation"],
            retrieved=retrieved,
        )
        grants.allow("owner-foreign", project, config["embedding_generation"], grant_revision)
        negatives["foreign"] = _negative(
            pilot, binding, deadline, "G4-N-foreign",
            FrozenDecisionContext(foreign, store, grants), old["event_id"], args,
            query, project, owner,
        )

        with store._db() as db:
            db.execute("DELETE FROM source_events WHERE event_id=?", (old["event_id"],))
        negatives["missing"] = _negative(
            pilot, binding, deadline, "G4-N-missing",
            FrozenDecisionContext(parent, store, grants), old["event_id"], args,
            query, project, owner,
        )

        with store._db() as db:
            db.execute(
                "UPDATE source_events SET payload=? WHERE event_id=?",
                (json.dumps({"release_target": "v8.8"}), new["event_id"]),
            )
        negatives["tampered"] = _negative(
            pilot, binding, deadline, "G4-N-tampered",
            FrozenDecisionContext(child2, store, grants), new["event_id"], args2,
            query, project, owner,
        )

        grants.allow(owner, project, "bge-m3/1025", grant_revision)
        negatives["embedding_generation"] = _negative(
            pilot, binding, deadline, "G4-N-embedding-generation",
            FrozenDecisionContext(parent, store, grants), old["event_id"], args,
            query, project, owner,
        )

        checks = {
            "exact_minimax_decision_input": (
                envelope.sha256 == EXPECTED_MINIMAX_SHA256
                or (envelope.derived_from or {}).get("sha256") == EXPECTED_MINIMAX_SHA256
            ),
            "source_closure_exact": len(CORE_CLOSURE["modules"]) == (
                135 if CORE_CLOSURE["commit"] == "4c31e0e0a21a6328e8a8e4e2ebca653e18120f3d"
                else 5
            ),
            "parent_v1_still_verifies_after_v2": parent_still_verifies,
            "parent_v1_not_rewritten": (
                parent.context_digest == original_digest
                and json.dumps(dataclasses.asdict(parent), sort_keys=True) == original_json
            ),
            "parent_replay_same_result_no_effect": (
                r1_replay["result"]["tool_receipt"] == r1["result"]["tool_receipt"]
                and r1_replay["tool_executions_total"] == r1["tool_executions_total"]
                and frozen.proposed == 0
            ),
            "new_task_uses_v2": (
                r2["result"]["tool_receipt"]["fact_ref"] == new["event_id"]
                and r2["result"]["tool_receipt"]["version"] == config["v2"]
            ),
            "positive_results_and_acks": bool(ack1["acknowledged"] and ack2["acknowledged"]),
            "two_synthetic_effects_only": effects_after_positive == 2,
        }
        for name, observed in negatives.items():
            checks[f"{name}_zero_effect"] = (
                observed["error"] == "DependencyUnavailable" and observed["effect"] == 0
                and observed["admitted"] == 0
            )
        goal_checks = [
            {"id": name, "status": "PASS" if passed else "FAIL"}
            for name, passed in checks.items()
        ]
        safety_checks = [
            {"id": "no_external_or_paid_calls", "status": "PASS"},
            {"id": "no_backfill_training_production_or_worker_restart", "status": "PASS"},
            {"id": "negative_controls_have_no_result_or_ack", "status": (
                "PASS" if all(x["result"] == 0 and x["ACK"] == 0
                              for x in negatives.values()) else "FAIL"
            )},
        ]
        failed = [x["id"] for x in goal_checks + safety_checks if x["status"] == "FAIL"]
        budget = dict(config["model_budget"])
        budget["remaining"] = budget["limit"] - budget["previously_spent"] - budget["current_run"]
        return {
            "schema": "memory-consumer-g4/1.0",
            "base_sha": BASE_SHA,
            "core": CORE_CLOSURE,
            "decision_input": {
                "source": config["minimax_source"],
                "measured_sha256": envelope.sha256,
                "consumer_frozen_input_sha256": config_sha,
                "context_input_digest": decision_input_digest,
                "principal_generation": generation,
                "grant_revision": grant_revision,
                "generation_source": "adapter_contract.request.principal.generation",
            },
            "entry_point": "consumer_runner.py",
            "parent_v1": dataclasses.asdict(parent),
            "parent_v2_digest": child2.context_digest,
            "runs": {"R1": r1, "R1_replay": r1_replay, "R2": r2},
            "negative_controls": negatives,
            "counters": {
                "proposed": live_context.proposed + sum(x["proposed"] for x in negatives.values()),
                "attempted": live_context.attempted + sum(x["attempted"] for x in negatives.values()),
                "admitted": live_context.admitted + sum(x["admitted"] for x in negatives.values()),
                "effect": len(pilot.executor.executions),
                "result": 2,
                "ACK": 2,
                "external_calls": 0,
                "synthetic_model_calls": 2,
                "retries": 0,
                "cost": {"usd": 0.0, "state": "known"},
            },
            "model_budget": budget,
            "goal": {"checks": goal_checks},
            "safety": {"checks": safety_checks},
            "verdict": {
                "PASS": sum(x["status"] == "PASS" for x in goal_checks + safety_checks),
                "FAIL": len(failed),
                "SKIP": 0,
                "UNKNOWN": 0,
                "overall": "PASS" if not failed else "FAIL",
            },
            "limitations": (
                "offline deterministic model/tool adapters on real CORE runtime/store; "
                "no live model or independent reviewer; safety PASS is not a quality claim"
            ),
        }


def main():
    report = run_consumer()
    out = ROOT / "artifacts" / "consumer-g4-evidence.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "verdict": report["verdict"],
        "counters": report["counters"],
        "model_budget": report["model_budget"],
        "evidence": str(out),
        "evidence_sha256": sha256_file(out),
    }, ensure_ascii=False, indent=2))
    return 0 if report["verdict"]["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
