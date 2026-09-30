# Forecast improvements

User authorization: implement all improvement areas in Changes.txt, preserve working features, use real data and no browser control. Existing source and snapshots are preserved in Git on main; work is on feature/forecast-improvements.

## Environment and contracts

Python 3.12, NumPy, vanilla HTML/JS. Checks: `py -3.12 -m unittest discover -s tests -v`, `node tests/test_availability.cjs`, `node tests/test_time.cjs`, `node tests/test_navigation.cjs`, `py -3.12 scripts/check-real-data.py`. No graph service is installed; direct caller/import inspection is used. Independent worker write sets are disjoint. The existing code has a test-dependent harm gate; move decisions to development data and report prior reviewed dates as exploratory, never fresh evidence.

Models and calibration are fitted from observations released before the development boundary. Features use only information available at the historical issue time. Point selection and probability selection are separate. All candidates use common verification cases. Probabilities for source subsets use separately fitted calibration. Confidence is an experimental evidence index with disclosed formula and score/error verification. Freshness uses model run time, not retrieval time. ERA5 remains reanalysis. Station observations require explicit quality/release/window/support contracts; unavailable records are not manufactured.

## Task 1: Router and rainfall candidates
Wave: 1
Status: completed
Risk: high
Dependencies: none
Model: inherit
Files: sih081/router.py, sih081/rainfall.py, tests/test_router_rain.py
Acceptance: causal standardized ridge predicts source errors; nonnegative normalized weights shrink to baseline; two-part rainfall CDF is bounded and monotone.
TDD: future-label and current-observation invariance, source exclusion, dry/wet distribution checks.
Steps: read forecasting contract; implement and check candidate APIs; root integrates.
Checkpoint: local tests and worker report.

## Task 2: Confidence evidence
Wave: 1
Status: completed
Risk: medium
Dependencies: none
Model: inherit
Files: sih081/confidence.py, tests/test_confidence.py
Acceptance: transparent experimental index, unknown inputs explicit, fixed-bin errors and score/error rank association.
TDD: current/future truth invariance, stale/missing source effects and summary checks.
Steps: implement causal factors and verify independently.
Checkpoint: local tests and worker report.

## Task 3: Observation quality, freshness and coverage
Wave: 1
Status: software completed; official station data acquisition blocked by provider access
Risk: high
Dependencies: none
Model: inherit
Files: sih081/data.py, sih081/freshness.py, tests/test_data_quality.py, scripts/fetch-expanded.py
Acceptance: stricter observation contracts with supplied release latency; real expanded archive fetched separately; official station access attempts recorded honestly; age computed from run.
TDD: invalid/missing timing, coordinates, negative data and stale/future runs.
Steps: preserve existing archive; validate importer; attempt real data acquisition; fetch broader coverage.
Checkpoint: actual manifest and provider access report.

## Task 4: Evaluation and probability subsets
Wave: 2
Status: completed
Risk: high
Dependencies: Task 1, Task 2
Model: inherit
Files: sih081/forecast.py, tests/test_improvements.py
Acceptance: train/development/final-test separation; no final-test method selection; common-case ablation; frozen router/calibration serialization; per-event reliability/detection/false alarms; subset calibrators gated independently.
TDD: changing final-test observations cannot change selected methods; all probabilities bounded; schema maintains existing UI consumers.
Steps: extend existing pipeline; run all candidates; verify causal invariants.
Checkpoint: real archive evaluation plus regression checks.

## Task 5: Safe issuance and CLI
Wave: 3
Status: completed
Risk: high
Dependencies: Task 3, Task 4
Model: inherit
Files: sih081/issue.py, sih081/__main__.py, scripts/register-refresh.ps1
Acceptance: immutable issue records and source hashes retained; stale sources withheld with reasons; stored selection models used consistently; replay clearly labeled; broader coverage defaults derive from evaluation.
TDD: source-subset calibration dispatch, stale/run availability, snapshot mismatch and immutability.
Steps: integrate candidates/freshness; smoke-check issuance in temporary directories; create a real live issuance snapshot.
Checkpoint: matched evaluation/issuance hashes.

## Task 6: Explainable dashboard and audit
Wave: 4
Status: completed
Risk: medium
Dependencies: Task 5
Model: inherit
Files: web/index.html, scripts/check-real-data.py, tests/test_availability.cjs, tests/test_dashboard.cjs
Acceptance: rainfall-first view, actual explanations, disagreement, evidence factors, freshness, ablation and event verification; existing IST/replay/navigation controls remain; local-only UI checks.
TDD: execute rendered JS against a minimal local DOM for filters and all evidence branches; no browser control.
Steps: add focused panels; preserve previous controls; audit downloaded snapshot values and hashes.
Checkpoint: all tests pass; context and README capture activation gates/data limitations.

## Final verification

32 Python tests and four Node checks pass. Local HTTP endpoints and source/state/case hashes match. Audit verifies19,700 archive values and90 issued values, with genuine missing cells retained. Five-city live run has30 point-only records. No browser control used. Official station observations, representativeness and adequate unseen hazard events remain external prerequisites; publication gates correctly withhold those products.
