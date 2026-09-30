"""Leakage-aware baseline, contextual blend and probability calibration."""
from __future__ import annotations

import json
import hashlib
import math
import random
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

from .data import SOURCES, VARIABLES, read_cases

HAZARDS = {"temperature": (40.0, "Temperature ≥ 40 °C at valid hour (not a heat-wave declaration)"),
           "wind": (15.0, "10 m mean wind ≥ 15 m/s at valid hour"),
           "rain": (64.5, "UTC 24 h rain ≥ 64.5 mm (not an IMD reporting-day warning)")}
RAIN_THRESHOLDS = {"heavy": 64.5, "very_heavy": 115.6, "extremely_heavy": 204.5}


def season(row: dict) -> str:
    month = row["valid_time"].month
    if row["region"] == "Chennai" and month in (10, 11, 12):
        return "northeast monsoon"
    return "southwest monsoon" if month in (6, 7, 8, 9) else "other"


def regime(row: dict) -> str:
    values = [row[s] for s in SOURCES if row[s] is not None]
    if not values:
        return "unknown"
    avg = sum(values) / len(values)
    cut = {"rain": 10, "temperature": 35, "wind": 10}[row["variable"]]
    return "elevated" if avg >= cut else "ordinary"


def task(row: dict) -> tuple:
    return row["variable"], row["lead_hours"]


def available_history(rows: list[dict], row: dict) -> list[dict]:
    return [r for r in rows if task(r) == task(row) and r["observed_at"] <= row["issue_time"]
            and any(r[s] is not None for s in SOURCES)]


def normalize(scores: dict[str, float]) -> dict[str, float]:
    total = sum(scores.values())
    return {s: v / total for s, v in scores.items()} if total else {}


def weights(row: dict, history: list[dict], adaptive: bool) -> dict[str, float]:
    sources = [s for s in SOURCES if row[s] is not None]
    if not sources:
        return {}
    broad = {}
    for source in sources:
        errors = [abs(r[source] - r["observation"]) for r in history if r[source] is not None]
        broad[source] = 1 / (sum(errors) / len(errors) + 0.1) if errors else 1
    baseline = normalize(broad)
    if not adaptive:
        return baseline
    local = [r for r in history if r["region"] == row["region"] and season(r) == season(row) and regime(r) == regime(row)]
    dates = len({r["issue_time"].date() for r in local})
    if dates < 12:
        return baseline
    local_score = {}
    for source in sources:
        errors = [abs(r[source] - r["observation"]) for r in local if r[source] is not None]
        local_score[source] = 1 / (sum(errors) / len(errors) + 0.1) if len(errors) >= 8 else broad[source]
    local_weight = normalize(local_score)
    alpha = dates / (dates + 60)
    return {s: (1 - alpha) * baseline[s] + alpha * local_weight[s] for s in sources}


def point(row: dict, w: dict[str, float]) -> float | None:
    return sum(row[s] * weight for s, weight in w.items()) if w else None


def corrected_source(row: dict, history: list[dict], source: str) -> float | None:
    if row[source] is None:
        return None
    residuals = [r["observation"] - r[source] for r in history if r[source] is not None]
    value = row[source] + (sum(residuals) / len(residuals) if residuals else 0)
    return max(0, value) if row["variable"] != "temperature" else value


def source_cdf(row: dict, history: list[dict], source: str, thresholds: list[float]) -> list[float] | None:
    residuals = [r["observation"] - r[source] for r in history if r[source] is not None]
    if len(residuals) < 12:
        return None
    result = []
    for threshold in thresholds:
        count = sum((max(0, row[source] + err) if row["variable"] != "temperature" else row[source] + err) < threshold for err in residuals)
        result.append((count + 0.5) / (len(residuals) + 1))
    return result


def monotone(values: list[float]) -> list[float]:
    """Bounded pool-adjacent-violators projection for a discrete CDF."""
    blocks = []
    for value in values:
        blocks.append([min(1.0, max(0.0, value)), 1])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            b = blocks.pop()
            a = blocks.pop()
            count = a[1] + b[1]
            blocks.append([(a[0] * a[1] + b[0] * b[1]) / count, count])
    return [v for value, count in blocks for v in [value] * count]


def base_distribution(row: dict, history: list[dict], w: dict[str, float], thresholds: list[float]) -> list[float] | None:
    members = [(s, source_cdf(row, history, s, thresholds)) for s in w]
    members = [(s, cdf) for s, cdf in members if cdf is not None]
    if not members:
        return None
    sw = sum(w[s] for s, _ in members)
    return [sum(w[s] * cdf[i] for s, cdf in members) / sw for i in range(len(thresholds))]


def lag_features(row: dict, history: list[dict], threshold: float) -> list[float]:
    past = sorted((r for r in history if r["region"] == row["region"]), key=lambda r: r["valid_time"], reverse=True)
    climatology = (sum(r["observation"] < threshold for r in history) + 1) / (len(history) + 2)
    return [climatology] + [float(past[i]["observation"] < threshold) if i < len(past) else climatology for i in range(2)]


def fit_pbc(samples: list[tuple[list[float], list[list[float]], list[float]]], count: int) -> list[dict]:
    """Each sample: raw CDF, per-threshold lag features, observed value."""
    result = []
    for k in range(count):
        x = np.asarray([[1, raw[k], *lags[k]] for raw, lags, observed in samples], dtype=float)
        y = np.asarray([observed[k] for raw, lags, observed in samples], dtype=float)
        if len(x) < 30:
            result.append({"bias": 0.0, "beta": [0, 1, 0, 0, 0]})
            continue
        ridge = np.diag([0.0, 1.0, 2.0, 4.0, 4.0])
        beta = np.linalg.solve(x.T @ x + ridge, x.T @ y)
        result.append({"bias": float(np.mean(y - x[:, 1])), "beta": beta.tolist()})
    return result


def fit_pbc_history(train: list[dict], thresholds: list[float]) -> list[dict]:
    samples = []
    for row in train:
        history = available_history(train, row)
        if len(history) < 30:
            continue
        raw = base_distribution(row, history, weights(row, history, True), thresholds)
        if raw:
            lags = [lag_features(row, history, t) for t in thresholds]
            observed = [float(row["observation"] < t) for t in thresholds]
            samples.append((raw, lags, observed))
    return fit_pbc(samples, len(thresholds))


def correct_parts(raw: list[float], lags: list[list[float]], model: list[dict]) -> tuple[list[float], list[float]]:
    debias = monotone([p + model[k]["bias"] for k, p in enumerate(raw)])
    persist = monotone([sum(a * b for a, b in zip(model[k]["beta"], [1, p, *lags[k]])) for k, p in enumerate(raw)])
    return debias, persist


def correct(raw: list[float], lags: list[list[float]], model: list[dict]) -> list[float]:
    debias, persist = correct_parts(raw, lags, model)
    return [(a + b) / 2 for a, b in zip(debias, persist)]


def quantiles(values: list[float], variable: str) -> list[float]:
    sorted_values = sorted(values)
    result = sorted(set(float(sorted_values[round((len(sorted_values) - 1) * q)]) for q in (0.2, 0.4, 0.6, 0.8)))
    return sorted(set([0.1] + [x for x in result if x > 0.1])) if variable == "rain" else result


def score(rows: list[dict], key: str, observed: str = "observation") -> dict:
    pairs = [(r[key], r[observed]) for r in rows if r.get(key) is not None]
    if not pairs:
        return {"n": 0, "mae": None, "rmse": None, "bias": None}
    errors = [a - b for a, b in pairs]
    return {"n": len(pairs), "mae": round(sum(abs(e) for e in errors) / len(errors), 4),
            "rmse": round(math.sqrt(sum(e * e for e in errors) / len(errors)), 4),
            "bias": round(sum(errors) / len(errors), 4)}


def probability_score(rows: list[dict], key: str) -> dict:
    valid = [r for r in rows if r.get(key) is not None]
    if not valid:
        return {"n": 0, "rps": None, "brier": None, "events": 0}
    rps = []
    brier = []
    events = 0
    bins = [{"forecast_sum": 0.0, "observed": 0, "n": 0} for _ in range(5)]
    hits = misses = false_alarms = 0
    for r in valid:
        cdf = r[key]
        indices = r["quantile_indices"]
        rps.append(sum((cdf[i] - float(r["observation"] < r["thresholds"][i])) ** 2 for i in indices) / max(1, len(indices)))
        event = r["observation"] >= r["hazard_threshold"]
        events += event
        probability = 1 - cdf[r["hazard_index"]]
        brier.append((probability - float(event)) ** 2)
        bucket = bins[min(4, int(probability * 5))]
        bucket["forecast_sum"] += probability
        bucket["observed"] += int(event)
        bucket["n"] += 1
        hits += int(probability >= 0.5 and event)
        misses += int(probability < 0.5 and event)
        false_alarms += int(probability >= 0.5 and not event)
    return {"n": len(valid), "rps": round(sum(rps) / len(rps), 5),
            "brier": round(sum(brier) / len(brier), 5), "events": events,
            "reliability": [{"forecast": round(b["forecast_sum"] / b["n"], 3),
                             "observed": round(b["observed"] / b["n"], 3), "n": b["n"]}
                            for b in bins if b["n"]],
            "threshold_50": {"hits": hits, "misses": misses, "false_alarms": false_alarms}}


def event_scores(rows: list[dict], key: str, thresholds: dict[str, float]) -> dict:
    result = {}
    for name, threshold in thresholds.items():
        valid = [r for r in rows if r.get(key) is not None]
        if not valid:
            result[name] = {"threshold": threshold, "n": 0, "events": 0, "brier": None}
            continue
        idx = valid[0]["thresholds"].index(threshold)
        events = sum(r["observation"] >= threshold for r in valid)
        brier = sum((1 - r[key][idx] - float(r["observation"] >= threshold)) ** 2 for r in valid) / len(valid)
        result[name] = {"threshold": threshold, "n": len(valid), "events": events, "brier": round(brier, 5)}
    return result


def paired_interval(rows: list[dict], first: str, second: str, kind: str) -> dict:
    """Seven-day circular block bootstrap of paired score differences."""
    paired = [r for r in rows if r.get(first) is not None and r.get(second) is not None]
    if not paired:
        return {"difference": None, "low": None, "high": None}
    dates = sorted({r["issue_time"].date() for r in paired})
    by_date = {day: [r for r in paired if r["issue_time"].date() == day] for day in dates}

    def loss(r: dict, key: str) -> float:
        if kind == "point":
            return abs(r[key] - r["observation"])
        indices = r["quantile_indices"]
        return sum((r[key][i] - float(r["observation"] < r["thresholds"][i])) ** 2 for i in indices) / max(1, len(indices))

    def difference(sample: list[dict]) -> float:
        return sum(loss(r, first) - loss(r, second) for r in sample) / len(sample)

    rng = random.Random(26081)
    draws = []
    for _ in range(500):
        selected_dates = []
        while len(selected_dates) < len(dates):
            start = rng.randrange(len(dates))
            selected_dates.extend(dates[(start + offset) % len(dates)] for offset in range(7))
        sample = [r for day in selected_dates[:len(dates)] for r in by_date[day]]
        draws.append(difference(sample))
    draws.sort()
    return {"difference": round(difference(paired), 5),
            "low": round(draws[int(len(draws) * 0.025)], 5),
            "high": round(draws[int(len(draws) * 0.975)], 5),
            "unit": "MAE difference" if kind == "point" else "RPS difference",
            "resampling": "500 circular seven-day blocks over issue dates"}


def evaluate(cases_path: Path, output: Path, manifest: dict | None = None) -> dict:
    if manifest and manifest.get("cases_sha256") and hashlib.sha256(cases_path.read_bytes()).hexdigest() != manifest["cases_sha256"]:
        raise ValueError("Case table does not match the supplied manifest hash")
    cases = [r for r in read_cases(cases_path) if r["observation"] is not None and sum(r[s] is not None for s in SOURCES) >= 2]
    dates = sorted({r["issue_time"].date() for r in cases})
    if len(dates) < 90:
        raise ValueError("At least 90 distinct issue dates are needed to train, select and test methods")
    train_end, valid_end = dates[int(len(dates) * 0.65)], dates[int(len(dates) * 0.8)]
    groups = defaultdict(list)
    for row in cases:
        groups[task(row)].append(row)
    results = []
    displays = []
    for (variable, lead), group in groups.items():
        train = [r for r in group if r["issue_time"].date() < train_end]
        validation = [r for r in group if train_end <= r["issue_time"].date() < valid_end]
        test = [r for r in group if r["issue_time"].date() >= valid_end]
        if len(train) < 60 or len(validation) < 15 or len(test) < 15:
            continue
        fixed_history = [r for r in train if r["observed_at"].date() < train_end]
        q = quantiles([r["observation"] for r in train], variable)
        threshold = HAZARDS[variable][0]
        hazard_targets = RAIN_THRESHOLDS if variable == "rain" else {"defined_event": threshold}
        thresholds = sorted(set(q + list(hazard_targets.values())))
        hazard_index = thresholds.index(threshold)
        if len({r["issue_time"].date() for r in train}) < 75:
            continue
        calibration = fit_pbc_history(train, thresholds)

        def predict(part: list[dict]) -> list[dict]:
            predicted = []
            for row in part:
                history = available_history(train, row)
                if len(history) < 30:
                    continue
                present = [s for s in SOURCES if row[s] is not None]
                equal = {s: 1 / len(present) for s in present}
                fixed = weights(row, fixed_history, False)
                adaptive = weights(row, history, True)
                raw = base_distribution(row, history, adaptive, thresholds)
                if raw is None:
                    continue
                lags = [lag_features(row, history, t) for t in thresholds]
                debias, persistence = correct_parts(raw, lags, calibration)
                predicted.append({**row, "equal": point(row, equal), "fixed": point(row, fixed),
                                  "adaptive": point(row, adaptive),
                                  **{s + "_corrected": corrected_source(row, history, s) for s in SOURCES},
                                  "point_weights": {"equal": equal, "fixed": fixed, "adaptive": adaptive,
                                                    **{s + "_corrected": {s: 1.0} for s in SOURCES}},
                                  "outage_points": {s: (sum(row[t] for t in present if t != s) / (len(present) - 1))
                                                    if s in present and len(present) > 1 else None for s in SOURCES},
                                  "raw": raw, "debias": debias, "persistence": persistence,
                                  "pbc": [(a + b) / 2 for a, b in zip(debias, persistence)],
                                  "thresholds": thresholds, "quantile_indices": [thresholds.index(t) for t in q],
                                  "hazard_threshold": threshold, "hazard_index": hazard_index})
            return predicted

        val, held = predict(validation), predict(test)
        if not val or not held:
            continue
        point_options = {name: score(val, name)["mae"] for name in ("equal", "fixed", "adaptive", *(s + "_corrected" for s in SOURCES))}
        winner = min((name for name in point_options if point_options[name] is not None), key=point_options.get)
        if winner == "adaptive" and point_options[winner] > min(point_options["equal"], point_options["fixed"]) * 0.99:
            winner = min(("equal", "fixed"), key=point_options.get)
        p_raw, p_pbc = probability_score(val, "raw"), probability_score(val, "pbc")
        enough_hazards = p_raw["events"] >= 10
        prob_winner = ("pbc" if p_pbc["rps"] <= p_raw["rps"] * 0.99
                       and (not enough_hazards or p_pbc["brier"] <= p_raw["brier"] * 1.05) else "raw")
        outages = {}
        for source in SOURCES:
            key = "outage_" + source
            for rows in (val, held):
                for r in rows:
                    r[key] = r["outage_points"][source]
            val_score = score(val, key)
            held_outage = score(held, key)
            outages[source] = {"approved": bool(val_score["n"] >= 15
                                                 and val_score["mae"] <= score(val, winner)["mae"] * 1.2
                                                 and held_outage["n"] >= 15
                                                 and held_outage["mae"] <= score(held, "equal")["mae"] * 1.2),
                               "validation": val_score, "test": held_outage}
        held_point = score(held, winner)
        held_equal = score(held, "equal")
        deploy_point = "equal" if held_point["mae"] > held_equal["mae"] * 1.05 else winner
        held_prob = probability_score(held, prob_winner)
        held_raw = probability_score(held, "raw")
        deploy_prob = "raw" if held_prob["rps"] > held_raw["rps"] * 1.05 else prob_winner
        metric = {"variable": variable, "lead_hours": lead, "train_n": len(train), "validation_n": len(val),
                  "test_n": len(held), "point_method": winner, "probability_method": prob_winner,
                  "deployment_point_method": deploy_point, "deployment_probability_method": deploy_prob,
                  "deployment_note": "Held-out harm gate used; a fresh untouched period is needed for final skill claims" if (deploy_point != winner or deploy_prob != prob_winner) else "Validation choice passed the held-out harm gate",
                  "paired_uncertainty": {"point_vs_equal": paired_interval(held, winner, "equal", "point"),
                                         "probability_vs_raw": paired_interval(held, prob_winner, "raw", "probability")},
                  "validation": {"point": {k: score(val, k) for k in (*SOURCES, *(s + "_corrected" for s in SOURCES), "equal", "fixed", "adaptive")},
                                 "probability": {k: probability_score(val, k) for k in ("raw", "debias", "persistence", "pbc")}},
                  "test": {"point": {k: score(held, k) for k in (*SOURCES, *(s + "_corrected" for s in SOURCES), "equal", "fixed", "adaptive")},
                           "probability": {k: probability_score(held, k) for k in ("raw", "debias", "persistence", "pbc")}},
                  "thresholds": thresholds,
                  "hazard": ("08:30 IST reporting-day rain ≥ 64.5 mm (research guidance)" if variable == "rain" and "03:00 UTC" in (manifest or {}).get("rain_window", "") else HAZARDS[variable][1]),
                  "outages": outages,
                  "hazard_status": "candidate tested" if enough_hazards else "insufficient validation events"}
        metric["event_scores"] = {"validation": event_scores(val, prob_winner, hazard_targets),
                                   "test": event_scores(held, prob_winner, hazard_targets)}
        results.append(metric)
        for r in held:
            chosen_cdf = r[prob_winner]
            displays.append({"issue_time": r["issue_time"].isoformat(), "valid_time": r["valid_time"].isoformat(),
                             "region": r["region"], "latitude": r["latitude"], "longitude": r["longitude"],
                             "variable": variable, "lead_hours": lead, "observation": r["observation"],
                             "point": round(r[winner], 3), "hazard_probability": round(1 - chosen_cdf[hazard_index], 4),
                             "event_probabilities": {name: round(1 - chosen_cdf[thresholds.index(t)], 4) for name, t in hazard_targets.items()},
                             "sources": {s: r[s] for s in SOURCES}, "weights": r["point_weights"][winner],
                             "probability_weights": r["point_weights"]["adaptive"],
                             "outage_points": r["outage_points"],
                             "point_method": winner, "probability_method": prob_winner,
                             "status": "held-out historical case",
                             "observation_source": (manifest or {}).get("truth", "user-supplied observations"),
                             "source_availability": "historical publication times unverified" if (manifest or {}).get("kind") == "retrospective_reanalysis" else (manifest or {}).get("availability", "see input manifest")})
    if not results:
        raise ValueError("No variable and lead has enough complete cases for evaluation")
    output.mkdir(parents=True, exist_ok=True)
    report = {"data_kind": (manifest or {}).get("kind", "external_case_table"),
              "truth": (manifest or {}).get("truth", "user-supplied observations"),
              "limitations": (manifest or {}).get("availability", "Check issue and observation release timing in the input table"),
              "manifest": manifest or {},
              "split": {"train_before": train_end.isoformat(), "validation_before": valid_end.isoformat(),
                        "test_from": valid_end.isoformat()},
              "units": {k: value[1] for k, value in VARIABLES.items()},
              "target_registry": {"rain": {"status": "threshold probabilities; issue only with sufficient validation events", "thresholds_mm": RAIN_THRESHOLDS},
                                  "heat_wave": {"status": "unavailable: daily maximum, normals, setting and persistence observations needed"},
                                  "wind_gust": {"status": "unavailable: gust observations and forecasts needed"}},
              "results": results, "cases": displays}
    (output / "state.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
