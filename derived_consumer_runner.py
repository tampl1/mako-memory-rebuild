"""Offline derived-envelope G4 profile on CORE 4c31; live remains lease-gated."""

from __future__ import annotations

import copy
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from consumer_runner import run_consumer
from src.derived_envelope import DerivedEnvelopeError, build_derived_envelope
from src.decision_consumer import EXPECTED_MINIMAX_SHA256, load_decision_envelope
from src.source_closure import sha256_file

ROOT = Path(__file__).resolve().parent
CONTROLS = ROOT / "controls" / "g4-derived-consumer-20261102"
BASE = CONTROLS / "minimax-decision-input.json"
DERIVED = CONTROLS / "minimax-derived-input.json"
FROZEN_NOW = datetime(2026, 11, 2, 13, 19, 4, 809000, tzinfo=timezone.utc)


def _derived_controls():
    results = {}
    envelope = load_decision_envelope(DERIVED, base_path=BASE, now=FROZEN_NOW)
    results["derived_envelope_accepted"] = {
        "passed": envelope.sha256 != EXPECTED_MINIMAX_SHA256
                  and envelope.derived_from["sha256"] == EXPECTED_MINIMAX_SHA256,
        "error": None, "effect": 0,
    }
    with tempfile.TemporaryDirectory(prefix="g4-derived-controls-") as tmp:
        tmp = Path(tmp)
        wrong_link = json.loads(DERIVED.read_text())
        wrong_link["derived_from"]["sha256"] = "0" * 64
        wrong_path = tmp / "wrong-link.json"
        wrong_path.write_text(json.dumps(wrong_link))
        try:
            load_decision_envelope(wrong_path, base_path=BASE, now=FROZEN_NOW)
            results["wrong_base_rejected"] = {"passed": False, "error": None, "effect": 0}
        except ValueError as exc:
            results["wrong_base_rejected"] = {"passed": True, "error": type(exc).__name__, "effect": 0}

        in_place = tmp / "in-place-base.json"
        in_place.write_text(BASE.read_text().replace(
            "2026-11-02T10:36:45.266234Z", "2026-11-02T10:46:45.266234Z"))
        try:
            load_decision_envelope(DERIVED, base_path=in_place, now=FROZEN_NOW)
            results["in_place_edit_rejected"] = {"passed": False, "error": None, "effect": 0}
        except ValueError as exc:
            results["in_place_edit_rejected"] = {"passed": True, "error": type(exc).__name__, "effect": 0}

        bad_digest = json.loads(DERIVED.read_text())
        bad_digest["captured_at"] = "2026-11-02T13:20:00.000Z"
        bad_path = tmp / "bad-digest.json"
        bad_path.write_text(json.dumps(bad_digest))
        try:
            load_decision_envelope(bad_path, base_path=BASE, now=FROZEN_NOW)
            results["digest_tamper_rejected"] = {"passed": False, "error": None, "effect": 0}
        except ValueError as exc:
            results["digest_tamper_rejected"] = {"passed": True, "error": type(exc).__name__, "effect": 0}

        try:
            build_derived_envelope(
                base_path=BASE, base_sha256=EXPECTED_MINIMAX_SHA256,
                now=datetime.now(timezone.utc), price_in=0.0, price_out=0.0,
                price_source="local-lan-runtime", deadline_horizon_s=601,
            )
            results["infinite_deadline_rejected"] = {"passed": False, "error": None, "effect": 0}
        except DerivedEnvelopeError as exc:
            results["infinite_deadline_rejected"] = {"passed": True, "error": type(exc).__name__, "effect": 0}

        unknown_now = datetime.now(timezone.utc)
        unknown = build_derived_envelope(
            base_path=BASE, base_sha256=EXPECTED_MINIMAX_SHA256,
            now=unknown_now, price_in=None, price_out=None,
            price_source="producer-unavailable", price_status="unknown",
        )
        unknown_path = tmp / "unknown-price.json"
        unknown_path.write_text(json.dumps(unknown))
        loaded = load_decision_envelope(unknown_path, base_path=BASE, now=unknown_now)
        results["unknown_price_stays_unknown"] = {
            "passed": all(item["price_in"] is None and item["price_status"] == "unknown"
                          for item in loaded.data["view"].values()),
            "error": None, "effect": 0,
        }
    return results


def run_derived():
    report = run_consumer(controls=CONTROLS, now=FROZEN_NOW)
    derived = _derived_controls()
    report["schema"] = "memory-consumer-g4-derived/1.0"
    report["base_sha"] = "563b2a9329390fed64956f80337c99840388b6a6"
    report["entry_point"] = "derived_consumer_runner.py"
    report["decision_input"]["derived_from"] = report["decision_input"].get("derived_from")
    envelope = json.loads(DERIVED.read_text())
    report["decision_input"]["derived_from"] = envelope["derived_from"]
    report["decision_input"]["envelope_sha256"] = envelope["envelope_sha256"]
    report["decision_input"]["core_profile"] = json.loads(
        (CONTROLS / "consumer-frozen-input.json").read_text())["core_profile"]
    report["derived_controls"] = derived
    report["live"] = {
        "status": "UNKNOWN", "reason": "lease_not_issued",
        "additional_model_calls": 0, "worker_restart": 0,
    }
    report["goal"]["checks"].extend([
        {"id": name, "status": "PASS" if item["passed"] else "FAIL"}
        for name, item in derived.items()
    ])
    all_checks = report["goal"]["checks"] + report["safety"]["checks"]
    failures = [item["id"] for item in all_checks if item["status"] == "FAIL"]
    report["verdict"] = {
        "PASS": sum(item["status"] == "PASS" for item in all_checks),
        "FAIL": len(failures), "SKIP": 0,
        "UNKNOWN": 1, "overall": "PASS" if not failures else "FAIL",
    }
    report["limitations"] += "; live memory recall remains UNKNOWN until actual M1M lease"
    return report


def main():
    report = run_derived()
    out = ROOT / "artifacts" / "derived-consumer-g4-hardened-evidence.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "verdict": report["verdict"], "counters": report["counters"],
        "live": report["live"], "evidence": str(out),
        "evidence_sha256": sha256_file(out),
    }, ensure_ascii=False, indent=2))
    return 0 if report["verdict"]["overall"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
