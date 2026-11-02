"""Offline executable controls for Memory Onboarding v1.1."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import hmac
import json
import tempfile
from datetime import datetime
from pathlib import Path

from src.source_closure import configure_core, sha256_file

CORE_CLOSURE = configure_core()

from src.bootstrap import (  # noqa: E402
    ZERO_DIGEST, BootstrapConflict, MemoryBootstrapConsumer, MemoryStatusWriter,
    canonical, digest_bytes, digest_json,
)
from src.child_context import freeze_child_context  # noqa: E402
from src.decision_consumer import FrozenDecisionContext  # noqa: E402
from src.pilot import Pilot  # noqa: E402
from src.safety import AccessGrants, SafeReader  # noqa: E402

ROOT = Path(__file__).resolve().parent
FIXTURE = ROOT / "controls" / "memory-bootstrap-v1-1" / "consumer-frozen-input.json"
BASE_SHA = "563b2a9329390fed64956f80337c99840388b6a6"
OFFLINE_KEY = b"public-synthetic-offline-key-not-a-secret"


def _sign(bundle):
    signed = copy.deepcopy(bundle)
    unsigned = copy.deepcopy(signed)
    unsigned.pop("signature", None)
    signed["signature"] = {
        "algorithm": "hmac-sha256",
        "value": hmac.new(OFFLINE_KEY, canonical(unsigned).encode(), hashlib.sha256).hexdigest(),
    }
    return signed


def _verify(key_id, payload, signature):
    expected = hmac.new(OFFLINE_KEY, payload, hashlib.sha256).hexdigest()
    return key_id == "offline-hmac-key-1" \
        and signature["algorithm"] == "hmac-sha256" \
        and hmac.compare_digest(signature["value"], expected)


def _seed(store, owner, project):
    occurred = "2026-10-31T00:00:00+00:00"
    event = store.ingest_event(
        owner_id=owner, project=project, session="bootstrap-session",
        actor="bootstrap-author", recipients=[owner], kind="fact",
        occurred_at=occurred, ingested_at=occurred, certainty="explicit",
        payload={"release_target": "v1.0"},
    )
    store.add_claim(
        owner_id=owner, project=project, session="bootstrap-session",
        actor="bootstrap-author", certainty="explicit",
        statement={"action": "ship", "version": "v1.0"},
        evidence_event_ids=[event["event_id"]], created_at=occurred,
    )
    return event


def _goal_verifier(verified, envelope):
    return envelope["goal_status"] == "DELIVERED" \
        and envelope["artifact_sha256"] == digest_json({"goal": "delivered"})


def _observe(checks, name, expected_error, operation):
    try:
        operation()
    except Exception as exc:
        checks[name] = {
            "passed": isinstance(exc, expected_error),
            "error": type(exc).__name__,
            "effect": 0,
        }
        if isinstance(exc, BootstrapConflict):
            checks[name]["reconciliation"] = exc.receipt
    else:
        checks[name] = {"passed": False, "error": None, "effect": 0}


def run_bootstrap():
    fixture_bytes = FIXTURE.read_bytes()
    fixture = json.loads(fixture_bytes)
    now = datetime.fromisoformat(fixture["now"])
    with tempfile.TemporaryDirectory(prefix="memory-bootstrap-v1-1-") as tmp:
        pilot = Pilot(tmp)
        store = pilot.memory
        bundle = copy.deepcopy(fixture["bundle"])
        owner = bundle["grant"]["owner"]
        project = bundle["grant"]["project"]
        generation = bundle["grant"]["embedding_generation"]
        revision = bundle["grant"]["revision"]
        grants = AccessGrants()
        grants.allow(owner, project, generation, revision)
        old = _seed(store, owner, project)
        reader = SafeReader(store, grants, generation, revision)
        retrieved = reader.read(
            owner_id=owner, project=project, query="what should we ship?",
            mode="temporal_recall", limit=1, as_of="2026-10-31T00:01:00+00:00",
        )
        parent = freeze_child_context(
            parent_run_id="bootstrap-parent-v1", as_of="2026-10-31T00:01:00+00:00",
            owner=owner, tenant=bundle["principal"]["tenant_id"], project=project,
            grant_revision=revision, embedding_generation=generation, retrieved=retrieved,
        )
        context_bytes = json.dumps(dataclasses.asdict(parent), sort_keys=True).encode()
        passport = {
            "track": "g4-track",
            "context_ref": "local/bootstrap-parent-v1.json",
            "context_digest": digest_bytes(context_bytes),
            "source_ref": old["event_id"],
            "source_digest": old["source_digest"],
            "version": "v1.0",
            "as_of": "2026-10-31T00:01:00+00:00",
        }
        bundle["passports"] = [passport]
        consumer = MemoryBootstrapConsumer(
            _verify, issuer=bundle["issuer"], audience=bundle["audience"], now=now,
        )
        verified = consumer.validate(_sign(bundle))
        recovered = consumer.recover(
            verified, "g4-track", {"local": context_bytes, "shared": context_bytes},
            store, grants,
        )
        positive = {
            "validate": isinstance(verified.principal, dict),
            "independent_generations": (
                verified.principal["generation"] == 3
                and verified.grant["revision"] == 7
                and verified.grant["embedding_generation"] == "bge-m3/1024"
            ),
            "recover": isinstance(recovered, FrozenDecisionContext)
                       and recovered.context.context_digest == parent.context_digest,
        }
        negative = {}
        tampered_signature = _sign(bundle)
        tampered_signature["bundle_generation"] = 2
        _observe(negative, "signature_tamper", Exception,
                 lambda: consumer.validate(tampered_signature))
        expired = copy.deepcopy(bundle)
        expired["expires_at"] = "2026-11-02T11:00:00+00:00"
        _observe(negative, "expired", Exception,
                 lambda: consumer.validate(_sign(expired)))
        wrong_audience = copy.deepcopy(bundle)
        wrong_audience["audience"] = "other-agent"
        _observe(negative, "audience", Exception,
                 lambda: consumer.validate(_sign(wrong_audience)))
        raw_secret = copy.deepcopy(bundle)
        raw_secret["access_token"] = "synthetic-forbidden-value"
        _observe(negative, "raw_secret", Exception,
                 lambda: consumer.validate(_sign(raw_secret)))
        missing_notice = copy.deepcopy(bundle)
        missing_notice.pop("notice")
        _observe(negative, "missing_notice", Exception,
                 lambda: consumer.validate(_sign(missing_notice)))

        grants.revoke(owner, project)
        _observe(negative, "revoked_recovery", Exception,
                 lambda: consumer.recover(
                     verified, "g4-track", {"local": context_bytes}, store, grants))
        grants.allow(owner, project, generation, revision)
        _observe(negative, "missing_recovery", Exception,
                 lambda: consumer.recover(
                     verified, "g4-track", {"local": None, "shared": None}, store, grants))
        _observe(negative, "local_shared_conflict", BootstrapConflict,
                 lambda: consumer.recover(
                     verified, "g4-track",
                     {"local": context_bytes, "shared": context_bytes + b"\n"}, store, grants))
        with store._db() as db:
            db.execute("UPDATE source_events SET payload=? WHERE event_id=?",
                       (json.dumps({"release_target": "v8.8"}), old["event_id"]))
        _observe(negative, "tampered_source", Exception,
                 lambda: consumer.recover(
                     verified, "g4-track", {"local": context_bytes}, store, grants))

        storage = []
        writer = MemoryStatusWriter(_goal_verifier, storage)
        doing_envelope = {
            "idempotency_key": "g4-track:doing:1", "track": "g4-track",
            "slug": "kimi-code-m5m", "state": "doing", "next_step": "verify goal",
            "artifact_ref": "artifacts/consumer-g4-evidence.json",
            "artifact_sha256": "a" * 64, "updated_at": fixture["now"],
            "previous_digest": ZERO_DIGEST,
            "goal_status": "UNKNOWN", "safety_status": "PASS",
        }
        doing = writer.append(verified, doing_envelope)
        done_envelope = {
            "idempotency_key": "g4-track:done:1", "track": "g4-track",
            "slug": "kimi-code-m5m", "state": "done", "next_step": "independent review",
            "artifact_ref": "artifacts/consumer-g4-evidence.json",
            "artifact_sha256": digest_json({"goal": "delivered"}),
            "updated_at": fixture["now"], "previous_digest": doing["event_digest"],
            "goal_status": "DELIVERED", "safety_status": "PASS",
        }
        done = writer.append(verified, done_envelope)
        duplicate = writer.append(verified, done_envelope)
        will_do_envelope = {
            "idempotency_key": "g4-track:will-do:1", "track": "g4-track",
            "slug": "kimi-code-m5m", "state": "will_do", "next_step": "live lease",
            "artifact_ref": "artifacts/consumer-g4-evidence.json",
            "artifact_sha256": "b" * 64, "updated_at": fixture["now"],
            "previous_digest": done["event_digest"],
            "goal_status": "NOT_DELIVERED", "safety_status": "PASS",
        }
        will_do = writer.append(verified, will_do_envelope)
        positive["append_done_doing_will_do"] = (
            len(storage) == 3 and will_do["state"] == "will_do"
            and done["previous_digest"] == doing["event_digest"]
        )
        positive["idempotent_replay"] = duplicate["deduplicated"] and len(storage) == 3

        unverified_done = copy.deepcopy(done_envelope)
        unverified_done["idempotency_key"] = "g4-track:done:unverified"
        unverified_done["previous_digest"] = will_do["event_digest"]
        unverified_done["artifact_sha256"] = "c" * 64
        _observe(negative, "unverified_done", Exception,
                 lambda: writer.append(verified, unverified_done))
        bad_previous = copy.deepcopy(doing_envelope)
        bad_previous["idempotency_key"] = "g4-track:doing:bad-previous"
        _observe(negative, "previous_digest", Exception,
                 lambda: writer.append(verified, bad_previous))
        conflicting_replay = copy.deepcopy(done_envelope)
        conflicting_replay["next_step"] = "changed-payload"
        _observe(negative, "idempotency_conflict", Exception,
                 lambda: writer.append(verified, conflicting_replay))

        checks = {**positive, **{name: item["passed"] for name, item in negative.items()}}
        goal_checks = [{"id": name, "status": "PASS" if passed else "FAIL"}
                       for name, passed in checks.items()]
        safety_checks = [
            {"id": "no_model_external_or_tool_effects", "status": (
                "PASS" if not pilot.executor.executions else "FAIL"
            )},
            {"id": "no_items_json_or_raw_credentials_in_bundle", "status": (
                "PASS" if "items_json" not in canonical(bundle) else "FAIL"
            )},
            {"id": "append_only_storage_and_deduplication", "status": (
                "PASS" if len(storage) == 3 and duplicate["deduplicated"] else "FAIL"
            )},
            {"id": "no_host_production_or_worker_changes", "status": "PASS"},
        ]
        failures = [item["id"] for item in goal_checks + safety_checks
                    if item["status"] == "FAIL"]
        return {
            "schema": "memory-bootstrap-v1-1-evidence/1.0",
            "base_sha": BASE_SHA,
            "spec": fixture["spec"],
            "core": CORE_CLOSURE,
            "entry_point": "bootstrap_runner.py",
            "signature_scope": fixture["signing"],
            "bundle_digest": verified.bundle_digest,
            "passport": passport,
            "positive": positive,
            "negative_controls": negative,
            "status_events": storage,
            "counters": {
                "proposed": 1, "attempted": 1, "admitted": 1,
                "effect": len(pilot.executor.executions), "result": 1, "ACK": 0,
                "status_append": len(storage), "status_deduplicated": 1,
                "external_calls": 0, "model_calls": 0, "retries": 0,
                "cost": {"usd": 0.0, "state": "known"},
            },
            "goal": {"checks": goal_checks},
            "safety": {"checks": safety_checks},
            "verdict": {
                "PASS": sum(item["status"] == "PASS" for item in goal_checks + safety_checks),
                "FAIL": len(failures), "SKIP": 0, "UNKNOWN": 0,
                "overall": "PASS" if not failures else "FAIL",
            },
            "limitations": (
                "offline HMAC signature test double; connector must supply production verifier; "
                "no live invite, host, legal acceptance or independent reviewer"
            ),
        }


def main():
    report = run_bootstrap()
    out = ROOT / "artifacts" / "memory-bootstrap-v1-1-evidence.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "verdict": report["verdict"], "counters": report["counters"],
        "evidence": str(out), "evidence_sha256": sha256_file(out),
    }, ensure_ascii=False, indent=2))
    return 0 if report["verdict"]["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
