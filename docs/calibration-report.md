# Market Risk Dashboard — Offline Calibration Report

**Document version:** 1.3 (Baa-10Y fallback replay and historical production-path evidence)
**Produced by:** data pipeline `scripts/calibration.py` + `pipeline/risk/calibration.py`
**Scope statement (red line):** The risk scores on this page are **model-based market stress estimates**, not exact crash probabilities, and do not constitute investment advice (architecture §1.8 / PRD §14.8).

---

## 1. Purpose

Review the heuristic risk score across three historical windows: the 2008 Global Financial Crisis, the 2018 Q4 sell-off and the 2020 COVID crash. The replay records warning lead time, score changes, drawdown, forward return volatility and risk-level changes.

## 2. Method

- **Current model:** the risk model uses Baa-10Y (`BAA10Y`) instead of HY OAS, per [ADR-0012](adr/0012-baa10y-replaces-hy-oas.md). The three-window figures use model version 1.2.0's Baa-10Y heuristic and latest-source data fetched on 2026-11-23. They do not exercise the production percentile path or establish out-of-sample performance.
- **Method:** composite score of VIX, Baa-10Y and SPX drawdown, using `heuristic_risk_score`. The production-path replay in §4 is an older result from 2026-08-08. It does not validate model version 1.2.0.
- **Data:** FRED `VIXCLS` and `BAA10Y`, plus yfinance SPX history. The results list source observation counts and the common date range for each window. Latest-source history can be revised and is not point-in-time vintage data.
- **Windows:**
  - 2008: 2008-08 → 2009-03 (main decline phase of the financial crisis)
  - 2018: 2018-09 → 2018-12 (Q4 sell-off)
  - 2020: 2020-02 → 2020-04 (COVID crash)
- **Evaluation metrics** (subset of PRD §15):
  - Warning lead time: trading days between the first risk score ≥60 and the SPX peak (**positive = before the peak; negative = after the peak**)
  - Risk score rate of change: trading days required for the risk score to move from 40 → 60
  - Max drawdown: maximum SPX drawdown within the window
  - Post-peak return volatility: sample standard deviation of the next 5/10/20/30 daily SPX returns, in percent
  - Risk-level stability: number of risk-level switches within the window (fewer = more stable)

## 3. Results summary

These fallback metrics use the current Baa-10Y heuristic. They are descriptive results from three windows, not a production-path validation.

| Window | Trading days | Max drawdown | Warning lead time vs SPX peak | 40→60 speed | Post-peak return volatility 5d/10d/20d/30d (%) | Level switches | First/peak/last score |
|---|---|---|---|---|---|---|---|
| 2008 Financial Crisis | 164 | -48.17% | 17 days after SPX peak | 23 days | 0.93 / 1.05 / 1.43 / 2.20 | 3 | 57.53 / 75.13 / 73.80 |
| 2018 Q4 Sell-off | 79 | -19.78% | Not reached 60 (peak 58.97) | Not reached 60 | 0.26 / 0.34 / 1.12 / 1.26 | 11 | 27.60 / 58.97 / 55.56 |
| 2020 COVID | 61 | -33.92% | 7 days after SPX peak | 4 days | 1.45 / 3.04 / 5.35 / 5.15 | 6 | 36.82 / 74.90 / 66.85 |

The common source dates were 2008-08-01 to 2009-03-30, 2018-09-04 to 2018-12-28, and 2020-02-03 to 2020-04-29. Each scored date had VIX, Baa-10Y and SPX observations.

### 3.1 2008 (2008 Financial Crisis)

- Input observations: VIXCLS 167, BAA10Y 165, SPX 166, common dates 164
- Common dates: 2008-08-01 to 2009-03-30
- Trading days: 164
- Max drawdown: -48.17%
- Warning lead time (score ≥60 vs SPX peak): -17 days (17 days after the peak)
- Risk score 40→60 speed: 23 days
- Post-peak return volatility (%): {'5d': 0.93, '10d': 1.05, '20d': 1.43, '30d': 2.2}
- Risk-level switches: 3
- Score range: first 57.53 / peak 75.13 / last 73.8

### 3.2 2018 (2018 Q4 Sell-off)

- Input observations: VIXCLS 82, BAA10Y 80, SPX 81, common dates 79
- Common dates: 2018-09-04 to 2018-12-28
- Trading days: 79
- Max drawdown: -19.78%
- Warning lead time (score ≥60 vs SPX peak): not reached (score peak 58.97)
- Risk score 40→60 speed: None days
- Post-peak return volatility (%): {'5d': 0.26, '10d': 0.34, '20d': 1.12, '30d': 1.26}
- Risk-level switches: 11
- Score range: first 27.6 / peak 58.97 / last 55.56

### 3.3 2020 (COVID Crash)

- Input observations: VIXCLS 62, BAA10Y 62, SPX 61, common dates 61
- Common dates: 2020-02-03 to 2020-04-29
- Trading days: 61
- Max drawdown: -33.92%
- Warning lead time (score ≥60 vs SPX peak): -7 days (7 days after the peak)
- Risk score 40→60 speed: 4 days
- Post-peak return volatility (%): {'5d': 1.45, '10d': 3.04, '20d': 5.35, '30d': 5.15}
- Risk-level switches: 6
- Score range: first 36.82 / peak 74.9 / last 66.85

## 4. Production-path replay (latest-source evidence)

The production-path replay was added after the original fallback-only report. It calls the same `RiskModel.score` implementation used by Risk Lab, in date order, with an expanding point-in-time history through each score date. A deterministic synthetic panel runs in CI; the figures below are from a manual 2015-01-01 → 2025-12-31 replay with a 5-year warm-up, fetched on 2026-08-08. This replay predates model version 1.2.0 and does not validate the Baa-10Y input. It includes the four governed ETF proxy histories used by the deferred cross-asset diagnostics.

| Forward horizon | Outcome coverage | Spearman score vs forward loss | Precision at score ≥60 | Recall | False-positive rate |
|---|---:|---:|---:|---:|---:|
| 5 observations | 99.82% | 0.3545 | 2.16% | 72.73% | 26.52% |
| 10 observations | 99.64% | 0.4002 | 4.45% | 56.90% | 26.29% |
| 20 observations | 99.28% | 0.4097 | 12.13% | 58.44% | 25.16% |
| 30 observations | 98.92% | 0.3847 | 19.54% | 52.35% | 24.29% |

The level-switch rate was 9.0778 per 100 evaluated observations with a mean absolute score change of 1.5749. The replay used the mixed production-percentile/heuristic path for all 2,765 evaluated observations. Macro coverage was incomplete for the two credit-spread series (3,421 missing observations each), reverse repo (153), and the Fed balance sheet (2); SPY, IWM, SOXX, XLY, XLP, HYG and IEF histories were complete. The null-aware confirmation denominator also means missing inputs no longer count as benign observations.

This is evidence of positive ranking signal, not evidence of a calibrated probability. The manual panel uses latest FRED/Yahoo observations and does not contain point-in-time source vintages; the result is therefore descriptive and subject to revision bias. The synthetic CI fixture proves determinism and look-ahead protection, but is not a performance claim.

### 4.1 Governed cross-asset diagnostics

These signal results are also from the 2026-08-08 replay. They predate the Baa-10Y signal and are not evidence for its current five-year percentile trigger.

The collection path fetches XLY, XLP, HYG and IEF once through the existing quotes registry and computes percentage-point gaps from each pair's latest one-day return. These are ETF relative-return proxies, not direct economic spreads:

| Signal | Inputs and transformation | Risk direction | Production status |
|---|---|---|---|
| Cyclicals versus defensives | XLY 1-day return minus XLP 1-day return | Negative is riskier | Diagnostic only |
| High-yield versus Treasuries | HYG 1-day return minus IEF 1-day return | Negative is riskier | Diagnostic only |

At the 20-observation target horizon, the new signals produced the following point-in-time evidence:

| Signal | Evaluated | Precision | Recall | False-positive rate | Precision delta vs current confirmation | Recall delta vs current confirmation |
|---|---:|---:|---:|---:|---:|---:|
| Cyclicals versus defensives | 2,745 | 5.68% | 48.05% | 47.39% | -1.32 pp | -48.05 pp |
| High-yield versus Treasuries | 2,745 | 5.94% | 50.65% | 47.66% | -1.07 pp | -45.45 pp |

The signals provide broad event coverage but weak precision and lower recall than the current confirmation comparison point. They therefore do not demonstrate a production improvement. Risk Lab publishes their value, trigger, provider, freshness state, unit and transformation for review, but the calibration policy keeps both outside production weighting until a governed refit has stronger point-in-time evidence.

## 5. Historical policy decision

This table records the earlier replay's decision. ADR-0012 replaces its HY OAS credit input and updates provisional regime floors for model version 1.2.0.

| Component | Decision | Rationale |
|---|---|---|
| Dimension weights | Retain | Ranking is positive but moderate; revised-source history and partial proxy coverage do not justify refitting. |
| Indicator weights | Retain | No stable out-of-sample attribution is available for a weight change; preserve the reviewed transparent mapping. |
| Risk-level thresholds | Retain | Score ≥60 gives 58.44% recall and 25.16% false-positive rate at 20 observations; higher thresholds trade recall for precision without a pre-registered operating objective. |
| Data-trust formula | Gate | Keep the existing product metric, but do not call it statistical confidence or probability. |
| Cross-asset aggregation | Gate | Keep the current null-aware proxy-backed aggregation; the new ETF signals remain diagnostic-only because their 20-observation precision is below 6% and recall is below the current comparison point. |

Policy version `1.0.0` was published in the Risk Lab contract and run report at the time of this decision. ADR-0012 later changed the credit input and regime floors provisionally. It did not authorize a production calibration claim.

The technical indicator path now uses standard 14-period Wilder smoothing for RSI instead
of a simple rolling average. This methodology correction can shift RSI-derived values;
the golden score test records the current output, without changing thresholds or weights.

## 6. Interpretation of the current fallback replay

- **Warning timing:** the first score ≥60 came 17 days after the SPX peak in 2008 and 7 days after it in 2020. These fallback results did not warn ahead of either peak.
- **2018 case:** the score peaked at 58.97 and did not reach 60.
- **Volatility amplification:** post-peak return volatility rises through the 20-day window in all three cases. The 2020 30-day value is 5.15%, slightly below its 20-day value of 5.35%.
- **Stability:** the model changed risk levels 3 times in 2008, 11 times in 2018 and 6 times in 2020. These three windows do not establish stable performance.

## 7. Limitations

- Market breadth history (2008-2012) is unavailable → this report does not include the breadth dimension (review P0-3); T05 recommends later rebuilding an approximate breadth series from SPX new high/new low counts / % above MA200 and re-running the backtest.
- The MVP risk mapping is a heuristic rule set (pipeline/risk/scoring.py); this harness evaluates the **heuristic fallback** path, not the production percentile path — the score meaning is a "model-based market stress estimate", not a statistical model.
- Free data sources have no SLA; backtest windows may be skipped when network data is unavailable; rerun locally with `python scripts/calibration.py` to reproduce.
- Single-window sample size is small (3 windows); conclusions are descriptive rather than statistically significant.
- The new cross-asset inputs are ETF relative-return proxies, not direct credit spreads or sector-flow measures; latest-source history has no point-in-time vintages and signal event rates are sensitive to the selected event definition.

## 8. Reproduction

```bash
python scripts/calibration.py   # Rerun the three-window backtest and refresh this report
.venv/bin/python scripts/calibrate_production.py --start 2015-01-01 --end 2025-12-31 --warmup-years 5 --regime mixed
```

## 9. Release

- This document is published with the repository (`docs/calibration-report.md` is un-ignored from gitignore; root-level `CALIBRATION.md` is the publishable copy).
- Any credibility claim about the risk score must reference this report; no UI/copy may describe the risk score as an "exact crash probability".
