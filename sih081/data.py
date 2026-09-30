"""Case-table creation and run-specific Open-Meteo acquisition."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

SOURCES = {"IFS": "ecmwf_ifs025", "GFS": "gfs_global", "AIFS": "ecmwf_aifs025_single"}
PLACES = {
    "Mumbai": (19.076, 72.878),
    "Pune": (18.520, 73.857),
    "Chennai": (13.083, 80.270),
    "Bengaluru": (12.972, 77.595),
}
VARIABLES = {"temperature": ("temperature_2m", "°C"), "wind": ("wind_speed_10m", "m/s"), "rain": ("precipitation", "mm/24h")}
REQUIRED_FIELDS = ["issue_time", "valid_time", "observed_at", "region", "latitude", "longitude", "variable", "lead_hours", "observation", "IFS", "GFS", "AIFS"]
FIELDS = REQUIRED_FIELDS + ["observation_source", "observation_quality", "window_start", "window_end"]


def write_cases(path: Path, cases: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows([{k: row.get(k, "") for k in FIELDS} for row in cases])


def read_cases(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing = set(REQUIRED_FIELDS) - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Missing case-table columns: {', '.join(sorted(missing))}")
        rows = []
        for line, raw in enumerate(reader, 2):
            try:
                row = dict(raw)
                for key in ("issue_time", "valid_time", "observed_at"):
                    row[key] = datetime.fromisoformat(row[key].replace("Z", "+00:00"))
                    if row[key].tzinfo is None:
                        row[key] = row[key].replace(tzinfo=timezone.utc)
                for key in ("window_start", "window_end"):
                    if row.get(key):
                        row[key] = datetime.fromisoformat(row[key].replace("Z", "+00:00"))
                        if row[key].tzinfo is None:
                            raise ValueError(f"{key} must include a timezone")
                for key in ("latitude", "longitude", "observation", "IFS", "GFS", "AIFS"):
                    row[key] = float(row[key]) if row[key] not in ("", None) else None
                row["lead_hours"] = int(row["lead_hours"])
                if row["variable"] not in VARIABLES or row["lead_hours"] <= 0:
                    raise ValueError("invalid variable or lead")
                if row["valid_time"] <= row["issue_time"] or row["observed_at"] < row["valid_time"]:
                    raise ValueError("invalid timing")
                if row.get("observation_quality") and row["observation_quality"] != "pass":
                    raise ValueError("observation quality must be pass")
                if row["variable"] == "rain" and (row.get("window_start") or row.get("window_end")):
                    if not row.get("window_start") or row.get("window_end") != row["valid_time"] or row["window_end"] - row["window_start"] != timedelta(hours=24):
                        raise ValueError("rain observation window must be 24 h ending at valid_time")
                if abs((row["valid_time"] - row["issue_time"]).total_seconds() / 3600 - row["lead_hours"]) > 0.001:
                    raise ValueError("lead does not match issue and valid times")
                if not row["region"] or not (-90 <= row["latitude"] <= 90 and -180 <= row["longitude"] <= 180):
                    raise ValueError("invalid region or coordinates")
                if row["variable"] in ("rain", "wind") and row["observation"] is not None and row["observation"] < 0:
                    raise ValueError("negative observation")
                if row["variable"] in ("rain", "wind") and any(row[s] is not None and row[s] < 0 for s in SOURCES):
                    raise ValueError("negative source forecast")
                if not all(math.isfinite(row[k]) for k in ("latitude", "longitude", "observation", "IFS", "GFS", "AIFS") if row[k] is not None):
                    raise ValueError("non-finite value")
                rows.append(row)
            except (ValueError, TypeError, KeyError) as e:
                raise ValueError(f"{path}:{line}: {e}") from e
    if not rows:
        raise ValueError("Case table is empty")
    return sorted(rows, key=lambda r: (r["issue_time"], r["region"], r["variable"], r["lead_hours"]))


def demo_cases(days: int = 240, seed: int = 26081) -> list[dict]:
    """Synthetic observations and forecasts. Never use these for skill claims."""
    rng = random.Random(seed)
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    out = []
    for day in range(days):
        issue = start + timedelta(days=day)
        for region, (lat, lon) in PLACES.items():
            for lead in (24, 72):
                valid = issue + timedelta(hours=lead)
                monsoon = valid.month in (6, 7, 8, 9)
                northeast = region == "Chennai" and valid.month in (10, 11, 12)
                wet = monsoon or northeast
                for variable in VARIABLES:
                    if variable == "temperature":
                        truth = 28 + 5 * math.sin((valid.timetuple().tm_yday - 65) * 2 * math.pi / 365) + rng.gauss(0, 2)
                        if region == "Pune": truth += 2
                        noise = 1.5
                    elif variable == "wind":
                        truth = max(0, 4 + (2 if wet else 0) + rng.gauss(0, 2))
                        noise = 1.7
                    else:
                        truth = max(0, rng.expovariate(1 / (23 if wet else 3)) - (3 if wet else 1))
                        noise = 14 if wet else 5
                    forecasts = {}
                    for source, bias in (("IFS", 0.2), ("GFS", -0.45), ("AIFS", 0.0)):
                        scale = noise * (1.35 if source == "GFS" else 1.0)
                        if source == "AIFS" and wet: scale *= 0.8
                        value = truth + bias * scale + rng.gauss(0, scale)
                        forecasts[source] = round(max(0, value) if variable != "temperature" else value, 3)
                    out.append({"issue_time": issue.isoformat(), "valid_time": valid.isoformat(),
                                "observed_at": (valid + timedelta(hours=12)).isoformat(),
                                "region": region, "latitude": lat, "longitude": lon,
                                "variable": variable, "lead_hours": lead,
                                "observation": round(truth, 3), **forecasts})
    return out


def _get_json(url: str) -> dict:
    with urlopen(url, timeout=35) as response:
        return json.load(response)


def fetch_run(run: datetime, region: str, lat: float, lon: float, source: str) -> dict:
    params = {"latitude": lat, "longitude": lon, "run": run.strftime("%Y-%m-%dT%H:%M"),
              "models": SOURCES[source], "hourly": "temperature_2m,wind_speed_10m,precipitation",
              "wind_speed_unit": "ms", "timezone": "UTC"}
    url = "https://single-runs-api.open-meteo.com/v1/forecast?" + urlencode(params)
    return _get_json(url)


def fetch_reanalysis(start: str, end: str, lat: float, lon: float) -> dict:
    params = {"latitude": lat, "longitude": lon, "start_date": start, "end_date": end,
              "hourly": "temperature_2m,wind_speed_10m,precipitation", "models": "era5",
              "wind_speed_unit": "ms", "timezone": "UTC"}
    return _get_json("https://archive-api.open-meteo.com/v1/archive?" + urlencode(params))


def fetch_cases(start: str, days: int, regions: list[str], out: Path, rain_window: str = "utc") -> dict:
    """Run-specific forecasts versus delayed ERA5 reanalysis; exploratory verification only."""
    if days < 1 or days > 180:
        raise ValueError("days must be 1..180")
    begin = datetime.fromisoformat(start).replace(tzinfo=timezone.utc)
    if begin.date() < datetime(2026, 4, 2, tzinfo=timezone.utc).date():
        raise ValueError("All three single-run sources require dates from 2026-04-02")
    if not regions or any(x not in PLACES for x in regions):
        raise ValueError(f"Choose from: {', '.join(PLACES)}")
    if rain_window not in ("utc", "imd"):
        raise ValueError("rain_window must be utc or imd")
    out.mkdir(parents=True, exist_ok=True)
    cases = []
    failures = []
    for region in regions:
        lat, lon = PLACES[region]
        obs_end = (begin + timedelta(days=days + 4)).date().isoformat()
        obs_file = out / "raw" / f"{region}-ERA5-{start}-{obs_end}.json"
        if obs_file.exists():
            obs_payload = json.loads(obs_file.read_text(encoding="utf-8"))
        else:
            obs_payload = fetch_reanalysis(start, obs_end, lat, lon)
            obs_file.parent.mkdir(exist_ok=True)
            obs_file.write_text(json.dumps(obs_payload), encoding="utf-8")
        obs = obs_payload["hourly"]
        observed = {t: i for i, t in enumerate(obs["time"])}
        for day in range(days):
            run = begin + timedelta(days=day)
            runs = {}
            for source in SOURCES:
                try:
                    raw_file = out / "raw" / f"{region}-{source}-{run.date()}.json"
                    payload = json.loads(raw_file.read_text(encoding="utf-8")) if raw_file.exists() else fetch_run(run, region, lat, lon, source)
                    runs[source] = payload["hourly"]
                    (out / "raw").mkdir(exist_ok=True)
                    if not raw_file.exists():
                        raw_file.write_text(json.dumps(payload), encoding="utf-8")
                except Exception as e:
                    failures.append({"region": region, "source": source, "run": run.isoformat(), "error": str(e)})
            if not runs:
                continue
            for nominal_lead in (24, 72):
                for variable, (field, unit) in VARIABLES.items():
                    lead = nominal_lead + (3 if variable == "rain" and rain_window == "imd" else 0)
                    valid = run + timedelta(hours=lead)
                    # Daily rain is a 24 h accumulation ending at valid time in UTC.
                    indices = range(lead - 23, lead + 1) if variable == "rain" else (lead,)
                    times = [(run + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in indices]
                    if any(t not in observed for t in times):
                        continue
                    values = [obs[field][observed[t]] for t in times]
                    if any(v is None for v in values):
                        continue
                    truth = sum(values) if variable == "rain" else values[0]
                    row = {"issue_time": run.isoformat(), "valid_time": valid.isoformat(),
                           "observed_at": (valid + timedelta(days=7)).isoformat(),
                           "region": region, "latitude": lat, "longitude": lon,
                           "variable": variable, "lead_hours": lead, "observation": truth,
                           "observation_source": "Open-Meteo ERA5 reanalysis", "observation_quality": "",
                           "window_start": (valid - timedelta(hours=24)).isoformat() if variable == "rain" else "",
                           "window_end": valid.isoformat() if variable == "rain" else ""}
                    for source in SOURCES:
                        series = runs.get(source)
                        if series and all(t in series["time"] for t in times):
                            lookup = {t: i for i, t in enumerate(series["time"])}
                            vals = [series[field][lookup[t]] for t in times]
                            row[source] = sum(vals) if variable == "rain" and all(v is not None for v in vals) else (vals[0] if variable != "rain" else "")
                        else:
                            row[source] = ""
                    if any(row[s] not in ("", None) for s in SOURCES):
                        cases.append(row)
    if not cases:
        raise ValueError("No matched cases were retrieved")
    write_cases(out / "cases.csv", cases)
    digest = hashlib.sha256((out / "cases.csv").read_bytes()).hexdigest()
    manifest = {"kind": "retrospective_reanalysis", "truth": "Open-Meteo ERA5 reanalysis, not station observations",
                "source": "Open-Meteo Single Runs API", "model_ids": SOURCES,
                "rain_window": "03:00 UTC (08:30 IST) 24h ending at valid_time" if rain_window == "imd" else "UTC 24h ending at valid_time",
                "availability": "run-specific initiation known; historical publication time unverified",
                "start": start, "days": days, "regions": regions, "cases": len(cases), "failed_requests": failures,
                "cases_sha256": digest, "raw_cache_files": len(list((out / "raw").glob("*.json")))}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def merge_observations(cases_path: Path, observations_path: Path, out: Path) -> dict:
    """Replace reanalysis truth with explicitly matched, quality-checked observations."""
    base = read_cases(cases_path)
    required = {"region", "latitude", "longitude", "variable", "valid_time", "observation",
                "observed_at", "source", "quality", "window_start", "window_end"}
    references = {}
    with observations_path.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if required - set(reader.fieldnames or []):
            raise ValueError("Observation CSV needs: " + ", ".join(sorted(required)))
        for line, raw in enumerate(reader, 2):
            try:
                if raw["quality"].lower() != "pass" or not raw["source"].strip():
                    raise ValueError("quality must be pass and source must be named")
                for field in ("valid_time", "observed_at", "window_start", "window_end"):
                    raw[field] = datetime.fromisoformat(raw[field].replace("Z", "+00:00")) if raw[field] else None
                    if raw[field] is not None and raw[field].tzinfo is None:
                        raise ValueError(f"{field} needs a timezone")
                raw["latitude"], raw["longitude"] = float(raw["latitude"]), float(raw["longitude"])
                raw["observation"] = float(raw["observation"])
                if not math.isfinite(raw["observation"]) or raw["observed_at"] < raw["valid_time"]:
                    raise ValueError("invalid observation or release time")
                if raw["variable"] in ("rain", "wind") and raw["observation"] < 0:
                    raise ValueError("negative observation")
                if raw["variable"] == "rain" and (raw["window_end"] != raw["valid_time"] or raw["window_start"] != raw["valid_time"] - timedelta(hours=24)):
                    raise ValueError("rain requires exact 24 h window ending at valid_time")
                key = (raw["region"], raw["variable"], raw["valid_time"])
                if key in references:
                    raise ValueError("duplicate observation target")
                references[key] = raw
            except (ValueError, TypeError, KeyError) as e:
                raise ValueError(f"{observations_path}:{line}: {e}") from e
    matched = []
    for row in base:
        obs = references.get((row["region"], row["variable"], row["valid_time"]))
        if obs is None:
            continue
        if abs(obs["latitude"] - row["latitude"]) > 0.01 or abs(obs["longitude"] - row["longitude"]) > 0.01:
            raise ValueError(f"Spatial mismatch for {row['region']} {row['valid_time']}; make the forecast target and observation support comparable")
        if row["variable"] == "rain" and row.get("window_start") and (obs["window_start"] != row["window_start"] or obs["window_end"] != row["window_end"]):
            raise ValueError(f"Rain window mismatch for {row['region']} {row['valid_time']}")
        matched.append({**row, "observation": obs["observation"], "observed_at": obs["observed_at"],
                        "observation_source": obs["source"], "observation_quality": "pass",
                        "window_start": obs["window_start"] or "", "window_end": obs["window_end"] or ""})
    if not matched:
        raise ValueError("No observations matched forecast location, variable, and valid time")
    out.mkdir(parents=True, exist_ok=True)
    write_cases(out / "cases.csv", matched)
    sources = sorted({r["observation_source"] for r in matched})
    manifest = {"kind": "quality_checked_observations", "truth": ", ".join(sources),
                "availability": "Observation release times supplied in CSV; historical model publication times remain unverified",
                "spatial_caveat": "Nearby point or station observations may differ from model grid-cell averages; verify representativeness",
                "cases": len(matched), "source_observations": str(observations_path),
                "rain_window": "exact 24 h intervals from observation CSV",
                "cases_sha256": hashlib.sha256((out / "cases.csv").read_bytes()).hexdigest(),
                "source_observations_sha256": hashlib.sha256(observations_path.read_bytes()).hexdigest()}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest
