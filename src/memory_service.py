"""Isolated memory-service lifecycle on the existing frozen-context canon."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from core_v1_contract import ModelResult
from .adapters import POLICY_REVISION
from .child_context import freeze_child_context, restore_child_context
from .decision_consumer import FrozenDecisionContext
from .final_answer import (
    FinalAnswerMemoryContext, SourceVersionVerifier, make_agent, make_request,
)
from .pilot import Pilot, make_binding
from .safety import AccessGrants, SafeReader


class ServiceNotReady(Exception):
    pass


class DeterministicContextModel:
    def __init__(self):
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        context = json.loads(request.messages[1]["content"])["retrieved_context"]
        item = context["items"][0]
        answer = {"source": item["fact_id"], "version": item["structured"]["version"]}
        return ModelResult(
            message={"role": "assistant", "content": json.dumps(answer)},
            receipt={"model": "deterministic-context-model-not-llm",
                     "operation_key": request.operation_key},
        )


class MemoryService:
    def __init__(self, workdir):
        self.workdir = Path(workdir)
        self.pilot = Pilot(self.workdir)
        self.store = self.pilot.memory
        self.owner = "owner-fixture"
        self.project = "g4-memory-consumer"
        self.grant_revision = 7
        self.embedding_generation = "bge-m3/1024"
        self.grants = AccessGrants()
        self.grants.allow(self.owner, self.project, self.embedding_generation,
                          self.grant_revision)
        self.binding = make_binding(self.owner)
        self.deadline = self.pilot._deadline()
        self.state = "new"
        self.children = {}
        self.runs = {}

    def _seed(self, version, occurred, kind, supersedes=None):
        event = self.store.ingest_event(
            owner_id=self.owner, project=self.project, session="memory-service",
            actor="service-fixture", recipients=[self.owner], kind=kind,
            occurred_at=occurred, ingested_at=occurred, certainty="explicit",
            payload={"release_target": version}, supersedes_event_id=supersedes,
            correction_of=supersedes,
        )
        if supersedes:
            self.store.invalidate_for_correction(event["event_id"])
        self.store.add_claim(
            owner_id=self.owner, project=self.project, session="memory-service",
            actor="service-fixture", certainty="explicit",
            statement={"action": "ship", "version": version},
            evidence_event_ids=[event["event_id"]], created_at=occurred,
        )
        return event

    def start(self):
        if not self.children:
            reader = SafeReader(self.store, self.grants,
                                self.embedding_generation, self.grant_revision)
            old = self._seed("v1.0", "2026-10-31T00:00:00+00:00", "fact")
            self._seed("v2.0", "2026-10-31T00:10:00+00:00",
                       "correction", old["event_id"])
            for version, as_of in (("v1.0", "2026-10-31T00:05:00+00:00"), ("v2.0", None)):
                retrieved = reader.read(
                    owner_id=self.owner, project=self.project, query="what should we ship?",
                    mode="temporal_recall", limit=1, as_of=as_of)
                self.children[version] = freeze_child_context(
                    parent_run_id=f"service-{version}", as_of=as_of, owner=self.owner,
                    tenant="tenant-synth", project=self.project,
                    grant_revision=self.grant_revision,
                    embedding_generation=self.embedding_generation, retrieved=retrieved)
        self.state = "ready"
        return {"state": self.state, "principal_generation": self.binding.principal.generation,
                "grant_revision": self.grant_revision}

    def execute(self, version, *, suffix=""):
        if self.state != "ready":
            raise ServiceNotReady(self.state)
        child = self.children[version]
        model = DeterministicContextModel()
        memory = FinalAnswerMemoryContext(FrozenDecisionContext(child, self.store, self.grants))
        agent = make_agent(
            store=self.pilot._run_store(), model=model, memory=memory,
            policy=self.pilot.policy, tools=self.pilot.executor,
            transport=self.pilot.transport,
            verifier=SourceVersionVerifier(child.source, child.version),
            instructions="Return only source/version from retrieved context")
        run = asyncio.run(agent.execute(
            self.binding, f"memory-service:{version}{suffix}",
            make_request("what should we ship?", POLICY_REVISION),
            self.deadline, holder=f"service-{version}{suffix}"))
        self.runs[version] = run
        return run

    def drain(self):
        self.state = "draining"
        pending = [key for key, run in self.runs.items() if run.state != "completed"]
        self.state = "stopped"
        return {"state": self.state, "pending": len(pending)}

    def restart(self):
        if self.state != "stopped":
            self.drain()
        service = MemoryService(self.workdir)
        service.deadline = self.deadline
        service.start()
        return service

    def replay(self, version):
        return self.execute(version)

    def restore_snapshot(self, blob):
        self.state = "unresolved"
        return restore_child_context(blob, self.store, self.grants)

    def outage(self):
        self.state = "outage"
        return {"state": self.state, "fallback": False, "effects": 0}
