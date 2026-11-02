import hashlib
import json
import unittest
from pathlib import Path


class DerivedManifestTests(unittest.TestCase):
    def test_derived_source_closure_and_base(self):
        root = Path(__file__).resolve().parent.parent
        manifest = json.loads((root / "integration" / "derived-consumer-g4-manifest.json").read_text())
        for name, expected in manifest["own_modules"].items():
            actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
            self.assertEqual(actual, expected, name)
        self.assertEqual(manifest["core"]["source_closure_mismatches"], 0)
        self.assertEqual(manifest["core"]["source_closure_manifest_files"], 139)
        self.assertEqual(manifest["live"]["status"], "UNKNOWN")
        self.assertFalse(manifest["model_runtime_readback"]["gpu_lease_issued"])
        self.assertTrue(manifest["model_runtime_readback"]["other_workloads_present"])


if __name__ == "__main__":
    unittest.main()
