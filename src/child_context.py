"""Immutable child context: memory stores references, not execution state."""

from __future__ import annotations

import dataclasses
import json

from core_v1_contract import Context
from .adapters import decide  # noqa: F401
from .safety import EvidenceError
from .temporal_store import digest


class ContextRestoreUnresolved(Exception):
    pass


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclasses.dataclass(frozen=True)
class ChildContext:
    parent_run_id: str
    source: str
    version: str
    as_of: str | None
    owner: str
    grant_revision: int
    embedding_generation: str
    items_json: str
    context_digest: str

    @property
    def items(self):
        return json.loads(self.items_json)


def _digest(fields):
    return digest({k: v for k, v in fields.items() if k != "context_digest"})


def freeze_child_context(*, parent_run_id, as_of, owner, grant_revision,
                         embedding_generation, retrieved):
    items = retrieved["items"]
    if len(items) != 1:
        raise EvidenceError("child context requires exactly one pinned source")
    item = items[0]
    source = item.get("fact_id")
    structured = item.get("structured") or {}
    version = structured.get("version")
    if not isinstance(source, str) or not source or not isinstance(version, str) or not version:
        raise EvidenceError("child source/version missing")
    fields = {
        "parent_run_id": parent_run_id, "source": source, "version": version,
        "as_of": as_of, "owner": owner, "grant_revision": grant_revision,
        "embedding_generation": embedding_generation,
        "items_json": _canonical(items),
    }
    return ChildContext(**fields, context_digest=_digest(fields))


def verify_child_context(context, store, grants):
    fields = dataclasses.asdict(context)
    if context.context_digest != _digest(fields):
        raise EvidenceError("child context digest mismatch")
    grants.require(context.owner, "live-synth", context.embedding_generation,
                   context.grant_revision)
    items = context.items
    if len(items) != 1:
        raise EvidenceError("child items missing or ambiguous")
    item = items[0]
    if item.get("fact_id") != context.source:
        raise EvidenceError("child source mismatch")
    structured = item.get("structured") or {}
    if structured.get("version") != context.version or structured.get("value") != context.version:
        raise EvidenceError("child version mismatch")
    event = store.get_event(context.source)
    if event is None:
        raise EvidenceError("child source unavailable")
    if event["owner_id"] != context.owner:
        raise EvidenceError("child owner mismatch")
    expected = digest({"owner_id": event["owner_id"], "project": event["project"],
                       "session": event["session"], "actor": event["actor"],
                       "occurred_at": event["occurred_at"], "payload": event["payload"]})
    if event["source_digest"] != expected:
        raise EvidenceError("child evidence digest mismatch")
    return True


def restore_child_context(blob, store, grants):
    try:
        raw = json.loads(blob)
        required = {"parent_run_id", "source", "version", "as_of", "owner",
                    "grant_revision", "embedding_generation", "items_json", "context_digest"}
        if set(raw) != required:
            raise ValueError("child fields mismatch")
        context = ChildContext(**raw)
        verify_child_context(context, store, grants)
        return context
    except Exception as exc:
        raise ContextRestoreUnresolved("child snapshot cannot be restored") from exc


class FrozenChildMemoryContext:
    def __init__(self, context):
        self.context = context
        self.recalls = 0

    async def recall(self, principal, run_id, query, deadline_at):
        self.recalls += 1
        receipt = {"mode": "frozen_child", "context_digest": self.context.context_digest,
                   "source": self.context.source, "version": self.context.version,
                   "as_of": self.context.as_of, "grant_revision": self.context.grant_revision,
                   "embedding_generation": self.context.embedding_generation}
        return Context(items=tuple(self.context.items), retrieval_receipt=receipt)

    async def remember(self, principal, run_id, operation_key, items):
        return {"remembered": len(items), "run_id": run_id}
