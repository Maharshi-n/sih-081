"""Leakage-aware baseline, contextual blend and probability calibration."""
from __future__ import annotations

import json
import hashlib
import math
import random
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from .data import SOURCES, VARIABLES, read_cases
from .router import disagreement, fit_router, router_weights
from .rainfall import fit_rainfall, rainfall_cdf
from .confidence import assess, summarize

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
    return [r for r in rows if task(r) == task(row) and r["observed_at"] < row["issue_time"] and r["issue_time"] < row["issue_time"]
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


def event_scores(rows: list[dict], key: str, thresholds: dict[str, float], climatology: dict | None = None) -> dict:
    result = {}
    for name, threshold in thresholds.items():
        valid = [r for r in rows if r.get(key) is not None]
        if not valid:
            result[name] = {"threshold": threshold, "n": 0, "events": 0, "brier": None}
            continue
        idx = valid[0]["thresholds"].index(threshold)
        events = sum(r["observation"] >= threshold for r in valid)
        probabilities = [1 - r[key][idx] for r in valid]
        outcomes = [r["observation"] >= threshold for r in valid]
        brier = sum((p - float(y)) ** 2 for p, y in zip(probabilities, outcomes)) / len(valid)
        hits = sum(p >= .5 and y for p, y in zip(probabilities, outcomes))
        misses = events - hits
        false_alarms = sum(p >= .5 and not y for p, y in zip(probabilities, outcomes))
        negatives = len(valid) - events
        bins = []
        for bucket in range(5):
            pairs = [(p, y) for p, y in zip(probabilities, outcomes) if min(4, int(p * 5)) == bucket]
            if not pairs:
                continue
            n = len(pairs); frequency = sum(y for p, y in pairs) / n
            center = (frequency + 1.96 ** 2 / (2 * n)) / (1 + 1.96 ** 2 / n)
            half = 1.96 * math.sqrt(frequency * (1 - frequency) / n + 1.96 ** 2 / (4 * n ** 2)) / (1 + 1.96 ** 2 / n)
            bins.append({"forecast": sum(p for p, y in pairs) / n, "observed": frequency, "n": n,
                         "low": max(0, center - half), "high": min(1, center + half)})
        base = (climatology or {}).get(name)
        base_brier = sum((base - float(y)) ** 2 for y in outcomes) / len(valid) if base is not None else None
        result[name] = {"threshold": threshold, "n": len(valid), "events": events, "brier": round(brier, 5),
                        "brier_skill": 1 - brier / base_brier if base_brier else None,
                        "detection_rate": hits / events if events else None,
                        "false_alarm_ratio": false_alarms / (hits + false_alarms) if hits + false_alarms else None,
                        "false_alarm_rate": false_alarms / negatives if negatives else None,
                        "hits": hits, "misses": misses, "false_alarms": false_alarms,
                        "reliability": bins, "ece": sum(b["n"] * abs(b["forecast"] - b["observed"]) for b in bins) / len(valid)}
    return result


def event_gate(validation: dict, test: dict, manifest: dict, fresh_test: bool) -> dict:
    """Conservative research approval policy, not operational certification."""
    reasons = []
    if manifest.get("kind") != "quality_checked_observations":
        reasons.append("Station or gauge observations have not been supplied; the reference is reanalysis.")
    if not manifest.get("units_verified") or not manifest.get("spatial_support_verified"):
        reasons.append("Observation units or spatial representativeness have not been verified.")
    if not fresh_test:
        reasons.append("The test calendar has already been reviewed; new unseen dates are required.")
    for name, metrics in (("Validation", validation), ("Test", test)):
        if metrics.get("n", 0) < 100 or metrics.get("events", 0) < 20:
            reasons.append(f"{name} has {metrics.get('events', 0)} events in {metrics.get('n', 0)} cases; policy requires 20 events and 100 cases.")
        if metrics.get("brier_skill") is None or metrics["brier_skill"] <= 0:
            reasons.append(f"{name} has not demonstrated positive Brier skill against training climatology.")
        if metrics.get("ece", 1) > .15 or any(b["high"] - b["low"] > .4 for b in metrics.get("reliability", [])):
            reasons.append(f"{name} reliability is weak or its sampling uncertainty is too large.")
    return {"approved": not reasons, "reasons": reasons,
            "policy": "Research gate: >=100 cases and >=20 events per period, positive Brier skill, ECE <=0.15, occupied-bin Wilson width <=0.4. This is not operational certification."}


def select_methods(validation: list[dict], point_candidates: tuple, probability_candidates: tuple) -> tuple[str, str]:
    """Development-only selection. Final test results never enter this function."""
    point_scores = {k: score(validation, k)["mae"] for k in point_candidates}
    winner = min((k for k, v in point_scores.items() if v is not None), key=point_scores.get)
    baseline = min(("equal", "fixed"), key=point_scores.get)
    if winner in ("adaptive", "router") and point_scores[winner] > point_scores[baseline] * .99:
        winner = baseline
    probability_scores = {k: probability_score(validation, k) for k in probability_candidates}
    best = min((k for k, v in probability_scores.items() if v["rps"] is not None), key=lambda k: probability_scores[k]["rps"])
    raw = probability_scores["raw"]
    if best != "raw" and (probability_scores[best]["rps"] > raw["rps"] * .99
                           or probability_scores[best]["brier"] > raw["brier"] * 1.05):
        best = "raw"
    return winner, best


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


def candidate_predictions(row: dict, history: list[dict], product: dict, missing: str | None = None) -> dict:
    """One inference path shared by evaluation, replay and issuance."""
    models = product["models"]
    if missing:
        row = {**row, missing: None}
        history = [{**r, missing: None} for r in history]
    past = available_history(history, row)
    present = [s for s in SOURCES if row.get(s) is not None]
    equal = {s: 1 / len(present) for s in present} if present else {}
    fixed = normalize({s: models["fixed_weights"].get(s, 0) for s in present})
    adaptive = weights(row, past, True)
    thresholds = product["thresholds"]
    if missing:
        calibration = models["subsets"][missing]["calibration"]
        raw = base_distribution(row, past, adaptive, thresholds)
        pbc = correct(raw, [lag_features(row, past, t) for t in thresholds], calibration) if raw else None
        return {"points": {"equal": point(row, equal)}, "point_weights": {"equal": equal},
                "cdfs": {"raw": raw, "pbc": pbc}, "probability_weights": {"raw": adaptive, "pbc": adaptive}}
    routed, explanation = router_weights(row, past, models["router"], fixed)
    raw = base_distribution(row, past, adaptive, thresholds)
    equal_raw = base_distribution(row, past, equal, thresholds)
    fixed_raw = base_distribution(row, past, fixed, thresholds)
    router_raw = base_distribution(row, past, routed, thresholds)
    lags = [lag_features(row, past, t) for t in thresholds]
    debias, persistence = correct_parts(raw, lags, models["calibration"]["raw"]) if raw else (None, None)
    pbc = [(a + b) / 2 for a, b in zip(debias, persistence)] if raw else None
    router_pbc = correct(router_raw, lags, models["calibration"]["router_raw"]) if router_raw else None
    two_part = rainfall_cdf(row, past, adaptive, thresholds, models["rainfall"]) if row["variable"] == "rain" else None
    point_weights = {"equal": equal, "fixed": fixed, "adaptive": adaptive, "router": routed,
                     **{s: {s: 1.0} for s in present}, **{s + "_corrected": {s: 1.0} for s in present}}
    points = {k: point(row, w) for k, w in point_weights.items()}
    points.update({s + "_corrected": corrected_source(row, past, s) for s in present})
    return {"points": points, "point_weights": point_weights,
            "cdfs": {"raw": raw, "equal_raw": equal_raw, "fixed_raw": fixed_raw, "pbc": pbc, "debias": debias, "persistence": persistence,
                     "router_raw": router_raw, "router_pbc": router_pbc, "two_part": two_part},
            "probability_weights": {"raw": adaptive, "equal_raw": equal, "fixed_raw": fixed, "pbc": adaptive, "debias": adaptive, "persistence": adaptive,
                                    "router_raw": routed, "router_pbc": routed, "two_part": adaptive},
            "router_explanation": explanation, "router_weights": routed}


def confidence_calibration(product: dict, missing: str | None = None) -> dict | None:
    """Only released development reliability can inform a future evidence index."""
    policy = product.get("outages", {}).get(missing, {}) if missing else product
    primary = "heavy" if product["variable"] == "rain" else "defined_event"
    evidence = policy.get("event_scores", {}).get("validation", {}).get(primary, {})
    if evidence.get("n", 0) < 100 or evidence.get("events", 0) < 20:
        return None
    return {**evidence, "available_at": product["selection_cutoff"],
            "variable": product["variable"], "lead_hours": product["lead_hours"]}


def _fit_router_calibration(train: list[dict], model: dict, thresholds: list[float]) -> list[dict]:
    samples = []
    cutoff = datetime.fromisoformat(model["as_of"])
    for row in train:
        if row["issue_time"] < cutoff:
            continue
        past = available_history(train, row)
        if len(past) < 30:
            continue
        w, _ = router_weights(row, past, model, weights(row, past, False))
        raw = base_distribution(row, past, w, thresholds)
        if raw:
            samples.append((raw, [lag_features(row, past, t) for t in thresholds],
                            [float(row["observation"] < t) for t in thresholds]))
    return fit_pbc(samples, len(thresholds))


def evaluate(cases_path: Path, output: Path, manifest: dict | None = None) -> dict:
    manifest = manifest or {}
    if manifest.get("cases_sha256") and hashlib.sha256(cases_path.read_bytes()).hexdigest() != manifest["cases_sha256"]:
        raise ValueError("Case table does not match the supplied manifest hash")
    cases = [r for r in read_cases(cases_path) if r["observation"] is not None and sum(r[s] is not None for s in SOURCES) >= 2]
    dates = sorted({r["issue_time"].date() for r in cases})
    if len(dates) < 90:
        raise ValueError("At least 90 distinct issue dates are needed to train, select and test methods")
    train_end, valid_end = dates[int(len(dates) * .65)], dates[int(len(dates) * .8)]
    train_cutoff = datetime.combine(train_end, datetime.min.time(), timezone.utc)
    selection_cutoff = datetime.combine(valid_end, datetime.min.time(), timezone.utc)
    previous = manifest.get("previously_reviewed_test_through")
    fresh_test = bool(previous and valid_end.isoformat() > previous)
    groups = defaultdict(list)
    for row in cases:
        groups[task(row)].append(row)
    results, displays = [], []
    point_names = (*SOURCES, *(s + "_corrected" for s in SOURCES), "equal", "fixed", "adaptive", "router")
    for (variable, lead), group in groups.items():
        train = [r for r in group if r["issue_time"].date() < train_end and r["observed_at"] < train_cutoff]
        validation = [r for r in group if train_end <= r["issue_time"].date() < valid_end and r["observed_at"] < selection_cutoff and all(r[s] is not None for s in SOURCES)]
        test = [r for r in group if r["issue_time"].date() >= valid_end and all(r[s] is not None for s in SOURCES)]
        if len(train) < 60 or len(validation) < 15 or len(test) < 15:
            continue
        train_dates = sorted({r["issue_time"].date() for r in train})
        if len(train_dates) < 60:
            continue
        # Router/rain fit precedes their calibration examples, avoiding in-sample router calibration.
        model_cutoff = datetime.combine(train_dates[int(len(train_dates) * .7)], datetime.min.time(), timezone.utc)
        router = fit_router(train, model_cutoff)
        rainfall = fit_rainfall(train, model_cutoff) if variable == "rain" else {}
        q = quantiles([r["observation"] for r in train], variable)
        hazard_targets = RAIN_THRESHOLDS if variable == "rain" else {"defined_event": HAZARDS[variable][0]}
        thresholds = sorted(set(q + list(hazard_targets.values())))
        hazard_index = thresholds.index(HAZARDS[variable][0])
        fixed = weights({**train[0], **{s: 0.0 for s in SOURCES}}, train, False)
        models = {"history_before": train_cutoff.isoformat(), "model_fit_before": model_cutoff.isoformat(),
                  "router": router, "rainfall": rainfall, "fixed_weights": fixed,
                  "calibration": {"raw": fit_pbc_history(train, thresholds),
                                  "router_raw": _fit_router_calibration(train, router, thresholds)},
                  "subsets": {s: {"calibration": fit_pbc_history([{**r, s: None} for r in train], thresholds)} for s in SOURCES}}
        product = {"models": models, "thresholds": thresholds}
        probability_names = ["raw", "equal_raw", "fixed_raw", "pbc", "debias", "persistence", "router_raw", "router_pbc"]
        if variable == "rain" and all(s in rainfall.get("sources", {}) for s in SOURCES) and rainfall.get("source_patterns", {}).get(",".join(SOURCES), 0) >= 12:
            probability_names.append("two_part")

        def predict(part):
            predicted = []
            for row in part:
                calculated = candidate_predictions(row, train, product)
                if any(calculated["cdfs"].get(k) is None for k in probability_names):
                    continue
                result = {**row, **calculated["points"], **calculated["cdfs"],
                          "point_weights": calculated["point_weights"], "probability_weights_all": calculated["probability_weights"],
                          "router_explanation": calculated["router_explanation"], "router_weights": calculated["router_weights"],
                          "thresholds": thresholds, "quantile_indices": [thresholds.index(t) for t in q],
                          "hazard_threshold": HAZARDS[variable][0], "hazard_index": hazard_index, "subsets": {}}
                for source in SOURCES:
                    subset = candidate_predictions(row, train, product, source)
                    result["outage_" + source] = subset["points"]["equal"]
                    result["subsets"][source] = subset
                predicted.append(result)
            return predicted

        val, held = predict(validation), predict(test)
        if len(val) < 15 or len(held) < 15:
            continue
        prob_candidates = tuple(k for k in probability_names if k not in ("debias", "persistence"))
        winner, prob_winner = select_methods(val, point_names, prob_candidates)
        rates = {name: sum(r["observation"] >= t for r in train) / len(train) for name, t in hazard_targets.items()}
        val_events = event_scores(val, prob_winner, hazard_targets, rates)
        held_events = event_scores(held, prob_winner, hazard_targets, rates)
        approvals = {name: event_gate(val_events[name], held_events[name], manifest, fresh_test) for name in hazard_targets}
        outages = {}
        for source in SOURCES:
            key = "outage_" + source
            subset_val = [{**r, **r["subsets"][source]["cdfs"]} for r in val]
            subset_test = [{**r, **r["subsets"][source]["cdfs"]} for r in held]
            vs, ts = score(val, key), score(held, key)
            subset_method = "pbc" if probability_score(subset_val, "pbc")["rps"] < probability_score(subset_val, "raw")["rps"] * .99 else "raw"
            sv = event_scores(subset_val, subset_method, hazard_targets, rates)
            st = event_scores(subset_test, subset_method, hazard_targets, rates)
            outages[source] = {"approved": bool(vs["n"] >= 15 and vs["mae"] <= score(val, winner)["mae"] * 1.2),
                               "validation": vs, "test": ts, "selection_basis": "development only",
                               "probability_method": subset_method,
                               "probability_validation": probability_score(subset_val, subset_method),
                               "probability_test": probability_score(subset_test, subset_method),
                               "event_scores": {"validation": sv, "test": st},
                               "event_approval": {name: event_gate(sv[name], st[name], manifest, fresh_test) for name in hazard_targets}}
        metric = {"variable": variable, "lead_hours": lead, "train_n": len(train), "validation_n": len(val), "test_n": len(held),
                  "point_method": winner, "probability_method": prob_winner,
                  "deployment_point_method": winner, "deployment_probability_method": prob_winner,
                  "deployment_note": "Methods frozen from development data; final test is reporting only",
                  "selection_basis": "development only", "selection_cutoff": selection_cutoff.isoformat(), "fresh_test_calendar": fresh_test,
                  "evaluation_status": "Unreviewed test calendar, retrospective reference" if fresh_test else "Exploratory previously reviewed or unverified test calendar",
                  "paired_uncertainty": {"point_vs_equal": paired_interval(held, winner, "equal", "point"),
                                         "probability_vs_raw": paired_interval(held, prob_winner, "raw", "probability")},
                  "validation": {"point": {k: score(val, k) for k in point_names},
                                 "probability": {k: probability_score(val, k) for k in probability_names}},
                  "test": {"point": {k: score(held, k) for k in point_names},
                           "probability": {k: probability_score(held, k) for k in probability_names}},
                  "thresholds": thresholds, "models": models, "outages": outages, "event_approval": approvals,
                  "hazard": "08:30 IST reporting-day rain >= 64.5 mm (research guidance)" if variable == "rain" and "03:00 UTC" in manifest.get("rain_window", "") else HAZARDS[variable][1],
                  "hazard_status": "candidate tested" if all(a["approved"] for a in approvals.values()) else "evidence gate not passed",
                  "event_scores": {"validation": val_events, "test": held_events},
                  "ablation": [], "common_case_policy": "All candidates use identical complete-input verification cases"}
        for label, pk, ck in [("Equal blend", "equal", "equal_raw"), ("Fixed blend", "fixed", "fixed_raw"),
                               ("Contextual blend", "adaptive", "raw"), ("Forecast router", "router", "router_raw"),
                               ("Router + PBC", "router", "router_pbc")]:
            metric["ablation"].append({"method": label, "point": score(held, pk), "probability": probability_score(held, ck),
                                      "events": event_scores(held, ck, hazard_targets, rates)})
        best_source = min(SOURCES, key=lambda s: score(val, s)["mae"])
        single_rows = []
        for row in held:
            single_rows.append({**row, "single_cdf": base_distribution(row, available_history(train, row), {best_source: 1.0}, thresholds)})
        metric["ablation"].insert(0, {"method": "Best individual (development): " + best_source,
                                    "point": score(held, best_source), "probability": probability_score(single_rows, "single_cdf"),
                                    "events": event_scores(single_rows, "single_cdf", hazard_targets, rates)})
        if "two_part" in probability_names:
            metric["ablation"].append({"method": "Two-part rainfall", "point": None, "probability": probability_score(held, "two_part"),
                                      "events": event_scores(held, "two_part", hazard_targets, rates)})
        confidence_rows = {"validation": [], "test": []}
        for part_name, part in (("validation", val), ("test", held)):
            for row in part:
                selected_weights = row["point_weights"][winner]
                confidence = assess(row, train, selected_weights, row[winner],
                                    source_status={s: {"run_initialized_at": row["issue_time"].isoformat(), "publication_time": None} for s in SOURCES},
                                    calibration=confidence_calibration(metric))
                confidence_rows[part_name].append({**row, "point": row[winner], "confidence": confidence})
                if part_name != "test":
                    continue
                cdf = row[prob_winner]
                displays.append({"issue_time": row["issue_time"].isoformat(), "valid_time": row["valid_time"].isoformat(),
                                 "region": row["region"], "latitude": row["latitude"], "longitude": row["longitude"],
                                 "variable": variable, "lead_hours": lead, "observation": row["observation"],
                                 "point": round(row[winner], 3), "hazard_probability": round(1 - cdf[hazard_index], 4),
                                 "event_probabilities": {name: round(1 - cdf[thresholds.index(t)], 4) for name, t in hazard_targets.items()},
                                 "sources": {s: row[s] for s in SOURCES}, "weights": selected_weights,
                                 "probability_weights": row["probability_weights_all"][prob_winner],
                                 "outage_points": {s: row["outage_" + s] for s in SOURCES},
                                 "outage_probabilities": {s: round(1 - row["subsets"][s]["cdfs"][outages[s]["probability_method"]][hazard_index], 4) for s in SOURCES},
                                 "outage_weights": {s: row["subsets"][s]["probability_weights"][outages[s]["probability_method"]] for s in SOURCES},
                                 "point_method": winner, "probability_method": prob_winner, "confidence": confidence,
                                 "outage_confidence": {s: assess({**row, s: None}, train,
                                     row["subsets"][s]["point_weights"]["equal"], row["outage_" + s],
                                     source_status={name: {"run_initialized_at": row["issue_time"].isoformat(),
                                                           "publication_time": None, "status": "unavailable" if name == s else "archived"} for name in SOURCES},
                                     calibration=confidence_calibration(metric, s)) if outages[s]["approved"] else None for s in SOURCES},
                                 "disagreement": disagreement(row), "router_weights": row["router_weights"],
                                 "router_explanation": row["router_explanation"], "status": "held-out historical case",
                                 "observation_source": manifest.get("truth", "user-supplied observations"),
                                 "source_availability": "historical publication times unverified"})
        metric["confidence_evaluation"] = {name: summarize(rows) for name, rows in confidence_rows.items()}
        results.append(metric)
    if not results:
        raise ValueError("No variable and lead has enough complete common cases for evaluation")
    output.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": 2, "data_kind": manifest.get("kind", "external_case_table"),
              "truth": manifest.get("truth", "user-supplied observations"),
              "limitations": manifest.get("availability", "Check release timing"), "manifest": manifest,
              "split": {"train_before": train_end.isoformat(), "validation_before": valid_end.isoformat(), "test_from": valid_end.isoformat()},
              "verification_policy": {"selection": "development only", "selection_cutoff": selection_cutoff.isoformat(), "previously_reviewed_test_through": previous,
                                      "fresh_test_calendar": fresh_test, "claim": "Retrospective research comparison; not operational certification"},
              "coverage": {"regions": sorted({r["region"] for r in cases}), "issue_dates": len(dates),
                           "seasons": sorted({season(r) for r in cases}), "start": dates[0].isoformat(), "end": dates[-1].isoformat()},
              "units": {k: value[1] for k, value in VARIABLES.items()},
              "target_registry": {"rain": {"status": "per-event evidence gates", "thresholds_mm": RAIN_THRESHOLDS},
                                  "heat_wave": {"status": "daily maximum, normals, setting and persistence observations needed"},
                                  "wind_gust": {"status": "gust observations and forecasts needed"}},
              "results": results, "cases": displays}
    target = output / "state.json"
    temporary = output / "state.json.tmp"
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(target)
    return report
