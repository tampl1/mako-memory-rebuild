"""Fail-closed retrieval, provenance and authorization checks for the pilot."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .temporal_store import digest


class SafetyError(Exception):
    pass


class RetrievalUnavailable(SafetyError):
    pass


class EvidenceError(SafetyError):
    pass


class AuthorizationError(SafetyError):
    pass


class EmbeddingGenerationMismatch(SafetyError):
    pass


class CheckpointError(SafetyError):
    pass


class AccessGrants:
    def __init__(self):
        self._grants = {}

    def allow(self, owner_id, project, generation):
        self._grants[(owner_id, project)] = generation

    def revoke(self, owner_id, project):
        self._grants.pop((owner_id, project), None)

    def require(self, owner_id, project, generation):
        if (owner_id, project) not in self._grants:
            raise AuthorizationError("grant missing or revoked")
        if self._grants[(owner_id, project)] != generation:
            raise EmbeddingGenerationMismatch("embedding generation changed")


class SafeReader:
    def __init__(self, store, grants, generation):
        self.store = store
        self.grants = grants
        self.generation = generation

    def _event_digest(self, event):
        return digest({
            "owner_id": event["owner_id"], "project": event["project"],
            "session": event["session"], "actor": event["actor"],
            "occurred_at": event["occurred_at"], "payload": event["payload"],
        })

    def _validate_item(self, item, owner_id, project):
        evidence_ids = item.get("provenance", {}).get("evidence_event_ids", [])
        fact_id = item.get("fact_id")
        if not evidence_ids or not fact_id or fact_id not in evidence_ids:
            raise EvidenceError("decision source is missing or disconnected")
        for event_id in evidence_ids:
            event = self.store.get_event(event_id)
            if event is None:
                raise EvidenceError("evidence event is unavailable")
            if event["owner_id"] != owner_id or event["project"] != project:
                raise EvidenceError("cross-owner evidence")
            if event["source_digest"] != self._event_digest(event):
                raise EvidenceError("evidence digest mismatch")
        if item.get("ref_type") == "claim":
            linked = {row["event_id"] for row in self.store.evidence_for(item["ref_id"])}
            if not linked or not set(evidence_ids).issubset(linked):
                raise EvidenceError("claim evidence link is missing")

    def read(self, *, owner_id, project, query, mode, limit):
        self.grants.require(owner_id, project, self.generation)
        try:
            if mode == "raw_history":
                out = self.store.raw_history(
                    owner_id=owner_id, project=project, query=query, limit=limit)
            elif mode == "temporal_recall":
                out = self.store.recall(
                    owner_id=owner_id, project=project, query=query, limit=limit)
            elif mode == "none":
                out = {"items": [], "retrieval_receipt": {"mode": "none"}}
            else:
                raise ValueError("unknown reader")
        except SafetyError:
            raise
        except Exception as exc:
            raise RetrievalUnavailable("retrieval unavailable; no fallback") from exc
        for item in out["items"]:
            self._validate_item(item, owner_id, project)
        out["embedding_generation"] = self.generation
        out["reranker"] = "none; deterministic raw ordering only"
        return out


def validate_decision(decision, retrieved):
    fact_ref = decision.get("fact_ref")
    if not fact_ref:
        raise EvidenceError("correct-looking answer has no source")
    matching = [item for item in retrieved["items"] if item.get("fact_id") == fact_ref]
    if not matching:
        raise EvidenceError("decision source was not validated by the reader")
    if not any(decision.get("version", "") in item.get("text", "") for item in matching):
        raise EvidenceError("decision value is absent from its source")
    return True


def inspect_untrusted_document(text):
    markers = (
        "ignore previous", "tool_call", "grant", "authority", "write memory",
        "train on", "base64", "transfer", "execute", "system prompt",
    )
    found = [marker for marker in markers if marker in text.lower()]
    return {
        "analysis_allowed": True,
        "suspicious_markers": found,
        "suspicious_quote": text if found else None,
        "granted_rights": [],
        "tool_calls": 0,
        "effects": 0,
        "memory_writes": 0,
        "training_writes": 0,
    }


def load_checkpoint(path):
    try:
        record = json.loads(Path(path).read_text(encoding="utf-8"))
        supplied = record.pop("sha256")
        expected = hashlib.sha256(
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        if supplied != expected:
            raise CheckpointError("checkpoint digest mismatch")
        if record.get("effect_count") not in (0, 1) or not record.get("run_id"):
            raise CheckpointError("checkpoint shape invalid")
        return record
    except CheckpointError:
        raise
    except Exception as exc:
        raise CheckpointError("checkpoint unavailable or corrupt") from exc
