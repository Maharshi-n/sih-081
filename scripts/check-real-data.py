"""Audit saved real values, payloads and evaluation/issuance provenance; no network."""
import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sih081.data import SOURCES, VARIABLES, _check_payload, read_cases
from sih081.freshness import source_freshness


def target_times(run, variable, lead):
    hours = range(lead - 23, lead + 1) if variable == "rain" else [lead]
    return [(run + timedelta(hours=hour)).strftime("%Y-%m-%dT%H:%M") for hour in hours]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-only", action="store_true", help="Check archive before replacing evaluated or issued snapshots")
    args = parser.parse_args()
    archive = ROOT / "data" / "archive"
    manifest = json.loads((archive / "manifest.json").read_bytes())
    assert manifest["kind"] == "retrospective_reanalysis", "Expected the clearly labeled model/ERA5 archive"
    assert manifest["model_ids"] == SOURCES
    case_hash = hashlib.sha256((archive / "cases.csv").read_bytes()).hexdigest()
    assert case_hash == manifest["cases_sha256"]
    for name, digest in manifest.get("raw_payload_sha256", {}).items():
        path = (archive / "raw" / name).resolve()
        assert path.is_relative_to((archive / "raw").resolve()), "Unsafe archive payload path"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest, name

    @lru_cache(maxsize=None)
    def raw(filename):
        payload = json.loads((archive / "raw" / filename).read_bytes())
        _check_payload(payload)
        hourly = payload["hourly"]
        return hourly, {time: index for index, time in enumerate(hourly["time"])}

    def expected(series, lookup, variable, times):
        if any(time not in lookup for time in times):
            return None
        values = [series[VARIABLES[variable][0]][lookup[time]] for time in times]
        return None if any(value is None for value in values) else sum(values) if variable == "rain" else values[0]

    end = (datetime.fromisoformat(manifest["start"]) + timedelta(days=manifest["days"] + 4)).date()
    rows = read_cases(archive / "cases.csv")
    assert len(rows) == manifest["cases"]
    checked = missing = 0
    failed = {(item["region"], item["source"], item["run"][:10]) for item in manifest["failed_requests"]}
    for row in rows:
        run, variable, lead = row["issue_time"], row["variable"], row["lead_hours"]
        times = target_times(run, variable, lead)
        assert row["observation_source"] == "Open-Meteo ERA5 reanalysis"
        assert not row.get("unit") or row["unit"] == VARIABLES[variable][1]
        if variable == "rain":
            assert row["window_end"] == row["valid_time"]
            assert row["window_end"] - row["window_start"] == timedelta(hours=24)
            if manifest["rain_window"].startswith("03:00"):
                assert row["window_end"].astimezone(timezone.utc).hour == 3
        for name in ["observation", *SOURCES]:
            filename = (f"{row['region']}-ERA5-{manifest['start']}-{end}.json" if name == "observation"
                        else f"{row['region']}-{name}-{run.date()}.json")
            if not (archive / "raw" / filename).exists():
                assert name != "observation" and row[name] is None and (row["region"], name, str(run.date())) in failed
                missing += 1
                continue
            series, lookup = raw(filename)
            value = expected(series, lookup, variable, times)
            if row[name] is None:
                assert name != "observation" and value is None, "Usable source values were silently dropped"
                missing += 1
            else:
                assert value is not None and math.isclose(row[name], value, rel_tol=1e-10, abs_tol=1e-10), (row["region"], run, name)
                checked += 1
    report = {"checked_at": datetime.now(timezone.utc).isoformat(), "data_kind": manifest["kind"],
              "regions": manifest["regions"], "archive_cases": len(rows), "values_matched_to_cached_responses": checked,
              "missing_source_values": missing, "raw_response_files_checked": raw.cache_info().currsize,
              "raw_response_hashes_checked": len(manifest.get("raw_payload_sha256", {})),
              "cases_sha256": case_hash, "reference": manifest["truth"],
              "limits": "Checks saved downloads and provenance; ERA5 is not station observation or independent provider authentication."}
    if not args.archive_only:
        state_bytes = (ROOT / "output" / "state.json").read_bytes()
        state = json.loads(state_bytes)
        issued_root = ROOT / "output" / "issued"
        issued = json.loads((issued_root / "latest.json").read_bytes())
        assert state["data_kind"] == manifest["kind"]
        assert state["manifest"]["cases_sha256"] == case_hash
        assert issued["validation_state_sha256"] == hashlib.sha256(state_bytes).hexdigest(), "Issued guidance uses a different evaluation"
        assert issued["model_ids"] == SOURCES
        by_key = {(row["region"], row["issue_time"].isoformat(), row["variable"], row["lead_hours"]): row for row in rows}
        assert len(by_key) == len(rows), "Duplicate archive targets"
        for case in state["cases"]:
            row = by_key[case["region"], case["issue_time"], case["variable"], case["lead_hours"]]
            assert case["valid_time"] == row["valid_time"].isoformat()
            assert math.isclose(case["observation"], row["observation"])
            assert all(value == row[name] for name, value in case["sources"].items())
        issued_payloads, issued_values = set(), 0
        if issued.get("schema_version", 1) >= 2:
            for record in issued["records"]:
                variable, lead = record["variable"], record["lead_hours"]
                run = datetime.fromisoformat(record["run_initialized_at"])
                assert datetime.fromisoformat(record["valid_time"]) == run + timedelta(hours=lead)
                if variable == "rain":
                    assert datetime.fromisoformat(record["window_end"]) == run + timedelta(hours=lead)
                    assert datetime.fromisoformat(record["window_end"]) - datetime.fromisoformat(record["window_start"]) == timedelta(hours=24)
                for name, status in record["source_status"].items():
                    if status.get("payload_file"):
                        path = (issued_root / status["payload_file"]).resolve()
                        assert path.is_relative_to(issued_root.resolve()), "Unsafe issued payload path"
                        payload_bytes = path.read_bytes()
                        assert hashlib.sha256(payload_bytes).hexdigest() == status["payload_sha256"], "Issued source hash mismatch"
                        payload = json.loads(payload_bytes)
                        _check_payload(payload)
                        issued_payloads.add(str(path))
                        if record["sources"].get(name) is not None:
                            series = payload["hourly"]
                            lookup = {time: index for index, time in enumerate(series["time"])}
                            value = expected(series, lookup, variable, target_times(run, variable, lead))
                            assert value is not None and math.isclose(record["sources"][name], value, rel_tol=1e-10, abs_tol=1e-10)
                            issued_values += 1
                    elif record["sources"].get(name) is not None:
                        raise AssertionError("Issued source value lacks a saved real payload")
                    freshness = status.get("freshness")
                    if freshness:
                        actual = source_freshness(status["run_initialized_at"], freshness["as_of"], name)
                        assert actual["status"] == freshness["status"] and actual["age_hours"] == freshness["age_hours"]
                        if freshness["status"] in ("invalid", "too stale"):
                            assert record["sources"].get(name) is None, "Stale source was used"
        report.update(displayed_cases=len(state["cases"]), issued_records=len(issued["records"]),
                      issued_source_payloads_checked=len(issued_payloads), issued_source_values_checked=issued_values,
                      issued_payload_verification="checked" if issued.get("schema_version", 1) >= 2 else "legacy snapshot lacks saved issue payloads")
    output = ROOT / "output" / ("archive-audit.json" if args.archive_only else "data-audit.json")
    output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
