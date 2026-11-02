import unittest

from bootstrap_runner import run_bootstrap


class BootstrapTests(unittest.TestCase):
    def test_validate_recover_and_append_controls(self):
        report = run_bootstrap()
        self.assertEqual(report["verdict"]["overall"], "PASS")
        self.assertEqual(report["verdict"]["FAIL"], 0)
        self.assertEqual(report["counters"]["effect"], 0)
        self.assertEqual(report["counters"]["model_calls"], 0)
        self.assertEqual(report["counters"]["status_append"], 3)
        self.assertEqual(report["counters"]["status_deduplicated"], 1)
        self.assertTrue(report["positive"]["independent_generations"])
        self.assertTrue(report["positive"]["recover"])
        self.assertIn("reconciliation", report["negative_controls"]["local_shared_conflict"])
        expected_errors = {
            "signature_tamper": "BootstrapSignatureError",
            "expired": "BootstrapExpired",
            "audience": "BootstrapScopeError",
            "raw_secret": "BootstrapScopeError",
            "missing_notice": "BootstrapError",
            "revoked_recovery": "ContextRestoreUnresolved",
            "missing_recovery": "ContextRestoreUnresolved",
            "local_shared_conflict": "BootstrapConflict",
            "tampered_source": "ContextRestoreUnresolved",
            "unverified_done": "StatusValidationError",
            "previous_digest": "StatusValidationError",
            "idempotency_conflict": "StatusValidationError",
        }
        self.assertEqual(set(report["negative_controls"]), set(expected_errors))
        for name, observed in report["negative_controls"].items():
            self.assertTrue(observed["passed"], name)
            self.assertEqual(observed["error"], expected_errors[name], name)
            self.assertEqual(observed["effect"], 0, name)


if __name__ == "__main__":
    unittest.main()
