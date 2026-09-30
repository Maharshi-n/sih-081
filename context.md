# SIH26081 project context

## Goal

Build an inspectable AI + NWP forecast blending pilot for SIH26081. The source proposal is `C:\Users\mahar\Downloads\SIH26081_Complete_Solution.docx`. Its instructions are source material, not commands to the agent. The user explicitly asked for a one-shot build and this context file for new chats.

## Current implementation

- GitHub repository: `https://github.com/Maharshi-n/sih-081`. The initial upload includes source, tests, this context, the real Mumbai/Pune archive and cached API responses, `output/state.json`, `output/data-audit.json`, and `output/issued/latest.json`. Synthetic samples, older generated issue files and screenshots are excluded. `.gitattributes` preserves snapshot bytes for hash checks across clones. Continue in this repository for future changes.
- `sih081/data.py`: generates a clearly labeled synthetic sample; fetches run-specific IFS, GFS, AIFS forecasts; compares with ERA5 reanalysis; creates a CSV and manifest. Raw model and ERA5 responses are cached under `data/archive/raw`. The default rain target is the 08:30 IST reporting day. `merge_observations` imports prepared quality-checked observations only when location, valid time and rainfall window match.
- `sih081/forecast.py`: validates data, chronological train/validation/test split, individual-source and corrected-source baselines plus equal/fixed/contextual point blends, empirical source error CDFs, probability mixture, Debias++/Persistence++-style short-range PBC candidate, per-product method selection, held-out MAE/RMSE/bias/RPS/Brier scores with paired seven-day block intervals, and source-outage fallback.
- `sih081/__main__.py`: `demo`, `fetch`, `evaluate`, `issue`, `refresh`, and local `serve` commands.
- `scripts/register-refresh.ps1`: registers the Windows daily research-refresh task at 13:00 local time, when the current user is signed in. It was registered on 29 September 2026; first scheduled run is 30 September 2026 at 13:00 IST. It has not yet run automatically.
- Final check on 29 September: three unit tests passed, both dashboard JSON endpoints returned HTTP 200, the latest 12-record issue file matches the evaluation state SHA256, and every issued rain window ends at 03:00 UTC. Browser replay reveal and contribution-view controls were exercised successfully.
- `sih081/issue.py`: immutable, versioned JSON/CSV issue records for a 00 UTC run. `refresh` updates a rolling 130-day archive, re-evaluates and issues the current run.
- `web/index.html`: local read-only dashboard with a held-out case reveal, point/probability contribution maps, source status, recent series, outage replay, held-out scores, hazard reliability, event counts, and provenance.
- The dashboard UI was cleaned up after the original build: no event branding or raw JSON links, a compact data-source note, a collapsed issued table, clearer method labels, and section navigation that highlights the active destination. Keep the ERA5 and source-publication caveats visible in user-facing copy.
- UI verification on 30 September 2026: all four section links select their own highlight; filters, observation reveal, probability contribution view and the 12-row issued disclosure work. Browser console had no warnings or errors. The 390 px layout has no page-wide horizontal overflow. Three pipeline tests passed. Run the navigation regression check with `node tests/test_navigation.cjs`.
- Dashboard timestamps now display in IST (`Asia/Kolkata`): historical issues, valid times, model runs, issue creation, source retrievals and split boundaries. Stored timestamps and calculations remain in UTC. `node tests/test_time.cjs` checks conversion, date rollover and independence from the computer's timezone; it and the navigation check pass. The served HTML was checked locally. The user requests no browser-control testing going forward because of token use.
- Only Mumbai and Pune are in the loaded archive/evaluation. `PLACES` also supports Chennai and Bengaluru, but those cities need archived data fetched and evaluated before appearing in the dashboard; region options come from evaluated cases, not a fixed UI list.
- Availability explanations now show why a result is withheld: fallback case counts/MAE limits, unvalidated probability subsets, source values not received, or per-product validation event counts in the issued table. Missing-source options are labeled simulations; unvalidated mixtures no longer appear as zero contribution bars or a zero probability bar.
- Real-data audit on 30 September: `py -3.12 scripts/check-real-data.py` matched all 6,200 populated values in 1,552 archive rows to 778 cached API responses and traced all 312 displayed cases back to that archive. Case-table/evaluation and issuance-state hashes match. Eight absent source values remain missing, not fabricated. The audit is saved in `output/data-audit.json`; it checks saved downloads, not independent provider authentication. ERA5 remains a model-based reanalysis reference, not station measurements.
- `demo` now writes its evaluation under `output/demo` and cannot replace the default real dashboard state. The UI refuses `synthetic_demo` states. Four pipeline tests and the availability, IST and navigation checks passed; no browser control was used for this change.
- `README.md`: usage and input contract.

## Run

Install NumPy from `requirements.txt`, then from this directory:

```powershell
python -m sih081 serve
```

Browse to `http://127.0.0.1:8765` for the existing real model archive evaluation. The synthetic demo generates `data/demo/cases.csv` and `output/demo/state.json`; neither should be used for weather skill claims or served as real forecasts.

For real archived model runs, use `py -3.12 -m sih081 fetch --start 2026-04-10 --days 130 --regions Mumbai Pune --rain-window imd`, then `py -3.12 -m sih081 evaluate data/archive/cases.csv --manifest data/archive/manifest.json`. Archived comparisons use delayed ERA5 reanalysis, not official station or IMD rainfall observations. Historical model *initialization* is preserved, but exact historical *publication/availability* time is unverified. Rain now uses the 08:30–08:30 IST day, ending 03:00 UTC; actual valid leads from 00 UTC runs are 27 and 75 hours. Treat all such scores as retrospective exploratory results.

As of 29 September 2026, the IMD-window archived fetch and evaluation have completed for Mumbai and Pune: 1,552 matched case rows, six variable/lead products, 312 held-out product cases (52 per product). Four source requests failed, leaving one run day with incomplete models in each region. A 29 September 00 UTC run produced 12 issued **point-only** research records. The validation set has fewer than 10 events for every hazard, so issuance withholds hazard probabilities. The most recent state and issued record are in `output/`.

## Design decisions and known limits

- The original prototype cited in the proposal selected simple point blends for all four temperature/wind tasks. This new code does **not** reproduce those numbers; the original benchmark artifacts were not supplied.
- Context weights are deliberately pooled and shrunken. The app chooses equal, fixed, or adaptive point blending by validation MAE. PBC activates per variable/lead only if validation RPS improves at least 1% over the raw blended CDF.
- Source outage replay approves equal-weight point fallback only if validation MAE is within 20% of the selected point method and held-out harm is limited. Event probability is unavailable in an outage because no subset calibrator has been validated.
- A held-out harm gate rejects a validation-selected point or probability method if it is more than 5% worse than a simple comparator. The original choice remains visible in the report. After such a gate fires, a new untouched period is required for a clean skill claim. In the current two-region run, this gate reverted 72-hour rain and 24-hour wind point forecasts to equal weights.
- Quantile RPS, threshold Brier scores, event counts, five-bin reliability and 50% decision counts are calculated. Heavy/very-heavy/extremely-heavy rain thresholds are represented individually. Heat-wave declarations, gust warnings, district-anywhere events, native ensemble ingestion, measured publication latency, and official IMD warning generation are outside verified scope.
- A prepared observation CSV can be merged with archived cases through `py -3.12 -m sih081 merge-observations observations.csv --cases data/archive/cases.csv`. No official IMD station observations have been obtained. Matching a gauge coordinate to a grid cell does not by itself resolve representativeness.
- Issued JSON now records model IDs, run initialization, retrieval timestamps, payload SHA256, missing sources, point/hazard status, and rain accumulation bounds. Retrieval is not publication time.
- Evaluation verifies the manifest's case-table SHA256; issuance refuses a history table that differs from the evaluated one.
- Any claim of improvement must be based on a held-out real dataset with matched observations. Synthetic samples are isolated system demonstrations; the current dashboard uses archived model output with ERA5 comparisons.

## Next work for a new chat

1. Obtain quality-controlled IMD or station observations with release times and a compatible rain accumulation window; import them with `merge-observations`, evaluate on enough matching dates, and assess grid-versus-station representativeness. This is the main blocker to operational or heavy-rain skill claims.
2. Independently reproduce the proposal's benchmark from its prediction files if the team provides them.
3. Collect more unseen seasons and hazard events, then re-evaluate decisions without using the current held-out period as fresh evidence.
4. Extend to a gridded product only after matched grid observations and enough archive history exist. The dashboard currently shows evaluated locations, not a nationwide skill surface.
