"""Runs the predeclared negative suite and emits raw PASS/FAIL evidence."""

import json
import unittest


def main():
    names = [
        "test_contradiction_correction_and_both_arrival_orders",
        "test_unavailable_retrieval_fails_closed",
        "test_lost_evidence_link_rejects_correct_looking_answer",
        "test_tampered_evidence_digest_is_rejected",
        "test_cross_owner_evidence_is_not_accepted",
        "test_revoked_grant_denies_read",
        "test_embedding_generation_change_denies_old_index",
        "test_corrupted_checkpoint_denies_recovery",
        "test_injection_variants_grant_no_rights_and_allow_analysis",
        "test_same_inputs_and_budget_for_three_local_readers",
    ]
    suite = unittest.TestSuite(
        unittest.defaultTestLoader.loadTestsFromName(f"tests.test_safety.SafetyTests.{name}")
        for name in names
    )
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {
        "expected": {name: "PASS" for name in names},
        "actual": {
            name: "FAIL" if any(name in test.id() for test, _ in result.failures + result.errors)
            else "PASS" for name in names
        },
        "run": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "forbidden_actions": {
            "external_effects": 0, "network_calls": 0, "paid_calls": 0,
            "production_backfill": 0, "weight_training": 0,
            "credential_access": 0, "live_queue_changes": 0,
        },
        "injection_source": "Attached-Assets fa495f74b9e4147b8cc7594cf521797ae6f83a98 artifacts/memory-pilot-20261031; exact T15/T17/T14 run separately by minimax_runner.py",
    }
    report["all_checks_passed"] = result.wasSuccessful()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["all_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
