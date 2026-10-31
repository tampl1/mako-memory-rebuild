import unittest

from src.live_retrieval import CallBudget, parse_score, validate_rerank_items
from src.safety import EvidenceError, RetrievalUnavailable


class LiveRetrievalOfflineTests(unittest.TestCase):
    def test_score_is_strict_json_in_range(self):
        self.assertEqual(parse_score('{"score": 0.75}'), 0.75)
        for text in ('', '0.5', '{"score": 2}', '{"score": true}', 'thinking'):
            with self.subTest(text=text), self.assertRaises(RetrievalUnavailable):
                parse_score(text)

    def test_call_budget_is_hard_cap(self):
        budget = CallBudget(maximum=2)
        budget.take("embedding")
        budget.take("rerank")
        self.assertEqual(budget.total, 2)
        with self.assertRaises(RetrievalUnavailable):
            budget.take("embedding")

    def test_rerank_ids_must_be_unique_bounded_and_from_candidates(self):
        def item(source_id):
            return {"ref_id": source_id, "fact_id": source_id,
                    "structured": {"source_event_ids": [source_id]}}
        self.assertTrue(validate_rerank_items([item("a"), item("b")], ["a", "b", "c"]))
        for items in (
            [item("a"), item("a")],
            [item("invented")],
            [item("a"), item("b"), item("c")],
            [{"ref_id": "a", "fact_id": "a", "structured": {"source_event_ids": ["b"]}}],
        ):
            with self.subTest(items=items), self.assertRaises(EvidenceError):
                validate_rerank_items(items, ["a", "b"])


if __name__ == "__main__":
    unittest.main()
