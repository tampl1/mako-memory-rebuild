"""Builds the factual kimi-code-m5m training inventory; read-only sources only."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "training" / "fleet-training-register.json"
OBSERVED = "2026-10-31T22:10:09+03:00"
FIELDS = [
    "track", "objective", "responsible_slug", "platform", "project_alias",
    "provider_job_id", "observed_at", "source_receipt", "state", "training_kind",
    "base_model", "immutable_revision", "tokenizer_digest", "base_license",
    "dataset_ref", "digest", "license_review", "split_lineage", "leakage_checks",
    "train_runtime", "device_or_provider", "current_resource_lease", "started_at",
    "last_checkpoint_at", "steps_or_epochs", "last_progress_at",
    "checkpoint_or_adapter_ref", "artifact_digest", "resumability", "baseline_eval",
    "heldout_eval", "retention_eval", "independent_reviewer", "known_cost", "currency",
    "unknown_cost_components", "blocker", "next_action", "action_owner",
]


def record(**values):
    item = {field: None for field in FIELDS}
    item.update(values)
    item["observed_at"] = OBSERVED
    return item


records = [
    record(
        track="nano-pretrain-0.63b", objective="Pretrain MAKO-nano from scratch",
        responsible_slug="zcode-m5m", platform="Vast.ai", project_alias="MAKO-nano",
        provider_job_id="vast-instance-57812704",
        source_receipt=["Attached-Assets/zcode/training-inventory-zcode-m5m-20261031.json",
                        "mako-local/pretrain_sentry.log: checkpoint saved step 30000 at 2026-10-31T20:46+03"],
        state="running", training_kind="other_weights", base_model="random-init-0.63B",
        base_license="n/a (from scratch)", dataset_ref="own corpus; digest not pinned",
        license_review="own data; formal review not pinned",
        train_runtime="Vast 4xRTX4090", device_or_provider="Vast.ai",
        current_resource_lease="running; $2.015/hour; training progress is 30000/30000",
        started_at="2026-10-26", last_checkpoint_at="2026-10-31T20:46:00+03:00",
        steps_or_epochs="30000/30000", last_progress_at="2026-10-31T20:46:00+03:00",
        resumability="checkpoint exists; clean finalization and artifact digest unknown",
        heldout_eval="loss 1.864 at 24450 (2026-10-29); final eval unknown",
        independent_reviewer=None, known_cost=255, currency="USD",
        unknown_cost_components=["storage", "time after step 30000 until stop"],
        blocker="provider instance continues billing; final artifact/eval receipt missing",
        next_action="owner/replit decides final readback and bounded stop; no automatic stop by this inventory",
        action_owner="owner + replit + zcode-m5m"),
    record(
        track="twin-sft-lora", objective="Safety/useful-refusal LoRA for Qwen3-8B",
        responsible_slug="kimi-code-m5m", platform="M5M MLX", project_alias="Двойник safety-SFT",
        provider_job_id=None, source_receipt=["Documents/MAKO-Docs/ДВОЙНИК-SAFETY-SFT-ПРОТОКОЛ-2026-10-27.md",
                                            "mako-local/twin-sft/sft_v5.log",
                                            "adapter-v4/adapters.safetensors sha256 61e340b1dbfc428f84fc5313d2b042de1285485a97ae15e9a245f7ca57c13097"],
        state="completed", training_kind="LoRA", base_model="Qwen3-8B MLX",
        immutable_revision="local /Users/Andreyled/Models/qwen3-8b-mlx; config sha256 14cbdde4e7726d8a6b5ea422540c298328c43c304140c627232b70fef6776b75",
        tokenizer_digest="sha256:be75606093db2094d7cd20f3c2f385c212750648bd6ea4fb2bf507a6a4c55506",
        base_license="Apache-2.0", dataset_ref="safety-train-v4.jsonl; sources lovable/mini-max/kimi",
        digest="sha256:e4a8599cb18c34029a2670ab26bf202633d086131dba7e762a65aab173a18a42",
        license_review="sources acknowledged; per-record license review not fully pinned",
        split_lineage="60 pairs v1; 88 pairs v2; holdout 10; later v4 lineage incomplete",
        leakage_checks="small holdout and manual flag review; no comprehensive leakage proof",
        train_runtime="MLX LoRA 16 layers rank8 batch2 lr5e-6", device_or_provider="M5M local GPU",
        current_resource_lease="none; latest run completed 2026-10-28",
        started_at="2026-10-27", last_checkpoint_at="2026-10-28T00:40:49+03:00",
        steps_or_epochs="v4 log reaches 200 and V4-COMPLETE; 350 was configured",
        last_progress_at="2026-10-28T00:40:51+03:00",
        checkpoint_or_adapter_ref="adapter-v1..v4; latest adapter-v4/adapters.safetensors",
        artifact_digest="sha256:61e340b1dbfc428f84fc5313d2b042de1285485a97ae15e9a245f7ca57c13097",
        resumability="checkpoints 100/200 exist; current driver stops on artifact existence, not clean resume",
        baseline_eval="v1 baseline 9/10; v1 corrected manual holdout 10/10",
        heldout_eval="v2 attacks 50/52, clean 16/24; v4 heldout unknown",
        retention_eval="not measured; small holdout only", independent_reviewer="manual author review, not independent",
        known_cost=0, currency="RUB", unknown_cost_components=["electricity", "future evaluation"],
        blocker="v4 independent quality/retention and complete lineage missing",
        next_action="independent reviewer evaluates v4 artifact before any production role",
        action_owner="replit + independent reviewer"),
    record(
        track="guard-detector-small", objective="Synthetic trigger classifier from scratch",
        responsible_slug="zcode-m5m", platform="M5M MLX", project_alias="MAKO-NANO Страж",
        provider_job_id=None, source_receipt="Attached-Assets/zcode/training-inventory-zcode-m5m-20261031.json + guard-train7.log",
        state="completed", training_kind="other_weights", base_model="random-init small classifier",
        base_license="n/a (from scratch)", dataset_ref="own synthetic; digest not pinned",
        license_review="own synthetic", split_lineage="train/holdout separated",
        leakage_checks="holdout acc 0.9994; formal leakage check not documented",
        train_runtime="M5M MLX", device_or_provider="M5M", current_resource_lease="none",
        started_at="2026-10-26", last_checkpoint_at="2026-10-27T00:53:00+03:00",
        steps_or_epochs="30 epochs", last_progress_at="2026-10-27T00:53:00+03:00",
        checkpoint_or_adapter_ref="guard-v1.pt", artifact_digest="sha256:af490492ea17ddb1... (prefix only in source)",
        resumability="plan and generator exist", heldout_eval="holdout acc 0.9994",
        retention_eval=None, independent_reviewer=None, known_cost=0, currency="RUB",
        unknown_cost_components=[], blocker="guard-v2 provenance/acceptance unknown; full artifact digest not pinned",
        next_action="verify v2 or mark experimental; no automatic production integration", action_owner="zcode-m5m"),
    record(
        track="zvuk-text-finetune-artifact", objective="ZVUK text model artifact",
        responsible_slug="zcode-m5m", platform="Ollama@M5M", project_alias="zvuk-text-finetune",
        provider_job_id=None, source_receipt="Ollama digest 89d98783e470a945079d65a3b8ea2045e034f5b713ad3c2a3393af68e3318329",
        state="unknown", training_kind="other_weights", base_model="unknown",
        immutable_revision=None, tokenizer_digest=None, base_license=None,
        dataset_ref=None, digest=None, license_review=None, split_lineage=None,
        leakage_checks=None, train_runtime="unknown", device_or_provider="unknown",
        current_resource_lease="inference only", checkpoint_or_adapter_ref="Ollama artifact",
        artifact_digest="sha256:89d98783e470a945079d65a3b8ea2045e034f5b713ad3c2a3393af68e3318329",
        resumability="no source training receipt", baseline_eval=None,
        heldout_eval="present in router; no baseline comparison", retention_eval=None,
        independent_reviewer=None, known_cost=None, currency="USD",
        unknown_cost_components=["original training"], blocker="no trainable base or training receipt; not asserted as trained weights",
        next_action="recover provenance or keep untracked inference artifact", action_owner="zcode-m5m + owner"),
    record(
        track="security-specialists-lora", objective="Security specialists SFT after reviewed corpus",
        responsible_slug="kimi-code-m5m", platform="planned M1M", project_alias="EntityHome specialists",
        provider_job_id=None, source_receipt="mako-memory-rebuild training/lora-9b-manifest.json + registry v1.3 fd28a9ae",
        state="planned", training_kind="LoRA", base_model="qwen3:8b inference candidate",
        immutable_revision=None, tokenizer_digest=None, base_license="Apache-2.0",
        dataset_ref="corpus/reviewed-sample.json", digest="sha256:98ef2b14df4e993cda63d3e756660f5c38c573e00fd2b67c1fcfb0752f51edad",
        license_review="synthetic CC0; independent reviewer pending", split_lineage="8 train/2 validation/2 test before mutations",
        leakage_checks="counterexamples and source SHA; no external holdout", train_runtime="missing",
        device_or_provider="M1M preferred after free lease", current_resource_lease="not requested",
        resumability="not started", baseline_eval="frozen plan only", heldout_eval="not run",
        retention_eval="missing", independent_reviewer=None, known_cost=0, currency="RUB",
        unknown_cost_components=["future training/eval time"], blocker="trainable base, reviewer, retention set and free lease missing",
        next_action="complete review and base/tokenizer before requesting a 3600s pilot", action_owner="replit + kimi-code-m5m"),
    record(
        track="memory-live-retrieval", objective="Evaluate live embedding/rerank memory adapter",
        responsible_slug="kimi-code-m5m", platform="M1M Ollama", project_alias="memory pilot",
        provider_job_id=None, source_receipt="mako-memory-rebuild issue #6; evidence sha256 893e25c2",
        state="completed", training_kind="evaluation_only", base_model="bge-m3 + qwen3.5:9b inference",
        immutable_revision="bge 7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab; qwen 6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7",
        tokenizer_digest=None, base_license="upstream model licenses unchanged",
        dataset_ref="small synthetic dialogs", digest="pinned in repository artifacts", license_review="synthetic",
        split_lineage="fixed questions before live run", leakage_checks="expected sources not in rerank prompt",
        train_runtime="no training", device_or_provider="M1M", current_resource_lease="closed SSH tunnel",
        started_at="2026-10-31", last_checkpoint_at=None, steps_or_epochs="47 calls including diagnostics",
        last_progress_at="2026-10-31", checkpoint_or_adapter_ref="live-retrieval-evidence.json",
        artifact_digest="sha256:893e25c2b3a2f38ac2ddb155eb4555e9d1e66729176c642312b26b8abb635a2c",
        resumability="offline replay only", baseline_eval="raw arm", heldout_eval="raw=rerank on 3 questions",
        retention_eval="not applicable", independent_reviewer="clean clone only", known_cost=0, currency="RUB",
        unknown_cost_components=[], blocker="small corpus; no dedicated cross-encoder",
        next_action="independent replay; not a training job", action_owner="replit"),
    record(
        track="accountant-academy-exams", objective="Agent academy examinations",
        responsible_slug="zcode-m5m", platform="M5M external inference", project_alias="accountant academy",
        provider_job_id=None, source_receipt="Attached-Assets/zcode/training-inventory-zcode-m5m-20261031.json",
        state="running", training_kind="evaluation_only", base_model="DeepSeek API",
        immutable_revision=None, tokenizer_digest=None, base_license="external API",
        dataset_ref="exam sets in git", digest=None, license_review="n/a", split_lineage="n/a",
        leakage_checks="exam threshold fixed 2026-10-31", train_runtime="no local training",
        device_or_provider="external inference", current_resource_lease="on demand",
        started_at="2026-10-23", last_progress_at="2026-10-31", resumability="n/a",
        heldout_eval="100-question exams; best 98/100 on 2026-10-26", retention_eval=None,
        independent_reviewer=None, known_cost=None, currency="USD",
        unknown_cost_components=["external inference tokens"], blocker="cost and provider revision unknown",
        next_action="continue exams; not counted as weights training", action_owner="zcode-m5m"),
]

report = {
    "schema": "fleet-training-register/1.0",
    "status": "awaiting_inventory",
    "reported_by": "kimi-code-m5m",
    "observed_at": OBSERVED,
    "inventory_note": "Partial factual inventory, not zero training and not complete coverage. Read-only; no jobs started/stopped/modified.",
    "required_fields": FIELDS,
    "records": records,
    "non_training_observations": [
        "voicestudio uvicorn PID 72031 is an inference service, not a training job",
        "bge-m3 Ollama is an embedding inference artifact, not a trained adapter",
        "M5M CPU/GPU process presence alone is not proof of training",
    ],
    "missing_platforms": [
        {"platform": "RunPod", "state": "unknown", "reason": "no direct source in this inventory"},
        {"platform": "Hugging Face Spaces", "state": "unknown", "reason": "no direct source in this inventory"},
        {"platform": "RegCloud/Selectel", "state": "unknown", "reason": "no direct source in this inventory"},
        {"platform": "Windows/provider jobs", "state": "unknown", "reason": "reports assigned to their owners"},
        {"platform": "other owner-reported platforms", "state": "unknown", "reason": "awaiting source receipts"},
    ],
    "quality_gates": {
        "completed_provider_job_is_not_specialist": True,
        "required_before_production": ["artifact hash", "independent eval receipt", "retention eval", "provenance and rights"],
        "stale_no_progress": "record cause/duration/owner; never invent restart",
    },
    "next_consolidation": "replit merges ZCode provider inventory and other agents' receipts without overwriting disagreements",
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(json.dumps({"records": len(records), "status": report["status"], "output": str(OUT)}))
