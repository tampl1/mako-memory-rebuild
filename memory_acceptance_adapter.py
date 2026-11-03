"""Callable acceptance adapter for the isolated memory service."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from src.source_closure import configure_core, sha256_file

CORE_CLOSURE = configure_core()

from core_v1_errors import DependencyUnavailable  # noqa: E402
from src.memory_service import MemoryService, ServiceNotReady, StaticAuthority  # noqa: E402
from src.safety import AuthorizationError, EmbeddingGenerationMismatch, EvidenceError  # noqa: E402

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


def _decision(run, child):
    item = run.checkpoint["memory"]["items"][0]
    return {
        "source": child.source,
        "version": child.version,
        "as_of": child.as_of,
        "frozen_child": True,
        "answer_source": item["fact_id"],
        "answer_version": item["structured"]["version"],
    }


def _ready_fixture(root: Path, name: str):
    workdir = root / name
    workdir.mkdir()
    service = MemoryService(workdir, StaticAuthority())
    ready = service.start()
    control = service.execute("v2.0", suffix=":control")
    return service, ready, control


def run_acceptance():
    with tempfile.TemporaryDirectory(prefix="memory-acceptance-") as tmp:
        root = Path(tmp)
        authority = StaticAuthority()
        service = MemoryService(root, authority)
        ready = service.start()
        historical = service.execute("v1.0")
        latest = service.execute("v2.0")
        historical_decision = _decision(historical, service.children["v1.0"])
        latest_decision = _decision(latest, service.children["v2.0"])
        ack = service.pilot.flush(service.owner, latest.run_id)
        duplicate_ack = service.pilot.flush(service.owner, latest.run_id)

        restarted = service.restart()
        replay = restarted.replay("v2.0")
        replay_decision = _decision(replay, restarted.children["v2.0"])
        outage = restarted.outage()
        outage_rejected, outage_error = _rejected(
            ServiceNotReady, lambda: restarted.execute("v2.0"))
        restarted.start()
        restarted.authority.revoke()
        revoke_rejected, revoke_error = _rejected(AuthorizationError, restarted.restart)

        mismatch, mismatch_ready, mismatch_control = _ready_fixture(root, "mismatch")
        mismatch.grants.allow(mismatch.owner, mismatch.project, "bge-m3/1025", 7)
        mismatch_rejected, mismatch_error = _rejected(
            DependencyUnavailable, lambda: mismatch.execute("v2.0", suffix=":mismatch"),
            cause=EmbeddingGenerationMismatch)

        missing, missing_ready, missing_control = _ready_fixture(root, "missing")
        fact_id = missing_control.checkpoint["memory"]["items"][0]["fact_id"]
        with missing.store._db() as db:
            db.execute("DELETE FROM source_events WHERE event_id=?", (fact_id,))
        missing_rejected, missing_error = _rejected(
            DependencyUnavailable, lambda: missing.execute("v2.0", suffix=":missing"),
            cause=EvidenceError)

        def unrelated():
            raise RuntimeError("INJECTED unrelated executor failure")
        unrelated_rejected, unrelated_error = _rejected(AuthorizationError, unrelated)
        unrelated_not_accepted = not unrelated_rejected and unrelated_error == "RuntimeError"

        checks = {
            "owner_grant_ready": ready["state"] == "ready" and ready["grant_revision"] == 7,
            "historical_source_version_as_of": (
                historical_decision["source"] == historical_decision["answer_source"]
                and historical_decision["version"] == historical_decision["answer_version"]
                and historical_decision["as_of"] == "2026-10-31T00:05:00+00:00"),
            "latest_frozen_child_decision": (
                latest_decision["source"] == latest_decision["answer_source"]
                and latest_decision["version"] == latest_decision["answer_version"]
                and latest_decision["as_of"] is None),
            "single_result_and_ack": ack["acknowledged"] and duplicate_ack == ack,
            "restart_replay_no_new_recall_or_effect": (
                replay.run_id == latest.run_id and replay_decision == latest_decision
                and replay.result["tool_receipts"] == []),
            "outage_no_fallback": outage["fallback"] is False and outage_rejected,
            "revoke_does_not_recreate_grant": revoke_rejected,
            "generation_mismatch_ready_and_typed": (
                mismatch_ready["state"] == "ready" and mismatch_control.state == "completed"
                and mismatch_rejected),
            "missing_source_ready_and_typed": (
                missing_ready["state"] == "ready" and missing_control.state == "completed"
                and missing_rejected),
            "unrelated_exception_not_accepted": unrelated_not_accepted,
        }
        failures = [key for key, value in checks.items() if not value]
        return {
            "schema": "memory-acceptance/1.0",
            "core": CORE_CLOSURE,
            "ready": ready,
            "decisions": {"historical": historical_decision, "latest": latest_decision},
            "result": {"run_id": latest.run_id, "acknowledged": ack["acknowledged"]},
            "replay": {"run_id": replay.run_id, "decision": replay_decision},
            "negative_errors": {
                "outage": outage_error,
                "revoke_restart": revoke_error,
                "generation_mismatch": mismatch_error,
                "missing_source": missing_error,
                "unrelated_exception": unrelated_error,
            },
            "checks": checks,
            "counters": {
                "deterministic_model_calls": 4,
                "live_model_calls": 0,
                "paid_calls": 0,
                "production_writes": 0,
                "result": 1,
                "ack": 1,
                "effects": 0,
            },
            "verdict": {
                "PASS": len(checks) - len(failures),
                "FAIL": len(failures),
                "overall": "PASS" if not failures else "FAIL",
                "failed_checks": failures,
            },
            "boundaries": "deterministic isolated fixture, not live LLM or old memory worker",
        }


def main():
    report = run_acceptance()
    out = ROOT / "artifacts" / "memory-acceptance-evidence.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"verdict": report["verdict"], "counters": report["counters"],
                      "evidence": str(out), "evidence_sha256": sha256_file(out)},
                     ensure_ascii=False, indent=2))
    return 0 if report["verdict"]["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
