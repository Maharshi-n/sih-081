"""Experimental dry/wet occurrence and truncated lognormal amount mixture.

Dry means less than 0.1 mm and is approximated by mass at zero. This is a
distribution candidate for comparison, not an official rainfall warning.
"""
from __future__ import annotations

import math
from datetime import datetime

import numpy as np

from .data import SOURCES
from .router import MIN_DATES, RIDGE, _predict, _present, _ridge, released_history

WET_CUTOFF = 0.1


def _features(row: dict, history: list[dict], source: str, regions: list[str]) -> dict:
    available = _present(row)
    values = [max(0, row[s]) for s in available]
    mean = sum(values) / len(values) if values else 0.0
    local = [r for r in released_history(history, row) if r["region"] == row["region"]]
    recent = local[-60:]
    month = row["valid_time"].month
    f = {"source_log_amount": math.log1p(max(0, row[source])), "ensemble_log_amount": math.log1p(mean),
         "spread_log": math.log1p(math.sqrt(sum((x - mean) ** 2 for x in values) / len(values))) if values else 0.0,
         "month_sin": math.sin(month * math.tau / 12), "month_cos": math.cos(month * math.tau / 12),
         "lead_hours": row["lead_hours"],
         "past_wet_frequency": (sum(r["observation"] >= WET_CUTOFF for r in recent) + 1) / (len(recent) + 2),
         "past_frequency_known": float(len(recent) >= 12)}
    f.update({"region_" + region: float(row["region"] == region) for region in regions})
    return f


def _fit_occurrence(x: np.ndarray, y: np.ndarray) -> dict:
    mean, scale = x.mean(axis=0), x.std(axis=0)
    scale[scale < 1e-8] = 1.0
    design = np.column_stack((np.ones(len(x)), (x - mean) / scale))
    beta = np.zeros(design.shape[1])
    beta[0] = math.log((float(y.sum()) + 1) / (len(y) - float(y.sum()) + 1))
    ridge = np.diag([1.0, *([RIDGE] * x.shape[1])])
    for _ in range(40):
        p = 1 / (1 + np.exp(-np.clip(design @ beta, -30, 30)))
        curvature = p * (1 - p)
        step = np.linalg.solve(design.T @ (curvature[:, None] * design) + ridge,
                               design.T @ (y - p) - ridge @ beta)
        beta += step
        if np.max(np.abs(step)) < 1e-6:
            break
    return {"mean": mean.tolist(), "scale": scale.tolist(), "intercept": float(beta[0]), "beta": beta[1:].tolist()}


def fit_rainfall(train: list[dict], as_of: datetime) -> dict:
    eligible = [r for r in train if r["variable"] == "rain" and r["issue_time"] < as_of
                and r["observed_at"] < as_of and r.get("observation") is not None
                and math.isfinite(r["observation"]) and r["observation"] >= 0]
    leads = {r["lead_hours"] for r in eligible}
    if len(leads) > 1:
        raise ValueError("Fit one rainfall distribution per lead, pooling regions")
    model = {"version": "occurrence-lognormal-v1", "as_of": as_of.isoformat(), "wet_cutoff_mm": WET_CUTOFF,
             "trained": False, "lead_hours": next(iter(leads)) if leads else None,
             "regions": sorted({r["region"] for r in eligible}), "sources": {}, "source_patterns": {},
             "n": len(eligible), "evidence_dates": len({r["issue_time"].date() for r in eligible})}
    patterns = {",".join(_present(r)) for r in eligible}
    model["source_patterns"] = {p: len({r["issue_time"].date() for r in eligible if ",".join(_present(r)) == p}) for p in patterns}
    for source in SOURCES:
        selected = [r for r in eligible if source in _present(r)]
        wet = [r for r in selected if r["observation"] >= WET_CUTOFF]
        if len({r["issue_time"].date() for r in selected}) < MIN_DATES or len(wet) < 12 or len(selected) - len(wet) < 12:
            continue
        factors = [_features(r, eligible, source, model["regions"]) for r in selected]
        names = list(factors[0])
        x = np.asarray([[f[k] for k in names] for f in factors])
        y = np.asarray([float(r["observation"] >= WET_CUTOFF) for r in selected])
        amount = _ridge(x[y == 1], np.log([r["observation"] for r in wet]))
        residuals = [math.log(r["observation"]) - _predict(f, names, amount)[0]
                     for r, f in zip(selected, factors) if r["observation"] >= WET_CUTOFF]
        amount["sigma"] = max(0.25, math.sqrt(sum(x * x for x in residuals) / len(residuals)))
        model["sources"][source] = {"feature_names": names, "occurrence": _fit_occurrence(x, y),
                                    "amount": amount, "n": len(selected), "wet_n": len(wet), "dry_n": len(selected) - len(wet)}
    model["trained"] = bool(model["sources"])
    return model


def rainfall_cdf(row: dict, history: list[dict], weights: dict, thresholds: list[float], model: dict) -> list[float] | None:
    """Return P(amount < threshold); each wet distribution is truncated at 0.1 mm."""
    if any(math.isnan(t) for t in thresholds) or thresholds != sorted(thresholds):
        raise ValueError("Rainfall thresholds must be ordered numbers")
    if any(not math.isfinite(float(value)) or float(value) < 0 for value in weights.values()):
        raise ValueError("Rainfall weights must be finite and nonnegative")
    cutoff = datetime.fromisoformat(model["as_of"])
    present = _present(row)
    if (row["variable"] != "rain" or model.get("lead_hours") != row["lead_hours"]
            or cutoff > row["issue_time"] or not model.get("trained")
            or model.get("source_patterns", {}).get(",".join(present), 0) < 12):
        return None
    usable = {s: float(weights[s]) for s in weights if s in present and weights[s] > 0}
    if not usable or any(s not in model["sources"] for s in usable):
        return None
    result = [0.0] * len(thresholds)
    total = sum(usable.values())
    for source, weight in usable.items():
        fit = model["sources"][source]
        f = _features(row, history, source, model["regions"])
        logit = _predict(f, fit["feature_names"], fit["occurrence"])[0]
        wet = 1 / (1 + math.exp(-min(30, max(-30, logit))))
        mu = max(math.log(WET_CUTOFF), _predict(f, fit["feature_names"], fit["amount"])[0])
        sigma = fit["amount"]["sigma"]
        lower = (1 + math.erf((math.log(WET_CUTOFF) - mu) / (sigma * math.sqrt(2)))) / 2
        for i, threshold in enumerate(thresholds):
            if threshold <= 0:
                probability = 0.0
            elif threshold <= WET_CUTOFF:
                probability = 1 - wet
            else:
                upper = (1 + math.erf((math.log(threshold) - mu) / (sigma * math.sqrt(2)))) / 2
                probability = 1 - wet + wet * (upper - lower) / (1 - lower)
            result[i] += weight / total * min(1.0, max(0.0, probability))
    return result
