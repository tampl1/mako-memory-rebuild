"""Final-answer CORE mode: real context to model, no tool effects."""

from __future__ import annotations

import json

from core_v1_agent import AgentRequest, CoreAgent
from core_v1_contract import ModelRequest


class FinalAnswerMemoryContext:
    def __init__(self, delegate):
        self.delegate = delegate

    def __getattr__(self, name):
        return getattr(self.delegate, name)

    async def recall(self, principal, run_id, query, deadline_at):
        return await self.delegate.recall(principal, run_id, query, deadline_at)

    async def remember(self, principal, run_id, operation_key, items):
        return {
            "durable": True,
            "operation_key": operation_key,
            "run_id": run_id,
            "remembered": len(items),
        }


class SourceVersionVerifier:
    def __init__(self, source, version):
        self.source = source
        self.version = version

    async def verify_answer(self, binding, run_id, generated, effects):
        if effects:
            return False
        message = generated.get("message") or {}
        try:
            answer = json.loads(message.get("content") or "")
        except (ValueError, TypeError):
            return False
        return answer == {"source": self.source, "version": self.version}


def make_agent(*, store, model, memory, policy, tools, transport, verifier,
               instructions=""):
    return CoreAgent(
        store=store, model=model, memory=memory, policy=policy, tools=tools,
        transport=transport, verifier=verifier, tool_catalog=[],
        max_model_steps=1, max_tool_calls=1, max_answer_repairs=0,
        instructions=instructions,
    )


def make_request(query, policy_revision):
    return AgentRequest(
        query=query, mode="auto", selected_model=None,
        policy_revision=policy_revision, output_limit=256,
    )


def model_input_digest(messages):
    from core_v1_store import encode
    import hashlib
    return hashlib.sha256(encode(messages).encode()).hexdigest()


def assert_model_request(request):
    if not isinstance(request, ModelRequest):
        raise TypeError("CORE ModelRequest required")
    if request.tools:
        raise ValueError("final-answer mode must not declare native tools")
    if len(request.messages) < 2:
        raise ValueError("retrieved context messages are missing")
    return True
