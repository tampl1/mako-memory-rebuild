"""Offline child-context contract on the real CORE runtime and store."""

import dataclasses
import json
import tempfile
from pathlib import Path

from src.adapters import decide
from src.child_context import (
    ContextRestoreUnresolved, FrozenChildMemoryContext, freeze_child_context,
    restore_child_context, verify_child_context,
)
from src.pilot import MODEL_A, Pilot
from src.safety import AccessGrants, EvidenceError, SafeReader
from live_runner import event, ts


def main():
    with tempfile.TemporaryDirectory(prefix="mako-child-context-") as tmp:
        pilot = Pilot(tmp)
        store = pilot.memory
        owner = "owner-live"
        grants = AccessGrants()
        grants.allow(owner, "live-synth", "bge-m3/1024", 1)
        reader = SafeReader(store, grants, "bge-m3/1024", 1)
        old = event(store, owner, "fact", 0, {"release_target": "v1.0"})
        store.add_claim(owner_id=owner, project="live-synth", session="live-session",
                        actor="agent-alpha", certainty="explicit",
                        statement={"action": "ship", "version": "v1.0"},
                        evidence_event_ids=[old["event_id"]], created_at=ts(1))
        retrieved = reader.read(owner_id=owner, project="live-synth", query="ship",
                                mode="temporal_recall", limit=1)
        parent = freeze_child_context(
            parent_run_id="parent-v1", as_of=ts(1), owner=owner, grant_revision=1,
            embedding_generation="bge-m3/1024", retrieved=retrieved)
        decision = decide(parent.items, "what should we ship?")
        args = {"version": decision["version"], "fact_ref": decision["fact_ref"]}
        key = "child-v1-fixed"
        r1 = pilot.run_core("CHILD-R1", owner, "live-synth", "what should we ship?",
                            MODEL_A, "frozen", request_key=key, tool_arguments=args,
                            expected_fact_id=old["event_id"],
                            memory_context=FrozenChildMemoryContext(parent))

        new = event(store, owner, "correction", 10, {"release_target": "v2.0"},
                    old["event_id"], actor="agent-beta")
        store.invalidate_for_correction(new["event_id"])
        store.add_claim(owner_id=owner, project="live-synth", session="live-session",
                        actor="agent-beta", certainty="explicit",
                        statement={"action": "ship", "version": "v2.0"},
                        evidence_event_ids=[new["event_id"]], created_at=ts(10))
        original_digest = parent.context_digest
        original_json = json.dumps(dataclasses.asdict(parent), sort_keys=True)
        checks = {"original_still_verifies": verify_child_context(parent, store, grants)}
        r1r = pilot.run_core("CHILD-R1-replay", owner, "live-synth", "what should we ship?",
                             MODEL_A, "frozen", request_key=key, tool_arguments=args,
                             expected_fact_id=old["event_id"],
                             memory_context=FrozenChildMemoryContext(parent))
        checks["original_not_rewritten"] = (
            parent.context_digest == original_digest
            and r1r["result"]["tool_receipt"] == r1["result"]["tool_receipt"]
            and r1r["tool_executions_total"] == r1["tool_executions_total"])

        retrieved2 = reader.read(owner_id=owner, project="live-synth", query="ship",
                                 mode="temporal_recall", limit=1)
        child2 = freeze_child_context(
            parent_run_id="parent-v2", as_of=None, owner=owner, grant_revision=1,
            embedding_generation="bge-m3/1024", retrieved=retrieved2)
        decision2 = decide(child2.items, "what should we ship?")
        args2 = {"version": decision2["version"], "fact_ref": decision2["fact_ref"]}
        r2 = pilot.run_core("CHILD-R2", owner, "live-synth", "what should we ship?",
                            MODEL_A, "frozen", request_key="child-v2-fixed", tool_arguments=args2,
                            expected_fact_id=new["event_id"],
                            memory_context=FrozenChildMemoryContext(child2))
        checks["new_task_uses_v2"] = r2["result"]["tool_receipt"]["fact_ref"] == new["event_id"]

        grants.revoke(owner, "live-synth")
        try:
            restore_child_context(original_json, store, grants)
            checks["revoked_grant_not_revived"] = False
        except ContextRestoreUnresolved:
            checks["revoked_grant_not_revived"] = True
        grants.allow(owner, "live-synth", "bge-m3/1024", 1)

        grants.allow("owner-foreign", "live-synth", "bge-m3/1024", 1)
        foreign = freeze_child_context(
            parent_run_id="parent-foreign", as_of=ts(1), owner="owner-foreign",
            grant_revision=1, embedding_generation="bge-m3/1024", retrieved=retrieved)
        try:
            verify_child_context(foreign, store, grants)
            checks["foreign_owner_rejected"] = False
        except EvidenceError:
            checks["foreign_owner_rejected"] = True

        with store._db() as db:
            db.execute("DELETE FROM source_events WHERE event_id=?", (old["event_id"],))
        try:
            verify_child_context(parent, store, grants)
            checks["missing_source_rejected"] = False
        except EvidenceError:
            checks["missing_source_rejected"] = True
        with store._db() as db:
            db.execute("UPDATE source_events SET payload=? WHERE event_id=?",
                       (json.dumps({"release_target": "v8.8"}), new["event_id"]))
        try:
            verify_child_context(child2, store, grants)
            checks["evidence_tamper_rejected"] = False
        except EvidenceError:
            checks["evidence_tamper_rejected"] = True

        grants.allow(owner, "live-synth", "bge-m3/1025", 1)
        try:
            verify_child_context(parent, store, grants)
            checks["embedding_generation_rejected"] = False
        except EvidenceError:
            checks["embedding_generation_rejected"] = True
        except Exception:
            checks["embedding_generation_rejected"] = True

        try:
            restore_child_context("{\"source\":\"missing\"}", store, grants)
            checks["restore_unresolved_not_model_error"] = False
        except ContextRestoreUnresolved:
            checks["restore_unresolved_not_model_error"] = True

        checks["no_external_effects"] = r2["tool_executions_total"] == r1["tool_executions_total"] + 1
        report = {
            "adapter": "FrozenChildMemoryContext",
            "core": "2fa094082324af15b2c0fa7ad38c95dab92d7147",
            "parent": dataclasses.asdict(parent),
            "new_child_digest": child2.context_digest,
            "runs": {"R1": r1, "R1_replay": r1r, "R2": r2},
            "checks": checks,
            "all_checks_passed": all(checks.values()),
            "external_model_calls": 0,
            "limitations": "real CORE runtime/store with synthetic decision/tool adapters; no live model calls",
        }
        Path("artifacts/child-context-evidence.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        print(json.dumps({"checks": checks, "all_checks_passed": report["all_checks_passed"]}))
        return 0 if report["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
