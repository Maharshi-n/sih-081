import csv
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sih081.data import demo_cases, merge_observations, read_cases, write_cases, _check_payload
from sih081.freshness import source_freshness


class DataQualityTest(unittest.TestCase):
    def test_freshness_is_run_age_not_retrieval_age(self):
        run = datetime(2026, 9, 1, tzinfo=timezone.utc)
        for hours, expected in ((0, "fresh"), (12, "fresh"), (12.01, "slightly stale"), (24, "slightly stale"), (24.01, "too stale"), (-1, "invalid")):
            self.assertEqual(source_freshness(run, run + timedelta(hours=hours), "IFS")["status"], expected)
        self.assertEqual(source_freshness("2026-09-01T05:30:00+05:30", "2026-09-01T12:00:00Z", "GFS")["age_hours"], 12)
        self.assertEqual(source_freshness("2026-09-01T00:00:00", run, "AIFS")["status"], "invalid")

    def test_import_contract_and_unknown_support_not_verified(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = next(row for row in demo_cases(1) if row["variable"] == "rain")
            valid = datetime.fromisoformat(row["valid_time"])
            row.update(window_start=(valid - timedelta(hours=24)).isoformat(), window_end=valid.isoformat())
            write_cases(root / "cases.csv", [row])
            observation = {"region": row["region"], "latitude": row["latitude"], "longitude": row["longitude"], "variable": "rain",
                           "valid_time": row["valid_time"], "observation": 12, "observed_at": row["observed_at"], "source": "test gauge",
                           "quality": "pass", "window_start": row["window_start"], "window_end": row["window_end"], "unit": "mm/24h"}

            def write(item):
                with (root / "observations.csv").open("w", newline="", encoding="utf-8") as file:
                    writer = csv.DictWriter(file, fieldnames=item)
                    writer.writeheader()
                    writer.writerow(item)

            write(observation)
            manifest = merge_observations(root / "cases.csv", root / "observations.csv", root / "matched", strict=True)
            self.assertTrue(manifest["units_verified"])
            self.assertFalse(manifest["spatial_support_verified"])
            self.assertEqual(manifest["observation_latency_hours"]["median"], 12)
            self.assertEqual(read_cases(root / "matched" / "cases.csv")[0]["observation_support"], "unknown")
            self.assertTrue(read_cases(root / "matched" / "cases.csv")[0]["units_verified"])
            for key, value in (("latitude", "nan"), ("longitude", 181), ("unit", "mm"), ("quality", "suspect"), ("source", "ERA5 reanalysis"),
                               ("observed_at", (valid - timedelta(hours=1)).isoformat()),
                               ("window_start", (valid - timedelta(hours=23)).isoformat()),
                               ("representativeness_status", "verified")):
                write({**observation, key: value})
                with self.assertRaises(ValueError, msg=key):
                    merge_observations(root / "cases.csv", root / "observations.csv", root / "invalid", strict=True)
            legacy = {key: value for key, value in observation.items() if key != "unit"}
            write(legacy)
            self.assertFalse(merge_observations(root / "cases.csv", root / "observations.csv", root / "legacy")["units_verified"])
            with self.assertRaisesRegex(ValueError, "unit"):
                merge_observations(root / "cases.csv", root / "observations.csv", root / "strict", strict=True)

    def test_real_payload_cannot_silently_change_units_or_values(self):
        payload = {"utc_offset_seconds": 0, "hourly_units": {"temperature_2m": "°C", "wind_speed_10m": "m/s", "precipitation": "mm"},
                   "hourly": {"time": ["2026-05-01T00:00"], "temperature_2m": [25], "wind_speed_10m": [3], "precipitation": [0]}}
        _check_payload(payload)
        payload["hourly"]["precipitation"] = [float("nan")]
        with self.assertRaises(ValueError):
            _check_payload(payload)


if __name__ == "__main__":
    unittest.main()
