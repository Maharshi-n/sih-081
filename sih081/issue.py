"""Immutable run-specific research records using the evaluated inference path."""
from __future__ import annotations
import csv
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .data import PLACES, SOURCES, VARIABLES, _check_payload, fetch_run, read_cases
from .forecast import HAZARDS, RAIN_THRESHOLDS, available_history, candidate_predictions, confidence_calibration
from .confidence import assess
from .router import disagreement
from .freshness import source_freshness


def issue(run_text: str, regions: list[str], history_path: Path, state_path: Path, output: Path,
          *, as_of: datetime | None = None, replay: bool = False) -> dict:
    run = datetime.fromisoformat(run_text.replace("Z", "+00:00"))
    run = run.replace(tzinfo=timezone.utc) if run.tzinfo is None else run.astimezone(timezone.utc)
    if any((run.hour, run.minute, run.second, run.microsecond)):
        raise ValueError("Issuance supports 00 UTC runs only")
    when = as_of or (run + timedelta(hours=6) if replay else datetime.now(timezone.utc))
    if when.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    when = when.astimezone(timezone.utc)
    if not replay and as_of is not None:
        raise ValueError("Historical as_of is allowed only with explicitly labelled replay")
    if when < run + timedelta(hours=6):
        raise ValueError("Run after the assumed 6 hour publication delay")
    if not regions or len(set(regions)) != len(regions) or any(r not in PLACES for r in regions):
        raise ValueError(f"Choose unique regions from {', '.join(PLACES)}")
    state_bytes = state_path.read_bytes()
    state = json.loads(state_bytes)
    state_hash = hashlib.sha256(state_bytes).hexdigest()
    if state["data_kind"] == "synthetic_demo":
        raise ValueError("Refusing real issuance from a synthetic training/validation report")
    if state.get("schema_version") != 2:
        raise ValueError("Re-evaluate the archive before using the updated issuance path")
    if run < datetime.fromisoformat(state["verification_policy"]["selection_cutoff"]):
        raise ValueError("Run precedes frozen development selections; retrospective leakage refused")
    expected_hash = state.get("manifest", {}).get("cases_sha256")
    if expected_hash and hashlib.sha256(history_path.read_bytes()).hexdigest() != expected_hash:
        raise ValueError("History case table differs from the evaluated state")
    target = output / f"forecast-{run.strftime('%Y%m%dT%H%MZ')}-{state_hash[:8]}.json"
    if target.exists():
        raise FileExistsError(f"Forecast record exists and is immutable: {target}")
    history = read_cases(history_path)
    output.mkdir(parents=True, exist_ok=True)
    payloads, statuses, errors = {}, {}, []
    for region in regions:
        lat, lon = PLACES[region]
        for source in SOURCES:
            freshness = source_freshness(run, when, source)
            meta = {"model_id": SOURCES[source], "run_initialized_at": run.isoformat(),
                    "publication_time": None, "freshness": freshness, "status": freshness["status"]}
            statuses[region, source] = meta
            if freshness["status"] in ("too stale", "invalid"):
                meta["reason"] = freshness["reason"]
                continue
            try:
                payload = fetch_run(run, region, lat, lon, source)
                _check_payload(payload)
                encoded = json.dumps(payload, sort_keys=True, allow_nan=False).encode("utf-8")
                digest = hashlib.sha256(encoded).hexdigest()
                path = output / "source_payloads" / f"{digest}.json"
                path.parent.mkdir(exist_ok=True)
                if path.exists() and path.read_bytes() != encoded:
                    raise ValueError("Source payload hash collision or corruption")
                if not path.exists():
                    path.write_bytes(encoded)
                meta.update(retrieved_at=datetime.now(timezone.utc).isoformat(), payload_sha256=digest,
                            payload_file=path.relative_to(output).as_posix(), provider_grid={"latitude": payload.get("latitude"), "longitude": payload.get("longitude")})
                payloads[region, source] = payload["hourly"]
            except Exception as error:
                meta.update(status="unavailable", reason=str(error))
                errors.append({"region": region, "source": source, "error": str(error)})
    records = []
    for region in regions:
        lat, lon = PLACES[region]
        for product in state["results"]:
            variable, lead = product["variable"], product["lead_hours"]
            valid = run + timedelta(hours=lead)
            row = {"issue_time": run, "valid_time": valid, "observed_at": None, "region": region,
                   "latitude": lat, "longitude": lon, "variable": variable, "lead_hours": lead, "observation": None,
                   "source_run_times": {s: run for s in SOURCES},
                   "source_age_hours": {s: statuses[region, s]["freshness"]["age_hours"] for s in SOURCES}}
            times = [(run + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M")
                     for i in (range(lead - 23, lead + 1) if variable == "rain" else (lead,))]
            for source in SOURCES:
                hourly = payloads.get((region, source))
                lookup = {t: i for i, t in enumerate(hourly["time"])} if hourly else {}
                values = [hourly[VARIABLES[variable][0]][lookup[t]] for t in times] if all(t in lookup for t in times) else []
                row[source] = (sum(values) if variable == "rain" else values[0]) if values and all(v is not None for v in values) else None
            present = [s for s in SOURCES if row[s] is not None]
            missing = [s for s in SOURCES if s not in present]
            source_status = {s: dict(statuses[region, s]) for s in SOURCES}
            for source in missing:
                if (region, source) in payloads:
                    source_status[source].update(status="unavailable", reason="Required hourly values are missing for this target or rainfall window")
            record = {"region": region, "latitude": lat, "longitude": lon, "variable": variable,
                      "lead_hours": lead, "run_initialized_at": run.isoformat(), "valid_time": valid.isoformat(),
                      "sources": {s: row[s] for s in SOURCES}, "missing_sources": missing, "source_status": source_status,
                      "window_start": (valid - timedelta(hours=24)).isoformat() if variable == "rain" else None,
                      "window_end": valid.isoformat() if variable == "rain" else None,
                      "point": None, "hazard_probability": None, "event_probabilities": {}, "event_reasons": {},
                      "weights": {}, "probability_weights": {}, "status": "unavailable", "disagreement": disagreement(row)}
            records.append(record)
            cutoff = datetime.fromisoformat(product["models"]["history_before"])
            frozen_history = [r for r in history if r["observed_at"] < cutoff and r["issue_time"] < cutoff]
            past = available_history(frozen_history, row)
            if region not in state["coverage"]["regions"]:
                record["reason"] = "This location has no evaluated historical coverage"
                continue
            if len(past) < 30 or not present:
                record["reason"] = "No usable fresh source inputs" if not present else "Insufficient released training history"
                continue
            if len(missing) > 1 or (missing and not product["outages"][missing[0]]["approved"]):
                record["reason"] = "This source combination has no development-approved point fallback"
                continue
            predicted = candidate_predictions(row, frozen_history, product, missing[0] if missing else None)
            policy = product["outages"][missing[0]] if missing else product
            method = "equal" if missing else product["deployment_point_method"]
            prob_method = policy["probability_method"] if missing else product["deployment_probability_method"]
            value = predicted["points"].get(method)
            chosen = predicted["point_weights"].get(method, {})
            record.update(point=round(value, 3) if value is not None else None, weights=chosen,
                          point_method=method, probability_method=prob_method,
                          probability_weights=predicted["probability_weights"].get(prob_method, {}),
                          status="point fallback" if missing else "point only",
                          selection_note="Selections frozen from development data; final test is reporting only")
            record["confidence"] = assess(row, frozen_history, chosen, value, source_status, as_of=when,
                                          calibration=confidence_calibration(product, missing[0] if missing else None))
            if not missing:
                record.update(router_weights=predicted["router_weights"], router_explanation=predicted["router_explanation"])
            cdf = predicted["cdfs"].get(prob_method)
            events = RAIN_THRESHOLDS if variable == "rain" else {"defined_event": HAZARDS[variable][0]}
            for name, threshold in events.items():
                gate = policy.get("event_approval", {}).get(name, {"approved": False, "reasons": ["No verified event evidence"]})
                record["event_probabilities"][name] = round(1 - cdf[product["thresholds"].index(threshold)], 4) if cdf is not None and gate["approved"] else None
                record["event_reasons"][name] = gate["reasons"] if cdf is not None else ["Insufficient probability training history"]
            primary = "heavy" if variable == "rain" else "defined_event"
            record["hazard_probability"] = record["event_probabilities"][primary]
            record["reason"] = "; ".join(record["event_reasons"][primary]) or "Research event evidence gate passed"
            if record["hazard_probability"] is not None:
                record["status"] = "issued research guidance" if not missing else "research subset guidance"
    result = {"schema_version": 2, "kind": "retrospective_replay" if replay else "research_guidance",
              "run_initialized_at": run.isoformat(), "generated_at": datetime.now(timezone.utc).isoformat(),
              "as_of": when.isoformat(), "assumed_issue_time": (run + timedelta(hours=6)).isoformat(),
              "availability_caveat": "Provider publication times are unknown; 6 hour arrival delay is an assumption. Replay is not a live forecast.",
              "validation_state_sha256": state_hash, "reference": state["truth"], "records": records,
              "source_errors": errors, "model_ids": SOURCES}
    encoded = json.dumps(result, indent=2, allow_nan=False)
    with target.open("x", encoding="utf-8") as handle:
        handle.write(encoded)
    with target.with_suffix(".csv").open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["region", "variable", "lead_hours", "valid_time", "point", "hazard_probability", "status", "point_method", "probability_method"])
        writer.writeheader()
        writer.writerows([{k: r.get(k, "") for k in writer.fieldnames} for r in records])
    temporary = output / "latest.json.tmp"
    temporary.write_text(encoded, encoding="utf-8")
    temporary.replace(output / "latest.json")
    return result
