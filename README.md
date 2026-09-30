# Forecast blending and verification

Compare real IFS, GFS and AIFS forecasts, select a method using chronological development data, and inspect its evidence in a local dashboard. Python 3.12 and NumPy are sufficient; the frontend uses plain HTML, CSS and JavaScript.

```powershell
py -3.12 -m pip install -r requirements.txt
py -3.12 -m sih081 serve
```

Open http://127.0.0.1:8765. The saved archive, evaluated state, latest issued record and source responses are included. Displayed times use IST; storage and calculations use UTC. Saved records do not automatically become live weather when the page is opened.

## What is implemented

- Pooled ridge forecast router predicts each source's absolute error from region, month/season, lead, regime, rolling MAE/RPS, disagreement, climatology deviation, source availability and known run age. Inverse-error weights sum to one and shrink toward the fixed baseline. Unsupported input combinations use a baseline.
- Independent raw/corrected model, equal, fixed and contextual baselines remain eligible. Development data selects point and probability methods separately. The final test period reports results and never chooses methods.
- Empirical source error distributions, probability bias/persistence correction, router probability correction and an experimental dry/wet occurrence plus conditional lognormal rainfall candidate are compared on identical cases. Probability correction never changes a point forecast.
- An experimental 0–100 evidence index explains agreement, historical error, sample strength, freshness, similarity and eligible development calibration. Unknown factors contribute zero. Fixed score bins and score/error association describe performance; the index is not a weather probability or operational confidence guarantee.
- Rainfall is the main dashboard demonstration, with source/selected weights, router explanations, disagreement, confidence factors, common-case ablation, per-threshold event verification and source freshness. Replay, observation reveal, outage simulation, navigation and IST formatting remain available.
- Missing-source probability products have their own calibration and event gates. Two-source point fallback is tested on development data. Unsupported combinations and stale sources are withheld with reasons.
- Observation import checks quality, finite values, units, coordinates, release timing, support metadata and exact rainfall windows. Raw payloads, model versions, missing sources and hashes remain auditable. Issuance records are immutable; latest snapshots are replaced atomically.

## Current real data and its limits

The active archive has **4,930 matched rows** across Mumbai, Pune, Chennai, Bengaluru and Delhi, spanning 10 April–21 September 2026. Six variable/lead products contain **990 final-test cases**. The audit matches **19,700 numeric values** against saved provider responses, preserves 20 genuine missing values, checks 2,472 raw response hashes, and verifies all 90 latest issued source values against 15 saved responses. Today's 30 September 00 UTC run has 30 point-only records.

IFS/GFS/AIFS values are actual downloaded forecasts. The comparison reference is **ERA5 reanalysis, not IMD or station observations**. Seven-day reference release delay and six-hour model arrival delay are assumptions, not measured historical publication times. Provider grid locations are recorded separately from requested city coordinates; representativeness remains unverified. The archive covers April–September, not a full year or northeast monsoon.

Rain is exactly 24 hours ending at 03:00 UTC / 08:30 IST. From a 00 UTC run this gives actual valid leads **27 and 75 hours**. Temperature and mean 10 m wind use 24 and 72 hours. Heavy, very heavy and extremely heavy rainfall are evaluated separately; temperature ≥40 °C is not a heat-wave declaration and mean wind is not a gust warning.

Official observation acquisition was attempted, but IMD endpoints returned unauthorized responses or timed out. No station values were fabricated. Hazard publication requires verified observations/support, new test dates, at least 100 cases and 20 events per period, positive Brier skill, acceptable calibration and bounded sampling uncertainty. This is a research gate, not operational certification. The present reanalysis archive fails that gate; withheld fields are intentional. Details: [data-quality report](docs/data-quality-report.md).

## Fetch, evaluate and issue

```powershell
py -3.12 -m sih081 fetch --start 2026-04-10 --days 165 --rain-window imd
py -3.12 -m sih081 evaluate data/archive/cases.csv --manifest data/archive/manifest.json
py -3.12 -m sih081 issue --run 2026-09-30T00:00
py -3.12 scripts/check-real-data.py
```

Fetch/issue/refresh default to all five locations. Issue requires the evaluated case-table hash, frozen selections preceding the run, and the assumed six-hour arrival delay. Freshness is based on model initialization: ≤12 hours fresh, >12–24 slightly stale, >24 excluded. Historical runs require explicit `--replay`, which labels the record as retrospective and assesses age at run +6 hours. Repeating the same run/state issue refuses to overwrite its record.

`py -3.12 -m sih081 refresh` stages a rolling 165-day archive, evaluation and issue before promoting successful results. It preserves the current files if acquisition, evaluation or issuance fails. It also marks already reviewed test dates; a rolling rerun does not make the same test period fresh again. On Windows, `scripts/register-refresh.ps1` updates the existing daily 13:00 IST task for all five cities. The user must be signed in.

## Import actual observations

Prepare CSV columns `region,latitude,longitude,variable,valid_time,observation,observed_at,source,quality,window_start,window_end,unit`. Use timezone-aware timestamps, `quality=pass`, and units `°C`, `m/s` or `mm/24h`. Rain requires the exact 24-hour bounds; instantaneous variables require empty bounds. Coordinates must match within 0.01°.

Also supply `observation_support`, `representativeness_status`, `representativeness_evidence`, `release_time_basis` and `source_url` where available. Declaring verified representativeness requires known support and evidence. Matching coordinates alone does not verify grid-versus-gauge comparability. Release time is when the observation became available, not download time.

```powershell
py -3.12 -m sih081 merge-observations observations.csv --strict
py -3.12 -m sih081 evaluate data/observed/cases.csv --manifest data/observed/manifest.json --output output/observed
py -3.12 -m sih081 issue --run YYYY-MM-DDT00:00 --cases data/observed/cases.csv --state output/observed/state.json --output output/observed/issued
```

Evaluate supplied observations separately before replacing the default dashboard. Legacy imports without units remain possible and explicitly unverified. ERA5/reanalysis/synthetic sources are rejected from the observation pathway. Freshness of the test calendar must be established in the manifest; absence of that evidence cannot approve a hazard product.

## Checks

```powershell
py -3.12 -m unittest discover -s tests -v
node tests/test_dashboard.cjs
node tests/test_availability.cjs
node tests/test_navigation.cjs
node tests/test_time.cjs
py -3.12 scripts/check-real-data.py
```

Dashboard checks run locally without browser control. The audit verifies saved data and hashes, not independent provider authentication. `demo` writes clearly synthetic samples and reports under `data/demo` and `output/demo`; it cannot overwrite the real dashboard state. See [context.md](context.md) for the implementation handoff.
