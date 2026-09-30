"""Audit the loaded archive against saved provider responses, without network calls."""
import csv
import hashlib
import json
import math
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
archive = ROOT / "data" / "archive"
state_bytes = (ROOT / "output" / "state.json").read_bytes()
state = json.loads(state_bytes)
manifest = json.loads((archive / "manifest.json").read_bytes())
issued = json.loads((ROOT / "output" / "issued" / "latest.json").read_bytes())
assert state["data_kind"] == manifest["kind"] == "retrospective_reanalysis", "Loaded data is not the model/ERA5 archive"
assert hashlib.sha256((archive / "cases.csv").read_bytes()).hexdigest() == manifest["cases_sha256"] == state["manifest"]["cases_sha256"]
assert issued["validation_state_sha256"] == hashlib.sha256(state_bytes).hexdigest(), "Issued guidance uses a different evaluation"
assert manifest["model_ids"] == issued["model_ids"] == {
    "IFS": "ecmwf_ifs025", "GFS": "gfs_global", "AIFS": "ecmwf_aifs025_single"}


@lru_cache(maxsize=None)
def raw(filename):
    payload = json.loads((archive / "raw" / filename).read_bytes())
    assert payload["utc_offset_seconds"] == 0
    h = payload["hourly"]
    return h, {t: i for i, t in enumerate(h["time"])}


fields = {"temperature": "temperature_2m", "wind": "wind_speed_10m", "rain": "precipitation"}
end = (datetime.fromisoformat(manifest["start"]) + timedelta(days=manifest["days"] + 4)).date()
rows = list(csv.DictReader((archive / "cases.csv").open(encoding="utf-8")))
assert len(rows) == manifest["cases"]
checked = missing = 0
for row in rows:
    run = datetime.fromisoformat(row["issue_time"])
    lead = int(row["lead_hours"])
    hours = range(lead - 23, lead + 1) if row["variable"] == "rain" else [lead]
    times = [(run + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M") for h in hours]
    assert row["observation_source"] == "Open-Meteo ERA5 reanalysis"
    for name in ["observation", *manifest["model_ids"]]:
        if not row[name]:
            missing += 1
            continue
        filename = (f"{row['region']}-ERA5-{manifest['start']}-{end}.json" if name == "observation"
                    else f"{row['region']}-{name}-{run.date()}.json")
        series, lookup = raw(filename)
        values = [series[fields[row["variable"]]][lookup[t]] for t in times]
        expected = sum(values) if row["variable"] == "rain" else values[0]
        assert math.isclose(float(row[name]), expected, rel_tol=1e-10, abs_tol=1e-10), (row["region"], row["issue_time"], name)
        checked += 1
by_key = {(r["region"], r["issue_time"], r["variable"], int(r["lead_hours"])): r for r in rows}
for case in state["cases"]:
    row = by_key[case["region"], case["issue_time"], case["variable"], case["lead_hours"]]
    assert case["valid_time"] == row["valid_time"]
    assert math.isclose(case["observation"], float(row["observation"]))
    for name, value in case["sources"].items():
        assert value == (float(row[name]) if row[name] else None)
report = {"checked_at": datetime.now(timezone.utc).isoformat(), "data_kind": state["data_kind"],
          "regions": manifest["regions"], "archive_cases": len(rows), "displayed_cases": len(state["cases"]),
          "values_matched_to_cached_responses": checked, "missing_source_values": missing,
          "raw_response_files_checked": raw.cache_info().currsize, "issued_records": len(issued["records"]),
          "cases_sha256": manifest["cases_sha256"], "reference": manifest["truth"],
          "limits": "Checks saved downloads and provenance; not station observations or independent provider authentication."}
(ROOT / "output" / "data-audit.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report, indent=2))
