"""Configured run-age policy. Retrieval time does not make an old model fresh."""
from datetime import datetime, timezone

# Initial review policy, not a provider guarantee or a learned skill threshold.
MAX_AGE_HOURS = {"IFS": 24.0, "GFS": 24.0, "AIFS": 24.0}
SLIGHTLY_STALE_AFTER_HOURS = 12.0


def source_freshness(run, as_of, source: str) -> dict:
    result = {"status": "invalid", "age_hours": None,
              "max_age_hours": MAX_AGE_HOURS.get(source),
              "slightly_stale_after_hours": SLIGHTLY_STALE_AFTER_HOURS,
              "policy": "configured run-age review policy"}
    try:
        def utc(value):
            value = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
            if not isinstance(value, datetime) or value.tzinfo is None:
                raise ValueError("timestamp must include a timezone")
            return value.astimezone(timezone.utc)
        run, as_of = utc(run), utc(as_of)
        age = (as_of - run).total_seconds() / 3600
        result.update(run_time=run.isoformat(), as_of=as_of.isoformat(), age_hours=round(age, 3))
        if source not in MAX_AGE_HOURS or age < 0:
            raise ValueError("unknown source or model run is in the future")
        result["status"] = "too stale" if age > MAX_AGE_HOURS[source] else "slightly stale" if age > SLIGHTLY_STALE_AFTER_HOURS else "fresh"
        result["reason"] = {"fresh": "Model run is within 12 hours", "slightly stale": "Model run is over 12 hours old but within the 24 hour limit", "too stale": "Model run exceeds the 24 hour age limit"}[result["status"]]
    except (ValueError, TypeError, AttributeError) as error:
        result["reason"] = str(error)
    return result
