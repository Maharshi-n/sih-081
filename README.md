# SIH26081 hybrid forecast blending pilot

Run from the repository root with Python 3.11+ and NumPy:

```powershell
python -m pip install -r requirements.txt
python -m sih081 serve
```

Open `http://127.0.0.1:8765` after fetching and evaluating real data, or use the existing evaluated archive. The dashboard refuses synthetic evaluations. `python -m sih081 demo` creates a separate **synthetic** report in `output/demo/state.json`; it does not replace the dashboard's `output/state.json`. Demo scores do not establish forecasting skill.

The repository includes the Mumbai/Pune archive, cached API responses, evaluated state and latest issued snapshot. You can run the dashboard immediately after installing dependencies. These are saved historical results, not a live weather feed; use `refresh` to fetch a new run. Generated samples, older issue files and screenshots are excluded from Git. Snapshot files retain their exact bytes so the provenance hashes survive a clone.

For archived IFS, GFS and AIFS forecasts compared with delayed ERA5 reanalysis:

```powershell
py -3.12 -m sih081 fetch --start 2026-04-10 --days 130 --regions Mumbai Pune --rain-window imd
python -m sih081 evaluate data/archive/cases.csv --manifest data/archive/manifest.json
python -m sih081 issue --run 2026-09-28T00:00 --regions Mumbai Pune
python -m sih081 serve
```

The fetch command uses individual 00 UTC model runs. It caches raw forecast and ERA5 API responses and creates a matched CSV. Temperature and 10 m wind are point values at 24 and 72 hours. With the default `--rain-window imd`, the 24-hour rain accumulation ends at 03:00 UTC (08:30 IST), so actual forecast valid leads are **27 and 75 hours** from the 00 UTC run. `--rain-window utc` retains the older 00 UTC rain window. ERA5 is a **reanalysis reference**, not station truth. Archive run initialization is known; historical publication times are not verified. Avoid operational skill claims from this mode.

Run `py -3.12 scripts/check-real-data.py` to match archive values and displayed cases against cached model/ERA5 responses and check evaluation/issuance hashes. It writes `output/data-audit.json`. This checks local provenance; it does not authenticate the provider independently or turn reanalysis into station observations. Withheld dashboard results show their specific reason, including validation event counts and fallback accuracy limits. Missing-source options are explicitly labeled simulations.

For prepared quality-checked observations, create a CSV with `region,latitude,longitude,variable,valid_time,observation,observed_at,source,quality,window_start,window_end`. Use UTC ISO timestamps with an explicit offset, `quality=pass`, and exact spatial coordinates (within 0.01° of the forecast target). Rain needs `window_start` exactly 24 hours before `window_end=valid_time`; leave both blank for instantaneous temperature/wind. Then run:

```powershell
py -3.12 -m sih081 merge-observations observations.csv --cases data/archive/cases.csv
py -3.12 -m sih081 evaluate data/observed/cases.csv --manifest data/observed/manifest.json
```

If issuing from that state, pass `--cases data/observed/cases.csv`; the issue command checks that its history file matches the evaluated manifest hash. The import fails on rain-window or location mismatches and keeps only matched targets. A nearby gauge is still a point measurement, while a model grid value represents an area; assess that representativeness before calling the scores station-verified skill. No official IMD observations are bundled with this repository.

`cases.csv` required columns: `issue_time,valid_time,observed_at,region,latitude,longitude,variable,lead_hours,observation,IFS,GFS,AIFS`. Optional columns: `observation_source,observation_quality,window_start,window_end`. Times are ISO 8601 UTC. Empty source cells represent missing inputs. Every row must identify one forecast target and contain an observation.

The evaluation uses chronological train, validation and test periods. It fits empirical source error distributions, compares raw and bias-corrected individual sources with three point blends, and tests probability correction (PBC) against the raw blend. Validation selects a method per variable and lead. A held-out harm gate rejects a chosen method if it is materially worse than the simpler baseline; after such a rejection, a fresh untouched period is needed for a final skill claim. The report includes paired seven-day block intervals for point MAE and probability RPS differences. Use at least 120 distinct issue dates for adequate training history. Method decisions and displayed cases are saved in `output/state.json`.

The `issue` command requires a non-synthetic evaluation state and matched history. It retrieves the named run, applies the approved methods, and writes versioned immutable JSON and CSV forecast records to `output/issued`. The dashboard reads the most recent issued record. Issued records include source model IDs, retrieval timestamps, payload hashes, missing-source status, forecast windows, and the evaluation-state hash. The retrieval time is not the source's original publication time. Hazard probabilities are withheld individually when validation contains fewer than 10 matching events. Exact historical publication times are not established; the issue job assumes six hours of global-model latency for its observation-history gate and records that assumption.

For a repeatable daily job after 06:00 UTC, run `py -3.12 -m sih081 refresh --regions Mumbai Pune`. It fetches a rolling 130-day archive ending early enough for the assumed observation delay, re-evaluates, and issues the day's 00 UTC run. Raw forecast downloads are cached, so later runs normally fetch only new dates. The command skips a run that already has an immutable forecast record. On Windows, `scripts/register-refresh.ps1` registers a 13:00 local daily task; it runs when the account is signed in. Check it with `Get-ScheduledTaskInfo -TaskName SIH26081-Refresh` and remove it with `Unregister-ScheduledTask -TaskName SIH26081-Refresh -Confirm:$false`.

The dashboard now includes a held-out case replay with hidden/revealed observation, individual sources, point/probability contribution views, outage replay, reliability bins, event counts, and Debias++/Persistence++ component scores. All dashboard timestamps display in IST; stored data and calculations use UTC. Run `node tests/test_time.cjs` to check time conversion. Heavy, very heavy and extremely heavy rainfall thresholds are tracked separately. Heat-wave and gust products remain explicitly unavailable until matching daily maximum, normal, persistence and gust data exist. This is an exploratory pilot; it does not issue IMD warnings or claim reanalysis scores are station-verified operational improvements. See [context.md](context.md) for project state and next work.
