"""MiniMax decision-envelope binding for live and frozen memory consumers."""

from __future__ import annotations

import dataclasses
import hashlib
import json
from datetime import datetime
from pathlib import Path

from core_v1_contract import Binding, Context, Principal
from .child_context import freeze_child_context, verify_child_context
from .derived_envelope import SCHEMA as DERIVED_SCHEMA, verify_derived_envelope
from .live_retrieval import LiveMemoryContext

EXPECTED_MINIMAX_SHA256 = "35fa0c9129c9f1c7960daee69c089bf178fdc0a83b7f7e82464edfb35d976279"


@dataclasses.dataclass(frozen=True)
class DecisionEnvelope:
    data: dict
    sha256: str

    @property
    def request(self):
        return self.data["adapter_contract"]["request"]

    @property
    def principal_data(self):
        return self.request["principal"]

    @property
    def derived_from(self):
        return self.data.get("derived_from")


def load_decision_envelope(path, *, base_path=None, now=None):
    raw = Path(path).read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    data = json.loads(raw)
    schema = data.get("schema")
    if schema == "router-producer-fixture/1.0":
        if actual != EXPECTED_MINIMAX_SHA256:
            raise ValueError("MiniMax decision input digest mismatch")
    elif schema == DERIVED_SCHEMA:
        linkage = data.get("derived_from") or {}
        if linkage.get("sha256") != EXPECTED_MINIMAX_SHA256 \
                or data.get("base_sha256") != EXPECTED_MINIMAX_SHA256:
            raise ValueError("derived envelope is not linked to the frozen MiniMax input")
        if base_path is None:
            raise ValueError("derived envelope requires the frozen base for verification")
        trusted_now = now or datetime.fromisoformat(
            linkage["derived_at"].replace("Z", "+00:00"))
        checks = verify_derived_envelope(
            data, base_path=base_path,
            base_sha256=EXPECTED_MINIMAX_SHA256, now=trusted_now,
        )
        failed = [check["name"] for check in checks if not check["ok"]]
        if failed:
            raise ValueError(f"derived envelope verification failed: {failed}")
    else:
        raise ValueError("unsupported MiniMax decision input schema")
    request = data.get("adapter_contract", {}).get("request", {})
    principal = request.get("principal", {})
    required = {"owner_id", "tenant_id", "identity_id", "connection_id",
                "generation", "key_thumbprint"}
    if set(principal) != required:
        raise ValueError("MiniMax principal fields mismatch")
    if type(principal["generation"]) is not int or principal["generation"] < 1:
        raise ValueError("MiniMax principal generation is invalid")
    for field in required - {"generation"}:
        if not isinstance(principal[field], str) or not principal[field]:
            raise ValueError(f"MiniMax principal field is empty: {field}")
    if not isinstance(request.get("run_id"), str) or not request["run_id"]:
        raise ValueError("MiniMax run_id is missing")
    return DecisionEnvelope(data=data, sha256=actual)


def binding_from_envelope(envelope):
    request = envelope.request
    return Binding(
        principal=Principal(**envelope.principal_data),
        source_message_id=request["run_id"],
        source_delivery_id="g4-offline-decision-input",
        source_attempt=1,
    )


def freeze_from_envelope(envelope, *, parent_run_id, as_of, project,
                         embedding_generation, retrieved):
    principal = envelope.principal_data
    return freeze_child_context(
        parent_run_id=parent_run_id,
        as_of=as_of,
        owner=principal["owner_id"],
        tenant=principal["tenant_id"],
        project=project,
        grant_revision=principal["generation"],
        embedding_generation=embedding_generation,
        retrieved=retrieved,
    )


class FrozenDecisionContext:
    """Frozen context with caller, grant and source checks on every first recall."""

    def __init__(self, context, store, grants):
        self.context = context
        self.store = store
        self.grants = grants
        self.recalls = 0
        self.proposed = 0
        self.attempted = 0
        self.admitted = 0

    def _validate_caller(self, principal):
        if principal is None or getattr(principal, "owner_id", None) != self.context.owner:
            raise ValueError("caller owner does not match frozen memory scope")
        if getattr(principal, "tenant_id", None) != self.context.tenant:
            raise ValueError("caller tenant does not match frozen memory scope")
        if getattr(principal, "project", self.context.project) != self.context.project:
            raise ValueError("caller project does not match frozen memory scope")

    async def recall(self, principal, run_id, query, deadline_at):
        self.proposed += 1
        self._validate_caller(principal)
        LiveMemoryContext._check_deadline(deadline_at)
        self.grants.require(
            self.context.owner, self.context.project,
            self.context.embedding_generation, self.context.grant_revision,
        )
        self.attempted += 1
        verify_child_context(self.context, self.store, self.grants)
        LiveMemoryContext._check_deadline(deadline_at)
        self.grants.require(
            self.context.owner, self.context.project,
            self.context.embedding_generation, self.context.grant_revision,
        )
        self.admitted += 1
        self.recalls += 1
        receipt = {
            "mode": "frozen_child",
            "context_digest": self.context.context_digest,
            "source": self.context.source,
            "version": self.context.version,
            "as_of": self.context.as_of,
            "grant_revision": self.context.grant_revision,
            "embedding_generation": self.context.embedding_generation,
            "caller": {
                "owner": self.context.owner,
                "tenant": self.context.tenant,
                "project": self.context.project,
            },
            "admission": {
                "proposed": self.proposed,
                "attempted": self.attempted,
                "admitted": self.admitted,
            },
        }
        return Context(items=tuple(self.context.items), retrieval_receipt=receipt)

    async def remember(self, principal, run_id, operation_key, items):
        return {"remembered": len(items), "run_id": run_id}
