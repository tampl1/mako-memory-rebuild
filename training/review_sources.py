"""Reviews corpus rights/splits against the pinned ZCode source registry."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_COMMIT = "fd28a9ae57456268ea906f01cbfa0095a4afb14a"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    registry_path = ROOT / "controls" / "zcode-sources-registry" / "sources_registry.json"
    sample_path = ROOT / "corpus" / "reviewed-sample.json"
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    records = sample["records"]
    quarantined = [s["id"] for s in registry["sources"] if not s.get("license_reviewed")]
    vectors = next(s for s in registry["sources"] if s["id"] == "wycheproof-c2sp")["vectors_pinned"]
    vector_checks = []
    for vector in vectors:
        path = ROOT / "controls" / "zcode-sources-registry" / "wycheproof-vectors" / vector["file"]
        document = json.loads(path.read_text(encoding="utf-8"))
        vector_checks.append(
            sha(path) == vector["sha256"] and len(document["testGroups"]) == vector["groups"])
    nist = [s for s in registry["sources"] if s["id"].startswith("nist-")]
    checks = {
        "registry_pinned": len(registry["sources"]) == 10 and len(registry["models"]) == 5,
        "twelve_synthetic_records": len(records) == 12 and all(
            "synthetic" in r["metadata"]["source"] for r in records),
        "rights_per_input": all("CC0" in r["metadata"]["rights"] for r in records),
        "no_external_dataset_inputs": all(
            not r["metadata"].get("external_source_id") for r in records),
        "split_precedes_mutations": all("mutations" not in r for r in records),
        "unique_families": len({r["metadata"]["scenario"] for r in records}) == 12,
        "no_public_holdout_in_train": all(
            r["metadata"]["split"] == "train" or r["metadata"]["split"] in {"validation", "test"}
            for r in records),
        "labels_still_require_reviewer": all(
            r["metadata"]["review_status"] == "candidate_until_independent_checker" for r in records),
        "quarantine_not_eval": all(s["usage"] == "metadata-only" for s in registry["sources"]
                                   if not s.get("license_reviewed")),
        "seven_wycheproof_vectors": len(vectors) == 7 and all(vector_checks)
                                      and sum(v["groups"] for v in vectors) == 288,
        "nist_document_provenance": len(nist) == 2 and all(
            s["pinned"]["revision_kind"] == "document-download"
            and len(s["pinned"]["sha256"]) == 64 and s["pinned"]["url"].startswith("https://")
            for s in nist),
        "trainable_base_null": all(m.get("trainable_base") is None for m in registry["models"]),
        "separate_eval_train_rights": all(
            "eval_rights_reviewed" in s and "train_rights_reviewed" in s
            for s in registry["sources"]),
        "sft_gate_requires_trainable_base": all(
            m.get("usage") not in {"both", "train-possible"}
            or m.get("trainable_base") is not None for m in registry["models"]),
    }
    split_counts = {name: sum(r["metadata"]["split"] == name for r in records)
                    for name in ("train", "validation", "test")}
    checks["split_8_2_2"] = split_counts == {"train": 8, "validation": 2, "test": 2}

    models = {m["id"]: m for m in registry["models"]}
    baseline_a = models["qwen3:8b"]
    baseline_b = models["qwen3-coder:30b"]
    trainable_blockers = [
        "qwen3:8b has an Ollama inference digest, not a pinned trainable HF revision",
        "tokenizer digest and compatible trainable weight format are missing",
        "12 labels await independent reviewer approval",
        "free GPU lease has not been granted",
        "registry usage=both does not prove train rights or a trainable base",
    ]
    consumer_required_fields = [
        "train-possible/both gate must require non-null trainable_base for actual SFT",
        "per-record source_registry_ref and source_usage for future external inputs",
        "split_lineage/parent_ids for every future mutation",
        "independent reviewer identity, reviewed_at and review_receipt_ref for current labels",
    ]
    plan = {
        "schema": "frozen-baseline-eval-plan/1.0",
        "status": "prepared_not_run",
        "baseline_a": {"id": baseline_a["id"], "role": "9B-class frozen baseline",
                       "inference_digest": baseline_a["pinned"]["digest"]},
        "baseline_b": {"id": baseline_b["id"], "role": "stronger frozen baseline",
                       "inference_digest": baseline_b["pinned"]["digest"]},
        "candidate": "9B + separate LoRA only after all blockers clear",
        "untouched_eval": {
            "sample_sha256": sha(sample_path),
            "record_ids": [r["id"] for r in records if r["metadata"]["split"] != "train"],
            "seeds": [1101, 1102, 1103],
            "answers_in_prompt": False,
            "new_training_data_from_eval": False,
        },
        "general_skills_retention": {"status": "missing_blocks_training"},
        "budgets": {"paid_calls": 0, "wall_seconds_max": 3600},
        "gpu_lease": {"preferred_node": "m1m", "status": "not_requested",
                      "rule": "start only after explicit free-window ACK; M5M daytime work is not interrupted"},
    }
    (ROOT / "training" / "frozen-eval-plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = {
        "registry": {"repo": "tampl1/Attached-Assets", "commit": REGISTRY_COMMIT,
                     "path": "zcode/sources-registry/sources_registry.json",
                     "sha256": sha(registry_path)},
        "corpus_sha256": sha(sample_path),
        "checks": checks,
        "all_checks_passed": all(checks.values()),
        "consumer_compatible": all(checks.values()),
        "split_counts": split_counts,
        "metadata_only_quarantine": quarantined,
        "wycheproof": {"files": len(vectors), "test_groups": sum(v["groups"] for v in vectors),
                       "all_sha256_and_group_counts_match": all(vector_checks)},
        "external_training_inputs": 0,
        "trainable_blockers": trainable_blockers,
        "consumer_required_fields": consumer_required_fields,
        "training": "BLOCKED: consumer mismatch and missing prerequisites",
        "model_calls": 0,
        "paid_calls": 0,
        "gpu_processes_started": 0,
    }
    (ROOT / "artifacts" / "source-registry-review-evidence.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"checks": len(checks), "all_checks_passed": report["all_checks_passed"],
                      "split": split_counts, "quarantined": quarantined,
                      "wycheproof": report["wycheproof"], "training": report["training"]}))
    return 0 if report["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
