import hashlib
import json
import unittest
from pathlib import Path


class BootstrapManifestTests(unittest.TestCase):
    def test_exact_source_closure(self):
        root = Path(__file__).resolve().parent.parent
        manifest = json.loads((root / "integration" / "memory-bootstrap-v1-1-manifest.json").read_text())
        for name, expected in manifest["own_modules"].items():
            actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
            self.assertEqual(actual, expected, name)
        self.assertEqual(manifest["spec"]["commit"], "0e6f19bd06835b6e83275f8f1469f2583fa6ba07")
        self.assertEqual(len(manifest["entry_points"]), 3)
        self.assertIn("not implemented", manifest["signature"]["production"])
        self.assertNotIn("fallback", manifest["command"].lower())


if __name__ == "__main__":
    unittest.main()
