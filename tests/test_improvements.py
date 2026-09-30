"""Integration checks for frozen selection, common cases and probability isolation."""
import json
import math
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from sih081.data import SOURCES, demo_cases, read_cases, write_cases
from sih081.forecast import candidate_predictions, evaluate


class ImprovementIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.original = cls.root / "original.csv"
        rows = demo_cases(125)
        write_cases(cls.original, rows)
        dates = sorted({datetime.fromisoformat(r["issue_time"]).date() for r in rows})
        test_from = dates[int(len(dates) * .8)]
        mutated = [{**r, "observation": 100000.0} if datetime.fromisoformat(r["issue_time"]).date() >= test_from else r
                   for r in rows]
        cls.changed = cls.root / "changed.csv"
        write_cases(cls.changed, mutated)
        manifest = {"kind": "synthetic_demo", "truth": "synthetic test fixture"}
        cls.before = evaluate(cls.original, cls.root / "before", manifest)
        cls.after = evaluate(cls.changed, cls.root / "after", manifest)
        cls.loaded = read_cases(cls.original)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_final_test_outcomes_never_select_or_train(self):
        self.assertEqual(self.before["split"], self.after["split"])
        self.assertEqual(len(self.before["results"]), 6)
        for before, after in zip(self.before["results"], self.after["results"]):
            self.assertEqual(before["models"], after["models"])
            for field in ("point_method", "probability_method", "deployment_point_method", "deployment_probability_method"):
                self.assertEqual(before[field], after[field])
            self.assertEqual(before["validation"], after["validation"])
            self.assertEqual(before["selection_basis"], "development only")
            for source in SOURCES:
                for field in ("approved", "probability_method", "validation", "probability_validation"):
                    self.assertEqual(before["outages"][source][field], after["outages"][source][field])
        self.assertNotEqual(self.before["results"][0]["test"], self.after["results"][0]["test"])
        self.assertEqual(len(self.before["cases"]), len(self.after["cases"]))
        for before, after in zip(self.before["cases"], self.after["cases"]):
            for field in ("point", "hazard_probability", "event_probabilities", "weights", "probability_weights",
                          "outage_points", "outage_probabilities", "outage_weights", "confidence", "outage_confidence"):
                self.assertEqual(before[field], after[field])

    def test_common_ablation_cases_and_probability_correction_does_not_change_points(self):
        for product in self.before["results"]:
            self.assertEqual({m["n"] for m in product["test"]["point"].values()}, {product["test_n"]})
            self.assertEqual({m["n"] for m in product["test"]["probability"].values()}, {product["test_n"]})
            for method in product["ablation"]:
                self.assertEqual(method["probability"]["n"], product["test_n"])
                if method["point"] is not None:
                    self.assertEqual(method["point"]["n"], product["test_n"])
            ablation = {r["method"]: r for r in product["ablation"]}
            self.assertEqual(ablation["Forecast router"]["point"], ablation["Router + PBC"]["point"])

    def test_subset_distributions_are_bounded_monotone_and_use_own_calibration(self):
        for product in self.before["results"]:
            history = [r for r in self.loaded if r["variable"] == product["variable"]
                       and r["lead_hours"] == product["lead_hours"]
                       and r["observed_at"] < datetime.fromisoformat(product["models"]["history_before"])]
            row = next(r for r in self.loaded if r["variable"] == product["variable"]
                       and r["lead_hours"] == product["lead_hours"]
                       and r["issue_time"].date().isoformat() >= self.before["split"]["test_from"])
            for missing in SOURCES:
                predicted = candidate_predictions(row, history, product, missing)
                self.assertNotIn(missing, predicted["point_weights"]["equal"])
                self.assertAlmostEqual(sum(predicted["point_weights"]["equal"].values()), 1)
                self.assertIn(missing, product["models"]["subsets"])
                for name, cdf in predicted["cdfs"].items():
                    self.assertIsNotNone(cdf, name)
                    self.assertEqual(len(cdf), len(product["thresholds"]))
                    self.assertTrue(all(math.isfinite(p) and 0 <= p <= 1 for p in cdf))
                    self.assertEqual(cdf, sorted(cdf))

    def test_saved_models_and_report_are_finite_json(self):
        state = self.root / "before" / "state.json"
        saved = json.loads(state.read_text(encoding="utf-8"), parse_constant=lambda value: self.fail(value))
        self.assertEqual(saved, self.before)
        for product in saved["results"]:
            self.assertEqual(json.loads(json.dumps(product["models"], allow_nan=False)), product["models"])
            self.assertFalse(product["confidence_evaluation"]["test"]["approved"])


if __name__ == "__main__":
    unittest.main()
