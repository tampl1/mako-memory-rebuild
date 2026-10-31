"""Тесты валидатора реестра источников (поручение CORE 31.10 п.6).

Проверяются гейты replit на синтетических записях + самозачёт реестра:
настоящий sources_registry.json обязан проходить свой же валидатор.
"""
import json
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from manifest_check import check_registry

REGISTRY = Path(__file__).parent / "sources_registry.json"

GOOD_SOURCE = {
    "id": "x", "kind": "dataset", "url": "https://example/x",
    "pinned": {"at": "2026-10-31",
               "revision": "a" * 40, "revision_kind": "git-commit",
               "license_spdx": "MIT"},
    "label_source": "paired", "upstream_code_rights": "MIT + per-source review",
    "coverage": "c", "usage": "train-possible", "license_reviewed": True,
    "eval_rights_reviewed": True, "train_rights_reviewed": True,
    "duplicates_policy": "project/chronological split",
    "execution_rule": "no-remote-code",
}


def source(**kw):
    s = json.loads(json.dumps(GOOD_SOURCE))
    s.update(kw)
    return s


class SourceGateTests(unittest.TestCase):
    def _errs(self, **kw):
        return check_registry_dict({"schema": "sources-registry/1.0",
                                    "sources": [source(**kw)], "models": []})

    def test_good_source_passes(self):
        self.assertEqual(self._errs(), [])

    def test_train_requires_license_reviewed(self):
        errs = self._errs(license_reviewed=False)
        self.assertTrue(any("license_reviewed" in e for e in errs))

    def test_train_requires_upstream_rights(self):
        errs = self._errs(upstream_code_rights="unknown")
        self.assertTrue(any("upstream_code_rights" in e for e in errs))

    def test_dangerous_sections_forbid_train(self):
        errs = self._errs(dangerous_sections=True)
        self.assertTrue(any("dangerous_sections" in e for e in errs))

    # replit 31.10: unknown license НЕ разрешает evaluation
    def test_eval_requires_license_reviewed(self):
        errs = self._errs(usage="eval-only", license_reviewed=False,
                          eval_rights_reviewed=False)
        self.assertTrue(any("metadata-only/quarantine" in e for e in errs))

    def test_metadata_only_always_allowed_without_review(self):
        errs = self._errs(usage="metadata-only", license_reviewed=False,
                          upstream_code_rights="unknown")
        self.assertEqual([e for e in errs if "usage" in e], [])

    def test_document_download_needs_url_version_sha256(self):
        errs = self._errs(pinned={"at": "2026-10-31",
                                  "revision_kind": "document-download",
                                  "sha256": "a" * 64,
                                  "license_spdx": "LicenseRef-X"})
        self.assertTrue(any("document-download требует" in e for e in errs))
        ok = self._errs(pinned={"at": "2026-10-31",
                                "revision_kind": "document-download",
                                "version": "v1", "url": "https://nvlpubs/x.pdf",
                                "sha256": "a" * 64,
                                "license_spdx": "LicenseRef-X"})
        self.assertFalse(any("document-download" in e for e in ok))

    def test_revision_format(self):
        errs = self._errs(pinned={"at": "2026-10-31",
                                  "revision": "deadbeef",
                                  "revision_kind": "git-commit",
                                  "license_spdx": "MIT"})
        self.assertTrue(any("40-hex" in e for e in errs))
        # doc-version — валидно без 40-hex
        errs2 = self._errs(pinned={"at": "2026-10-31", "revision": "v1.1",
                                   "revision_kind": "doc-version",
                                   "license_spdx": "LicenseRef-X"})
        self.assertFalse(any("40-hex" in e for e in errs2))

    def test_execution_rule_forbids_exec(self):
        errs = self._errs(execution_rule="exec")
        self.assertTrue(any("execution_rule" in e for e in errs))

    def test_dataset_unknown_rights_blocked(self):
        errs = self._errs(kind="dataset",
                          usage="eval-only", license_reviewed=False,
                          upstream_code_rights="unknown")
        self.assertTrue(any("dataset с upstream_code_rights=unknown" in e
                            for e in errs))

    def test_label_and_coverage_required(self):
        errs = self._errs(label_source="", coverage="")
        self.assertTrue(any("label_source" in e for e in errs))
        self.assertTrue(any("coverage" in e for e in errs))


class ModelGateTests(unittest.TestCase):
    def _errs(self, **kw):
        m = {"id": "m", "kind": "model", "registry": "test",
             "pinned": {"at": "2026-10-31",
                        "digest": "sha256:" + "b" * 64,
                        "license_spdx": "Apache-2.0"},
             "role": "baseline", "usage": "eval-only",
             "license_reviewed": True,
             "execution_rule": "verified-runtime-only"}
        m.update(kw)
        return check_registry_dict({"schema": "sources-registry/1.0",
                                    "sources": [], "models": [m]})

    def test_good_model_passes(self):
        self.assertEqual(self._errs(), [])

    def test_model_requires_digest(self):
        errs = self._errs(pinned={"at": "2026-10-31",
                                  "license_spdx": "Apache-2.0"})
        self.assertTrue(any("digest обязателен" in e for e in errs))

    def test_lora_train_requires_compatibility(self):
        errs = self._errs(usage="both", compatibility=None)
        self.assertTrue(any("compatibility" in e for e in errs))

    def test_model_exec_rule(self):
        errs = self._errs(execution_rule="exec")
        self.assertTrue(any("execution_rule" in e for e in errs))


class RegistrySelfTests(unittest.TestCase):
    def test_real_registry_passes_its_own_validator(self):
        self.assertEqual(check_registry(REGISTRY), [])

    def test_hf_keys_decision_justified(self):
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        d = data["hf_keys_decision"]
        self.assertFalse(d["required"])
        self.assertTrue(len(d.get("justification", "")) > 20)

    def test_every_source_has_pinned_revision_and_license(self):
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        for s in data["sources"]:
            p = s["pinned"]
            if p.get("revision_kind") == "document-download":
                self.assertIn("sha256", p, s["id"])
                self.assertIn("url", p, s["id"])
                self.assertIn("version", p, s["id"])
            else:
                self.assertIn("revision", p, s["id"])
            self.assertIn("license_spdx", p, s["id"])

    def test_unreviewed_licenses_are_quarantined(self):
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        for s in data["sources"]:
            if not s.get("license_reviewed"):
                self.assertEqual(s["usage"], "metadata-only", s["id"])

    def test_nist_pinned_by_document_checksum(self):
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        for s in data["sources"]:
            if s["id"].startswith("nist-"):
                self.assertEqual(s["pinned"]["revision_kind"], "document-download")
                self.assertEqual(len(s["pinned"]["sha256"]), 64)

    def test_wycheproof_vectors_pinned(self):
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        w = [s for s in data["sources"] if s["id"] == "wycheproof-c2sp"][0]
        self.assertEqual(len(w["vectors_pinned"]), 7)
        for v in w["vectors_pinned"]:
            self.assertEqual(len(v["sha256"]), 64)

    def test_models_trainable_base_honest_null(self):
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
        for m in data["models"]:
            self.assertIsNone(m["trainable_base"], m["id"])
            self.assertIn("заменяет", m["trainable_note"])


def check_registry_dict(data: dict) -> list[str]:
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".json",
                                     delete=False, encoding="utf-8") as fh:
        json.dump(data, fh)
        tmp = fh.name
    try:
        return check_registry(tmp)
    finally:
        os.unlink(tmp)


if __name__ == "__main__":
    unittest.main()


class RegistryV12Tests(unittest.TestCase):
    """v1.2: review-квитанции, split-lineage, train_requirements."""

    def _data(self):
        return json.loads(REGISTRY.read_text(encoding="utf-8"))

    def test_review_receipts_present_and_independent(self):
        rr = self._data()["review_receipts"]
        self.assertTrue(any(r["independent"] and r["reviewer"] != "zcode-m5m"
                            for r in rr))

    def test_train_requirements_present_but_unfilled(self):
        for m in self._data()["models"]:
            tr = m["train_requirements"]
            self.assertIn("hf_revision", tr)
            self.assertIsNone(tr["hf_revision"])   # не заполняем ради заполнения
            self.assertIn("required_when", tr)

    def test_trainable_base_set_requires_full_checklist(self):
        m = {"id": "m", "kind": "model", "registry": "t",
             "pinned": {"digest": "sha256:" + "b" * 64,
                        "license_spdx": "Apache-2.0"},
             "role": "r", "usage": "both", "license_reviewed": True,
             "execution_rule": "verified-runtime-only",
             "compatibility": "same base+tokenizer",
             "trainable_base": "hf://someone/base-9b"}
        errs = check_registry_dict({"schema": "sources-registry/1.2",
                                    "sources": [], "models": [m],
                                    "review_receipts": []})
        self.assertTrue(any("train_requirements" in e for e in errs))

    def test_metadata_only_null_trainable_base_ok(self):
        # replit: для metadata-only trainable_base=null допустим
        m = {"id": "m", "kind": "model", "registry": "t",
             "pinned": {"digest": "sha256:" + "b" * 64,
                        "license_spdx": "Apache-2.0"},
             "role": "r", "usage": "eval-only", "license_reviewed": True,
             "execution_rule": "verified-runtime-only"}
        errs = check_registry_dict({"schema": "sources-registry/1.2",
                                    "sources": [], "models": [m],
                                    "review_receipts": []})
        self.assertFalse(any("train_requirements" in e for e in errs))

    def test_review_status_pending_per_source(self):
        for s in self._data()["sources"]:
            self.assertEqual(s["review"]["status"], "pending_independent_review")
            self.assertIsNone(s["review"]["reviewer"])


class RegistryV13Tests(unittest.TestCase):
    """v1.3: eval_rights_reviewed и train_rights_reviewed раздельны."""

    def _data(self):
        return json.loads(REGISTRY.read_text(encoding="utf-8"))

    def test_flags_are_separate_fields(self):
        for s in self._data()["sources"]:
            self.assertIn("eval_rights_reviewed", s, s["id"])
            self.assertIn("train_rights_reviewed", s, s["id"])

    def test_train_never_reviewed_yet(self):
        # ни один источник не прошёл train-ревью: обучение не разрешено
        for s in self._data()["sources"]:
            self.assertFalse(s["train_rights_reviewed"], s["id"])
            self.assertNotEqual(s["usage"], "train-possible", s["id"])

    def test_train_requires_train_rights_not_eval_only(self):
        s = source()  # GOOD_SOURCE train-possible c license_reviewed=True
        s["eval_rights_reviewed"] = True
        s["train_rights_reviewed"] = False
        errs = check_registry_dict({"schema": "sources-registry/1.3",
                                    "sources": [s], "models": [],
                                    "review_receipts": []})
        self.assertTrue(any("train_rights_reviewed" in e for e in errs))

    def test_eval_right_does_not_grant_train(self):
        # eval-ревью не переносится на train (ключевое правило v1.3)
        s = source(usage="eval-only", license_reviewed=True)
        s["eval_rights_reviewed"] = True
        s["train_rights_reviewed"] = False
        errs = check_registry_dict({"schema": "sources-registry/1.3",
                                    "sources": [s], "models": [],
                                    "review_receipts": []})
        self.assertFalse(any("eval_rights_reviewed" in e for e in errs))
