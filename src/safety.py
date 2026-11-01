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


class GrantRevisionMismatch(AuthorizationError):
    pass


class AccessGrants:
    def __init__(self):
        self._grants = {}

    def allow(self, owner_id, project, generation, revision=1):
        self._grants[(owner_id, project)] = {
            "embedding_generation": generation, "grant_revision": revision,
        }

    def revoke(self, owner_id, project):
        self._grants.pop((owner_id, project), None)

    def require(self, owner_id, project, generation, grant_revision):
        grant = self._grants.get((owner_id, project))
        if grant is None:
            raise AuthorizationError("grant missing or revoked")
        if grant["embedding_generation"] != generation:
            raise EmbeddingGenerationMismatch("embedding generation changed")
        if grant["grant_revision"] != grant_revision:
            raise GrantRevisionMismatch("grant revision changed")


class SafeReader:
    def __init__(self, store, grants, generation, grant_revision=1):
        self.store = store
        self.grants = grants
        self.generation = generation
        self.grant_revision = grant_revision

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
        structured_version = None
        for event_id in evidence_ids:
            event = self.store.get_event(event_id)
            if event is None:
                raise EvidenceError("evidence event is unavailable")
            if event["owner_id"] != owner_id or event["project"] != project:
                raise EvidenceError("cross-owner evidence")
            if event["source_digest"] != self._event_digest(event):
                raise EvidenceError("evidence digest mismatch")
            if event_id == fact_id:
                structured_version = event["payload"].get("version")
                if structured_version is None:
                    structured_version = event["payload"].get("release_target")
        if item.get("ref_type") == "claim":
            linked = {row["event_id"] for row in self.store.evidence_for(item["ref_id"])}
            if not linked or not set(evidence_ids).issubset(linked):
                raise EvidenceError("claim evidence link is missing")
            claim = self.store.get_claim(item["ref_id"])
            if claim is None or claim["owner_id"] != owner_id or claim["project"] != project:
                raise EvidenceError("claim scope mismatch")
            statement_version = claim["statement"].get("version")
            if statement_version != structured_version:
                raise EvidenceError("claim and source versions differ")
        item["structured"] = {
            "version": structured_version,
            "value": structured_version,
            "source_event_ids": list(evidence_ids),
        }

    def read(self, *, owner_id, project, query, mode, limit, as_of=None):
        self.grants.require(owner_id, project, self.generation, self.grant_revision)
        try:
            if mode == "raw_history":
                out = self.store.raw_history(
                    owner_id=owner_id, project=project, query=query, limit=limit, as_of=as_of)
            elif mode == "temporal_recall":
                out = self.store.recall(
                    owner_id=owner_id, project=project, query=query, limit=limit, as_of=as_of)
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
        out["grant_revision"] = self.grant_revision
        out["reranker"] = "none; deterministic raw ordering only"
        return out


def validate_decision(decision, retrieved):
    if not isinstance(decision, dict):
        raise EvidenceError("decision must be an object")
    fact_ref = decision.get("fact_ref")
    version = decision.get("version")
    if not isinstance(fact_ref, str) or not fact_ref:
        raise EvidenceError("correct-looking answer has no source")
    if not isinstance(version, str) or not version:
        raise EvidenceError("version must be a non-empty string")
    matching = [item for item in retrieved["items"] if item.get("fact_id") == fact_ref]
    if not matching:
        raise EvidenceError("decision source was not validated by the reader")
    for item in matching:
        structured = item.get("structured")
        if not isinstance(structured, dict):
            raise EvidenceError("structured source linkage is missing")
        if fact_ref not in structured.get("source_event_ids", []):
            raise EvidenceError("structured source linkage is broken")
        if type(structured.get("version")) is not str or not structured["version"]:
            raise EvidenceError("structured source version is missing")
        if structured["version"] != version or structured.get("value") != version:
            raise EvidenceError("decision version differs from its source")
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
        "scope": "this inspection function only; not an agent-chain authorization proof",
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
