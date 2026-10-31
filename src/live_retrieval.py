"""Live Ollama embedding and generative rerank retrieval for the isolated pilot."""

from __future__ import annotations

import json
import math
import time
import urllib.request

from .adapters import CORE_DIR  # noqa: F401,E402
from core_v1_contract import Context  # noqa: E402
from .safety import EvidenceError, RetrievalUnavailable
from .temporal_store import digest

BGE_DIGEST = "7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab"
RERANK_DIGEST = "6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7"


class CallBudget:
    def __init__(self, maximum=64, seconds=1800):
        self.maximum = maximum
        self.deadline = time.monotonic() + seconds
        self.embedding_calls = 0
        self.rerank_calls = 0

    @property
    def total(self):
        return self.embedding_calls + self.rerank_calls

    def take(self, kind):
        if time.monotonic() > self.deadline or self.total >= self.maximum:
            raise RetrievalUnavailable("local live-call budget exhausted")
        if kind == "embedding":
            self.embedding_calls += 1
        else:
            self.rerank_calls += 1


def parse_score(text):
    try:
        value = json.loads(text.strip())
        score = value["score"]
        if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1:
            raise ValueError("score outside 0..1")
        return float(score)
    except Exception as exc:
        raise RetrievalUnavailable("reranker returned no strict JSON score") from exc


def validate_rerank_items(items, candidate_ids, limit=2):
    if not isinstance(items, list) or len(items) > limit:
        raise EvidenceError("rerank result exceeds the candidate limit")
    ids = [item.get("ref_id") for item in items]
    if len(ids) != len(set(ids)):
        raise EvidenceError("rerank returned duplicate source IDs")
    if any(source_id not in candidate_ids for source_id in ids):
        raise EvidenceError("rerank invented a source outside the candidate set")
    for item in items:
        structured = item.get("structured") or {}
        if structured.get("source_event_ids") != [item.get("fact_id")]:
            raise EvidenceError("rerank source linkage is invalid")
    return True


class OllamaLiveRetriever:
    def __init__(self, store, grants, owner_id, project, endpoint, budget):
        self.store = store
        self.grants = grants
        self.owner_id = owner_id
        self.project = project
        self.endpoint = endpoint.rstrip("/")
        self.budget = budget
        self._embeddings = {}

    def _post(self, path, payload, kind):
        self.budget.take(kind)
        request = urllib.request.Request(
            self.endpoint + path, data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return json.load(response)
        except Exception as exc:
            raise RetrievalUnavailable(f"live {kind} call failed") from exc

    def _embed(self, text):
        key = digest(text)
        if key not in self._embeddings:
            out = self._post("/api/embed", {"model": "bge-m3:latest", "input": text}, "embedding")
            vector = out["embeddings"][0]
            if len(vector) != 1024:
                raise RetrievalUnavailable("unexpected embedding generation/dimension")
            self._embeddings[key] = vector
        return self._embeddings[key]

    @staticmethod
    def _cosine(a, b):
        dot = sum(x * y for x, y in zip(a, b))
        norm = math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))
        return dot / norm if norm else 0.0

    def _events(self, as_of):
        with self.store._db() as db:
            rows = db.execute(
                "SELECT event_id FROM source_events WHERE owner_id=? AND project=?",
                (self.owner_id, self.project)).fetchall()
        events = []
        for row in rows:
            event = self.store.get_event(row["event_id"])
            expected = digest({"owner_id": event["owner_id"], "project": event["project"],
                               "session": event["session"], "actor": event["actor"],
                               "occurred_at": event["occurred_at"], "payload": event["payload"]})
            if event["source_digest"] != expected:
                raise EvidenceError("live retrieval evidence digest mismatch")
            if as_of is not None and not (
                    event["valid_from"] <= as_of
                    and (event["valid_to"] is None or event["valid_to"] > as_of)):
                continue
            if as_of is None and event["valid_to"] is not None:
                continue
            events.append(event)
        return events

    @staticmethod
    def _text(event):
        return " ".join([str(key) for key in event["payload"].keys()]
                        + [str(value) for value in event["payload"].values()])

    def _item(self, event, similarity, score=None):
        version = event["payload"].get("release_target") or event["payload"].get("version")
        if event["kind"] in {"fact", "correction"} and (
                not isinstance(version, str) or not version):
            raise EvidenceError("fact event is missing required release_target/version")
        return {
            "ref_id": event["event_id"], "ref_type": "event", "fact_id": event["event_id"],
            "text": self._text(event), "certainty": event["certainty"],
            "valid_from": event["valid_from"], "similarity": similarity,
            "rerank_score": score,
            "provenance": {"evidence_event_ids": [event["event_id"]],
                           "source_digests": [event["source_digest"]]},
            "structured": {"version": version, "value": version,
                           "source_event_ids": [event["event_id"]]},
        }

    def retrieve(self, query, mode="raw", as_of=None, limit=2):
        self.grants.require(self.owner_id, self.project, "bge-m3/1024", 1)
        events = self._events(as_of)
        query_vector = self._embed(query)
        ranked = []
        for event in events:
            similarity = self._cosine(query_vector, self._embed(self._text(event)))
            ranked.append((similarity, event))
        ranked.sort(key=lambda pair: (-pair[0], pair[1]["event_id"]))
        candidates = ranked[:limit]
        if mode == "rerank":
            reranked = []
            for similarity, event in candidates:
                prompt = (
                    'Output only JSON {"score": number from 0 to 1}. '
                    f"Query: {query}\nDocument: {self._text(event)}")
                out = self._post("/api/generate", {
                    "model": "qwen3.5:9b", "prompt": prompt, "stream": False,
                    "think": False, "options": {"temperature": 0, "num_predict": 64},
                }, "rerank")
                reranked.append((parse_score(out["response"]), similarity, event))
            reranked.sort(key=lambda row: (-row[0], -row[1], row[2]["event_id"]))
            items = [self._item(event, similarity, score)
                     for score, similarity, event in reranked]
        else:
            items = [self._item(event, similarity) for similarity, event in candidates]
        candidate_ids = [event["event_id"] for _, event in candidates]
        if mode == "rerank":
            validate_rerank_items(items, candidate_ids, limit)
        receipt = {
            "mode": mode, "embedding_model": "bge-m3:latest",
            "embedding_digest": BGE_DIGEST, "rerank_model": "qwen3.5:9b" if mode == "rerank" else None,
            "rerank_digest": RERANK_DIGEST if mode == "rerank" else None,
            "rerank_kind": "generative_llm_json_score" if mode == "rerank" else None,
            "rerank_error_policy": "typed_unavailable_no_silent_fallback" if mode == "rerank" else None,
            "as_of": as_of, "candidate_ids": [event["event_id"] for _, event in candidates],
            "result_ids": [item["ref_id"] for item in items],
            "calls": {"embedding": self.budget.embedding_calls, "rerank": self.budget.rerank_calls},
        }
        return {"items": items, "retrieval_receipt": receipt}


class LiveMemoryContext:
    def __init__(self, retriever, mode="raw"):
        self.retriever = retriever
        self.mode = mode
        self.recalls = 0

    async def recall(self, principal, run_id, query, deadline_at):
        self.recalls += 1
        out = self.retriever.retrieve(query, self.mode)
        return Context(items=tuple(out["items"]), retrieval_receipt=out["retrieval_receipt"])

    async def remember(self, principal, run_id, operation_key, items):
        return {"remembered": len(items), "run_id": run_id}
