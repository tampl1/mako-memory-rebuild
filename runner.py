"""Pilot evidence runner: executes scenario + comparison in a temp dir and
prints a single JSON object. Leaves no processes or database files behind."""

from __future__ import annotations

import hashlib
import json
import platform
import resource
import sys
import tempfile
import time
from pathlib import Path

from src.adapters import CORE_DIR
from src.pilot import Pilot


def core_digests():
    out = {}
    for name in ("core_v1_runtime.py", "core_v1_store.py", "core_v1_contract.py"):
        data = (CORE_DIR / name).read_bytes()
        out[name] = hashlib.sha256(data).hexdigest()
    return out


def main():
    started = time.perf_counter()
    environment = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "core_dir": str(CORE_DIR),
        "core_git_head": "1cbe2e7c0d258f02c19105c15f7d540e89398a93",
        "core_remote": "https://github.com/tampl1/Attached-Assets.git",
        "core_file_digests": core_digests(),
        "stdlib_only": True,
        "data": "synthetic multi-agent dialogs only, no personal data",
    }
    limitations = [
        "synthetic model/tool/policy/transport/verifier adapters are not a live LLM; "
        "the CORE runtime, SQLiteRunStore and exactly-once recovery are the real 0.2.6 code",
        "raw_history is a deterministic raw control without a live reranker or embeddings",
        "single-machine SQLite; no concurrency stress, no network, no paid calls",
        "RAM figure is ru_maxrss of this process (platform units), not a controlled measurement",
    ]
    with tempfile.TemporaryDirectory(prefix="mako-memory-pilot-") as tmp:
        pilot = Pilot(tmp)
        expected = {
            "pilot": {
                "R1_action": {"version": "v1.2", "fact_ref": "seed event E1"},
                "R2_action_after_change": {"version": "v1.3",
                                           "fact_ref": "correction event E2 (!= E1)"},
                "restart": {"crashed": True, "restart_state": "completed",
                            "tool_executions_delta": 0,
                            "model_calls_after_restart": 0,
                            "memory_recalls_after_restart": 0},
                "call_counters": {
                    "total": {"model_calls": 4, "memory_recalls": 4},
                    "restart_only": {"model_calls": 0, "memory_recalls": 0},
                },
                "model_swap": {"data_digest_unchanged": True,
                               "receipts_differ": True},
            },
            "comparison": {
                "none": {"correct": False},
                "raw_history": {"correct": True},
                "temporal_recall": {"correct": True},
            },
        }
        pilot_evidence = pilot.run_pilot()
        comparison = pilot.run_comparison(tmp)
        pilot_runs = pilot_evidence["runs"]
        run_names = ("R1", "R2", "R3", "R3_restart", "R4")
        total_counters = {
            "model_calls": sum(pilot_runs[name]["model_calls"] for name in run_names),
            "memory_recalls": sum(pilot_runs[name]["memory_recalls"] for name in run_names),
        }
        restart_counters = {
            "model_calls": pilot_evidence["restart"]["restart_model_calls"],
            "memory_recalls": pilot_evidence["restart"]["restart_memory_recalls"],
        }
        actual = {
            "pilot": {
                "R1": {"state": pilot_runs["R1"]["state"],
                       "tool_receipt": pilot_runs["R1"]["result"]["tool_receipt"]},
                "R2": {"state": pilot_runs["R2"]["state"],
                       "tool_receipt": pilot_runs["R2"]["result"]["tool_receipt"]},
                "invalidated_claims": next(
                    p["invalidated"] for p in pilot_evidence["phases"]
                    if p["phase"] == "correct"),
                "as_of": next(p for p in pilot_evidence["phases"]
                              if p["phase"] == "as_of"),
                "restart": pilot_evidence["restart"],
                "call_counters": {"total": total_counters,
                                  "restart_only": restart_counters},
                "model_swap": pilot_evidence["model_swap"],
                "receipt_fact_links": pilot_evidence["receipt_fact_links"],
            },
            "comparison": {
                mode: {"correct": c["correct"], "version": c["version"],
                       "fact_ref": c["fact_ref"],
                       "memory_receipt": c["memory_receipt"]}
                for mode, c in comparison["conditions"].items()
            },
        }
        receipts = {
            "R1_model": pilot_runs["R1"]["result"]["model_receipt"],
            "R2_model": pilot_runs["R2"]["result"]["model_receipt"],
            "R4_model": pilot_runs["R4"]["result"]["model_receipt"],
            "R1_tool": pilot_runs["R1"]["result"]["tool_receipt"],
            "R2_tool": pilot_runs["R2"]["result"]["tool_receipt"],
            "R3_restart_tool": pilot_runs["R3_restart"]["result"]["tool_receipt"],
        }
        checks = {
            "R1_completed": actual["pilot"]["R1"]["state"] == "completed",
            "R2_completed": actual["pilot"]["R2"]["state"] == "completed",
            "receipt_links_new_fact":
                actual["pilot"]["receipt_fact_links"]["R2_tool_receipt_fact_ref"]
                == actual["pilot"]["receipt_fact_links"]["new_fact_event"],
            "invalidation_ran": len(actual["pilot"]["invalidated_claims"]) >= 2,
            "restart_exactly_once":
                actual["pilot"]["restart"]["executions_after_restart"]
                == actual["pilot"]["restart"]["executions_after_crash"],
            "total_counters_match":
                total_counters == expected["pilot"]["call_counters"]["total"],
            "restart_only_counters_match":
                restart_counters == expected["pilot"]["call_counters"]["restart_only"],
            "model_swap_data_unchanged":
                actual["pilot"]["model_swap"]["data_digest_unchanged"],
            "raw_baseline_not_strawman":
                comparison["conditions"]["raw_history"]["correct"],
            "no_memory_control_wrong":
                not comparison["conditions"]["none"]["correct"],
        }
    duration = round(time.perf_counter() - started, 3)
    report = {
        "environment": environment,
        "expected": expected,
        "actual": actual,
        "receipts": receipts,
        "checks": checks,
        "all_checks_passed": all(checks.values()),
        "limitations": limitations,
        "duration_seconds": duration,
        "ram_ru_maxrss": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "cleanup": "temp dir removed on exit; no background processes started",
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["all_checks_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
