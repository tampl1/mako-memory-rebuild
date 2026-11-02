import asyncio
import json
import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from core_v1_contract import ModelRequest, ModelResult, Principal
from core_v1_cost_ledger import CostLedger
from core_v1_errors import CapabilityUnsupported, DependencyUnavailable, EffectUnknown
from src.final_profile import build_snapshot, load_profile, make_port


class Handler(BaseHTTPRequestHandler):
    bodies = []
    mode = "ok"

    def do_POST(self):
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).bodies.append(json.loads(raw))
        if type(self).mode == "drop":
            self.close_connection = True
            return
        if type(self).mode == "malformed":
            body = b"{broken"
        else:
            body = json.dumps({
                "choices": [{"message": {"role": "assistant", "content": '{"source":"event-v2","version":"v2.0"}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14},
            }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class ModelPortABITests(unittest.TestCase):
    def setUp(self):
        Handler.bodies = []
        Handler.mode = "ok"
        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(__file__).resolve().parent.parent
        self.profile = load_profile(root / "controls" / "g4-final-m1m" / "producer-profile.json")
        now = datetime.now(timezone.utc)
        self.price = {
            "observed_at": now.isoformat(),
            "freshness_until": (now + timedelta(minutes=10)).isoformat(),
            "price_in": 0.0, "price_out": 0.0,
            "source": "local-lan-runtime-test-double", "price_status": "known",
        }
        self.snapshot, _ = build_snapshot(self.profile, self.price, self.tmp.name)

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, tools=()):
        return ModelRequest(
            run_id="abi-run", operation_key="abi-run:model:0",
            deadline_at=(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
            principal=Principal("owner-fixture", "tenant-fixture", "kimi-code-m5m",
                                "offline", 1, "thumb"),
            mode="auto", selected_model=None,
            messages=[{"role": "user", "content": '{"retrieved_context":{"items":[{"fact_id":"event-v2","structured":{"version":"v2.0"}}]}}'},
                      {"role": "user", "content": "what should we ship?"}],
            tools=list(tools), policy_revision="g4-policy-7", output_limit=256,
        )

    def port(self, ledger):
        port = make_port(self.profile, self.snapshot, self.price,
                         context_digest="a" * 64, ledger=ledger)
        port.base_url = self.base_url
        return port

    def test_core_request_unchanged_and_one_ledger(self):
        ledger = CostLedger()
        request = self.request()
        result = asyncio.run(self.port(ledger).generate(request))
        self.assertIsInstance(result, ModelResult)
        self.assertEqual(result.receipt["g5"]["request_operation_key"], request.operation_key)
        self.assertEqual(result.receipt["g5"]["messages"], 2)
        self.assertEqual(result.receipt["usage"]["total_tokens"], 14)
        self.assertEqual(len(ledger.attempts()), 1)
        self.assertEqual(Handler.bodies[0]["messages"], request.messages)
        self.assertEqual(Handler.bodies[0]["model"], "qwen3.5:9b")

    def test_native_tools_rejected_before_call(self):
        with self.assertRaises(CapabilityUnsupported):
            asyncio.run(self.port(CostLedger()).generate(self.request(tools=[{"name": "native"}])))
        self.assertEqual(len(Handler.bodies), 0)

    def test_malformed_and_drop_are_not_success(self):
        Handler.mode = "malformed"
        ledger = CostLedger()
        with self.assertRaises(DependencyUnavailable):
            asyncio.run(self.port(ledger).generate(self.request()))
        self.assertEqual(ledger.attempts()[0].outcome, "error")
        Handler.mode = "drop"
        with self.assertRaises(EffectUnknown):
            asyncio.run(self.port(ledger).generate(
                replace(self.request(), operation_key="abi-run:model:1")))
        self.assertEqual(len(ledger.attempts()), 2)
        self.assertNotEqual(ledger.attempts()[-1].outcome, "ok")


if __name__ == "__main__":
    unittest.main()
