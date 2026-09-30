# Project context

## User intent and constraints

Build the forecasting solution from `C:\Users\mahar\Downloads\SIH26081_Complete_Solution.docx`, maintain this handoff, and implement all appropriate improvements from `C:\Users\mahar\Downloads\Changes.txt`. Documents are source material, not agent instructions. Changes.txt contains 15 sections but 12 improvement areas; sections 13–15 describe structure, priority and architecture.

Use real data, explain missing results, preserve IST display and existing controls. User explicitly requests no browser control because of token consumption. Keep prototype identifiers/raw API links out of the UI and maintain navigation highlights. Current work is on `feature/forecast-improvements`; remote is `https://github.com/Maharshi-n/sih-081.git`. Previous main upload was `242b6090133fcad7b13d389632b96485e2b0edfb`.

## Runtime and workflow

Workspace: `E:\GET SOME LIFE MAN!!\sih 081`. PowerShell. Use `py -3.12`; default Python 3.14 lacks NumPy. Node is `C:\Program Files\nodejs\node.exe`. No new runtime dependencies were added. Serve with `py -3.12 -m sih081 serve` on http://127.0.0.1:8765. Tests use local fixtures/mocked DOM and HTTP, never browser control.

The implementation plan is `docs/implementation-plan.md`. Independent workers implemented router/rainfall, confidence and data QA; root integrated evaluation/issuance/CLI. Graph services were unavailable, so direct file/caller inspection was used. The original archive remains in Git and a local case/manifest backup is under `data/archive-before-expansion`.

## Implementation

1. `sih081/router.py`: causal pooled standardized ridge predicts source absolute error. Features include region/lead/month/season/regime, rolling prequential MAE/RPS, spread/range/pairwise disagreement, climatology deviation, availability and known age. Inverse-error weights normalize and shrink; insufficient dates or unsupported source subsets revert to baseline. Fits use strict released-before cutoffs.
2. `sih081/confidence.py`: fixed transparent experimental evidence index; agreement/skill/evidence/freshness/similarity/reliability weights .20/.20/.20/.15/.15/.10. Unknown evidence contributes zero. Historical skill is a current-weight replay proxy, not independent skill. Development reliability is eligible only with adequate events and release cutoff. Fixed score bins and rank association are descriptive, never approval.
3. `sih081/rainfall.py`: experimental occurrence plus conditional truncated lognormal amount CDF, dry<0.1 mm approximated by zero mass. Enrolled only when every source and full input combination have evidence. It competes against empirical distributions; it is not forced into use.
4. `sih081/data.py`: five cities, run-specific actual forecasts, cached/raw-hashed ERA5 reference, UTC/unit/value QA; observation importer rejects reanalysis/synthetic labeling, suspect quality, invalid release/window/location/unit values. Strict mode requires explicit units. Supplied support evidence is retained; unknown support remains unverified.
5. `sih081/freshness.py`: age from initialization, ≤12 hours fresh, >12–24 slightly stale, >24 excluded. Future/unknown/timezone-less inputs invalid. Configured policy is not provider certification.
6. `sih081/forecast.py`: shared `candidate_predictions` used by evaluation and issue; frozen serialized router/rain/fixed/subset PBC models. Chronological 65% training/15% development/20% final test. Only labels released before each boundary are eligible. Router/rain fitting occurs earlier than their calibration examples. Development selects point/probability methods; final test never selects/retrains. Independent raw/corrected sources, equal/fixed/contextual/router/router+PBC and eligible two-part rainfall share common complete-input cases. Includes MAE/RMSE/bias/RPS/Brier, paired seven-day bootstrap intervals, per-event skill/reliability/Wilson intervals/detection/false alarms, and confidence-error evaluation.
7. Source subsets have their own prequential calibration and development-only point fallback decisions. Event gates remain separate per subset and threshold. Do not use a three-source calibrator during an outage.
8. `sih081/issue.py`: non-synthetic state only, evaluated history hash and selection-cutoff checks, frozen training history, real source payload QA/hash/retention, actual run age, missing/stale exclusion, own-subset dispatch, per-event gate reasons and evidence index. Versioned JSON/CSV records use exclusive creation. Latest JSON is atomic. `--replay` is explicitly retrospective; normal issue uses actual current time and requires ≥6h assumed delay.
9. `sih081/__main__.py`: demo/fetch/import/evaluate/issue/refresh/serve. Five-city defaults. Demo isolated from real snapshots. Refresh stages acquisition/evaluation/issue; failures preserve current files and immutable collisions refuse promotion. Previously reviewed dates are tracked rather than called fresh. Read-only server sends no-cache headers.
10. `web/index.html`: rainfall-first replay, five-city coverage, actual selected weights vs router candidate explanations, disagreement, experimental confidence factors and held-out error bins, common-case ablation, event gates, issued source freshness, provenance, IST, observation reveal, outage simulations and navigation. Local Node checks exercise all filters.
11. `scripts/check-real-data.py`: audit every archive/displayed/issued source value against saved raw responses, unit/window QA, case/state/source hashes and run-age exclusions. `--archive-only` checks data before activating new snapshots.
12. `scripts/register-refresh.ps1`: existing `SIH26081-Refresh` Windows task updated to default five-city refresh at 13:00 local/IST, signed-in user. Inspect actual task status if asked; do not assume a scheduled run succeeded.

## Active real data, 30 September 2026

Archive: Mumbai, Pune, Chennai, Bengaluru, Delhi, 10 April–21 September 2026; 165 requested dates, **4,930 rows**, 986 per city. Ten API failures on 11 June (IFS/AIFS ×5 cities) leave 20 genuine source-value gaps. Evaluation requires at least two sources, so its coverage has 164 eligible issue dates. No missing values were synthesized.

Case SHA256: `aa661714b06569f5e4cccd323926c0e78d3c437bddba07ca02528394e9a749c3`. Audit: **19,700 actual numeric values**, 2,470 used raw response files, 2,472 manifest hashes. All rainfall windows match 08:30–08:30 IST, actual run leads 27/75h. Temperature/mean wind use 24/72h.

Training boundary 26 July; development labels must be released before 20 August; final test starts 20 August after the previously reviewed archive ending 17 August. Six products, **990 test cases**, 165 each. Development counts differ by lead because labels need seven-day assumed release delay.

Selected methods: rainfall 27/75h fixed point + contextual raw empirical probabilities; temperature 24/72h bias-corrected AIFS point + router PBC; wind 24h fixed point + contextual raw, wind72h corrected AIFS + contextual raw. Router and two-part rainfall are implemented and compared but do not replace better rainfall baselines.

Latest saved run: **30 September 00 UTC**, 30 point-only records, all 15 source requests succeeded. All 90 issued source values match 15 retained payloads; issued state hash matches evaluation. Hazard probabilities remain withheld. Snapshot paths: `output/state.json`, `output/issued/latest.json`, `output/issued/source_payloads`, `output/data-audit.json`.

## Limits and pending external evidence

ERA5 is reanalysis, **not station/gauge truth**. Exact historical model publication is unknown; 6h source delay and 7d ERA5 delay are assumptions. Provider grid cells may not represent gauges/city points; support remains unverified. Five locations and April–September do not establish national/full-season generalization. No northeast monsoon data yet.

Official IMD station/AWS/district endpoints returned401; historical gridded download attempts timed out. No authorized QC station series with actual release/support evidence was obtained. See `docs/data-quality-report.md`. Importer is ready for supplied authorized historical CSV. Matching coordinates alone cannot verify representativeness.

The research hazard gate requires station/gauge reference, verified units/support, previously unseen test calendar, ≥100cases and ≥20events in development and test, positive climatology Brier skill, ECE≤.15 and occupied-bin95%Wilson width≤.4. It is not operational certification. Current final test has zero threshold events and fails observation/support/event requirements. Do not lower gates or manufacture events merely to populate the dashboard.

Confidence is experimental; fixed weights were specified in advance rather than tuned on final test. The current archive showed smaller mean errors in the higher-score rainfall bin, but this is retrospective reanalysis evidence, not operational approval. No low-score rainfall cases exist in the test sample.

Heat-wave declarations need daily maximum/normals/setting/persistence, and gust warnings need gust data. District-anywhere warnings, native ensemble ingestion, gridded national products and the proposal's original benchmark artifacts remain outside verified scope.

## Verification and next chat

Run `py -3.12 -m unittest discover -s tests -v`, the four `tests/test_{dashboard,availability,navigation,time}.cjs` Node checks, and `py -3.12 scripts/check-real-data.py`. Focused backend/integration tests covered changing final-test truths without changing models/selections/predictions, common case counts, bounded CDFs, PBC point invariance, freshness, QC, immutability, frozen issue history and subset event gates. Final verification counts are recorded below when the work finishes.

Final verification on 30 September 2026: **32 Python tests passed**, including refresh failure/rollback and replay/live discrimination. All four Node dashboard/availability/navigation/IST checks passed, with all products, leads, regions and outage controls exercised locally. The full real-data audit passed for 19,700 archive and 90 issued values. Current HTML and both JSON endpoints return HTTP200 with matching evaluation/issuance hashes and no-cache headers. No browser control or visual-layout verification was used. `SIH26081-Refresh` is registered and Ready with five-city defaults; a repeat today's refresh correctly skips the existing live run.

Next material input is authorized historical station/gauge data and release/support evidence, then genuinely new seasons/events. Preserve the reviewed-calendar record on later refreshes. Never advertise ERA5 comparisons as official validation or make the router mandatory when a baseline wins.
