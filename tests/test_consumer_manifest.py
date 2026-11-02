import hashlib
import json
import unittest
from pathlib import Path


class ConsumerManifestTests(unittest.TestCase):
    def test_source_closure_and_frozen_input_hashes(self):
        root = Path(__file__).resolve().parent.parent
        manifest = json.loads((root / "integration" / "consumer-g4-manifest.json").read_text())
        for name, expected in manifest["own_modules"].items():
            actual = hashlib.sha256((root / name).read_bytes()).hexdigest()
            self.assertEqual(actual, expected, name)
        self.assertEqual(
            set(manifest["pinned_context_fields"]),
            {"source", "version", "as_of", "owner", "tenant", "project",
             "grant_revision", "embedding_generation", "context_digest"},
        )
        self.assertEqual(manifest["model_budget"]["remaining"], 17)
        self.assertNotIn("fallback", manifest["command"].lower())


if __name__ == "__main__":
    unittest.main()
