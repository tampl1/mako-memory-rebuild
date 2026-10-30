"""Temporal event/claim store on SQLite. stdlib only.

Invariants:
- events and claims are immutable once written; the only mutable columns are
  the temporal invalidation fields (valid_to, invalid_due_to_event_id on
  claims; valid_to on events), written solely by invalidate_for_correction.
- all relations are by stored IDs (event_id/claim_id); no text similarity.
- occurred_at/ingested_at are always explicit; unknown authors are stored as
  None (rendered "unknown"), never fabricated.
- a CORE run/receipt is stored only as an opaque reference (core_run_id);
  this store never executes or authorizes actions.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from .validation import ValidationError, validate

CERTAINTIES = ("explicit", "deterministic", "inferred")
LINK_ROLES = ("supports", "contradicts", "derived_from", "corrects")

_SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "contracts"


def load_schema(name):
    return json.loads((_SCHEMAS_DIR / name).read_text(encoding="utf-8"))


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class TemporalStore:
    def __init__(self, path, clock=time.time):
        self.path = str(Path(path).absolute())
        self.clock = clock
        self._event_schema = load_schema("source-event.schema.json")
        self._claim_schema = load_schema("claim.schema.json")
        self._link_schema = load_schema("evidence-link.schema.json")
        self._snap_schema = load_schema("context-snapshot.schema.json")
        self._mig_schema = load_schema("migration-record.schema.json")
        with self._db() as db:
            db.executescript("""
                PRAGMA journal_mode=DELETE;
                CREATE TABLE IF NOT EXISTS source_events (
                  event_id TEXT PRIMARY KEY,
                  owner_id TEXT NOT NULL, project TEXT NOT NULL, session TEXT NOT NULL,
                  actor TEXT, recipients TEXT NOT NULL, kind TEXT NOT NULL,
                  occurred_at TEXT, ingested_at TEXT NOT NULL,
                  valid_from TEXT NOT NULL, valid_to TEXT,
                  correction_of TEXT, supersedes_event_id TEXT,
                  certainty TEXT NOT NULL, model_versions TEXT NOT NULL,
                  source_digest TEXT NOT NULL, core_run_id TEXT,
                  payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS claims (
                  claim_id TEXT PRIMARY KEY,
                  owner_id TEXT NOT NULL, project TEXT NOT NULL, session TEXT NOT NULL,
                  actor TEXT, statement TEXT NOT NULL, certainty TEXT NOT NULL,
                  created_at TEXT NOT NULL, valid_from TEXT NOT NULL, valid_to TEXT,
                  invalid_due_to_event_id TEXT,
                  derived_from_claim_ids TEXT NOT NULL, model_versions TEXT NOT NULL,
                  source_digest TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS evidence_links (
                  link_id TEXT PRIMARY KEY,
                  claim_id TEXT NOT NULL, event_id TEXT NOT NULL,
                  role TEXT NOT NULL, created_at TEXT NOT NULL, note TEXT,
                  UNIQUE(claim_id, event_id, role)
                );
                CREATE TABLE IF NOT EXISTS context_snapshots (
                  snapshot_id TEXT PRIMARY KEY,
                  owner_id TEXT NOT NULL, project TEXT NOT NULL,
                  created_at TEXT NOT NULL, mode TEXT NOT NULL,
                  items TEXT NOT NULL, retrieval_receipt TEXT NOT NULL,
                  as_of TEXT, core_run_id TEXT
                );
                CREATE TABLE IF NOT EXISTS migration_records (
                  migration_id TEXT PRIMARY KEY,
                  applied_at TEXT NOT NULL, tool_version TEXT NOT NULL,
                  record_digest TEXT NOT NULL, record TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_events_scope ON source_events(owner_id, project, valid_to);
                CREATE INDEX IF NOT EXISTS idx_claims_scope ON claims(owner_id, project, valid_to);
                CREATE INDEX IF NOT EXISTS idx_links_claim ON evidence_links(claim_id);
                CREATE INDEX IF NOT EXISTS idx_links_event ON evidence_links(event_id);
            """)

    def _db(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        return db

    # ---------------------------------------------------------------- events
    def ingest_event(self, *, owner_id, project, session, actor, recipients,
                     kind, occurred_at, certainty, payload,
                     model_versions=None, ingested_at=None, valid_from=None,
                     correction_of=None, supersedes_event_id=None,
                     core_run_id=None, event_id=None):
        if occurred_at is None:
            raise ValidationError("occurred_at is required; refusing to invent a timestamp")
        if certainty not in CERTAINTIES:
            raise ValidationError(f"certainty must be one of {CERTAINTIES}")
        if kind not in ("message", "decision", "action_result", "fact", "correction"):
            raise ValidationError(f"unknown event kind {kind!r}")
        ingested_at = ingested_at or utc_now()
        valid_from = valid_from or occurred_at
        for label, ts in (("occurred_at", occurred_at), ("ingested_at", ingested_at),
                          ("valid_from", valid_from)):
            datetime.fromisoformat(ts.replace("Z", "+00:00"))
        source_digest = digest({"owner_id": owner_id, "project": project,
                                "session": session, "actor": actor,
                                "occurred_at": occurred_at, "payload": payload})
        event_id = event_id or "ev_" + source_digest[:24]
        record = {
            "event_id": event_id, "kind": kind, "owner_id": owner_id,
            "project": project, "session": session, "actor": actor,
            "recipients": list(recipients or []), "occurred_at": occurred_at,
            "ingested_at": ingested_at, "valid_from": valid_from, "valid_to": None,
            "correction_of": correction_of, "supersedes_event_id": supersedes_event_id,
            "certainty": certainty, "model_versions": dict(model_versions or {}),
            "source_digest": source_digest, "core_run_id": core_run_id,
            "payload": payload,
        }
        validate(record, self._event_schema)
        with self._db() as db:
            cur = db.execute(
                """INSERT OR IGNORE INTO source_events
                   (event_id,owner_id,project,session,actor,recipients,kind,
                    occurred_at,ingested_at,valid_from,valid_to,correction_of,
                    supersedes_event_id,certainty,model_versions,source_digest,
                    core_run_id,payload)
                   VALUES (?,?,?,?,?,?,?,?,?,?,NULL,?,?,?,?,?,?,?)""",
                (event_id, owner_id, project, session, actor,
                 json.dumps(recipients or []), kind, occurred_at, ingested_at,
                 valid_from, correction_of, supersedes_event_id, certainty,
                 json.dumps(model_versions or {}), source_digest, core_run_id,
                 json.dumps(payload, sort_keys=True)))
            db.commit()
            row = db.execute("SELECT * FROM source_events WHERE event_id=?",
                             (event_id,)).fetchone()
        already = cur.rowcount == 0
        result = dict(row)
        result["already_present"] = already
        result["recipients"] = json.loads(result["recipients"])
        result["model_versions"] = json.loads(result["model_versions"])
        result["payload"] = json.loads(result["payload"])
        return result

    def get_event(self, event_id):
        with self._db() as db:
            row = db.execute("SELECT * FROM source_events WHERE event_id=?",
                             (event_id,)).fetchone()
        if row is None:
            return None
        out = dict(row)
        for key in ("recipients", "model_versions", "payload"):
            out[key] = json.loads(out[key])
        return out

    # ---------------------------------------------------------------- claims
    def add_claim(self, *, owner_id, project, session, actor, statement,
                  certainty, evidence_event_ids, derived_from_claim_ids=None,
                  model_versions=None, created_at=None, claim_id=None,
                  link_role="supports"):
        if certainty not in CERTAINTIES:
            raise ValidationError(f"certainty must be one of {CERTAINTIES}")
        if link_role not in LINK_ROLES:
            raise ValidationError(f"link role must be one of {LINK_ROLES}")
        created_at = created_at or utc_now()
        datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        evidence_event_ids = list(evidence_event_ids or [])
        derived_from_claim_ids = list(derived_from_claim_ids or [])
        with self._db() as db:
            missing = [eid for eid in evidence_event_ids if db.execute(
                "SELECT 1 FROM source_events WHERE event_id=?", (eid,)
            ).fetchone() is None]
            if missing:
                raise ValidationError(f"evidence events not found: {missing}")
            for cid in derived_from_claim_ids:
                if db.execute("SELECT 1 FROM claims WHERE claim_id=?",
                              (cid,)).fetchone() is None:
                    raise ValidationError(f"derived-from claim not found: {cid}")
            source_digest = digest({"statement": statement,
                                    "evidence": sorted(evidence_event_ids),
                                    "derived_from": sorted(derived_from_claim_ids)})
            claim_id = claim_id or "cl_" + source_digest[:24]
            record = {
                "claim_id": claim_id, "owner_id": owner_id, "project": project,
                "session": session, "actor": actor, "statement": statement,
                "certainty": certainty, "created_at": created_at,
                "valid_from": created_at, "valid_to": None,
                "invalid_due_to_event_id": None,
                "derived_from_claim_ids": derived_from_claim_ids,
                "model_versions": dict(model_versions or {}),
                "source_digest": source_digest,
            }
            validate(record, self._claim_schema)
            cur = db.execute(
                """INSERT OR IGNORE INTO claims
                   (claim_id,owner_id,project,session,actor,statement,certainty,
                    created_at,valid_from,valid_to,invalid_due_to_event_id,
                    derived_from_claim_ids,model_versions,source_digest)
                   VALUES (?,?,?,?,?,?,?,?,?,NULL,NULL,?,?,?)""",
                (claim_id, owner_id, project, session, actor,
                 json.dumps(statement, sort_keys=True), certainty, created_at,
                 created_at, json.dumps(derived_from_claim_ids),
                 json.dumps(model_versions or {}), source_digest))
            for eid in evidence_event_ids:
                link_id = "ln_" + digest([claim_id, eid, link_role])[:24]
                link = {"link_id": link_id, "claim_id": claim_id, "event_id": eid,
                        "role": link_role, "created_at": created_at, "note": None}
                validate(link, self._link_schema)
                db.execute(
                    """INSERT OR IGNORE INTO evidence_links
                       (link_id,claim_id,event_id,role,created_at,note)
                       VALUES (?,?,?,?,?,NULL)""",
                    (link_id, claim_id, eid, link_role, created_at))
            db.commit()
            row = db.execute("SELECT * FROM claims WHERE claim_id=?",
                             (claim_id,)).fetchone()
        out = dict(row)
        out["already_present"] = cur.rowcount == 0
        for key in ("statement", "derived_from_claim_ids", "model_versions"):
            out[key] = json.loads(out[key])
        return out

    def get_claim(self, claim_id):
        with self._db() as db:
            row = db.execute("SELECT * FROM claims WHERE claim_id=?",
                             (claim_id,)).fetchone()
        if row is None:
            return None
        out = dict(row)
        for key in ("statement", "derived_from_claim_ids", "model_versions"):
            out[key] = json.loads(out[key])
        return out

    def evidence_for(self, claim_id):
        with self._db() as db:
            rows = db.execute(
                "SELECT event_id, role, created_at FROM evidence_links "
                "WHERE claim_id=? ORDER BY event_id, role", (claim_id,)).fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------- invalidation
    def invalidate_for_correction(self, correcting_event_id):
        """Close the superseded event and incrementally invalidate dependent
        claims: direct evidence links first, then derived_from chains."""
        event = self.get_event(correcting_event_id)
        if event is None:
            raise ValidationError(f"event not found: {correcting_event_id}")
        target = event["supersedes_event_id"] or event["correction_of"]
        if target is None:
            raise ValidationError("correcting event supersedes nothing")
        closed_at = event["ingested_at"]
        invalidated = []
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "UPDATE source_events SET valid_to=? WHERE event_id=? AND valid_to IS NULL",
                (closed_at, target))
            direct = {r["claim_id"] for r in db.execute(
                """SELECT DISTINCT claim_id FROM evidence_links
                   WHERE event_id=? AND EXISTS
                     (SELECT 1 FROM claims WHERE claims.claim_id=evidence_links.claim_id
                      AND claims.valid_to IS NULL)""", (target,))}
            frontier = set(direct)
            seen = set()
            if frontier:
                dependents = {}
                for r in db.execute(
                        "SELECT claim_id, derived_from_claim_ids FROM claims"):
                    for parent in json.loads(r["derived_from_claim_ids"]):
                        dependents.setdefault(parent, set()).add(r["claim_id"])
            while frontier:
                claim_id = frontier.pop()
                if claim_id in seen:
                    continue
                seen.add(claim_id)
                db.execute(
                    """UPDATE claims SET valid_to=?, invalid_due_to_event_id=?
                       WHERE claim_id=? AND valid_to IS NULL""",
                    (closed_at, correcting_event_id, claim_id))
                invalidated.append(claim_id)
                frontier |= {c for c in dependents.get(claim_id, set())
                             if c not in seen}
            db.commit()
        return {"closed_event": target, "closed_at": closed_at,
                "invalidated_claim_ids": sorted(invalidated)}

    # ---------------------------------------------------------------- recall
    @staticmethod
    def _tokens(text):
        import re
        return [t for t in re.findall(r"[a-z0-9_]+", text.lower()) if len(t) >= 2]

    def _score(self, query_tokens, text):
        low = text.lower()
        return sum(1 for t in query_tokens if t in low)

    def _claim_item(self, db, row, query_tokens):
        statement = json.loads(row["statement"])
        text = " ".join(str(v) for v in statement.values())
        links = db.execute(
            "SELECT event_id, role FROM evidence_links WHERE claim_id=? "
            "ORDER BY event_id, role", (row["claim_id"],)).fetchall()
        ev_rows = [db.execute("SELECT source_digest, event_id FROM source_events "
                              "WHERE event_id=?", (l["event_id"],)).fetchone() for l in links]
        score = self._score(query_tokens, text)
        return score, {
            "ref_id": row["claim_id"], "ref_type": "claim", "text": text,
            "certainty": row["certainty"], "valid_from": row["valid_from"],
            "fact_id": links[0]["event_id"] if links else None,
            "provenance": {
                "evidence_event_ids": [l["event_id"] for l in links],
                "source_digests": [e["source_digest"] for e in ev_rows if e],
            },
        }

    def recall(self, *, owner_id, project, query, session=None, as_of=None,
               limit=5, core_run_id=None):
        """Current-valid claims (or as_of-valid) with explicit provenance.
        as_of is a UTC RFC3339 instant; None means 'now'."""
        query_tokens = self._tokens(query)
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM claims WHERE owner_id=? AND project=?"
                + (" AND session=?" if session else ""),
                (owner_id, project, *(([session] if session else [])))).fetchall()
            items = []
            for row in rows:
                if as_of is None:
                    if row["valid_to"] is not None:
                        continue
                else:
                    if not (row["valid_from"] <= as_of and
                            (row["valid_to"] is None or row["valid_to"] > as_of)):
                        continue
                score, item = self._claim_item(db, row, query_tokens)
                items.append((score, item))
        items.sort(key=lambda p: (-p[0], p[1]["ref_id"]))
        chosen = [item for score, item in items if score > 0][:limit]
        receipt = {"mode": "temporal_recall", "query_digest": digest(query),
                   "as_of": as_of, "owner_id": owner_id, "project": project,
                   "considered": len(items), "returned": len(chosen),
                   "result_digest": digest([i["ref_id"] for i in chosen])}
        self._snapshot(owner_id, project, "temporal_recall", chosen, receipt, as_of, core_run_id)
        return {"items": chosen, "retrieval_receipt": receipt}

    def raw_history(self, *, owner_id, project, query, session=None, as_of=None,
                    limit=5, core_run_id=None):
        """Deterministic baseline: keyword rerank over the raw event log,
        ties broken by occurred_at desc then event_id."""
        query_tokens = self._tokens(query)
        with self._db() as db:
            rows = db.execute(
                "SELECT * FROM source_events WHERE owner_id=? AND project=?"
                + (" AND session=?" if session else ""),
                (owner_id, project, *(([session] if session else [])))).fetchall()
            scored = []
            for row in rows:
                if as_of is not None and not (
                        row["valid_from"] <= as_of and
                        (row["valid_to"] is None or row["valid_to"] > as_of)):
                    continue
                payload = json.loads(row["payload"])
                text = " ".join([str(k) for k in payload.keys()] +
                                [str(v) for v in payload.values()])
                score = self._score(query_tokens, text)
                scored.append((score, row["occurred_at"], row["event_id"], {
                    "ref_id": row["event_id"], "ref_type": "event", "text": text,
                    "certainty": row["certainty"], "valid_from": row["valid_from"],
                    "fact_id": row["event_id"],
                    "provenance": {"evidence_event_ids": [row["event_id"]],
                                   "source_digests": [row["source_digest"]]},
                }))
        scored.sort(key=lambda p: (-p[0], p[1], p[2]))
        chosen = [item for score, _, _, item in scored if score > 0][:limit]
        receipt = {"mode": "raw_history", "query_digest": digest(query),
                   "as_of": as_of, "owner_id": owner_id, "project": project,
                   "scanned": len(scored), "returned": len(chosen),
                   "result_digest": digest([i["ref_id"] for i in chosen])}
        self._snapshot(owner_id, project, "raw_history", chosen, receipt, as_of, core_run_id)
        return {"items": chosen, "retrieval_receipt": receipt}

    def _snapshot(self, owner_id, project, mode, items, receipt, as_of, core_run_id):
        snap = {"snapshot_id": "sn_" + digest([mode, receipt["result_digest"],
                                               receipt["query_digest"]])[:24],
                "owner_id": owner_id, "project": project,
                "created_at": utc_now(), "mode": mode, "items": items,
                "retrieval_receipt": receipt, "as_of": as_of,
                "core_run_id": core_run_id}
        validate(snap, self._snap_schema)
        with self._db() as db:
            db.execute(
                """INSERT OR REPLACE INTO context_snapshots
                   (snapshot_id,owner_id,project,created_at,mode,items,
                    retrieval_receipt,as_of,core_run_id) VALUES (?,?,?,?,?,?,?,?,?)""",
                (snap["snapshot_id"], owner_id, project, snap["created_at"], mode,
                 json.dumps(items, sort_keys=True), json.dumps(receipt, sort_keys=True),
                 as_of, core_run_id))
            db.commit()
        return snap["snapshot_id"]

    # ------------------------------------------------------------- migration
    def apply_migration(self, migration_id, record, tool_version):
        record_digest = digest(record)
        applied_at = utc_now()
        with self._db() as db:
            cur = db.execute(
                """INSERT OR IGNORE INTO migration_records
                   (migration_id,applied_at,tool_version,record_digest,record)
                   VALUES (?,?,?,?,?)""",
                (migration_id, applied_at, tool_version, record_digest,
                 json.dumps(record, sort_keys=True)))
            db.commit()
            row = db.execute(
                "SELECT * FROM migration_records WHERE migration_id=?",
                (migration_id,)).fetchone()
        out = dict(row)
        out["record"] = json.loads(out["record"])
        out["already_applied"] = cur.rowcount == 0
        return out

    def close(self):
        Path(self.path).unlink(missing_ok=True)
