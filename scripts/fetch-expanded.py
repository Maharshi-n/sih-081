"""Non-destructive expanded real archive download. Resumes from cached raw responses."""
import json
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sih081.data import PLACES, SOURCES, _check_payload, fetch_cases, fetch_run


def main():
    out = ROOT / "data" / "expanded"
    raw = out / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    for old in (ROOT / "data" / "archive" / "raw").glob("*.json"):
        target = raw / old.name
        if not target.exists():
            shutil.copy2(old, target)
    begin = datetime(2026, 4, 10, tzinfo=timezone.utc)
    jobs = [(region, source, begin + timedelta(days=day)) for day in range(165) for region in PLACES for source in SOURCES
            if not (raw / f"{region}-{source}-{(begin + timedelta(days=day)).date()}.json").exists()]
    errors = []

    def download(job):
        region, source, run = job
        try:
            payload = fetch_run(run, region, *PLACES[region], source)
            _check_payload(payload)
            target = raw / f"{region}-{source}-{run.date()}.json"
            temporary = target.with_suffix(".json.tmp")
            temporary.write_text(json.dumps(payload), encoding="utf-8")
            temporary.replace(target)
            return None
        except Exception as error:
            return {"region": region, "source": source, "run": run.isoformat(), "error": str(error)}
        finally:
            time.sleep(0.5)

    print(f"Expanded archive: {len(jobs)} missing real source downloads", flush=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        for count, future in enumerate(as_completed([pool.submit(download, job) for job in jobs]), 1):
            error = future.result()
            if error:
                errors.append(error)
            if count % 25 == 0 or count == len(jobs):
                print(f"Downloaded {count}/{len(jobs)}, failed {len(errors)}", flush=True)
    (out / "download-errors.json").write_text(json.dumps(errors, indent=2), encoding="utf-8")
    print("Matching real downloaded forecast/reference values", flush=True)
    manifest = fetch_cases("2026-04-10", 165, list(PLACES), out, "imd")
    manifest["previously_reviewed_test_through"] = "2026-08-17"
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps({key: manifest[key] for key in ("regions", "cases", "coverage")}), flush=True)


if __name__ == "__main__":
    main()
