import tempfile
import unittest
import csv
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sih081 import __main__ as cli
from sih081.data import demo_cases, merge_observations, read_cases, write_cases
from sih081.forecast import available_history, evaluate, monotone, paired_interval


class PipelineTest(unittest.TestCase):
    def test_demo_does_not_replace_real_dashboard_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / "output" / "state.json"
            state.parent.mkdir()
            state.write_text('real-data-sentinel')
            with patch.object(cli, "ROOT", root), patch("sys.argv", ["sih081", "demo", "--days", "1"]), patch.object(cli, "evaluate", return_value={"results": [], "cases": []}) as evaluate_mock:
                cli.main()
            self.assertEqual(evaluate_mock.call_args.args[1], root / "output" / "demo")
            self.assertEqual(state.read_text(), 'real-data-sentinel')

    def test_time_gate_and_probability_order(self):
        issue = datetime(2026, 5, 1, tzinfo=timezone.utc)
        current = {"issue_time": issue, "variable": "rain", "lead_hours": 24}
        past = {"issue_time": issue - timedelta(days=2), "observed_at": issue - timedelta(hours=1),
                "variable": "rain", "lead_hours": 24, "IFS": 3.0, "GFS": None, "AIFS": None}
        future_release = {**past, "observed_at": issue + timedelta(hours=1)}
        self.assertEqual(available_history([past, future_release], current), [past])
        projected = monotone([0.9, 0.2, 1.4, -0.3])
        self.assertEqual(projected, sorted(projected))
        self.assertTrue(all(0 <= x <= 1 for x in projected))
        rows = [{"issue_time": issue + timedelta(days=i), "observation": float(i), "a": float(i), "b": float(i)} for i in range(10)]
        self.assertEqual(paired_interval(rows, "a", "b", "point")["high"], 0)

    def test_end_to_end_demo_is_labeled_and_tested(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = Path(tmp) / "cases.csv"
            out = Path(tmp) / "out"
            write_cases(cases, demo_cases(125))
            self.assertEqual(len(read_cases(cases)), 125 * 4 * 2 * 3)
            report = evaluate(cases, out, {"kind": "synthetic_demo", "truth": "synthetic"})
            self.assertEqual(report["data_kind"], "synthetic_demo")
            self.assertEqual(len(report["results"]), 6)
            self.assertTrue(report["cases"])
            self.assertTrue((out / "state.json").exists())
            for product in report["results"]:
                self.assertIn(product["point_method"], ("equal", "fixed", "adaptive", "IFS_corrected", "GFS_corrected", "AIFS_corrected"))
                self.assertIn(product["probability_method"], ("raw", "pbc"))
                self.assertGreater(product["test_n"], 0)
                self.assertIn("reliability", product["test"]["probability"]["raw"])

    def test_observations_require_exact_rain_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "base.csv"
            row = next(x for x in demo_cases(1) if x["variable"] == "rain")
            valid = datetime.fromisoformat(row["valid_time"])
            row["window_start"] = (valid - timedelta(hours=24)).isoformat()
            row["window_end"] = valid.isoformat()
            write_cases(base, [row])
            observation = root / "observations.csv"
            item = {"region": row["region"], "latitude": row["latitude"], "longitude": row["longitude"],
                    "variable": "rain", "valid_time": row["valid_time"], "observation": "12",
                    "observed_at": row["observed_at"], "source": "test gauge", "quality": "pass",
                    "window_start": row["window_start"], "window_end": row["window_end"]}
            with observation.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=item)
                writer.writeheader(); writer.writerow(item)
            manifest = merge_observations(base, observation, root / "matched")
            self.assertEqual(manifest["cases"], 1)
            self.assertEqual(read_cases(root / "matched" / "cases.csv")[0]["observation"], 12)
            item["window_start"] = (valid - timedelta(hours=23)).isoformat()
            with observation.open("w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=item)
                writer.writeheader(); writer.writerow(item)
            with self.assertRaisesRegex(ValueError, "rain requires exact 24 h window"):
                merge_observations(base, observation, root / "invalid")


if __name__ == "__main__":
    unittest.main()
