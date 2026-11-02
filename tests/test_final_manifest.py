import hashlib
import json
import unittest
from pathlib import Path


class FinalManifestTests(unittest.TestCase):
    def test_final_source_closure_and_lease_gate(self):
        root = Path(__file__).resolve().parent.parent
        manifest = json.loads((root / "integration" / "final-g4-manifest.json").read_text())
        for name, expected in manifest["own_modules"].items():
            actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
            self.assertEqual(actual, expected, name)
        self.assertEqual(manifest["policy"]["principal_generation"], 1)
        self.assertEqual(manifest["policy"]["grant_revision"], 7)
        self.assertEqual(manifest["producer_profile"]["binding_generation"], 2)
        self.assertEqual(manifest["producer_profile"]["endpoint"], "http://10.1.0.75:11434/v1")
        self.assertEqual(manifest["live"]["state"], "ARMED_not_active")
        self.assertEqual(manifest["live"]["previous_spend"], 51)
        self.assertEqual(manifest["live"]["max_new_calls"], 8)


if __name__ == "__main__":
    unittest.main()
