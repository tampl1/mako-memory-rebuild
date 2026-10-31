import unittest

from src.live_retrieval import CallBudget, parse_score
from src.safety import RetrievalUnavailable


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


if __name__ == "__main__":
    unittest.main()
