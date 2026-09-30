"""Generate an immutable forecast record from one run-specific model cycle."""
from __future__ import annotations

import csv
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .data import PLACES, SOURCES, VARIABLES, fetch_run, read_cases
from .forecast import HAZARDS, available_history, base_distribution, correct, corrected_source, fit_pbc_history, lag_features, point, weights


def issue(run_text: str, regions: list[str], history_path: Path, state_path: Path, output: Path) -> dict:
    run = datetime.fromisoformat(run_text.replace("Z", "+00:00"))
    if run.tzinfo is None:
        run = run.replace(tzinfo=timezone.utc)
    run = run.astimezone(timezone.utc)
    if run.hour != 0 or run.minute != 0:
        raise ValueError("This pilot issues 00 UTC runs only")
    if any(region not in PLACES for region in regions):
        raise ValueError(f"Choose regions from {', '.join(PLACES)}")
    state_bytes = state_path.read_bytes()
    state = json.loads(state_bytes)
    state_hash = hashlib.sha256(state_bytes).hexdigest()
    if state["data_kind"] == "synthetic_demo":
        raise ValueError("Refusing real issuance from a synthetic training/validation report")
    expected_cases_hash = state.get("manifest", {}).get("cases_sha256")
    if expected_cases_hash and hashlib.sha256(history_path.read_bytes()).hexdigest() != expected_cases_hash:
        raise ValueError("History case table differs from the evaluated state; pass the same --cases file used for evaluation")
    history = read_cases(history_path)
    source_payloads = {}
    source_status = {}
    source_errors = []
    for region in regions:
        lat, lon = PLACES[region]
        for source in SOURCES:
            try:
                payload = fetch_run(run, region, lat, lon, source)
                source_payloads[region, source] = payload["hourly"]
                source_status[region, source] = {"model_id": SOURCES[source], "run_initialized_at": run.isoformat(),
                                                  "retrieved_at": datetime.now(timezone.utc).isoformat(),
                                                  "payload_sha256": hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
                                                  "publication_time": None}
            except Exception as e:
                source_errors.append({"region": region, "source": source, "error": str(e)})
    products = {(r["variable"], r["lead_hours"]): r for r in state["results"]}
    records = []
    for region in regions:
        lat, lon = PLACES[region]
        for (variable, lead), product in products.items():
            valid = run + timedelta(hours=lead)
            row = {"issue_time": run + timedelta(hours=6), "valid_time": valid, "observed_at": None,
                   "region": region, "latitude": lat, "longitude": lon, "variable": variable,
                   "lead_hours": lead, "observation": None}
            field = VARIABLES[variable][0]
            indices = range(lead - 23, lead + 1) if variable == "rain" else (lead,)
            times = [(run + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M") for i in indices]
            for source in SOURCES:
                payload = source_payloads.get((region, source))
                if not payload or any(t not in payload["time"] for t in times):
                    row[source] = None
                    continue
                lookup = {t: i for i, t in enumerate(payload["time"])}
                values = [payload[field][lookup[t]] for t in times]
                row[source] = (sum(values) if variable == "rain" else values[0]) if all(v is not None for v in values) else None
            present = [s for s in SOURCES if row[s] is not None]
            record = {"region": region, "latitude": lat, "longitude": lon, "variable": variable,
                      "lead_hours": lead, "run_initialized_at": run.isoformat(), "valid_time": valid.isoformat(),
                      "sources": {s: row[s] for s in SOURCES}, "missing_sources": [s for s in SOURCES if s not in present],
                      "source_status": {s: source_status.get((region, s), {"model_id": SOURCES[s], "status": "unavailable"}) for s in SOURCES},
                      "window_start": (valid - timedelta(hours=24)).isoformat() if variable == "rain" else None,
                      "window_end": valid.isoformat() if variable == "rain" else None,
                      "point": None, "hazard_probability": None, "event_probabilities": {}, "weights": {}, "status": "unavailable"}
            if not present:
                records.append(record)
                continue
            past = available_history(history, row)
            if len(past) < 30:
                record["reason"] = "insufficient released historical cases"
                records.append(record)
                continue
            if len(present) < len(SOURCES):
                if len(present) == len(SOURCES) - 1 and product["outages"][[s for s in SOURCES if s not in present][0]]["approved"]:
                    record.update({"point": round(sum(row[s] for s in present) / len(present), 3),
                                   "weights": {s: 1 / len(present) for s in present},
                                   "status": "point fallback", "reason": "probability fallback unvalidated"})
                else:
                    record["reason"] = "input subset has no approved fallback"
                records.append(record)
                continue
            method = product["deployment_point_method"]
            if method.endswith("_corrected"):
                source = method.removesuffix("_corrected")
                chosen = {source: 1.0}
                value = corrected_source(row, past, source)
            else:
                fixed_past = [r for r in past if r["observed_at"].date() < date.fromisoformat(state["split"]["train_before"])]
                chosen = ({s: 1 / len(present) for s in present} if method == "equal"
                          else weights(row, past if method == "adaptive" else fixed_past, method == "adaptive"))
                value = point(row, chosen)
            record["point"] = round(value, 3)
            record["weights"] = chosen
            record["point_method"] = method
            if method != product["point_method"]:
                record["selection_note"] = product["deployment_note"]
            thresholds = product["thresholds"]
            prob_weights = weights(row, past, True)
            raw = base_distribution(row, past, prob_weights, thresholds)
            if raw is not None:
                if product["deployment_probability_method"] == "pbc":
                    comparable = [r for r in history if r["variable"] == variable and r["lead_hours"] == lead and r["observed_at"] <= row["issue_time"]]
                    calibration = fit_pbc_history(comparable, thresholds)
                    raw = correct(raw, [lag_features(row, past, t) for t in thresholds], calibration)
                threshold_index = thresholds.index(HAZARDS[variable][0])
                for name, evidence in product.get("event_scores", {}).get("validation", {}).items():
                    record["event_probabilities"][name] = (round(1 - raw[thresholds.index(evidence["threshold"])], 4)
                                                             if evidence["events"] >= 10 else None)
                if product["hazard_status"] == "candidate tested":
                    record["hazard_probability"] = round(1 - raw[threshold_index], 4)
                else:
                    record["reason"] = "hazard probability withheld: fewer than 10 validation events"
                record["probability_method"] = product["deployment_probability_method"]
                record["status"] = "issued research guidance" if record["hazard_probability"] is not None else "point only"
            else:
                record["status"] = "point only"
                record["reason"] = "insufficient probability training history"
            records.append(record)
    output.mkdir(parents=True, exist_ok=True)
    stamp = run.strftime("%Y%m%dT%H%MZ")
    tag = state_hash[:8]
    target = output / f"forecast-{stamp}-{tag}.json"
    if target.exists():
        raise FileExistsError(f"Forecast record exists and is immutable: {target}")
    result = {"kind": "research_guidance", "run_initialized_at": run.isoformat(),
              "generated_at": datetime.now(timezone.utc).isoformat(),
              "assumed_issue_time": (run + timedelta(hours=6)).isoformat(),
              "availability_caveat": "Historical publication time not verified; 6 h run latency assumed for retrospective history gate",
              "validation_state_sha256": state_hash,
              "reference": state["truth"], "records": records, "source_errors": source_errors,
              "model_ids": SOURCES}
    target.write_text(json.dumps(result, indent=2), encoding="utf-8")
    csv_file = output / f"forecast-{stamp}-{tag}.csv"
    with csv_file.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["region", "variable", "lead_hours", "valid_time", "point", "hazard_probability", "status", "point_method", "probability_method"])
        writer.writeheader()
        writer.writerows([{k: r.get(k, "") for k in writer.fieldnames} for r in records])
    (output / "latest.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result
