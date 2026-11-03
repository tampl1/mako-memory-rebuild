import hashlib
import json
import unittest
from pathlib import Path


class MemoryServiceManifestTests(unittest.TestCase):
    def test_lifecycle_manifest(self):
        root = Path(__file__).resolve().parent.parent
        manifest = json.loads((root / "integration" / "memory-service-lifecycle-manifest.json").read_text())
        for name, expected in manifest["own_modules"].items():
            actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
            self.assertEqual(actual, expected, name)
        self.assertEqual(manifest["verdict"]["fail"], 0)
        self.assertEqual(len(manifest["superseded_failures"]["failed_controls"]), 3)
        self.assertEqual(manifest["counters"]["live_model_calls"], 0)


if __name__ == "__main__":
    unittest.main()
