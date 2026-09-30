import unittest
from datetime import datetime, timedelta, timezone

from sih081.confidence import assess, summarize


class ConfidenceTest(unittest.TestCase):
    def setUp(self):
        self.issue = datetime(2026, 7, 1, tzinfo=timezone.utc)
        self.row = {"issue_time": self.issue, "region": "Mumbai", "variable": "rain", "lead_hours": 27,
                    "IFS": 40, "GFS": 42, "AIFS": 41, "observation": 300}
        self.history = [{**self.row, "issue_time": self.issue - timedelta(days=i + 3),
                         "observed_at": self.issue - timedelta(days=i + 1), "observation": 35 + i % 10}
                        for i in range(60)]
        self.weights = {"IFS": 1/3, "GFS": 1/3, "AIFS": 1/3}
        self.fresh = {s: {"run_initialized_at": (self.issue - timedelta(hours=6)).isoformat(),
                          "publication_time": (self.issue - timedelta(hours=3)).isoformat(),
                          "freshness": {"status": "fresh", "max_age_hours": 24}}
                      for s in self.weights}

    def test_causal_and_bounded(self):
        actual = assess(self.row, self.history, self.weights, 41, self.fresh)
        future = {**self.history[0], "observed_at": self.issue + timedelta(seconds=1), "observation": 99999}
        self.assertEqual(actual, assess({**self.row, "observation": -9999}, self.history + [future], self.weights, 41, self.fresh))
        self.assertGreaterEqual(actual["score"], 0)
        self.assertLessEqual(actual["score"], 100)
        self.assertEqual(actual["evidence_count"], 60)
        self.assertEqual(actual["status"], "experimental")
        self.assertIsNone(actual["factors"]["reliability"]["value"])
        incompatible = {**self.history[0], "region": "Pune", "observation": 99999}
        self.assertEqual(actual, assess(self.row, self.history + [incompatible], self.weights, 41, self.fresh))

    def test_missing_stale_and_retrieval_are_not_freshness(self):
        fresh = assess(self.row, self.history, self.weights, 41, self.fresh)
        stale = {s: {**meta, "run_initialized_at": (self.issue - timedelta(hours=60)).isoformat(),
                      "retrieved_at": self.issue.isoformat()} for s, meta in self.fresh.items()}
        self.assertLess(assess(self.row, self.history, self.weights, 41, stale)["score"], fresh["score"])
        self.assertLess(assess({**self.row, "AIFS": None}, self.history, self.weights, 41, self.fresh)["score"], fresh["score"])
        self.assertLess(assess(self.row, self.history, self.weights, 41)["score"], fresh["score"])
        self.assertEqual(assess(self.row, [], {}, None)["score"], 0)
        future = {s: {**meta, "publication_time": (self.issue + timedelta(hours=1)).isoformat()}
                  for s, meta in self.fresh.items()}
        self.assertEqual(assess(self.row, self.history, self.weights, 41, future)["factors"]["freshness"]["value"], 0)

    def test_release_gates_calibration(self):
        calibration = {"n": 40, "calibration_error": .1, "available_at": self.issue + timedelta(hours=1)}
        self.assertIsNone(assess(self.row, self.history, self.weights, 41, calibration=calibration)["factors"]["reliability"]["value"])
        calibration["available_at"] = self.issue - timedelta(hours=1)
        self.assertIsNotNone(assess(self.row, self.history, self.weights, 41, calibration=calibration)["factors"]["reliability"]["value"])
        calibration["region"] = "Pune"
        self.assertIsNone(assess(self.row, self.history, self.weights, 41, calibration=calibration)["factors"]["reliability"]["value"])

    def test_fixed_bins_and_tied_ranks(self):
        rows = [{"variable": "rain", "lead_hours": 27, "confidence": {"score": s}, "point": e, "observation": 0}
                for s, e in ((10, 8), (20, 6), (50, 4), (90, 2), (100, 0))]
        result = summarize(rows)
        self.assertEqual([b["n"] for b in result["bins"]], [2, 1, 2])
        self.assertEqual([b["mae"] for b in result["bins"]], [7, 4, 1])
        self.assertEqual(result["score_error_rank_association"], -1)
        self.assertFalse(result["approved"])
        self.assertIsNone(summarize([rows[0]] * 3)["score_error_rank_association"])
        mixed = summarize(rows + [{**rows[0], "variable": "wind"}])
        self.assertIsNone(mixed["score_error_rank_association"])
        self.assertEqual(len(mixed["by_product"]), 2)


if __name__ == "__main__":
    unittest.main()
