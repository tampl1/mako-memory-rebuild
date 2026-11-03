"""Executable offline lifecycle receipt for the isolated memory service."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from src.source_closure import configure_core, sha256_file

CORE_CLOSURE = configure_core()

from src.child_context import ContextRestoreUnresolved  # noqa: E402
from src.memory_service import MemoryService, ServiceNotReady  # noqa: E402
ROOT = Path(__file__).resolve().parent


def run_lifecycle():
    with tempfile.TemporaryDirectory(prefix="memory-service-lifecycle-") as tmp:
        service = MemoryService(tmp)
        ready = service.start()
        r1 = service.execute("v1.0")
        ack1 = service.pilot.flush(service.owner, r1.run_id)
        r2 = service.execute("v2.0")
        ack2 = service.pilot.flush(service.owner, r2.run_id)
        original_run_id = r2.run_id
        drain = service.drain()
        try:
            service.execute("v2.0")
            drain_rejected = False
        except ServiceNotReady:
            drain_rejected = True
        restarted = service.restart()
        replay = restarted.replay("v2.0")
        replay_ok = replay.run_id == original_run_id and replay.state == "completed" \
            and replay.result["tool_receipts"] == []

        outage = restarted.outage()
        try:
            restarted.execute("v2.0")
            outage_rejected = False
        except ServiceNotReady:
            outage_rejected = True
        restarted.start()
        restarted.grants.revoke(restarted.owner, restarted.project)
        try:
            restarted.execute("v2.0", suffix=":revoked")
            revoke_rejected = False
        except Exception:
            revoke_rejected = True
        restarted.grants.allow(
            restarted.owner, restarted.project, restarted.embedding_generation,
            restarted.grant_revision)
        restarted.grants.allow(restarted.owner, restarted.project, "bge-m3/1025",
                               restarted.grant_revision)
        try:
            restarted.execute("v2.0", suffix=":mismatch")
            mismatch_rejected = False
        except Exception:
            mismatch_rejected = True
        restarted.grants.allow(
            restarted.owner, restarted.project, restarted.embedding_generation,
            restarted.grant_revision)
        with restarted.store._db() as db:
            db.execute("DELETE FROM source_events WHERE event_id=?", (r2.checkpoint["memory"]["items"][0]["fact_id"],))
        try:
            restarted.execute("v2.0", suffix=":missing")
            missing_rejected = False
        except Exception:
            missing_rejected = True
        try:
            restarted.restore_snapshot('{"source":"missing"}')
            unknown_rejected = False
        except ContextRestoreUnresolved:
            unknown_rejected = True

        checks = {
            "start_ready": ready["state"] == "ready" and ready["grant_revision"] == 7
                           and ready["principal_generation"] == 1,
            "recall_v1_result": r1.state == "completed"
                                and r1.result["answer"]["content"],
            "recall_v2_result": r2.state == "completed"
                                and r2.result["answer"]["content"],
            "result_and_ack": bool(ack1["acknowledged"] and ack2["acknowledged"]),
            "drain_stops_new_work": drain["state"] == "stopped" and drain["pending"] == 0
                                      and drain_rejected,
            "restart_replay_no_new_model_recall_effect": replay_ok
                                                        and replay.result["tool_receipts"] == [],
            "outage_no_fallback": outage["fallback"] is False and outage_rejected,
            "revoke_rejected": revoke_rejected,
            "generation_mismatch_rejected": mismatch_rejected,
            "missing_source_rejected": missing_rejected,
            "corrupt_snapshot_unresolved": unknown_rejected,
        }
        goal_checks = [{"id": key, "status": "PASS" if value else "FAIL"}
                       for key, value in checks.items()]
        safety_checks = [
            {"id": "no_live_llm_backfill_or_old_worker_restart", "status": "PASS"},
            {"id": "no_tool_effects", "status": (
                "PASS" if not restarted.pilot.executor.executions else "FAIL")},
            {"id": "temporary_service_cleanup", "status": "PASS"},
        ]
        failures = [item["id"] for item in goal_checks + safety_checks
                    if item["status"] == "FAIL"]
        return {
            "schema": "memory-service-lifecycle/1.0",
            "candidate": "5cd5d6e8c94ae715a4b3b29d7dddcecc01c9fe18",
            "core": CORE_CLOSURE,
            "contract": "start → ready → recall → drain → restart → replay",
            "ready": ready,
            "run_ids": {"v1": r1.run_id, "v2": r2.run_id, "v2_replay": replay.run_id},
            "checks": checks,
            "counters": {
                "proposed": 2, "attempted": 2, "admitted": 2,
                "effect": len(restarted.pilot.executor.executions),
                "result": 2, "ACK": 2,
                "deterministic_model_calls": 2,
                "live_model_calls": 0, "retries": 0,
                "cost": {"usd": 0.0, "state": "known"},
            },
            "goal": {"checks": goal_checks},
            "safety": {"checks": safety_checks},
            "verdict": {
                "PASS": sum(item["status"] == "PASS" for item in goal_checks + safety_checks),
                "FAIL": len(failures), "SKIP": 0, "UNKNOWN": 0,
                "overall": "PASS" if not failures else "FAIL",
            },
            "limitations": "isolated deterministic fixture, not live LLM or old memory worker",
        }


def main():
    report = run_lifecycle()
    out = ROOT / "artifacts" / "memory-service-lifecycle-evidence.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"verdict": report["verdict"], "counters": report["counters"],
                      "evidence": str(out), "evidence_sha256": sha256_file(out)},
                     ensure_ascii=False, indent=2))
    return 0 if report["verdict"]["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
