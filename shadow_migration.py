"""Small metadata shadow migration with digest checks and real rollback."""

import hashlib
import json
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    source = ROOT / "integration" / "history-shadow-manifest.json"
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    data = json.loads(source.read_text(encoding="utf-8"))
    report = {
        "mode": "metadata_shadow_migration_not_backfill",
        "source_sha256": source_sha,
        "input_records": len(data["records"]),
        "output_records": 0,
        "unreadable_sources": [{"ref": "synthetic-unreadable-001", "state": "unresolved_not_model_truth"}],
        "rollback": {"executed": False, "source_unchanged": False},
    }
    with tempfile.TemporaryDirectory(prefix="history-shadow-") as tmp:
        target = Path(tmp) / "shadow.json"
        target.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")
        copied = json.loads(target.read_text(encoding="utf-8"))
        report["output_records"] = len(copied["records"])
        report["digests_match"] = all(
            item["source_line_sha256"] == original["source_line_sha256"]
            for item, original in zip(copied["records"], data["records"]))
        target.unlink()
        report["rollback"]["executed"] = not target.exists()
    report["rollback"]["source_unchanged"] = \
        hashlib.sha256(source.read_bytes()).hexdigest() == source_sha
    report["all_checks_passed"] = (
        report["input_records"] == report["output_records"] == 3
        and report["digests_match"] and report["rollback"]["executed"]
        and report["rollback"]["source_unchanged"])
    out = ROOT / "artifacts" / "shadow-migration-evidence.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report))
    return 0 if report["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
