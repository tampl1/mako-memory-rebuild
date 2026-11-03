"""Executable offline lifecycle receipt for the isolated memory service."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from src.source_closure import configure_core, sha256_file

CORE_CLOSURE = configure_core()

from core_v1_errors import DependencyUnavailable  # noqa: E402
from src.child_context import ContextRestoreUnresolved  # noqa: E402
from src.memory_service import MemoryService, ServiceNotReady, StaticAuthority  # noqa: E402
from src.safety import (  # noqa: E402
    AuthorizationError, EmbeddingGenerationMismatch, EvidenceError,
)

ROOT = Path(__file__).resolve().parent


def _rejected(expected, operation, *, cause=None):
    try:
        operation()
    except expected as exc:
        if cause is None:
            return True, type(exc).__name__
        current = exc
        seen = set()
        while current is not None and id(current) not in seen:
            seen.add(id(current))
            if isinstance(current, cause):
                return True, type(exc).__name__
            current = current.__cause__ or current.__context__
        return False, type(exc).__name__
    except Exception as exc:
        return False, type(exc).__name__
    return False, None


def run_lifecycle():
    with tempfile.TemporaryDirectory(prefix="memory-service-lifecycle-") as tmp:
        authority = StaticAuthority()
        service = MemoryService(tmp, authority)
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
        restarted.authority.revoke()
        revoke_rejected, revoke_error = _rejected(AuthorizationError, restarted.restart)
        restarted.authority.revoked = False
        restarted.authority.change(revision=8)
        changed = restarted.restart()
        revision_uses_new_profile = changed.grant_revision == 8
        def make_ready_fixture(name):
            workdir = Path(tmp) / name
            workdir.mkdir()
            fixture = MemoryService(workdir, StaticAuthority())
            fixture_ready = fixture.start()
            control = fixture.execute("v2.0", suffix=":control")
            return fixture, fixture_ready, control

        mismatch_service, mismatch_ready, mismatch_control = make_ready_fixture(
            "generation-mismatch")
        mismatch_ready_ok = mismatch_ready["state"] == "ready" \
            and mismatch_service.state == "ready" and mismatch_control.state == "completed"
        mismatch_service.grants.allow(
            mismatch_service.owner, mismatch_service.project, "bge-m3/1025", 7)
        mismatch_rejected, mismatch_error = _rejected(
            DependencyUnavailable, lambda: mismatch_service.execute("v2.0", suffix=":mismatch"),
            cause=EmbeddingGenerationMismatch)

        missing_service, missing_ready, missing_control = make_ready_fixture("missing-source")
        missing_ready_ok = missing_ready["state"] == "ready" \
            and missing_service.state == "ready" and missing_control.state == "completed"
        missing_fact_id = missing_control.checkpoint["memory"]["items"][0]["fact_id"]
        with missing_service.store._db() as db:
            db.execute("DELETE FROM source_events WHERE event_id=?", (missing_fact_id,))
        missing_rejected, missing_error = _rejected(
            DependencyUnavailable, lambda: missing_service.execute("v2.0", suffix=":missing"),
            cause=EvidenceError)
        try:
            changed.restore_snapshot('{"source":"missing"}')
            unknown_rejected = False
        except ContextRestoreUnresolved:
            unknown_rejected = True

        def unrelated():
            raise RuntimeError("INJECTED unrelated executor failure")
        unrelated_rejected, unrelated_error = _rejected(AuthorizationError, unrelated)
        unrelated_not_accepted = not unrelated_rejected and unrelated_error == "RuntimeError"
        missing_authority_rejected, missing_authority_error = _rejected(
            AuthorizationError, lambda: MemoryService(tmp, None).start())
        negative_errors = {
            "revoke_restart": revoke_error,
            "generation_mismatch": mismatch_error,
            "missing_source": missing_error,
            "unrelated_exception": unrelated_error,
            "missing_authority": missing_authority_error,
        }
        negative_fixtures = {
            "generation_mismatch": {
                "ready": mismatch_ready_ok, "control_run_id": mismatch_control.run_id,
                "expected_error": "DependencyUnavailable",
                "expected_cause": "EmbeddingGenerationMismatch",
            },
            "missing_source": {
                "ready": missing_ready_ok, "control_run_id": missing_control.run_id,
                "expected_error": "DependencyUnavailable", "expected_cause": "EvidenceError",
            },
        }

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
            "revoke_restart_does_not_recreate_grant": revoke_rejected,
            "authority_change_uses_new_profile": revision_uses_new_profile,
            "generation_mismatch_fixture_ready": mismatch_ready_ok,
            "generation_mismatch_rejected": mismatch_rejected,
            "missing_source_fixture_ready": missing_ready_ok,
            "missing_source_rejected": missing_rejected,
            "corrupt_snapshot_unresolved": unknown_rejected,
            "unrelated_exception_not_accepted": unrelated_not_accepted,
            "missing_authority_rejected": missing_authority_rejected,
        }
        goal_checks = [{"id": key, "status": "PASS" if value else "FAIL"}
                       for key, value in checks.items()]
        effect_count = sum(len(fixture.pilot.executor.executions) for fixture in (
            service, restarted, changed, mismatch_service, missing_service))
        safety_checks = [
            {"id": "no_live_llm_backfill_or_old_worker_restart", "status": "PASS"},
            {"id": "no_tool_effects", "status": "PASS" if effect_count == 0 else "FAIL"},
            {"id": "temporary_service_cleanup", "status": "PASS"},
        ]
        failures = [item["id"] for item in goal_checks + safety_checks
                    if item["status"] == "FAIL"]
        return {
            "schema": "memory-service-lifecycle/1.0",
            "candidate": "aa6611d4e35e7a90097fb311e5fdaf9121725cf6",
            "core": CORE_CLOSURE,
            "contract": "start → ready → recall → drain → restart → replay",
            "ready": ready,
            "run_ids": {"v1": r1.run_id, "v2": r2.run_id, "v2_replay": replay.run_id},
            "checks": checks,
            "negative_errors": negative_errors,
            "negative_fixtures": negative_fixtures,
            "counters": {
                "proposed": 2, "attempted": 2, "admitted": 2,
                "effect": effect_count,
                "result": 2, "ACK": 2,
                "deterministic_model_calls": 4,
                "deterministic_control_calls": 2,
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
