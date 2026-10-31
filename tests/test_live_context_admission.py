import asyncio
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from src.live_retrieval import LiveMemoryContext
from src.safety import AccessGrants, AuthorizationError, EvidenceError, RetrievalUnavailable


class FakeRetriever:
    def __init__(self, grants, delay=0, mismatch=False, block=False):
        self.owner_id = "o"
        self.project = "p"
        self.grants = grants
        self.delay = delay
        self.mismatch = mismatch
        self.block = block
        self.calls = 0
        self.runtime_checks = 0
        self.started = threading.Event()
        self.release = threading.Event()

    def retrieve(self, query, mode):
        self.calls += 1
        self.started.set()
        if self.block:
            self.release.wait(5)
        elif self.delay:
            time.sleep(self.delay)
        item = {"ref_id": "e1", "fact_id": "e1", "text": "release_target v1.0",
                "structured": {"version": "v1.0", "value": "v1.0",
                               "source_event_ids": ["e1"]}}
        return {"items": [item], "retrieval_receipt": {"result_ids": ["e1"]}}

    def verify_runtime(self, mode="raw"):
        self.runtime_checks += 1
        if self.mismatch:
            raise EvidenceError("runtime digest mismatch")
        return True


class LiveContextAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.grants = AccessGrants()
        self.grants.allow("o", "p", "bge-m3/1024", 1)
        self.principal = SimpleNamespace(owner_id="o", tenant_id="t", project="p")
        self.deadline = (datetime.now(timezone.utc) + timedelta(seconds=30)).isoformat()

    def context(self, retriever):
        return LiveMemoryContext(retriever, "raw", tenant_id="t")

    def test_wrong_caller_and_expired_deadline_stop_before_retrieval(self):
        for principal in (
            SimpleNamespace(owner_id="other", tenant_id="t", project="p"),
            SimpleNamespace(owner_id="o", tenant_id="other", project="p"),
            SimpleNamespace(owner_id="o", tenant_id="t", project="other"),
        ):
            retriever = FakeRetriever(self.grants)
            context = self.context(retriever)
            with self.subTest(principal=principal), self.assertRaises(EvidenceError):
                asyncio.run(context.recall(principal, "r", "q", self.deadline))
            self.assertEqual((context.proposed, context.attempted, retriever.calls), (1, 0, 0))
        retriever = FakeRetriever(self.grants)
        context = self.context(retriever)
        with self.assertRaises(RetrievalUnavailable):
            asyncio.run(context.recall(self.principal, "r", "q", 0))
        self.assertEqual((context.proposed, context.attempted, retriever.calls), (1, 0, 0))

    def test_positive_caller_gets_context(self):
        retriever = FakeRetriever(self.grants)
        context = self.context(retriever)
        result = asyncio.run(context.recall(self.principal, "r", "q", self.deadline))
        self.assertEqual(len(result.items), 1)
        self.assertTrue(result.retrieval_receipt["runtime_verified"])
        self.assertEqual((context.proposed, context.attempted, context.admitted), (1, 1, 1))

    def test_synchronous_retrieval_does_not_block_event_loop(self):
        retriever = FakeRetriever(self.grants, delay=0.1)
        retriever.release.set()
        context = self.context(retriever)
        async def run():
            task = asyncio.create_task(context.recall(self.principal, "r", "q", self.deadline))
            await asyncio.sleep(0.02)
            self.assertFalse(task.done())
            return await task
        result = asyncio.run(run())
        self.assertEqual(len(result.items), 1)

    def test_cancellation_tracks_thread_until_actual_finish(self):
        retriever = FakeRetriever(self.grants, block=True)
        context = self.context(retriever)
        async def run():
            task = asyncio.create_task(context.recall(self.principal, "r", "q", self.deadline))
            await asyncio.sleep(0.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(context.active, 1)
            self.assertEqual(context.admitted, 0)
            retriever.release.set()
            for _ in range(20):
                if context.finished:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(context.active, 0)
            self.assertEqual(context.cancelled, 1)
        asyncio.run(run())

    def test_revocation_and_deadline_after_retrieval_block_admission(self):
        for revoke in (True, False):
            self.grants.allow("o", "p", "bge-m3/1024", 1)
            retriever = FakeRetriever(self.grants, block=True)
            context = self.context(retriever)
            deadline = self.deadline if revoke else \
                (datetime.now(timezone.utc) + timedelta(milliseconds=20)).isoformat()
            async def run():
                task = asyncio.create_task(context.recall(self.principal, "r", "q", deadline))
                await asyncio.sleep(0.05)
                if revoke:
                    self.grants.revoke("o", "p")
                else:
                    await asyncio.sleep(0.05)
                retriever.release.set()
                with self.assertRaises(AuthorizationError if revoke else RetrievalUnavailable):
                    await task
                self.assertEqual(context.admitted, 0)
            asyncio.run(run())

    def test_runtime_mismatch_is_not_admitted(self):
        retriever = FakeRetriever(self.grants, mismatch=True)
        context = self.context(retriever)
        with self.assertRaises(EvidenceError):
            asyncio.run(context.recall(self.principal, "r", "q", self.deadline))
        self.assertEqual(context.admitted, 0)
        self.assertGreaterEqual(retriever.runtime_checks, 1)


if __name__ == "__main__":
    unittest.main()
