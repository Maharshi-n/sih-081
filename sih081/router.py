"""Small pooled expected-error router with strictly released, past-only features."""
from __future__ import annotations

import math
from datetime import datetime

import numpy as np

from .data import SOURCES

MIN_DATES = 30
RIDGE = 20.0


def _present(row: dict) -> list[str]:
    return [s for s in SOURCES if isinstance(row.get(s), (int, float)) and math.isfinite(row[s])]


def released_history(rows: list[dict], row: dict) -> list[dict]:
    """Release at issue time is excluded; the current target is never a feature."""
    return sorted((r for r in rows if r["variable"] == row["variable"]
                   and r["lead_hours"] == row["lead_hours"]
                   and r["issue_time"] < row["issue_time"]
                   and r["observed_at"] < row["issue_time"]
                   and r.get("observation") is not None and math.isfinite(r["observation"])),
                  key=lambda r: (r["issue_time"], r["region"]))


def disagreement(row: dict) -> dict:
    present = _present(row)
    values = [row[s] for s in present]
    average = sum(values) / len(values) if values else 0.0
    std = math.sqrt(sum((x - average) ** 2 for x in values) / len(values)) if values else 0.0
    return {"std": std, "range": max(values) - min(values) if values else 0.0,
            "relative": std / max(abs(average), 1.0),
            "pairwise": {a + "_" + b: abs(row[a] - row[b])
                         for i, a in enumerate(present) for b in present[i + 1:]}}


def _season(row: dict) -> str:
    month = row["valid_time"].month
    if row["region"] == "Chennai" and month in (10, 11, 12):
        return "northeast"
    return "southwest" if month in (6, 7, 8, 9) else "other"


def _age(row: dict, source: str) -> float | None:
    try:
        value = (row.get("source_age_hours") or {}).get(source)
        if value is None:
            runs = row.get("source_run_times") or row.get("source_runs") or {}
            run = runs.get(source)
            if run:
                run = datetime.fromisoformat(run.replace("Z", "+00:00")) if isinstance(run, str) else run
                if run.tzinfo is None:
                    return None
                value = (row["issue_time"] - run).total_seconds() / 3600
        return float(value) if value is not None and math.isfinite(float(value)) and float(value) >= 0 else None
    except (ValueError, TypeError, AttributeError):
        return None


def features(row: dict, history: list[dict], regions: list[str] | tuple = ()) -> dict[str, float]:
    history = released_history(history, row)
    local = [r for r in history if r["region"] == row["region"]]
    reference = local if len(local) >= 12 else history
    recent_dates = sorted({r["issue_time"].date() for r in reference})[-30:]
    recent = [r for r in reference if r["issue_time"].date() in recent_dates]
    climate = [r["observation"] for r in local if _season(r) == _season(row)]
    if len(climate) < 12:
        climate = [r["observation"] for r in reference]
    present = _present(row)
    magnitude = sum(row[s] for s in present) / len(present) if present else 0.0
    spread = disagreement(row)
    month = row["valid_time"].month
    result = {"lead_hours": float(row["lead_hours"]), "month_sin": math.sin(month * math.tau / 12),
              "month_cos": math.cos(month * math.tau / 12), "season_southwest": float(_season(row) == "southwest"),
              "season_northeast": float(_season(row) == "northeast"), "forecast_mean": magnitude,
              "regime_elevated": float(magnitude >= {"rain": 10, "temperature": 35, "wind": 10}[row["variable"]]),
              "disagreement_std": spread["std"], "disagreement_range": spread["range"],
              "disagreement_relative": spread["relative"],
              "climatology_deviation": magnitude - sum(climate) / len(climate) if climate else 0.0,
              "climatology_known": float(bool(climate)), "history_dates": float(len(recent_dates)),
              "region_unknown": float(row["region"] not in regions)}
    result.update({"region_" + region: float(row["region"] == region) for region in regions})
    cuts = {"rain": [0.1, 10, 64.5, 115.6, 204.5], "temperature": [15, 25, 35, 40], "wind": [2, 5, 10, 15]}[row["variable"]]
    for source in SOURCES:
        errors = [abs(r[source] - r["observation"]) for r in recent if source in _present(r)]
        rps = []
        for old in recent:
            if source not in _present(old):
                continue
            residuals = [r["observation"] - r[source] for r in reference
                         if r["issue_time"] < old["issue_time"] and r["observed_at"] < old["issue_time"]
                         and source in _present(r)]
            if len(residuals) >= 12:
                samples = [old[source] + error for error in residuals]
                if row["variable"] != "temperature":
                    samples = [max(0, x) for x in samples]
                rps.append(sum(((sum(x < t for x in samples) + 0.5) / (len(samples) + 1)
                                - float(old["observation"] < t)) ** 2 for t in cuts) / len(cuts))
        age = _age(row, source)
        result.update({"forecast_" + source: row[source] if source in present else magnitude,
                       "available_" + source: float(source in present),
                       "age_hours_" + source: age if age is not None else 0.0,
                       "age_known_" + source: float(age is not None),
                       "rolling_mae_" + source: sum(errors) / len(errors) if errors else 0.0,
                       "rolling_mae_known_" + source: float(bool(errors)),
                       "rolling_rps_" + source: sum(rps) / len(rps) if rps else 0.0,
                       "rolling_rps_known_" + source: float(bool(rps))})
    for i, a in enumerate(SOURCES):
        for b in list(SOURCES)[i + 1:]:
            result["difference_" + a + "_" + b] = row[a] - row[b] if a in present and b in present else 0.0
    return result


def _ridge(x: np.ndarray, y: np.ndarray) -> dict:
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale < 1e-8] = 1.0
    z = (x - mean) / scale
    intercept = float(y.mean())
    beta = np.linalg.solve(z.T @ z + np.eye(x.shape[1]) * RIDGE, z.T @ (y - intercept))
    return {"mean": mean.tolist(), "scale": scale.tolist(), "beta": beta.tolist(), "intercept": intercept}


def _predict(factors: dict, names: list[str], model: dict) -> tuple[float, dict]:
    contributions = {name: (factors.get(name, 0.0) - mean) / scale * beta
                     for name, mean, scale, beta in zip(names, model["mean"], model["scale"], model["beta"])}
    return model["intercept"] + sum(contributions.values()), contributions


def fit_router(train: list[dict], as_of: datetime) -> dict:
    eligible = [r for r in train if r["issue_time"] < as_of and r["observed_at"] < as_of
                and r.get("observation") is not None and math.isfinite(r["observation"])]
    tasks = {(r["variable"], r["lead_hours"]) for r in eligible}
    if len(tasks) > 1:
        raise ValueError("Fit one router per variable and lead, pooling regions")
    model = {"version": "ridge-error-v1", "as_of": as_of.isoformat(), "trained": False,
             "task": list(next(iter(tasks))) if tasks else None,
             "regions": sorted({r["region"] for r in eligible}), "sources": {}, "source_patterns": {},
             "evidence_dates": len({r["issue_time"].date() for r in eligible}), "n": len(eligible)}
    samples = []
    for row in eligible:
        history = released_history(eligible, row)
        if len({r["issue_time"].date() for r in history}) >= 12:
            samples.append((row, features(row, history, model["regions"])))
    if not samples:
        return model
    names = list(samples[0][1])
    model["feature_names"] = names
    patterns = {",".join(_present(r)) for r, _ in samples}
    model["source_patterns"] = {p: len({r["issue_time"].date() for r, _ in samples if ",".join(_present(r)) == p}) for p in patterns}
    for source in SOURCES:
        selected = [(r, f) for r, f in samples if source in _present(r)]
        dates = len({r["issue_time"].date() for r, _ in selected})
        if dates >= MIN_DATES and len(selected) >= 40:
            x = np.asarray([[f[name] for name in names] for _, f in selected])
            y = np.asarray([abs(r[source] - r["observation"]) for r, _ in selected])
            model["sources"][source] = {**_ridge(x, y), "n": len(selected), "dates": dates}
    model["trained"] = bool(model["sources"])
    return model


def router_weights(row: dict, history: list[dict], model: dict, baseline: dict) -> tuple[dict, dict]:
    present = _present(row)
    if any(not math.isfinite(float(baseline.get(s, 0))) or float(baseline.get(s, 0)) < 0 for s in present):
        raise ValueError("Baseline weights must be finite and nonnegative")
    base = {s: max(0.0, float(baseline.get(s, 0))) for s in present}
    total = sum(base.values())
    base = {s: value / total if total else 1 / len(present) for s, value in base.items()}
    factors = features(row, history, model.get("regions", []))
    explanation = {"method": "baseline fallback", "predicted_errors": {}, "drivers": {}, "shrinkage": 0.0,
                   "evidence_dates": 0, "feature_values": factors, "reason": "Not enough released router training evidence"}
    cutoff = datetime.fromisoformat(model["as_of"])
    eligible = released_history(history, row)
    dates = len({r["issue_time"].date() for r in eligible if r["region"] == row["region"]})
    explanation["evidence_dates"] = dates
    if model.get("task") != [row["variable"], row["lead_hours"]] or cutoff > row["issue_time"]:
        explanation["reason"] = "Router task or training cutoff does not match this issue"
        return base, explanation
    if model.get("source_patterns", {}).get(",".join(present), 0) < 12:
        explanation["reason"] = "This source combination lacks router training evidence"
        return base, explanation
    if not present or any(s not in model.get("sources", {}) for s in present) or dates < 12:
        return base, explanation
    inverse = {}
    for source in present:
        prediction, drivers = _predict(factors, model["feature_names"], model["sources"][source])
        error = max(0.1, prediction)
        explanation["predicted_errors"][source] = error
        explanation["drivers"][source] = [{"feature": name, "error_contribution": value}
                                            for name, value in sorted(drivers.items(), key=lambda x: abs(x[1]), reverse=True)[:4]]
        inverse[source] = 1 / error
    strength = min(dates, min(model["sources"][s]["dates"] for s in present))
    alpha = strength / (strength + 60)
    total = sum(inverse.values())
    weights = {s: (1 - alpha) * base[s] + alpha * inverse[s] / total for s in present}
    explanation.update(method="ridge expected error with baseline shrinkage", shrinkage=alpha,
                       reason="Source errors learned on released training labels; coefficients explain expected-error changes")
    return weights, explanation
