# ADR-0009: Sahm-style labor stress from BLS unemployment

- **Status:** Accepted for provisional implementation
- **Date:** 2026-11-16

## Context

The approved cloud source policy already permits the BLS unemployment series. The macro dataset publishes its current rate and monthly history, but the risk model has no labor input. Adding both the unemployment level and payroll changes would give two related measures separate shares of the macro score.

The BLS ledger permits one request per UTC day. The 15-minute publication schedule can reuse those observations between requests. Stored observations contain the revised data returned by BLS rather than the versions available at each historical release.

## Decision

Add one `sahm_style_labor_stress` input to the macro dimension. For observation month `t`, calculate the unemployment rate's average over months `t-2` through `t`. Subtract the lowest three-month average ending in months `t-12` through `t-1`. Each result requires 15 consecutive monthly observations. A duplicate month invalidates any window containing it. An as-of date excludes later observations.

[FRED's Sahm Rule notes](https://fred.stlouisfed.org/data/SAHMCURRENT) describe a 0.50 percentage-point threshold relative to the minimum of the previous 12 three-month averages. This implementation derives its measure from the approved BLS unemployment history. It labels the result "Sahm-style" because the history can be revised and does not reproduce the official real-time series.

The six-calendar-year BLS request supplies at most about 58 derived monthly results after the rolling calculation's warm-up. That does not meet the model's 60-sample percentile floor. Use this explicit provisional curve, with interpolation between points and clamping outside them:

| Labor stress, percentage points | Risk score |
|---:|---:|
| 0.00 | 15 |
| 0.25 | 40 |
| 0.50 | 75 |
| 0.75 | 90 |
| 1.00 | 97 |

Assign the new signal a relative indicator weight of 2.5 within the macro dimension. Each existing macro input retains weight 5. The six dimension weights and global risk bands retain their existing values. Bump the model and calibration policy versions to `1.1.0`, and retain the `provisional` calibration status. Neither the curve nor its weight represents an empirical refit or a recession probability.

Extend `RiskIndicator` with `score_included`, defaulting to true for existing payloads, and an optional `observation_period`. The BLS-derived signal contributes only when it has a matching current observation month and the source status is `fresh` or `delayed`. A stale or degraded value remains visible but counts as missing evidence when calculating scores, coverage, bounds, and top drivers. The same evidence predicate controls each calculation.

The Risk Lab displays the BLS source, statistical month, retrieval time, source status, formula, and provisional-data caveat in both languages. It hides the risk-score contribution of an excluded row and gives the exclusion reason.

Write the computed `model_version` into every new risk-history row. Previous-day totals, dimension trends, and weekly or monthly changes use only rows with the exact active model version. Rows without a version remain readable but cannot become comparison baselines. The trend chart draws a separate series for each recorded version and preserves date gaps within it. It shows unknown-version rows only in its accessible data table.

## Consequences

- The risk score gains one labor measure without another provider or additional BLS requests.
- The new relative weight changes the macro mixture and its evidence coverage. The next generated score can therefore differ from the previous model version.
- A model change starts a new comparable history. Daily and dimension trends remain unavailable until a matching-version previous day exists. Weekly and monthly comparisons likewise need matching-version observations in their existing date windows.
- Missing source observations, incomplete monthly windows, or a mismatch with the current BLS month exclude the signal rather than inserting a neutral score as evidence.
- Historical revisions can change earlier derived values. The current calibration panel lacks BLS unemployment and cannot validate this model change.
- The official real-time Sahm series, recession dating, and point-in-time multi-regime calibration remain outside this implementation.
