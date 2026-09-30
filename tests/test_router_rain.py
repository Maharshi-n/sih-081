"""Leakage and probability-contract checks for the learned candidates."""
import copy
import json
import unittest
from datetime import datetime, timedelta, timezone

from sih081.rainfall import WET_CUTOFF, fit_rainfall, rainfall_cdf
from sih081.router import disagreement, features, fit_router, router_weights


def cases(days=100):
    start = datetime(2026, 4, 1, tzinfo=timezone.utc)
    rows = []
    for day in range(days):
        for region, extra in (("Mumbai", 0), ("Pune", 2)):
            amount = 0.0 if day % 4 == 0 else float((day * 13) % 65 + extra + 0.1)
            issue = start + timedelta(days=day)
            rows.append({"issue_time": issue, "valid_time": issue + timedelta(hours=27),
                         "observed_at": issue + timedelta(days=2), "variable": "rain", "lead_hours": 27,
                         "region": region, "observation": amount,
                         "IFS": max(0, amount + (day % 3 - 1) * 3),
                         "GFS": max(0, amount + (day % 7 - 3) * 5),
                         "AIFS": max(0, amount + (day % 5 - 2) * 2)})
    return rows


class RouterRainTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = cases()
        cls.as_of = cls.rows[-1]["issue_time"] + timedelta(days=3)
        cls.router = fit_router(cls.rows, cls.as_of)
        cls.rain = fit_rainfall(cls.rows, cls.as_of)
        cls.current = {**cls.rows[-2], "issue_time": cls.as_of + timedelta(days=1),
                       "valid_time": cls.as_of + timedelta(days=2, hours=3),
                       "IFS": 30.0, "GFS": 55.0, "AIFS": 35.0}

    def test_frozen_fit_excludes_future_and_exact_cutoff_labels(self):
        forbidden = [{**self.rows[0], "observation": 1e6, "observed_at": self.as_of},
                     {**self.rows[0], "observation": 1e6, "observed_at": self.as_of + timedelta(days=1)}]
        self.assertEqual(fit_router(self.rows + forbidden, self.as_of), self.router)
        self.assertEqual(fit_rainfall(self.rows + forbidden, self.as_of), self.rain)
        json.dumps(self.router, allow_nan=False)
        json.dumps(self.rain, allow_nan=False)
        self.assertTrue(self.router["trained"])
        self.assertTrue(self.rain["trained"])

    def test_current_truth_and_unreleased_history_never_change_features(self):
        current = {**self.current, "observation": 1e9, "observed_at": self.current["issue_time"] - timedelta(days=1)}
        unreleased = {**self.rows[0], "observed_at": self.current["issue_time"], "observation": 1e8}
        f = features(self.current, self.rows, self.router["regions"])
        self.assertEqual(f, features(current, self.rows + [current, unreleased], self.router["regions"]))
        baseline = {"IFS": 0.4, "GFS": 0.2, "AIFS": 0.4}
        first = router_weights(self.current, self.rows, self.router, baseline)
        self.assertEqual(first, router_weights(current, self.rows + [current, unreleased], self.router, baseline))
        weights = first[0]
        thresholds = [-1, 0, WET_CUTOFF, 10, 64.5, 115.6, 204.5]
        self.assertEqual(rainfall_cdf(self.current, self.rows, weights, thresholds, self.rain),
                         rainfall_cdf(current, self.rows + [current, unreleased], weights, thresholds, self.rain))
        self.assertTrue(all(x >= 0 for x in weights.values()))
        self.assertAlmostEqual(sum(weights.values()), 1)
        self.assertTrue(first[1]["predicted_errors"])
        self.assertTrue(all(x >= 0 for x in first[1]["predicted_errors"].values()))
        self.assertGreater(first[1]["shrinkage"], 0)
        self.assertLess(first[1]["shrinkage"], 1)
        self.assertEqual(f["age_known_IFS"], 0)
        self.assertEqual(features({**current, "source_age_hours": {"IFS": 6}}, self.rows)["age_hours_IFS"], 6)

    def test_outage_requires_training_and_excludes_missing_source(self):
        outage = {**self.current, "AIFS": None}
        baseline = {"IFS": 0.4, "GFS": 0.2, "AIFS": 0.4}
        weights, why = router_weights(outage, self.rows, self.router, baseline)
        self.assertEqual(set(weights), {"IFS", "GFS"})
        self.assertAlmostEqual(sum(weights.values()), 1)
        self.assertEqual(why["shrinkage"], 0)
        self.assertIsNone(rainfall_cdf(outage, self.rows, weights, [0.1, 64.5], self.rain))
        masked = [{**r, "AIFS": None} for r in self.rows]
        fit = fit_router(masked, self.as_of)
        learned, why = router_weights(outage, masked, fit, baseline)
        self.assertEqual(set(learned), {"IFS", "GFS"})
        self.assertGreater(why["shrinkage"], 0)
        self.assertIsNotNone(rainfall_cdf(outage, masked, learned, [0.1, 64.5], fit_rainfall(masked, self.as_of)))

    def test_rainfall_is_monotone_bounded_and_handles_dry_class(self):
        weights = {"IFS": 0.4, "GFS": 0.2, "AIFS": 0.4}
        thresholds = [-1, 0, 0.05, WET_CUTOFF, 1, 10, 64.5, 115.6, 204.5, float("inf")]
        for value in (0.0, 50.0, 200.0):
            row = {**self.current, "IFS": value, "GFS": value, "AIFS": value}
            cdf = rainfall_cdf(row, self.rows, weights, thresholds, self.rain)
            self.assertEqual(cdf, sorted(cdf))
            self.assertTrue(all(0 <= x <= 1 for x in cdf))
            self.assertEqual(cdf[:2], [0, 0])
            self.assertEqual(cdf[2], cdf[3])
            self.assertAlmostEqual(cdf[-1], 1)
        dry_row = {**self.current, "IFS": 0.0, "GFS": 0.0, "AIFS": 0.0}
        wet_row = {**self.current, "IFS": 50.0, "GFS": 50.0, "AIFS": 50.0}
        self.assertGreater(rainfall_cdf(dry_row, self.rows, weights, [0.1], self.rain)[0],
                           rainfall_cdf(wet_row, self.rows, weights, [0.1], self.rain)[0])
        self.assertEqual(disagreement(dry_row)["range"], 0)
        self.assertGreater(disagreement(self.current)["std"], 0)
        with self.assertRaises(ValueError):
            rainfall_cdf(self.current, self.rows, weights, [64.5, 0.1], self.rain)
        with self.assertRaises(ValueError):
            rainfall_cdf(self.current, self.rows, {"IFS": float("inf")}, [0.1], self.rain)
        with self.assertRaises(ValueError):
            router_weights(self.current, self.rows, self.router, {"IFS": -1})

    def test_sparse_evidence_and_future_fit_withhold_candidates(self):
        sparse = self.rows[:10]
        fit = fit_router(sparse, self.as_of)
        weights, why = router_weights(self.current, sparse, fit, {"IFS": 1, "GFS": 1, "AIFS": 1})
        self.assertEqual(why["shrinkage"], 0)
        self.assertAlmostEqual(sum(weights.values()), 1)
        self.assertIsNone(rainfall_cdf(self.current, sparse, weights, [0.1, 64.5], fit_rainfall(sparse, self.as_of)))
        future = copy.deepcopy(self.rain)
        future["as_of"] = (self.current["issue_time"] + timedelta(days=1)).isoformat()
        self.assertIsNone(rainfall_cdf(self.current, self.rows, weights, [0.1, 64.5], future))


if __name__ == "__main__":
    unittest.main()
