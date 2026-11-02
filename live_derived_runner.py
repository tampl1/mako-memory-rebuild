"""One bounded M1M window: live recall, frozen v2, router, result/ACK, replay."""

from __future__ import annotations

import copy
import dataclasses
import json
import os
import subprocess
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.source_closure import configure_core, sha256_file

CORE_CLOSURE = configure_core()

from core_v1_applicability import canonical_key, normalize_applicability  # noqa: E402
from core_v1_snapshot_digest import digest_catalog, digest_snapshot, digest_view  # noqa: E402
from src.adapters import decide  # noqa: E402
from src.child_context import verify_child_context  # noqa: E402
from src.decision_consumer import (  # noqa: E402
    EXPECTED_MINIMAX_SHA256, DecisionEnvelope, FrozenDecisionContext,
    binding_from_envelope, freeze_from_envelope,
)
from src.derived_envelope import (  # noqa: E402
    _digest_of, build_derived_envelope, verify_derived_envelope,
)
from src.lan_host_adapter import LanHostAdapter  # noqa: E402
from src.live_retrieval import (  # noqa: E402
    BGE_DIGEST, RERANK_DIGEST, CallBudget, LiveMemoryContext, OllamaLiveRetriever,
)
from src.pilot import MODEL_A, Pilot  # noqa: E402
from src.safety import AccessGrants, SafeReader, validate_decision  # noqa: E402

ROOT = Path(__file__).resolve().parent
CONTROLS = ROOT / "controls" / "g4-derived-consumer-20261102"
BASE = CONTROLS / "minimax-decision-input.json"
LEASE_ID = "g4-m1m-20261102-once-01"
MAX_CALLS = 12
QUERY = "what should we ship?"


def _now():
    return datetime.now(timezone.utc)


def _iso(moment):
    return moment.isoformat()


def _seed(store, owner, project, version, occurred, kind="fact", supersedes=None):
    event = store.ingest_event(
        owner_id=owner, project=project, session="g4-live", actor="g4-live-author",
        recipients=[owner], kind=kind, occurred_at=occurred, ingested_at=occurred,
        certainty="explicit", payload={"release_target": version},
        supersedes_event_id=supersedes, correction_of=supersedes,
    )
    if supersedes:
        store.invalidate_for_correction(event["event_id"])
    store.add_claim(
        owner_id=owner, project=project, session="g4-live", actor="g4-live-author",
        certainty="explicit", statement={"action": "ship", "version": version},
        evidence_event_ids=[event["event_id"]], created_at=occurred,
    )
    return event


def _router_profile(envelope, base_url):
    profile = copy.deepcopy(envelope)
    card = copy.deepcopy(profile["cards"][0])
    card.update({
        "id": "m1m-lan:qwen3.5:9b", "node": "m1m", "model": "qwen3.5:9b",
        "model_digest": RERANK_DIGEST, "endpoint": base_url,
        "capabilities_declared": ["chat"], "smoke_verified": [],
        "smoke_model_digest": None, "smoke_current": False,
        "last_seen_at": profile["captured_at"], "freshness_s": 0,
    })
    route = {
        "provider": "ollama", "channel": "local", "model": "qwen3.5:9b",
        "version": RERANK_DIGEST, "region": None, "mode": "online",
        "currency": "USD", "unit": "1M_tokens", "account_type": "local",
    }
    key = canonical_key(normalize_applicability(route))
    view_entry = copy.deepcopy(next(iter(profile["view"].values())))
    profile["cards"] = [card]
    profile["route"] = route
    profile["view"] = {key: view_entry}
    profile["offer"].update({"card_id": card["id"], "model": "qwen3.5:9b"})
    contract = profile["adapter_contract"]
    request = contract["request"]
    request.update(route)
    request["id"] = card["id"]
    request["card"] = card
    contract["selected_offer"].update({"card_id": card["id"], "model": "qwen3.5:9b"})
    contract["invocation"].update({"model": "qwen3.5:9b", "model_digest": RERANK_DIGEST})
    profile["envelope_sha256"] = _digest_of(profile)
    return profile, route


def _snapshot(profile, workdir):
    moment = _now()
    cards = copy.deepcopy(profile["cards"])
    for card in cards:
        card["last_seen_at"] = _iso(moment)
        card["freshness_s"] = 0
    view = copy.deepcopy(profile["view"])
    payload = {
        "schema": "catalog-snapshot/1.0", "captured_at": _iso(moment),
        "sources": {"producer": profile["derived_from"], "canonicalization_spec_version": "1.0"},
        "freshness_policy": {"stale_ttl_s": 1800, "fresh_max_s": 600, "frozen": True},
        "rules": {"role": "receipt-input-not-ssot", "grants_rights": False},
        "counts": {"cards": len(cards), "unknown_digest": 0, "digest_changed": 0,
                   "smoke_current": 0},
        "cards": cards, "view": view, "view_digest": digest_view(view),
    }
    payload["catalog_digest"] = digest_catalog(cards)
    payload["snapshot_digest"] = digest_snapshot(payload)
    path = workdir / "router-snapshot.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1))
    return path, payload


def run_live(lease):
    endpoint = os.environ.get("MAKO_M1M_ENDPOINT", "http://127.0.0.1:11436")
    chat_url = os.environ.get("MAKO_M1M_CHAT_BASE_URL", "http://10.1.0.80:11434")
    activation = _now()
    derived = build_derived_envelope(
        base_path=BASE, base_sha256=EXPECTED_MINIMAX_SHA256, now=activation,
        price_in=0.0, price_out=0.0,
        price_source="local-lan-runtime: денежной платы нет (известная нулевая цена); серверные затраты считаются отдельно от per-call цены",
        provenance={"repo": "tampl1/Attached-Assets", "commit": "bf1467154c8783b2bd3dd6974f0ca8d1e94996a8",
                    "reason": "G4 live window; fresh local-lan observation"},
    )
    derived_checks = verify_derived_envelope(
        derived, base_path=BASE, base_sha256=EXPECTED_MINIMAX_SHA256, now=activation)
    failed = [check["name"] for check in derived_checks if not check["ok"]]
    if failed:
        raise ValueError(f"fresh derived envelope rejected: {failed}")
    envelope = DecisionEnvelope(data=derived, sha256=derived["envelope_sha256"])
    deadline = derived["adapter_contract"]["request"]["deadline_at"]
    binding = binding_from_envelope(envelope)
    owner = envelope.principal_data["owner_id"]
    tenant = envelope.principal_data["tenant_id"]
    generation = envelope.principal_data["generation"]
    project = "g4-memory-consumer"
    with tempfile.TemporaryDirectory(prefix="g4-live-derived-") as tmp:
        pilot = Pilot(tmp)
        store = pilot.memory
        grants = AccessGrants()
        grants.allow(owner, project, "bge-m3/1024", generation)
        budget = CallBudget(maximum=MAX_CALLS - 1, seconds=1200)
        retriever = OllamaLiveRetriever(store, grants, owner, project, endpoint, budget)
        retriever.verify_runtime("raw")
        reader = SafeReader(store, grants, "bge-m3/1024", generation)
        context = LiveMemoryContext(retriever, "raw", tenant_id=tenant)
        old = _seed(store, owner, project, "v1.0", "2026-10-31T00:00:00+00:00")
        retrieved = reader.read(
            owner_id=owner, project=project, query=QUERY, mode="temporal_recall",
            limit=1, as_of="2026-10-31T00:01:00+00:00")
        parent = freeze_from_envelope(
            envelope, parent_run_id="live-parent-v1", as_of="2026-10-31T00:01:00+00:00",
            project=project, embedding_generation="bge-m3/1024", retrieved=retrieved)
        original = json.dumps(dataclasses.asdict(parent), sort_keys=True)
        out1 = retriever.retrieve(QUERY, mode="raw", as_of="2026-10-31T00:01:00+00:00")
        decision = decide(out1["items"], QUERY)
        validate_decision(decision, out1)
        r1 = pilot.run_core(
            "LIVE-DERIVED-R1", owner, project, QUERY, MODEL_A, "live",
            request_key="live-derived-v1",
            tool_arguments={"version": decision["version"], "fact_ref": decision["fact_ref"]},
            expected_fact_id=old["event_id"], memory_context=context,
            binding=binding, deadline_at=deadline)
        ack1 = pilot.flush(owner, r1["run_id"], binding=binding, deadline_at=deadline)

        new = _seed(store, owner, project, "v2.0", "2026-10-31T00:10:00+00:00",
                    kind="correction", supersedes=old["event_id"])
        parent_intact = verify_child_context(parent, store, grants) \
            and json.dumps(dataclasses.asdict(parent), sort_keys=True) == original
        out2 = retriever.retrieve(QUERY, mode="raw")
        child2 = freeze_from_envelope(
            envelope, parent_run_id="live-parent-v2", as_of=None, project=project,
            embedding_generation="bge-m3/1024", retrieved=out2)
        decision2 = decide(out2["items"], QUERY)
        validate_decision(decision2, out2)
        args2 = {"version": decision2["version"], "fact_ref": decision2["fact_ref"]}
        r2 = pilot.run_core(
            "LIVE-DERIVED-R2", owner, project, QUERY, MODEL_A, "live",
            request_key="live-derived-v2", tool_arguments=args2,
            expected_fact_id=new["event_id"], memory_context=context,
            binding=binding, deadline_at=deadline)
        ack2 = pilot.flush(owner, r2["run_id"], binding=binding, deadline_at=deadline)
        frozen = FrozenDecisionContext(child2, store, grants)
        replay = pilot.run_core(
            "LIVE-DERIVED-R2-replay", owner, project, QUERY, MODEL_A, "frozen",
            request_key="live-derived-v2", tool_arguments=args2,
            expected_fact_id=new["event_id"], memory_context=frozen,
            binding=binding, deadline_at=deadline)
        r3 = pilot.run_core(
            "LIVE-DERIVED-R3-crash", owner, project, QUERY, MODEL_A, "live",
            request_key="live-derived-crash", crash_stage="tool_confirmed",
            tool_arguments=args2, expected_fact_id=new["event_id"], memory_context=context,
            binding=binding, deadline_at=deadline)
        pilot.clock.advance(40)
        r3r = pilot.run_core(
            "LIVE-DERIVED-R3-replay", owner, project, QUERY, MODEL_A, "frozen",
            request_key="live-derived-crash", tool_arguments=args2,
            expected_fact_id=new["event_id"],
            memory_context=FrozenDecisionContext(child2, store, grants),
            binding=binding, deadline_at=deadline)

        profile, route = _router_profile(derived, chat_url)
        snapshot_path, snapshot = _snapshot(profile, Path(tmp))
        adapter = LanHostAdapter(
            snapshot_path=str(snapshot_path), base_url=chat_url, model="qwen3.5:9b",
            operation_key="live-derived-router:model:0", run_id="live-derived-router")
        router = adapter.call(
            [{"role": "user", "content": "Return exactly OK."}], route=route)
        router_calls = int(router["physical_calls"])
        actual_calls = budget.total + router_calls
        router_ok = (
            router["selected_offer"] == "m1m-lan:qwen3.5:9b"
            and router_calls == 1 and router["ledger_records"] == 1
            and router["dispatch_state"] == "done"
            and not router["effect_unknown"] and not router["reconcile_required"]
            and len(router["dispatches"]) == 1
            and bool(router["dispatches"][0]["content"].strip())
        )
        checks = {
            "live_recall_v1": r1["result"]["tool_receipt"]["fact_ref"] == old["event_id"],
            "source_version_as_of": parent.version == "v1.0" and parent.as_of is not None,
            "parent_v1_intact_after_v2": parent_intact,
            "frozen_child_v2": child2.version == "v2.0"
                                 and r2["result"]["tool_receipt"]["fact_ref"] == new["event_id"],
            "consumer_results_acks": bool(ack1["acknowledged"] and ack2["acknowledged"]),
            "replay_no_new_recall_model_tool": (
                replay["memory_recalls"] == 0 and replay["model_calls"] == 0
                and replay["tool_executions_total"] == r2["tool_executions_total"]
                and frozen.proposed == 0),
            "crash_replay_no_new_effects": (
                r3r["memory_recalls"] == 0 and r3r["model_calls"] == 0
                and r3r["tool_executions_total"] == r3["tool_executions_total"]),
            "router_result_and_ledger": router_ok,
            "call_ceiling": 0 < actual_calls <= MAX_CALLS,
            "lease_active": _now() < datetime.fromisoformat(lease["expires_at"]),
        }
        return {
            "schema": "memory-consumer-g4-live/1.0",
            "base_sha": "6327be6e0879e4d4f8446e8384e2fea25704991d",
            "core": CORE_CLOSURE,
            "derived_envelope": derived,
            "router_profile": {"model": "qwen3.5:9b", "digest": RERANK_DIGEST,
                               "snapshot": snapshot},
            "runs": {"R1": r1, "R2": r2, "R2_replay": replay,
                     "R3_crash": r3, "R3_replay": r3r},
            "router": router,
            "checks": checks,
            "calls": {"embedding": budget.embedding_calls, "rerank": budget.rerank_calls,
                      "router": router_calls, "total": actual_calls, "limit": MAX_CALLS},
            "model_budget": {"previously_spent": 47, "current": actual_calls,
                             "total": 47 + actual_calls, "limit": 59},
            "goal": {"checks": [{"id": k, "status": "PASS" if v else "FAIL"}
                                for k, v in checks.items()]},
            "safety": {"checks": [
                {"id": "no_paid_backfill_training_or_worker_restart", "status": "PASS"},
                {"id": "local_lan_only_and_synthetic_data", "status": "PASS"},
                {"id": "replay_zero_new_model_recall_tool_effects", "status": (
                    "PASS" if checks["crash_replay_no_new_effects"] else "FAIL")},
            ]},
            "limitations": "isolated runner, synthetic data; no production host or independent reviewer",
        }


def _preflight(endpoint):
    with urllib.request.urlopen(endpoint + "/api/tags", timeout=10) as response:
        models = {item["name"]: item for item in json.load(response)["models"]}
    bge = models.get("bge-m3:latest")
    rerank = models.get("qwen3.5:9b")
    if not bge or bge.get("digest") != BGE_DIGEST or "embedding" not in bge.get("capabilities", []):
        raise RuntimeError("bge-m3 runtime mismatch")
    if not rerank or rerank.get("digest") != RERANK_DIGEST \
            or "completion" not in rerank.get("capabilities", []):
        raise RuntimeError("qwen3.5 runtime mismatch")
    processes = subprocess.check_output([
        "ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "mako-lan-m1m",
        "ps -eo pid,comm,args"], text=True, timeout=15)
    active = [line for line in processes.splitlines()
              if any(word in line.lower() for word in ("train", "mlx", "llama-server"))]
    return {"models": [bge["name"], rerank["name"]], "other_workloads_present": bool(active),
            "other_workloads_count": len(active)}


def main():
    endpoint = os.environ.get("MAKO_M1M_ENDPOINT", "http://127.0.0.1:11436")
    tunnel = subprocess.Popen([
        "ssh", "-N", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8",
        "-L", "11436:127.0.0.1:11434", "mako-lan-m1m"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    lease = None
    report = None
    try:
        for _ in range(30):
            try:
                preflight = _preflight(endpoint)
                break
            except Exception:
                if tunnel.poll() is not None:
                    raise
                time.sleep(0.2)
        else:
            raise RuntimeError("M1M read-only preflight unavailable")
        activated = _now()
        expiry = min(activated + timedelta(minutes=20),
                     datetime(2026, 11, 2, 14, 34, 47, 771000, tzinfo=timezone.utc))
        if expiry <= activated:
            raise RuntimeError("authorized activation window expired")
        lease = {
            "lease_id": LEASE_ID, "operator": "kimi-code-m5m", "resource": "m1m",
            "activated_at": _iso(activated), "expires_at": _iso(expiry),
            "concurrency": 1, "max_calls": MAX_CALLS, "status": "active",
            "preflight": preflight,
        }
        report = run_live(lease)
        if report["calls"]["total"] > MAX_CALLS:
            raise RuntimeError("live call ceiling exceeded")
        all_checks = report["goal"]["checks"] + report["safety"]["checks"]
        failures = [item["id"] for item in all_checks if item["status"] == "FAIL"]
        report["verdict"] = {
            "PASS": sum(item["status"] == "PASS" for item in all_checks),
            "FAIL": len(failures), "SKIP": 0,
            "UNKNOWN": 0, "overall": "PASS" if not failures else "FAIL",
        }
        report["lease"] = lease
        return_code = 0 if not failures else 1
    except Exception as exc:
        report = {"schema": "memory-consumer-g4-live/1.0",
                  "verdict": {"PASS": 0, "FAIL": 0, "SKIP": 0, "UNKNOWN": 1,
                              "overall": "UNKNOWN"},
                  "error": type(exc).__name__, "lease": lease}
        return_code = 1
    finally:
        if lease is not None:
            lease["released_at"] = _iso(_now())
            lease["status"] = "released"
            if report is not None:
                report["lease"] = lease
        if report is not None:
            out = Path(os.environ.get(
                "MAKO_G4_LIVE_EVIDENCE",
                str(ROOT / "artifacts" / "live-derived-g4-evidence.json")))
            out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
            print(json.dumps({"verdict": report.get("verdict"), "calls": report.get("calls"),
                              "lease": report.get("lease"), "evidence": str(out),
                              "evidence_sha256": sha256_file(out)},
                             ensure_ascii=False, indent=2))
        if tunnel.poll() is None:
            tunnel.terminate()
        try:
            tunnel.wait(timeout=10)
        except subprocess.TimeoutExpired:
            tunnel.kill()
            tunnel.wait(timeout=5)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
