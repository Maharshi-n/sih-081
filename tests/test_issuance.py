"""Issuance boundary checks using local fixtures and the real inference function."""
import copy
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sih081.data import PLACES, SOURCES, demo_cases, read_cases, write_cases
from sih081.forecast import candidate_predictions
from sih081.issue import issue


class IssuanceTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.history = self.root / "history.csv"
        rows = demo_cases(60)
        # This deliberately hostile post-training observation must never enter issuance.
        rows.append({**rows[0], "issue_time": "2026-04-27T00:00:00+00:00",
                     "valid_time": "2026-04-28T00:00:00+00:00", "observed_at": "2026-04-28T12:00:00+00:00",
                     "observation": 999999})
        write_cases(self.history, rows)
        self.state_path = self.root / "state.json"
        self.output = self.root / "issued"
        self.run = datetime(2026, 5, 1, tzinfo=timezone.utc)
        self.when = self.run + timedelta(hours=6)
        self.cutoff = datetime(2025, 4, 1, tzinfo=timezone.utc)
        thresholds = [0.1, 64.5, 115.6, 204.5]
        identity = [{"bias": 0, "beta": [0, 1, 0, 0, 0]} for _ in thresholds]
        subset_calibration = [{"bias": .2, "beta": [0, 1, 0, 0, 0]} for _ in thresholds]
        denied = {k: {"approved": False, "reasons": ["Fixture has no event approval"]}
                  for k in ("heavy", "very_heavy", "extremely_heavy")}
        self.product = {"variable": "rain", "lead_hours": 24, "thresholds": thresholds,
                        "deployment_point_method": "equal", "deployment_probability_method": "raw",
                        "event_approval": copy.deepcopy(denied),
                        "models": {"history_before": self.cutoff.isoformat(), "model_fit_before": self.cutoff.isoformat(),
                                   "fixed_weights": {s: 1 / 3 for s in SOURCES},
                                   "router": {"as_of": self.cutoff.isoformat(), "regions": list(PLACES),
                                              "task": ["rain", 24], "trained": False, "sources": {}},
                                   "rainfall": {"as_of": self.cutoff.isoformat(), "lead_hours": 24, "trained": False},
                                   "calibration": {"raw": identity, "router_raw": identity},
                                   "subsets": {s: {"calibration": subset_calibration} for s in SOURCES}},
                        "outages": {s: {"approved": True, "probability_method": "pbc", "event_approval": copy.deepcopy(denied)}
                                    for s in SOURCES}}
        self.state = {"schema_version": 2, "data_kind": "quality_checked_observations", "truth": "local test fixture",
                      "manifest": {"cases_sha256": hashlib.sha256(self.history.read_bytes()).hexdigest()},
                      "verification_policy": {"selection_cutoff": self.cutoff.isoformat()},
                      "coverage": {"regions": ["Mumbai"]}, "results": [self.product]}
        self.save_state()

    def save_state(self):
        self.state_path.write_text(json.dumps(self.state, allow_nan=False), encoding="utf-8")

    def payload(self, source):
        i = list(SOURCES).index(source)
        return {"utc_offset_seconds": 0, "latitude": PLACES["Mumbai"][0], "longitude": PLACES["Mumbai"][1],
                "hourly_units": {"temperature_2m": "°C", "wind_speed_10m": "m/s", "precipitation": "mm"},
                "hourly": {"time": [(self.run + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in range(76)],
                           "temperature_2m": [25.0 + i] * 76, "wind_speed_10m": [5.0 + i] * 76,
                           "precipitation": [1.0 + i] * 76}}

    def invoke(self, **options):
        return issue(self.run.isoformat(), ["Mumbai"], self.history, self.state_path, self.output,
                     as_of=options.pop("as_of", self.when), replay=options.pop("replay", True), **options)

    def expected(self, missing=None):
        history = [r for r in read_cases(self.history) if r["issue_time"] < self.cutoff and r["observed_at"] < self.cutoff]
        row = {"issue_time": self.run, "valid_time": self.run + timedelta(hours=24), "observed_at": None,
               "region": "Mumbai", "variable": "rain", "lead_hours": 24, "observation": None,
               "source_run_times": {s: self.run for s in SOURCES},
               **{s: (None if s == missing else 24.0 * (1 + list(SOURCES).index(s))) for s in SOURCES}}
        return candidate_predictions(row, history, self.product, missing)

    def test_real_inference_path_frozen_history_hashes_and_immutability(self):
        with patch("sih081.issue.fetch_run", side_effect=lambda run, region, lat, lon, source: self.payload(source)) as fetched:
            result = self.invoke()
            record = result["records"][0]
            expected = self.expected()
            self.assertEqual(record["point"], round(expected["points"]["equal"], 3))
            self.assertEqual(record["weights"], expected["point_weights"]["equal"])
            self.assertIsNone(record["hazard_probability"])
            self.assertEqual(record["event_reasons"]["heavy"], ["Fixture has no event approval"])
            self.assertEqual(result["kind"], "retrospective_replay")
            self.assertEqual(result["validation_state_sha256"], hashlib.sha256(self.state_path.read_bytes()).hexdigest())
            for source, metadata in record["source_status"].items():
                raw = (self.output / metadata["payload_file"]).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), metadata["payload_sha256"])
                self.assertEqual(json.loads(raw), self.payload(source))
            self.assertEqual(fetched.call_count, 3)
            with self.assertRaises(FileExistsError):
                self.invoke()
            self.assertEqual(fetched.call_count, 3)

    def test_missing_source_uses_own_subset_cdf_and_event_gate(self):
        self.product["outages"]["AIFS"]["event_approval"]["heavy"] = {"approved": True, "reasons": []}
        self.save_state()
        def fetch(run, region, lat, lon, source):
            if source == "AIFS":
                raise RuntimeError("Fixture source outage")
            return self.payload(source)
        with patch("sih081.issue.fetch_run", side_effect=fetch):
            record = self.invoke()["records"][0]
        expected = self.expected("AIFS")
        self.assertEqual(record["missing_sources"], ["AIFS"])
        self.assertEqual(record["point"], round(expected["points"]["equal"], 3))
        self.assertEqual(record["hazard_probability"], round(1 - expected["cdfs"]["pbc"][1], 4))
        self.assertEqual(record["probability_method"], "pbc")
        self.assertEqual(record["probability_weights"], expected["probability_weights"]["pbc"])
        self.assertNotEqual(expected["cdfs"]["pbc"], expected["cdfs"]["raw"])
        self.assertEqual(record["status"], "research subset guidance")
        self.assertIsNone(record["event_probabilities"]["very_heavy"])

    def test_stale_inputs_are_excluded_without_fetching(self):
        with patch("sih081.issue.fetch_run") as fetched:
            result = self.invoke(as_of=self.run + timedelta(hours=25))
            fetched.assert_not_called()
        self.assertEqual(result["records"][0]["reason"], "No usable fresh source inputs")
        self.assertEqual(result["records"][0]["missing_sources"], list(SOURCES))
        self.assertTrue(all(m["freshness"]["status"] == "too stale" for m in result["records"][0]["source_status"].values()))

    def test_payload_timezone_and_unit_qa(self):
        for field, bad in (("utc_offset_seconds", 19800), ("hourly_units", {"temperature_2m": "°C", "wind_speed_10m": "km/h", "precipitation": "mm"})):
            with self.subTest(field=field):
                payload = self.payload("IFS")
                payload[field] = bad
                self.output = self.root / field
                with patch("sih081.issue.fetch_run", return_value=payload):
                    result = self.invoke()
                self.assertEqual(len(result["source_errors"]), 3)
                self.assertIsNone(result["records"][0]["point"])
                self.assertEqual(result["records"][0]["status"], "unavailable")

    def test_time_synthetic_and_hash_boundaries_refuse_before_fetch(self):
        with patch("sih081.issue.fetch_run") as fetched:
            with self.assertRaisesRegex(ValueError, "publication delay"):
                self.invoke(as_of=self.run + timedelta(hours=5))
            with self.assertRaisesRegex(ValueError, "only with explicitly labelled replay"):
                self.invoke(replay=False)
            with self.assertRaisesRegex(ValueError, "timezone"):
                self.invoke(as_of=datetime(2026, 5, 1, 6))
            with self.assertRaisesRegex(ValueError, "publication delay"):
                issue("2099-05-01T00:00:00Z", ["Mumbai"], self.history, self.state_path, self.output)
            self.state["verification_policy"]["selection_cutoff"] = "2026-06-01T00:00:00+00:00"
            self.save_state()
            with self.assertRaisesRegex(ValueError, "retrospective leakage"):
                self.invoke()
            self.state["verification_policy"]["selection_cutoff"] = self.cutoff.isoformat()
            self.state["data_kind"] = "synthetic_demo"
            self.save_state()
            with self.assertRaisesRegex(ValueError, "synthetic"):
                self.invoke()
            self.state["data_kind"] = "quality_checked_observations"
            self.state["manifest"]["cases_sha256"] = "wrong"
            self.save_state()
            with self.assertRaisesRegex(ValueError, "differs"):
                self.invoke()
            fetched.assert_not_called()


if __name__ == "__main__":
    unittest.main()
