"""Replays saved live receipts without imports or calls to model transports."""

import hashlib
import json
from pathlib import Path

EXPECTED_SHA = "893e25c2b3a2f38ac2ddb155eb4555e9d1e66729176c642312b26b8abb635a2c"
ROOT = Path(__file__).resolve().parent


def main():
    path = ROOT / "artifacts" / "live-retrieval-evidence.json"
    actual_sha = hashlib.sha256(path.read_bytes()).hexdigest()
    evidence = json.loads(path.read_text(encoding="utf-8"))
    checks = {"evidence_sha": actual_sha == EXPECTED_SHA}
    replay = {}
    for name, comparison in evidence["comparison"].items():
        replay[name] = {}
        for arm, saved in comparison["arms"].items():
            ids = saved["result_ids"]
            scores = saved["scores"]
            ordered = sorted(
                zip(ids, scores),
                key=(lambda row: (-row[1]["similarity"], row[0])) if arm == "raw"
                else (lambda row: (-row[1]["rerank"], -row[1]["similarity"], row[0])),
            )
            checks[f"{name}_{arm}_unique_bounded"] = len(ids) == len(set(ids)) and len(ids) <= 2
            checks[f"{name}_{arm}_ranking"] = [row[0] for row in ordered] == ids
            checks[f"{name}_{arm}_source"] = bool(ids) and ids[0] == comparison["expected_source"]
            replay[name][arm] = {"result_ids": ids, "top1": ids[0] if ids else None}
    runs = evidence["core_runs"]
    checks["R1_source"] = runs["R1"]["result"]["tool_receipt"]["fact_ref"] != \
                          runs["R2"]["result"]["tool_receipt"]["fact_ref"]
    checks["restart_no_effect"] = runs["R3_restart"]["tool_executions_total"] == \
                                  runs["R3_crash"]["tool_executions_total"]
    checks["restart_no_calls"] = runs["R3_restart"]["model_calls"] == 0 and \
                                 runs["R3_restart"]["memory_recalls"] == 0
    report = {
        "mode": "offline_replay_of_saved_receipts_not_live",
        "source_evidence_sha256": actual_sha,
        "replay": replay,
        "checks": checks,
        "all_checks_passed": all(checks.values()),
        "model_calls": 0,
        "embedding_calls": 0,
        "rerank_calls": 0,
        "limitations": "raw generation strings and embedding vectors were not stored; parsed receipts are replayed",
    }
    out = ROOT / "artifacts" / "offline-replay-evidence.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"checks": len(checks), "all_checks_passed": report["all_checks_passed"],
                      "model_calls": 0}))
    return 0 if report["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
