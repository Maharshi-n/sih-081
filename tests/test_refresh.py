"""Refresh staging protects the active dashboard until every step succeeds."""
import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sih081 import __main__ as cli
from sih081.confidence import assess
from sih081.forecast import confidence_calibration


class RefreshTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.archive = self.root / "data" / "archive"
        self.archive.mkdir(parents=True)
        (self.archive / "raw").mkdir()
        (self.archive / "raw" / "cached.json").write_bytes(b"cached source")
        (self.archive / "cases.csv").write_bytes(b"active cases")
        (self.archive / "manifest.json").write_bytes(b'{"cases": 1}')
        self.issued = self.root / "output" / "issued"
        self.issued.mkdir(parents=True)
        self.active = self.root / "output" / "state.json"
        self.active.write_bytes(b'{"coverage": {"end": "2026-04-25"}, "fixture": "active"}')
        self.latest = self.issued / "latest.json"
        self.latest.write_bytes(b'{"fixture": "active issue"}')
        self.old_forecast = self.issued / "forecast-old.json"
        self.old_forecast.write_bytes(b"immutable existing forecast")
        self.new_state = {"coverage": {"end": "2026-04-30"}, "fixture": "staged"}
        self.run = datetime(2026, 5, 1, tzinfo=timezone.utc)
        self.failure = None
        self.capture = {}

    def snapshots(self):
        paths = (self.archive / "cases.csv", self.archive / "manifest.json", self.active, self.latest, self.old_forecast)
        return {p: p.read_bytes() for p in paths}

    def fetch(self, start, days, regions, staging, window):
        self.capture["staging"] = staging
        self.assertEqual(window, "imd")
        self.assertTrue((staging / "raw" / "cached.json").exists())
        (staging / "cases.csv").write_bytes(b"staged cases")
        (staging / "raw" / "new.json").write_bytes(b"new real source fixture")
        if self.failure == "fetch":
            raise RuntimeError("fetch failed")
        return {"cases": 100, "kind": "fixture", "regions": regions}

    def evaluate(self, cases, output, manifest):
        self.capture["manifest"] = copy.deepcopy(manifest)
        output.mkdir()
        (output / "state.json").write_text(json.dumps(self.new_state), encoding="utf-8")
        if self.failure == "evaluate":
            raise RuntimeError("evaluate failed")
        return self.new_state

    def issue(self, run, regions, cases, state, output):
        output.mkdir()
        result = {"kind": "research_guidance", "run_initialized_at": run, "validation_state_sha256": hashlib.sha256(state.read_bytes()).hexdigest(),
                  "records": [{"region": r} for r in regions], "fixture": "staged issue"}
        filename = self.capture.get("forecast_filename", "forecast-new.json")
        (output / filename).write_bytes(b"new immutable forecast")
        (output / filename.replace(".json", ".csv")).write_bytes(b"new immutable csv")
        (output / "latest.json").write_text(json.dumps(result), encoding="utf-8")
        if self.failure == "issue":
            raise RuntimeError("issue failed")
        return result

    def invoke(self, regions=("Mumbai",)):
        argv = ["sih081", "refresh", "--run", self.run.isoformat(), "--regions", *regions]
        with patch.object(cli, "ROOT", self.root), patch("sys.argv", argv), \
             patch.object(cli, "fetch_cases", side_effect=self.fetch) as fetched, \
             patch.object(cli, "evaluate", side_effect=self.evaluate) as evaluated, \
             patch.object(cli, "issue", side_effect=self.issue) as issued:
            cli.main()
            return fetched.call_count, evaluated.call_count, issued.call_count

    def test_fetch_evaluate_and_issue_failures_preserve_active_snapshots(self):
        before = self.snapshots()
        for step in ("fetch", "evaluate", "issue"):
            with self.subTest(step=step):
                self.failure = step
                with self.assertRaisesRegex(RuntimeError, f"{step} failed"):
                    self.invoke()
                self.assertEqual(self.snapshots(), before)
                self.assertFalse(self.capture["staging"].exists())
                self.assertFalse((self.archive / "raw" / "new.json").exists())

    def test_success_promotes_staged_results_and_preserves_old_issues(self):
        self.assertEqual(self.invoke(("Mumbai", "Pune")), (1, 1, 1))
        self.assertEqual((self.archive / "cases.csv").read_bytes(), b"staged cases")
        self.assertEqual(json.loads(self.active.read_text()), self.new_state)
        self.assertEqual(json.loads(self.latest.read_text())["validation_state_sha256"], hashlib.sha256(self.active.read_bytes()).hexdigest())
        self.assertEqual(self.old_forecast.read_bytes(), b"immutable existing forecast")
        self.assertEqual((self.issued / "forecast-new.json").read_bytes(), b"new immutable forecast")
        self.assertEqual((self.archive / "raw" / "cached.json").read_bytes(), b"cached source")
        self.assertTrue((self.archive / "raw" / "new.json").exists())
        self.assertEqual(self.capture["manifest"]["previously_reviewed_test_through"], "2026-04-25")
        self.assertFalse(self.capture["staging"].exists())

    def test_conflicting_immutable_issue_aborts_promotion(self):
        self.capture["forecast_filename"] = self.old_forecast.name
        before = self.snapshots()
        with self.assertRaisesRegex(FileExistsError, "immutable"):
            self.invoke()
        self.assertEqual(self.snapshots(), before)
        self.assertFalse((self.archive / "raw" / "new.json").exists())

    def test_same_run_skip_requires_matching_state_hash_and_regions(self):
        valid = {"kind": "research_guidance", "run_initialized_at": self.run.isoformat(),
                 "validation_state_sha256": hashlib.sha256(self.active.read_bytes()).hexdigest(),
                 "records": [{"region": "Mumbai"}]}
        self.latest.write_text(json.dumps(valid))
        self.assertEqual(self.invoke(), (0, 0, 0))
        self.latest.write_text(json.dumps({**valid, "validation_state_sha256": "wrong"}))
        self.assertEqual(self.invoke(), (1, 1, 1))
        self.assertEqual(self.invoke(), (0, 0, 0))
        self.assertEqual(self.invoke(("Mumbai", "Pune")), (1, 1, 1))
        self.assertEqual(self.invoke(("Pune", "Mumbai")), (0, 0, 0))

    def test_matching_replay_does_not_skip_live_refresh(self):
        self.latest.write_text(json.dumps({"kind": "retrospective_replay", "run_initialized_at": self.run.isoformat(),
                                         "validation_state_sha256": hashlib.sha256(self.active.read_bytes()).hexdigest(),
                                         "records": [{"region": "Mumbai"}]}))
        self.assertEqual(self.invoke(), (1, 1, 1))
        self.assertEqual(json.loads(self.latest.read_text())["kind"], "research_guidance")

    def test_one_shot_snapshot_replace_failure_rolls_back_all_active_files(self):
        before = self.snapshots()
        original_replace = Path.replace
        for failing_destination in (self.active, self.latest):
            with self.subTest(destination=failing_destination.name):
                failed = []
                def replace(path, destination):
                    if Path(destination) == failing_destination and path.suffix == ".tmp" and not failed:
                        failed.append(True)
                        raise OSError("Fixture snapshot promotion failure")
                    return original_replace(path, destination)
                with patch.object(Path, "replace", new=replace):
                    with self.assertRaisesRegex(OSError, "promotion failure"):
                        self.invoke()
                self.assertEqual(failed, [True])
                self.assertEqual(self.snapshots(), before)
                self.assertFalse(self.capture["staging"].exists())


class ConfidenceCalibrationTest(unittest.TestCase):
    def test_development_only_minimum_evidence_and_subset_identity(self):
        cutoff = datetime(2026, 7, 1, tzinfo=timezone.utc)
        metrics = {"n": 120, "events": 22, "ece": .08}
        subset = {"n": 105, "events": 20, "ece": .17}
        product = {"variable": "rain", "lead_hours": 27, "selection_cutoff": cutoff.isoformat(),
                   "event_scores": {"validation": {"heavy": metrics}, "test": {"heavy": {"n": 10000, "events": 9000, "ece": 0}}},
                   "outages": {"AIFS": {"event_scores": {"validation": {"heavy": subset}}}}}
        full = confidence_calibration(product)
        self.assertEqual(full["ece"], .08)
        self.assertEqual(full["available_at"], cutoff.isoformat())
        self.assertEqual(confidence_calibration(product, "AIFS")["ece"], .17)
        self.assertIsNone(confidence_calibration(product, "GFS"))
        row = {"issue_time": cutoff - timedelta(hours=1), "region": "Mumbai", "variable": "rain", "lead_hours": 27,
               "IFS": 40, "GFS": 42, "AIFS": 41}
        self.assertIsNone(assess(row, [], {"IFS": 1}, 40, calibration=full)["factors"]["reliability"]["value"])
        row["issue_time"] = cutoff + timedelta(hours=1)
        self.assertIsNotNone(assess(row, [], {"IFS": 1}, 40, calibration=full)["factors"]["reliability"]["value"])
        for n, events in ((99, 22), (120, 19)):
            product["event_scores"]["validation"]["heavy"] = {**metrics, "n": n, "events": events}
            self.assertIsNone(confidence_calibration(product))


if __name__ == "__main__":
    unittest.main()
