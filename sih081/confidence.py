"""Transparent experimental evidence index, never a weather probability."""
from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np

from .data import SOURCES


FACTOR_WEIGHTS = {"agreement": .20, "skill": .20, "evidence": .20,
                  "freshness": .15, "similarity": .15, "reliability": .10}


def _time(value):
    if value is None:
        return None
    try:
        value = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def _number(value):
    try:
        return float(value) if math.isfinite(float(value)) else None
    except (ValueError, TypeError):
        return None


def assess(row, history, weights, point_value, source_status=None, as_of=None, calibration=None):
    """Use only released compatible regional evidence at this issue time.

    Historical errors replay the current weights; they are descriptive proxies,
    not independent verification of the selected/corrected point forecast.
    Unknown evidence contributes zero rather than reallocating its weight.
    """
    issue = _time(row.get("issue_time"))
    when = _time(as_of) or issue
    cutoff = min(issue, when) if issue and when else issue
    local = [r for r in history if cutoff and _time(r.get("observed_at"))
             and _time(r.get("observed_at")) <= cutoff
             and _time(r.get("issue_time")) and _time(r["issue_time"]) < cutoff
             and r.get("region") == row.get("region")
             and r.get("variable") == row.get("variable")
             and r.get("lead_hours") == row.get("lead_hours")
             and _number(r.get("observation")) is not None]
    present = {s: _number(row.get(s)) for s in SOURCES if _number(row.get(s)) is not None}
    chosen = {s: float(w) for s, w in weights.items() if s in present and _number(w) is not None and float(w) > 0}
    total = sum(chosen.values())
    chosen = {s: w / total for s, w in chosen.items()} if total else {}
    floor = 5.0 if row.get("variable") == "rain" else 1.0
    truths = [float(r["observation"]) for r in local]
    scale = max(floor, float(np.std(truths))) if truths else floor
    errors = [abs(sum(float(r[s]) * w for s, w in chosen.items()) - float(r["observation"]))
              for r in local if chosen and all(_number(r.get(s)) is not None for s in chosen)]
    uncertainty = ["The evidence index is experimental and is not a probability of correct weather.",
                   "Model agreement does not establish accuracy."]
    factors = {}

    def add(name, value, detail, status="available"):
        factors[name] = {"value": round(min(1, max(0, value)), 4) if value is not None else None,
                         "weight": FACTOR_WEIGHTS[name], "status": status, "detail": detail}
        if value is None or status != "available":
            uncertainty.append(detail)

    spread = float(np.std(list(present.values()))) if len(present) >= 2 else None
    add("agreement", 1 / (1 + spread / scale) if spread is not None else None,
        "Source spread relative to historical observation variation; at least two sources are required.")
    estimated = float(np.mean(errors)) if errors else None
    add("skill", 1 / (1 + estimated / scale) if estimated is not None else None,
        "Current weights replayed on released regional history; this is a historical error proxy, not a validation score.")
    dates = len({_time(r["issue_time"]).date() for r in local})
    add("evidence", dates / (dates + 30), f"{len(local)} released compatible regional cases across {dates} issue dates.")

    ages = []
    freshness_notes = []
    for s in SOURCES:
        if s not in present:
            ages.append(0)
            freshness_notes.append(f"{s} input missing")
            continue
        meta = (source_status or {}).get(s, {})
        fresh = meta.get("freshness", meta)
        status = str(fresh.get("status", meta.get("status", "unknown"))).lower().replace("_", " ")
        run = _time(meta.get("run_initialized_at") or meta.get("run_time"))
        age = (when - run).total_seconds() / 3600 if when and run else _number(fresh.get("age_hours"))
        limit = _number(fresh.get("max_age_hours", meta.get("max_age_hours"))) or 24.0
        if status in ("unavailable", "missing", "too stale", "stale", "excluded") or (age is not None and (age < 0 or age > limit)):
            value = 0
            freshness_notes.append(f"{s} missing, excluded or beyond its run age limit")
        elif age is not None:
            value = max(0, 1 - age / limit)
            freshness_notes.append(f"{s} run age {age:.1f} h")
        elif status == "fresh":
            value = .5
            freshness_notes.append(f"{s} marked fresh but run age unverified")
        else:
            value = .25
            freshness_notes.append(f"{s} run age unknown")
        published = _time(meta.get("publication_time") or meta.get("available_at"))
        if published and cutoff and published > cutoff:
            value = 0
            freshness_notes.append(f"{s} published after the issue time")
        elif published is None:
            value *= .75
            freshness_notes.append(f"{s} original publication time unverified")
        ages.append(value)
    add("freshness", sum(ages) / len(SOURCES), "; ".join(freshness_notes),
        "limited" if any("unknown" in s or "unverified" in s or "missing" in s for s in freshness_notes) else "available")
    historical_means = [float(np.mean([float(r[s]) for s in chosen])) for r in local
                        if chosen and all(_number(r.get(s)) is not None for s in chosen)]
    current_mean = float(np.mean([present[s] for s in chosen])) if chosen else None
    similarity = None
    if historical_means and current_mean is not None:
        centre = float(np.mean(historical_means))
        width = max(floor, float(np.std(historical_means)))
        similarity = 1 / (1 + abs(current_mean - centre) / width)
    add("similarity", similarity, "Forecast magnitude compared with released regional history; unseen regimes remain uncertain.")
    calibration = calibration or {}
    released = _time(calibration.get("available_at") or calibration.get("observed_at") or calibration.get("released_at"))
    calibration_n = _number(calibration.get("n")) or 0
    gap = _number(calibration.get("calibration_error", calibration.get("ece")))
    bins = calibration.get("reliability", [])
    if gap is None and bins:
        usable = [b for b in bins if _number(b.get("forecast")) is not None
                  and _number(b.get("observed")) is not None and (_number(b.get("n")) or 0) > 0]
        calibration_n = sum(float(b["n"]) for b in usable)
        gap = sum(float(b["n"]) * abs(float(b["forecast"]) - float(b["observed"])) for b in usable) / calibration_n if calibration_n else None
    compatible = all(calibration.get(k, row.get(k)) == row.get(k) for k in ("region", "variable", "lead_hours"))
    reliability = ((1 - min(1, max(0, gap))) * calibration_n / (calibration_n + 30)
                   if compatible and cutoff and released and released <= cutoff and gap is not None and calibration_n > 0 else None)
    add("reliability", reliability,
        "Calibration gap weighted by released calibration sample size." if reliability is not None
        else "No compatible calibration evidence with a verified release time was supplied.")
    score = round(100 * sum((f["value"] or 0) * f["weight"] for f in factors.values()), 1)
    if not present or _number(point_value) is None:
        score = 0.0
        uncertainty.append("No usable point forecast is available.")
    drivers = [f"{name}: {f['detail']}" for name, f in sorted(factors.items(), key=lambda p: p[1]["value"] or 0)[:3]]
    return {"score": score, "label": "Limited evidence" if score < 40 else "Moderate evidence" if score < 70 else "Stronger evidence",
            "status": "experimental", "factors": factors, "drivers": drivers,
            "uncertainty_reasons": uncertainty, "estimated_error": round(estimated, 4) if estimated is not None else None,
            "estimated_error_kind": "historical current-weight replay proxy", "evidence_count": len(local),
            "error_evidence_count": len(errors), "issue_dates": dates,
            "disagreement": {"std": round(spread, 4) if spread is not None else None,
                             "range": round(max(present.values()) - min(present.values()), 4) if present else None},
            "formula": {"weights": FACTOR_WEIGHTS, "unknown_factors": "contribute zero; weights are not redistributed",
                        "scale": round(scale, 4), "evidence_half_strength_issue_dates": 30,
                        "freshness_default_max_age_hours": 24, "tuned_on_test": False},
            "as_of": cutoff.isoformat() if cutoff else None}


def _ranks(values):
    order = np.argsort(values, kind="stable")
    result = np.empty(len(values), dtype=float)
    start = 0
    while start < len(values):
        end = start + 1
        while end < len(values) and values[order[end]] == values[order[start]]:
            end += 1
        result[order[start:end]] = (start + end - 1) / 2
        start = end
    return result


def summarize(cases):
    """Describe fixed-bin held-out errors without approving or tuning the index."""
    valid = [(r, _number(r.get("confidence", {}).get("score")), _number(r.get("point")), _number(r.get("observation"))) for r in cases]
    valid = [(r, s, abs(p - o)) for r, s, p, o in valid if s is not None and 0 <= s <= 100 and p is not None and o is not None]

    def group_summary(items):
        bins = []
        for low, high in ((0, 40), (40, 70), (70, 101)):
            errors = [error for _, s, error in items if low <= s < high]
            bins.append({"min_score": low, "max_score_exclusive": high, "n": len(errors),
                         "mae": round(float(np.mean(errors)), 4) if errors else None})
        scores = _ranks([s for _, s, _ in items])
        errors = _ranks([e for _, _, e in items])
        association = (float(np.corrcoef(scores, errors)[0, 1])
                       if len(items) >= 3 and np.std(scores) > 0 and np.std(errors) > 0 else None)
        return {"n": len(items), "bins": bins, "score_error_rank_association": round(association, 4) if association is not None else None}

    groups = {}
    for row, score, error in valid:
        key = f"{row.get('variable', 'unknown')}|{row.get('lead_hours', 'unknown')}"
        groups.setdefault(key, []).append((row, score, error))
    result = group_summary(valid)
    if len({r.get("variable") for r, _, _ in valid}) > 1:
        result["score_error_rank_association"] = None
        for b in result["bins"]:
            b["mae"] = None
        result["note"] = "Mixed physical units: assess MAE and association separately by variable and lead."
    result.update({"status": "experimental", "approved": False, "tuned_on_test": False,
                   "association_interpretation": "Negative association suggests higher scores accompany smaller errors; this is descriptive, not operational approval.",
                   "by_product": {k: group_summary(v) for k, v in groups.items()}})
    return result


summarise = summarize
