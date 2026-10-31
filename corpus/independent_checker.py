"""Standalone checker: no imports from the corpus builder or synthetic model."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_CATEGORIES = {"format", "tool", "infra", "reasoning", "authorization", "unknown_effect"}
SPLITS = {"train", "validation", "test"}


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    sample_path = ROOT / "corpus" / "reviewed-sample.json"
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    checks = {}

    def require(name, condition):
        checks[name] = bool(condition)
        if not condition:
            raise AssertionError(name)

    records = sample["records"]
    require("exactly_12_records", len(records) == 12 == sample["record_count"])
    require("unique_ids", len({r["id"] for r in records}) == 12)
    require("unique_scenario_families", len({r["metadata"]["scenario"] for r in records}) == 12)
    require("all_categories", {r["metadata"]["category"] for r in records} == REQUIRED_CATEGORIES)
    require("source_commit", sample["base_memory_commit"] == "650b97a60ef18099b5297a92f8048e0695544bf6")
    require("synthetic_only", sample["synthetic"] is True)

    for record in records:
        rid = record["id"]
        meta = record["metadata"]
        require(rid + "_correction", record["output"] == record["accept"])
        require(rid + "_counterexample", record["counterexample"] != record["output"])
        require(rid + "_attempt_preserved", bool(record["input"]["unsuccessful_attempt"]))
        require(rid + "_category", meta["category"] in REQUIRED_CATEGORIES)
        require(rid + "_split", meta["split"] in SPLITS)
        require(rid + "_seed", isinstance(meta["seed"], int))
        require(rid + "_rights", "synthetic" in meta["rights"]
                and "no personal data" in meta["source"] and "no Hub" in meta["source"])
        require(rid + "_versions", bool(meta["model_version"] and meta["tool_versions"]["CORE"]))
        require(rid + "_review_gate", meta["review_status"] == "candidate_until_independent_checker")
        require(rid + "_source_refs", len(meta["source_refs"]) >= 1)
        for source in meta["source_refs"]:
            filename, anchor = source["ref"].split("#", 1)
            path = ROOT / filename
            require(rid + "_source_sha", path.is_file() and sha(path) == source["sha256"])
            evidence = json.loads(path.read_text(encoding="utf-8"))
            node = evidence
            for part in anchor.strip("/").split("/"):
                node = node[int(part)] if isinstance(node, list) else node[part]
            require(rid + "_receipt_present", node is not None)
        output = record["output"]
        bad = record["counterexample"]
        require(rid + "_no_raw_success", output["status"] == "corrected")
        require(rid + "_negative_effect_distinct", output.get("effect") != bad.get("effect")
                or output.get("action") != bad.get("action")
                or output.get("value") != bad.get("value"))
        if meta["category"] in {"authorization", "unknown_effect", "infra"}:
            require(rid + "_zero_forbidden_effect", output["effect"] == 0)
        if record["id"] == "unknown-001":
            require(rid + "_unknown_not_free", output["value"]["cost"] == "unknown")
        if record["id"] == "infra-001":
            require(rid + "_restart_counters", output["value"]["restart_model_calls"] == 0
                    and output["value"]["restart_memory_recalls"] == 0)

    manifest_path = ROOT / "training" / "lora-9b-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require("manifest_data_sha", manifest["data"]["sha256"] == sha(sample_path))
    require("manifest_train_count", manifest["data"]["train_records"] == 8)
    require("training_blocked", manifest["status"] == "blocked_training_not_started")
    require("no_unpinned_base", manifest["base_model"]["trainable_hf_revision"] is None)
    require("runtime_missing", manifest["runtime"]["training_runtime"] == "missing")
    require("sixty_minute_cap", manifest["limits"]["wall_seconds_max"] == 3600)
    require("no_paid_or_hub", manifest["limits"]["paid_calls"] == 0 and not manifest["limits"]["hub_publish"])

    report = {
        "checker": "independent deterministic implementation; not an independent reviewer",
        "sample_sha256": sha(sample_path),
        "records_checked": len(records),
        "verified_corrections": len(records) if all(checks.values()) else 0,
        "counterexamples_checked": len(records),
        "checks": checks,
        "all_checks_passed": all(checks.values()),
        "sft_eligibility": "train split only; validation/test excluded; reviewer approval still required",
        "training": "SKIP: missing runtime and no free GPU lease",
        "forbidden_actions": {"training_runs": 0, "gpu_processes_started": 0, "paid_calls": 0,
                              "private_backfill": 0, "hub_publications": 0, "production_model_changes": 0},
    }
    out = ROOT / "artifacts" / "corpus-review-evidence.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("records_checked", "verified_corrections", "all_checks_passed", "training")}))
    return 0 if report["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
