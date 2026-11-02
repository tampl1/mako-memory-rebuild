import copy
from datetime import timedelta
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from derived_consumer_runner import BASE, DERIVED, FROZEN_NOW, run_derived
from src.decision_consumer import load_decision_envelope
from src.derived_envelope import _digest_of
from src.source_closure import configure_core, SourceClosureError
import consumer_runner


class AdmissionHardening(unittest.TestCase):
    def check_rejection(self, mutate, reason):
        data = json.loads(DERIVED.read_text())
        mutate(data)
        data["envelope_sha256"] = _digest_of(data)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "request.json"
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, reason):
                load_decision_envelope(path, base_path=BASE, now=FROZEN_NOW)

    def test_principal_fields_cannot_change(self):
        original = json.loads(DERIVED.read_text())
        for field in original["adapter_contract"]["request"]["principal"]:
            with self.subTest(field=field):
                value = 91 if field == "generation" else "foreign"
                self.check_rejection(
                    lambda d: d["adapter_contract"]["request"]["principal"].update(
                        {field: value}), "immutable")

    def test_request_authority_cannot_change(self):
        changes = {"provider": "foreign", "model": "foreign", "version": "foreign",
                   "unit": "foreign", "tools": [{"name": "foreign"}],
                   "messages": [{"role": "user", "content": "different"}],
                   "admission": {"max_cost_usd": 1000}, "operation_key": "other"}
        for field, value in changes.items():
            with self.subTest(field=field):
                self.check_rejection(
                    lambda d: d["adapter_contract"]["request"].update(
                        {field: value}), "immutable")

    def test_external_clock_is_required(self):
        with self.assertRaisesRegex(ValueError, "external timezone-aware clock"):
            load_decision_envelope(DERIVED, base_path=BASE)
        with self.assertRaisesRegex(ValueError, "deadline"):
            load_decision_envelope(DERIVED, base_path=BASE,
                                   now=FROZEN_NOW + timedelta(seconds=601))

    def test_future_price_observation_rejected(self):
        def mutate(data):
            for value in data["view"].values():
                value["observed_at"] = (FROZEN_NOW + timedelta(seconds=1)).isoformat()
        self.check_rejection(mutate, "bounded price")

    def test_nonfinite_and_boolean_prices_rejected(self):
        for bad in (float("nan"), float("inf"), -1, True):
            with self.subTest(value=str(bad)):
                def mutate(data):
                    for value in data["view"].values():
                        value["price_in"] = bad
                self.check_rejection(mutate, "bounded price")

    def test_dependency_failure_does_not_count_as_policy_denial(self):
        original = consumer_runner._negative
        def wrong_cause(*args, **kwargs):
            observation = copy.deepcopy(original(*args, **kwargs))
            observation["error"] = "ModuleNotFoundError"
            return observation
        with patch.object(consumer_runner, "_negative", wrong_cause):
            report = run_derived()
        self.assertEqual(report["verdict"]["overall"], "FAIL")
        self.assertEqual(report["verdict"]["FAIL"], 5)

    def test_full_negative_path_runtime_required(self):
        source = Path(os.environ["MAKO_CORE_DIR"])
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("core_v1_contract.py", "core_v1_errors.py",
                         "core_v1_output_budget.py", "core_v1_runtime.py",
                         "core_v1_store.py"):
                shutil.copyfile(source / name, Path(tmp) / name)
            with patch.dict(os.environ, {"MAKO_CORE_DIR": tmp}):
                with self.assertRaisesRegex(SourceClosureError, "missing CORE runtime module"):
                    configure_core()


if __name__ == "__main__":
    unittest.main()
