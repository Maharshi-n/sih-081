# Data quality and coverage

## References and observations

Archived IFS, GFS and AIFS values are actual downloaded model forecasts. The comparison reference is **ERA5 reanalysis**, not a station measurement, gauge analysis or official IMD warning. The source API unit and UTC fields are checked, raw payload hashes are retained, and no synthetic fallback fills missing values.

Rain is an exact 24 hour accumulation ending at 03:00 UTC / 08:30 IST. This means actual model leads of 27 and 75 hours from the 00 UTC cycle. The assumed ERA5 release delay is seven days; the manifest labels its 168 hour latency as an assumption, not verified historical publication latency. Returned provider grid coordinates and requested point coordinates are recorded separately. Spatial representativeness is **unverified**, particularly important near coasts and terrain.

## Official observation acquisition attempts

Checked on 30 September 2026:

- The [official IMD API reference](https://api.imd.gov.in/public/api_reference.html) documents current weather, city station rainfall, AWS/ARG and district rainfall services. It confirms the city rainfall field covers 08:30 IST of the previous day to 08:30 IST today.
- Requests to `https://api.imd.gov.in/api/v1/aws_data?id=NDL`, the older `https://city.imd.gov.in/api/aws_data_api.php`, and `https://mausam.imd.gov.in/api/districtwise_rainfall_api.php` each returned **HTTP 401 Unauthorized**. The [official API portal](https://api.imd.gov.in/public/index.php) offers registration. No credentials were invented, bypassed or used.
- The [IMD gridded rainfall download page](https://imdpune.gov.in/cmpg/Griddata/Rainfall_25_Bin.html) was reachable once, but follow-up requests timed out. No 2026 binary file, matching release timestamps or spatial-support documentation was obtained.
- The [IMD Data Service Portal](https://dsp.imdpune.gov.in/) offers historical observational data procurement. A downloaded district average or satellite/gauge grid could not substitute directly for a city station point without matching spatial support and labeling.

**No official observations have been imported.** Verified-observation skill claims therefore remain blocked. Required next input: authorized quality-controlled historical rainfall observations for the target period, exact coordinates/support, units, 24 hour window, actual release time and evidence of spatial representativeness. Release time must not be replaced by download time.

## Observation importer

The existing CSV importer remains compatible with legacy files. `strict=True` additionally requires explicit units (`°C`, `m/s`, `mm/24h`). Both modes reject non-finite values/coordinates, out-of-range locations, suspect quality, invalid or future release timestamps, incorrect rainfall windows, instantaneous-variable accumulation windows, duplicate targets and mismatched coordinates.

Optional support metadata: `observation_support` (`station_point`, `grid_cell`, `area_average`, `unknown`), `representativeness_status`, `representativeness_evidence`, `release_time_basis`, `source_url`. Unknown support defaults to unverified. Declaring verified support requires a known support type and evidence. These are supplied evidence, not independent authentication. Manifests report unit/support verification separately, raw observation CSV SHA256, case SHA256, and min/median/max release latency. Legacy missing units remain explicitly unverified.

## Freshness policy

Run age is calculated against issue generation time, never retrieval time. Initial configured policy for IFS, GFS and AIFS: at most 12 hours fresh; over 12 through 24 hours slightly stale; over 24 hours too stale. Future runs, unknown sources and timestamps without a timezone are invalid. This is a conservative configurable review policy, not evidence that forecast skill changes abruptly at these boundaries.

## Expanded archive

The non-destructive downloader targets Mumbai, Pune, Chennai, Bengaluru and Delhi across 165 issue days from 10 April through 21 September 2026. Delhi provides a northern inland contrast to coastal Mumbai/Chennai and the elevated southern/western inland locations. Regions still share pooled/shrunken forecast models; adding five locations does not establish nationwide skill or cover the northeast monsoon.

The expanded real data has been promoted into the active `data/archive` without deleting old raw payloads. The original two-region case table and manifest are preserved under `data/archive-before-expansion`. Downloads resume from saved real payloads under `data/expanded/raw`. The expansion manifest records per-region coverage, failed requests and source hashes, plus `previously_reviewed_test_through=2026-08-17`, so the next evaluation can distinguish newly unseen dates from previously reviewed results.

Completed: **4,930 matched rows**, 986 for each region, with 165 distinct issue dates per region. Ten source requests failed (IFS and AIFS on 11 June in each of the five regions), leaving incomplete targets in that run rather than substituting data. All regions cover April through September; the northeast monsoon and full annual coverage are still absent.

Exact status and provenance are recorded in `data/expanded/manifest.json`; initial download errors are in `data/expanded/download-errors.json`. The final case SHA256 is `aa661714b06569f5e4cccd323926c0e78d3c437bddba07ca02528394e9a749c3`. Unit checks passed; spatial support remains unverified. A source or target that cannot be retrieved remains missing, never fabricated.

The promoted archive passed `py -3.12 scripts/check-real-data.py --archive-only`: **19,700 numeric values** matched saved forecast/reference responses, with 20 genuine missing source values. The check validated 2,470 used raw responses and 2,472 manifest payload hashes, plus all 1,650 rainfall windows. Results are saved in `output/archive-audit.json`. The full audit also checks evaluation-state linkage, displayed cases, schema 2 issued-source payload hashes/values and run-age exclusions when those snapshots are available.
