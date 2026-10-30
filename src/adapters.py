"""Synthetic CORE adapters + shared decision logic. No network, no LLM.

The synthetic model decides purely from retrieved context items, so the three
memory conditions (none / raw history / temporal) are the only variable.
Receipts always name the model slug, so a model swap changes receipts without
touching stored data.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

CORE_DIR = Path("/Users/Andreyled/MAKO-Local/kimi-code-m5m/attached-assets-price-review/repo/scripts/backend/mnemosyne-core")
if str(CORE_DIR) not in sys.path:
    sys.path.insert(0, str(CORE_DIR))

from core_v1_contract import Context, Decision, Effect, ModelResult  # noqa: E402

POLICY_REVISION = "synth-policy-v1"
PRIOR_DEFAULT_VERSION = "v1.2"  # stale training prior used by the no-memory control


def _canon(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def decide(context_items, query):
    """Deterministic decision from context items (uniform 'text'/'fact_id').
    Falls back to the stale prior only when nothing was retrieved. Ties break
    by (valid_from, certainty rank) so an explicit current fact always beats
    an inferred old lesson."""
    rank = {"explicit": 2, "deterministic": 1, "inferred": 0}
    best = None
    for item in context_items:
        text = (item.get("text") or "").lower()
        fact_id = item.get("fact_id")
        if "release_target" in text or "ship" in text:
            version = None
            for token in text.replace("=", " ").split():
                if token.startswith("v") and token[1:2].isdigit():
                    version = token.strip(",.;")
                    break
            if version and fact_id:
                key = (item.get("valid_from") or "", rank.get(item.get("certainty"), 0))
                if best is None or key >= best[0]:
                    best = (key, {"action": "ship_release", "version": version,
                                  "fact_ref": fact_id,
                                  "basis": f"item:{item.get('ref_id')}"})
    if best:
        return dict(best[1])
    return {"action": "ship_release", "version": PRIOR_DEFAULT_VERSION,
            "fact_ref": None, "basis": "prior_default_no_context"}


class SyntheticModel:
    def __init__(self, slug):
        self.slug = slug
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        retrieved = request.messages[0]["content"]
        context = json.loads(retrieved)["retrieved_context"]
        message = decide(context.get("items", []), request.messages[1]["content"])
        receipt = {
            "model": self.slug,
            "model_version": "syn-1.0",
            "input_digest": hashlib.sha256(
                _canon([m["content"] for m in request.messages]).encode()).hexdigest(),
            "usage": {"context_items": len(context.get("items", [])), "calls": self.calls},
            "policy_revision": request.policy_revision,
        }
        return ModelResult(message=message, receipt=receipt)


class SyntheticToolExecutor:
    """Counts real executions so tests can prove exactly-once across restart."""

    def __init__(self):
        self.executions = []
        self.ledger = []

    async def execute(self, request, decision):
        self.executions.append(dict(request.arguments))
        seq = len(self.executions)
        if request.name != "ship_release":
            return Effect(request.operation_key, "denied", None)
        receipt = {
            "operation": request.name,
            "version": request.arguments.get("version"),
            "fact_ref": request.arguments.get("fact_ref"),
            "executor_seq": seq,
            "arguments_digest": request.arguments_digest,
        }
        self.ledger.append(receipt)
        return Effect(request.operation_key, "confirmed", receipt)

    async def reconcile(self, request):
        for receipt in reversed(self.ledger):
            if receipt.get("version") == request.arguments.get("version"):
                return Effect(request.operation_key, "confirmed", receipt)
        return Effect(request.operation_key, "unknown", None)


class StaticPolicyGate:
    def __init__(self, allowed=("ship_release",)):
        self.allowed = tuple(allowed)

    async def authorize(self, request):
        outcome = "allow" if request.name in self.allowed else "deny"
        return Decision(outcome, POLICY_REVISION, f"synthetic:{outcome}")

    async def validate_approval(self, request, approval_id):
        return Decision("deny", POLICY_REVISION, "synthetic:no_approvals")


class SyntheticTransport:
    def __init__(self):
        self.deliveries = []

    async def verify_binding(self, binding):
        if not binding.principal.owner_id:
            raise ValueError("synthetic binding rejected")

    async def wake(self, binding, run_id, operation_key):
        return {"woke": True}

    async def deliver_result(self, binding, run_id, operation_key, result):
        self.deliveries.append({"run_id": run_id, "operation_key": operation_key})
        return {"run_id": run_id, "operation_key": operation_key,
                "source_delivery_id": binding.source_delivery_id,
                "source_attempt": binding.source_attempt, "durable": True}

    async def acknowledge(self, binding, run_id, durable_result_receipt):
        return {"acknowledged": True, "run_id": run_id}


def _consistent(model_json, tool_receipt):
    """The executed effect must match what the model actually decided."""
    message = (model_json or {}).get("message", {})
    receipt = (tool_receipt or {}).get("receipt", {})
    return (message.get("action") == receipt.get("operation")
            and message.get("version") == receipt.get("version")
            and message.get("fact_ref") == receipt.get("fact_ref"))


class PermissiveVerifier:
    """Control-condition verifier: checks the run was internally consistent
    (model decision == executed effect) but not fact currency; correctness of
    the control is judged on the action/source afterwards."""

    def __init__(self, expected_fact_id=None):
        self.expected_fact_id = expected_fact_id
        self.checked = []

    async def verify(self, binding, run_id, model_json, tool_receipt):
        self.checked.append({"run_id": run_id, "tool_receipt": tool_receipt})
        return _consistent(model_json, tool_receipt)


class FactVerifyingVerifier:
    """Independent check: consistent run AND the tool receipt references the
    CURRENT fact version the memory handed out (the new version, never the
    superseded one)."""

    def __init__(self, expected_fact_id):
        self.expected_fact_id = expected_fact_id
        self.checked = []

    async def verify(self, binding, run_id, model_json, tool_receipt):
        self.checked.append({"run_id": run_id, "tool_receipt": tool_receipt})
        receipt = (tool_receipt or {}).get("receipt", {})
        return _consistent(model_json, tool_receipt) and \
            receipt.get("fact_ref") == self.expected_fact_id


class StoreMemoryContext:
    """Wraps TemporalStore.recall/raw_history behind the CORE MemoryContext
    protocol. mode: 'temporal_recall' | 'raw_history' | 'none'."""

    def __init__(self, store, owner_id, project, mode="temporal_recall"):
        self.store, self.owner_id, self.project, self.mode = store, owner_id, project, mode
        self.recalls = 0

    async def recall(self, principal, run_id, query, deadline_at):
        self.recalls += 1
        if self.mode == "none":
            items, receipt = [], {"mode": "none", "returned": 0, "scanned": 0,
                                  "result_digest": "none"}
        elif self.mode == "raw_history":
            out = self.store.raw_history(owner_id=self.owner_id, project=self.project,
                                         query=query, core_run_id=run_id)
            items, receipt = out["items"], out["retrieval_receipt"]
        else:
            out = self.store.recall(owner_id=self.owner_id, project=self.project,
                                    query=query, core_run_id=run_id)
            items, receipt = out["items"], out["retrieval_receipt"]
        return Context(items=tuple(items), retrieval_receipt=receipt)

    async def remember(self, principal, run_id, operation_key, items):
        return {"remembered": len(items), "run_id": run_id}
