import unittest

from derived_consumer_runner import run_derived


class DerivedConsumerTests(unittest.TestCase):
    def test_derived_profile_and_controls(self):
        report = run_derived()
        self.assertEqual(report["core"]["commit"], "4c31e0e0a21a6328e8a8e4e2ebca653e18120f3d")
        self.assertEqual(report["verdict"]["overall"], "PASS")
        self.assertEqual(report["verdict"]["FAIL"], 0)
        self.assertEqual(report["verdict"]["UNKNOWN"], 1)
        self.assertEqual(report["live"]["status"], "UNKNOWN")
        self.assertEqual(report["live"]["additional_model_calls"], 0)
        self.assertEqual(report["counters"]["external_calls"], 0)
        self.assertEqual(report["model_budget"]["previously_spent"], 47)
        self.assertEqual(report["model_budget"]["remaining"], 17)
        self.assertEqual(len(report["derived_controls"]), 6)
        for name, observed in report["derived_controls"].items():
            self.assertTrue(observed["passed"], name)
            self.assertEqual(observed["effect"], 0, name)


if __name__ == "__main__":
    unittest.main()
