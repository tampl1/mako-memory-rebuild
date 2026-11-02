"""Memory Onboarding v1.1 consumer: validate, read-only recover, append-only status."""

from __future__ import annotations

import copy
import dataclasses
import hashlib
import json
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .child_context import ContextRestoreUnresolved, restore_child_context
from .decision_consumer import FrozenDecisionContext

ZERO_DIGEST = "0" * 64


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def digest_json(value):
    return digest_bytes(canonical(value).encode())


def _digest(value):
    return isinstance(value, str) and len(value) == 64 and all(
        char in "0123456789abcdef" for char in value
    )


def _time(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("UTC timestamp required")
    return parsed


class BootstrapError(Exception):
    pass


class BootstrapSignatureError(BootstrapError):
    pass


class BootstrapExpired(BootstrapError):
    pass


class BootstrapScopeError(BootstrapError):
    pass


class BootstrapConflict(BootstrapError):
    def __init__(self, receipt):
        super().__init__("local/shared passport conflict")
        self.receipt = receipt


class StatusValidationError(BootstrapError):
    pass


@dataclasses.dataclass(frozen=True)
class VerifiedBootstrap:
    bundle: dict
    bundle_digest: str
    principal: dict
    grant: dict
    passports: tuple


class MemoryBootstrapConsumer:
    def __init__(self, signature_verifier, *, issuer, audience, now):
        self.signature_verifier = signature_verifier
        self.issuer = issuer
        self.audience = audience
        self.now = now

    @staticmethod
    def _reject_secret_fields(node, path=""):
        forbidden = {"access_token", "refresh_token", "private_key", "password",
                     "secret", "dsn", "authorization"}
        if isinstance(node, dict):
            for key, value in node.items():
                if key.lower() in forbidden:
                    raise BootstrapScopeError(f"raw credential field forbidden: {path}/{key}")
                MemoryBootstrapConsumer._reject_secret_fields(value, f"{path}/{key}")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                MemoryBootstrapConsumer._reject_secret_fields(value, f"{path}[{index}]")

    def validate(self, bundle):
        if not isinstance(bundle, dict):
            raise BootstrapError("bundle must be an object")
        required = {
            "bundle_id", "issuer", "audience", "issued_at", "expires_at",
            "bundle_generation", "canonicalization", "key_id", "signature",
            "memory_endpoint", "memory_protocol_version", "credential_ref",
            "principal", "grant", "passports", "recovery_contract",
            "notice", "data_residency", "retention", "metadata_allowlist",
        }
        missing = required - set(bundle)
        if missing:
            raise BootstrapError(f"bundle fields missing: {sorted(missing)}")
        self._reject_secret_fields(bundle)
        if bundle["issuer"] != self.issuer or bundle["audience"] != self.audience:
            raise BootstrapScopeError("bundle issuer or audience mismatch")
        if bundle["canonicalization"] != "JCS":
            raise BootstrapError("unsupported bundle canonicalization")
        if type(bundle["bundle_generation"]) is not int or bundle["bundle_generation"] < 1:
            raise BootstrapError("invalid bundle generation")
        try:
            issued = _time(bundle["issued_at"])
            expires = _time(bundle["expires_at"])
        except Exception as exc:
            raise BootstrapError("invalid bundle timestamps") from exc
        if expires <= issued or not issued <= self.now < expires:
            raise BootstrapExpired("bundle expired or not yet valid")
        if not isinstance(bundle["credential_ref"], str) or not bundle["credential_ref"]:
            raise BootstrapScopeError("credential_ref is required")
        endpoint = urlsplit(bundle["memory_endpoint"])
        if endpoint.scheme not in {"https", "file"} or endpoint.username or endpoint.password:
            raise BootstrapScopeError("memory endpoint is invalid or contains credentials")

        principal = bundle["principal"]
        principal_required = {"owner_id", "tenant_id", "identity_id", "connection_id",
                              "generation", "key_thumbprint"}
        if not isinstance(principal, dict) or set(principal) != principal_required:
            raise BootstrapScopeError("principal fields mismatch")
        if type(principal["generation"]) is not int or principal["generation"] < 1:
            raise BootstrapScopeError("invalid principal generation")
        for field in principal_required - {"generation"}:
            if not isinstance(principal[field], str) or not principal[field]:
                raise BootstrapScopeError(f"principal field is empty: {field}")

        grant = bundle["grant"]
        grant_required = {"owner", "tenant", "project", "revision",
                          "embedding_generation", "scopes"}
        if not isinstance(grant, dict) or not grant_required.issubset(grant):
            raise BootstrapScopeError("grant fields mismatch")
        if grant["owner"] != principal["owner_id"] or grant["tenant"] != principal["tenant_id"]:
            raise BootstrapScopeError("principal and grant scope differ")
        if type(grant["revision"]) is not int or grant["revision"] < 1:
            raise BootstrapScopeError("invalid grant revision")
        if not isinstance(grant["embedding_generation"], str) or not grant["embedding_generation"]:
            raise BootstrapScopeError("embedding generation is required")
        scopes = grant["scopes"]
        if not isinstance(scopes, list) or "memory:read" not in scopes \
                or any(scope not in {"memory:read", "memory:status:write"} for scope in scopes):
            raise BootstrapScopeError("grant scopes are invalid")

        recovery = bundle["recovery_contract"]
        prohibited = {"model_calls", "effects", "backfill", "training", "worker_restart"}
        if not isinstance(recovery, dict) or recovery.get("read_only") is not True \
                or recovery.get("execution_owner") != "CORE" \
                or recovery.get("on_conflict") != "unresolved" \
                or set(recovery.get("prohibited", [])) != prohibited:
            raise BootstrapScopeError("read-only recovery contract mismatch")
        if not isinstance(bundle["notice"], dict) \
                or bundle["notice"].get("rkn") not in {"submitted", "not_submitted"}:
            raise BootstrapScopeError("notice is missing or invalid")
        if not isinstance(bundle["data_residency"], str) or not bundle["data_residency"]:
            raise BootstrapScopeError("data residency is required")
        if not isinstance(bundle["retention"], str) or not bundle["retention"]:
            raise BootstrapScopeError("retention is required")
        if not isinstance(bundle["metadata_allowlist"], list) \
                or not bundle["metadata_allowlist"] \
                or any(not isinstance(item, str) or not item
                       for item in bundle["metadata_allowlist"]):
            raise BootstrapScopeError("metadata allowlist is invalid")

        passports = bundle["passports"]
        if not isinstance(passports, list):
            raise BootstrapScopeError("passports must be a list")
        passport_required = {"track", "context_ref", "context_digest", "source_ref",
                             "source_digest", "version", "as_of"}
        for passport in passports:
            if not isinstance(passport, dict) or set(passport) != passport_required:
                raise BootstrapScopeError("passport fields mismatch")
            if not isinstance(passport["track"], str) or not passport["track"]:
                raise BootstrapScopeError("passport track is required")
            for field in ("context_digest", "source_digest"):
                if not _digest(passport[field]):
                    raise BootstrapScopeError(f"passport {field} is invalid")
            for field in ("context_ref", "source_ref", "version"):
                if not isinstance(passport[field], str) or not passport[field]:
                    raise BootstrapScopeError(f"passport {field} is required")
            if passport["as_of"] is not None:
                try:
                    _time(passport["as_of"])
                except Exception as exc:
                    raise BootstrapScopeError("passport as_of is invalid") from exc

        signature = bundle["signature"]
        if not isinstance(signature, dict) or set(signature) != {"algorithm", "value"}:
            raise BootstrapSignatureError("signature shape is invalid")
        unsigned = copy.deepcopy(bundle)
        unsigned.pop("signature")
        if not self.signature_verifier(
                bundle["key_id"], canonical(unsigned).encode(), signature):
            raise BootstrapSignatureError("bundle signature verification failed")
        return VerifiedBootstrap(
            bundle=copy.deepcopy(bundle), bundle_digest=digest_json(bundle),
            principal=copy.deepcopy(principal), grant=copy.deepcopy(grant),
            passports=tuple(copy.deepcopy(passports)),
        )

    def recover(self, verified, passport_ref, copies, store, grants):
        if not isinstance(verified, VerifiedBootstrap):
            raise BootstrapError("bundle must be validated before recovery")
        matches = [p for p in verified.passports
                   if passport_ref in {p["track"], p["context_ref"]}]
        if len(matches) != 1:
            raise ContextRestoreUnresolved("passport reference is missing or ambiguous")
        passport = matches[0]
        available = {name: data for name, data in copies.items() if data is not None}
        if not available:
            raise ContextRestoreUnresolved("passport content is unavailable")
        measured = {}
        for name, data in available.items():
            if not isinstance(data, bytes):
                raise ContextRestoreUnresolved("passport content must be bytes")
            measured[name] = digest_bytes(data)
        if len(set(measured.values())) != 1 \
                or any(value != passport["context_digest"] for value in measured.values()):
            raise BootstrapConflict({
                "status": "unresolved", "reason": "local/shared context digest mismatch",
                "context_ref": passport["context_ref"],
                "expected_digest": passport["context_digest"],
                "copy_digests": measured,
            })
        context = restore_child_context(next(iter(available.values())), store, grants)
        event = store.get_event(context.source)
        if context.source != passport["source_ref"] \
                or context.version != passport["version"] \
                or context.as_of != passport["as_of"] \
                or event is None or event["source_digest"] != passport["source_digest"]:
            raise ContextRestoreUnresolved("passport source linkage mismatch")
        if context.owner != verified.grant["owner"] \
                or context.tenant != verified.principal["tenant_id"] \
                or context.project != verified.grant["project"] \
                or context.grant_revision != verified.grant["revision"] \
                or context.embedding_generation != verified.grant["embedding_generation"]:
            raise ContextRestoreUnresolved("passport scope differs from verified bundle")
        return FrozenDecisionContext(context, store, grants)


class MemoryStatusWriter:
    def __init__(self, goal_verifier, storage=None):
        self.goal_verifier = goal_verifier
        self.storage = storage if storage is not None else []

    def append(self, verified, envelope):
        if not isinstance(verified, VerifiedBootstrap):
            raise StatusValidationError("bundle must be validated before status append")
        if "memory:status:write" not in verified.grant["scopes"]:
            raise StatusValidationError("status write scope is missing")
        required = {"idempotency_key", "track", "slug", "state", "next_step",
                    "artifact_ref", "artifact_sha256", "updated_at", "previous_digest",
                    "goal_status", "safety_status"}
        if not isinstance(envelope, dict) or not required.issubset(envelope):
            raise StatusValidationError("status fields mismatch")
        if envelope["slug"] != verified.principal["identity_id"]:
            raise StatusValidationError("status writer identity mismatch")
        if envelope["state"] not in {"done", "doing", "will_do"}:
            raise StatusValidationError("status state is invalid")
        if envelope["goal_status"] not in {"DELIVERED", "NOT_DELIVERED", "UNKNOWN"}:
            raise StatusValidationError("goal status is invalid")
        if envelope["safety_status"] not in {"PASS", "FAIL", "UNKNOWN"}:
            raise StatusValidationError("safety status is invalid")
        for field in ("idempotency_key", "track", "next_step", "artifact_ref"):
            if not isinstance(envelope[field], str) or not envelope[field]:
                raise StatusValidationError(f"status field is empty: {field}")
        if not _digest(envelope["artifact_sha256"]) or not _digest(envelope["previous_digest"]):
            raise StatusValidationError("status digest is invalid")
        try:
            _time(envelope["updated_at"])
        except Exception as exc:
            raise StatusValidationError("status updated_at is invalid") from exc

        base = {key: copy.deepcopy(value) for key, value in envelope.items()}
        measured_digest = digest_json(base)
        for existing in self.storage:
            if existing["idempotency_key"] == envelope["idempotency_key"]:
                if existing["event_digest"] != measured_digest:
                    raise StatusValidationError("idempotency key payload conflict")
                return {**existing, "deduplicated": True}
        lineage = [item for item in self.storage
                   if item["track"] == envelope["track"] and item["slug"] == envelope["slug"]]
        expected_previous = lineage[-1]["event_digest"] if lineage else ZERO_DIGEST
        if envelope["previous_digest"] != expected_previous:
            raise StatusValidationError("previous status digest mismatch")
        if envelope["state"] == "done" and (
                envelope["goal_status"] != "DELIVERED"
                or self.goal_verifier(verified, envelope) is not True):
            raise StatusValidationError("done requires independently verified goal delivery")
        event = {**base, "event_id": "status-" + measured_digest[:32],
                 "event_digest": measured_digest}
        self.storage.append(event)
        return {**event, "deduplicated": False}
