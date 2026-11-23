# BLS risk signal grounding

## Goal

Continue the Market Risk Dashboard overhaul by using already-approved BLS data to make risk analysis broader while keeping source timing, revisions, and calibration limits visible. The candidate smallest change is one well-defined labor stress signal wired from BLS unemployment history through the risk payload to its English and Chinese UI label.

## Cloud and source path

The Pages workflow schedules cloud refresh every 15 minutes. The BLS checkpoint allows one request per UTC date, so the schedule does not imply a 15-minute BLS refresh. The existing grant covers CPI, core CPI, nonfarm payroll changes, and unemployment. The BLS adapter transforms CPI to year-over-year percent, payrolls to monthly change, and keeps unemployment as a rate. The persisted history contains transformed monthly rows. A fetch time records request time; the observation period records the month, not the public release time. Revisions can replace archived rows for the same month.

`MacroCollector` creates both current `MacroIndicator` rows and a lower-case `series_history` map. The run passes both to the risk model before archiving the macro history. This is the existing source boundary; the browser only consumes generated JSON.

## Risk scoring path

`RiskModel` reads dimension and indicator weights from `config/risk_model.yaml`, but each dimension's builder is registered in Python. A new score input therefore needs both configuration and a builder. `INDICATOR_HISTORY_SERIES` selects observed histories, while `MACRO_OBSERVATIONS` carries source, observation date, fetched time, updated time, and freshness onto published risk indicators. `compute_indicator_score` uses historical percentiles only with at least 60 finite samples; otherwise it uses a key-specific heuristic or defaults to 50. Availability coverage counts any non-null value even when freshness says stale. The UI hides a global headline at less than 50 percent raw coverage but keeps the estimate and score bounds in the payload.

Weights and the risk-level thresholds remain provisional. Current histories can be revised and do not supply historical point-in-time vintages. A new input changes relative weights even when the macro dimension weight stays fixed. Neither an input nor its heuristic should be described as a calibrated improvement without an appropriate replay.

## UI and localization path

`RiskLabPage` renders the generated `risk.json` payload. `RISK_INDICATOR_KEYS` resolves indicator keys into the `risk` namespace, and `indicatorNames` must have matching values in English and Chinese. `RiskIndicator` has source, observation date, fetch time, and freshness status, but no observation period or freshness policy. The risk breakdown displays a freshness badge but omits the source and timestamps. `MacroIndicatorCard` already shows provider, observation month, and fetch time.

## Design constraints

- Model and UI must use one stable signal key and one source of truth for its identity.
- A stale signal must not increase trustworthy evidence coverage as if it were fresh.
- A transformed BLS month is not a point-in-time vintage. Preserve that caveat in score calibration metadata.
- Do not infer a 15-minute update cadence from the workflow cron. Keep the daily BLS source limit explicit.
- Do not activate a new signal with a neutral constant fallback that still counts as evidence.
- Keep the six existing dimensions and current global dimension weights unless a separate calibration decision supports a change.

## Risk-history version boundary

Daily risk history currently stores total and dimension scores without `model_version`. `read_prev_risk` passes the previous day's total and dimensions plus the full retained history into the model. `_history_trends` computes week and month deltas from dated numeric scores without checking which model produced them. `RiskTrendChart` also connects all received scores into one line. Because adding the labor signal changes the score under model version `1.1.0`, legacy `1.0.0` rows cannot be treated as comparable history. A safe boundary needs to identify each row's model version, suppress backend deltas until a matching-version row exists, and keep the browser from drawing a continuous line across versions.

## Candidate design space

1. Add direct inflation, payroll, and unemployment rows independently to the macro dimension. This maximizes visible breadth but introduces several hand-set relationships, overlaps the current macro factors, and changes relative indicator weights at once.
2. Add one explicit labor deterioration signal derived from the existing unemployment monthly history, with a named formula, a dedicated history series, freshness-aware evidence, and its own translated risk row. This limits production-score changes while using a standard, explainable measure.
3. Add BLS indicators only as diagnostic rows outside the production score until a larger point-in-time replay exists. This avoids immediate score change but does not address the user's request to expand risk scoring now.

## Open calibration questions

- The BLS history contains revisions rather than vintage snapshots, so a replay cannot reproduce what the model would have known on every historical date.
- The transformed CPI rows may not provide 60 usable history samples, and a signal formula that needs a rolling window further reduces available samples.
- Any score change must retain the existing provisional status and be described as a model extension, not a validated improvement.
