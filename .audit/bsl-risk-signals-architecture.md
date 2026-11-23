# BLS labor signal architecture

## Problem

The cloud pipeline already collects and publishes BLS unemployment data, but the risk score ignores it. The browser reads static `risk.json` and cannot join macro data into the score. A new input must keep the BLS observation month, fetch time, and freshness visible. The stored unemployment history contains revisions, not the historical release vintages needed to claim real-time calibration.

## Usage

The existing pipeline call remains unchanged:

```python
macro, meta = MacroCollector(...).collect()
context = build_risk_context(macro=macro, series_history=meta["series_history"], ...)
result = RiskModel(settings).score(context)
```

`RiskModel` derives a monthly labor signal from `series_history["unrate"]` and attaches the current BLS row's freshness and provenance. `RiskDimensionBreakdown` shows the signal's value, risk contribution, observation month, fetch time, and a localized caveat.

## Shape

Keep the rolling formula in a pure `pipeline/risk/labor_signal.py` module. The model parses provider rows into typed monthly values, then asks the module to return the complete derived history.

```python
@dataclass(frozen=True)
class MonthlyUnemploymentRate:
    period: date
    rate_pct: float

@dataclass(frozen=True)
class MonthlyLaborStress:
    period: date
    value_pp: float

def derive_sahm_style_labor_stress(
    observations: Sequence[MonthlyUnemploymentRate],
    *,
    as_of: date | None = None,
) -> tuple[MonthlyLaborStress, ...]: ...
```

For each month `t`, the formula subtracts the minimum of the 12 prior three-month unemployment averages from the current three-month average. It requires 15 consecutive monthly values for the first result. FRED documents 0.50 percentage points as the Sahm Rule trigger. This project derives a Sahm-style measure from BLS data and does not claim to reproduce the official real-time vintage series or a recession probability. [FRED's Sahm Rule series notes](https://fred.stlouisfed.org/data/SAHMCURRENT)

`RiskModel._macro_indicators` adds one `sahm_style_labor_stress` row to the existing macro dimension. The row is available only when the latest derived month matches `macro.labor.unrate.observation_period`. Its history uses the same derived series. The six-year BLS request yields at most about 58 results after the 15-month warm-up, below the model's 60-sample percentile floor. The score therefore uses an explicit, provisional heuristic. The new configured weight is 2.5, half the current macro input weight, to limit the first score change before a suitable replay exists. The model and calibration policy remain provisional.

Add `score_included: bool = True` and `observation_period: str | None` to `RiskIndicator`. The default keeps older payloads unchanged. The BLS row sets `score_included` to true only for `fresh` or `delayed` source status. A single `counts_as_risk_evidence` predicate controls dimension score, coverage, score bounds, missing inputs, evidence state, and top drivers. Stale and degraded values remain visible, but they do not change the score or evidence coverage.

The Python contract generates the TypeScript contract. `RISK_INDICATOR_KEYS`, the English and Chinese risk locale files, and `RiskDimensionBreakdown` own the new label and explanation. The page shows the source, statistical month, fetch time, and excluded status in the existing indicator row.

## Synthesis decision

Candidate 3 is the base. Candidate 1 proposed direct UNRATE and PAYEMS rows with an `evidence_eligible` field and nullable risk scores. Candidate 2 proposed the same two direct rows with `score_included` and a backward-compatible default. Candidate 3 derives one Sahm-style history from UNRATE and keeps the formula out of the dimension builder. It scored 24/25 against the arena criteria, compared with 22/25 for candidate 2 and 17/25 for candidate 1. All three reused only approved BLS data. The first two converged on a direct-series design, so candidate 3 was requested to compare a structurally different shape.

| Criterion | Candidate 1 | Candidate 2 | Candidate 3 |
|---|---:|---:|---:|
| Approved source and cadence | 5 | 5 | 5 |
| Defined formula and history | 3 | 4 | 5 |
| Freshness and provenance | 4 | 5 | 5 |
| Score impact and calibration limits | 2 | 4 | 4 |
| Model and UI structure | 3 | 4 | 5 |

The cross-judge phase was skipped. Every configured arena seat resolves to the parent model family, so the configured pool has no independent family for a cross-judge. Candidate 2's `score_included` flag and backward-compatible default are grafted into the chosen design. Candidate 1's nullable score is rejected because it spreads exclusion state into an existing numeric contract. Its two direct labor rows are rejected because they count related unemployment and payroll evidence separately. Candidate 2's 2.5-point relative weight informs the chosen signal's provisional weight.

## Tradeoffs accepted

- The score gains one interpretable labor signal instead of two correlated series.
- The model uses a hand-set threshold curve because the derived history cannot meet the current percentile sample floor.
- A stale value remains visible for diagnosis but contributes neither to the score nor to evidence coverage.
- The new 2.5-point within-dimension weight is a reviewable initial choice, not an empirically fitted value.
- The signal remains provisional because current BLS observations can be revised after their first release.

## Alternatives considered

- Direct UNRATE and PAYEMS inputs avoid rolling calculations, but count related labor measures twice and shift two indicator weights.
- BLS rows outside the score would preserve current totals, but would not expand risk scoring.
- FRED's published Sahm series includes current and real-time versions, but this project has not approved FRED for cloud redistribution and does not store the real-time input vintages. The design derives the measure from its approved BLS source.

## Implementation reconciliation

The implementation uses the selected typed monthly input and result records in `pipeline/risk/labor_signal.py`. A pure function excludes duplicated or gapped monthly windows and honors the as-of cutoff. `RiskModel` parses the existing history rows, requires a BLS current input, matches the derived month to its observation period, and assigns freshness eligibility before aggregation. The implementation retains one `counts_as_risk_evidence` predicate across dimension scoring, coverage, bounds, missing inputs, available counts, and drivers.

The chosen 2.5 relative weight, provisional heuristic, backward-compatible `score_included` default, and optional observation period follow the saved contract. The UI retains excluded values and displays their source month, retrieval time, formula, and reason in both languages. The model does not claim a calibration replay because the current panel lacks BLS unemployment.

Root expanded the implementation scope to permit updates to existing config contract expectations in `tests/pipeline/test_config_drift.py`. The existing policy-version and exact macro-key assertions now reflect version `1.1.0` and the fifth macro input. The decision allowlist already accepts `change` and required no edit. No test case was added and no test was run.

Independent review and real-artifact verification remain with root. No architecture deviation awaits acceptance.

## Open questions and risks

- The current calibration panel lacks BLS unemployment. It cannot validate this score change.
- Annual BLS seasonal-factor revisions can change earlier values in this signal.
- BLS fetches at most once per UTC date. The 15-minute workflow schedule does not change that source limit.

## Next implementation step

Build the pure monthly formula, then wire its current value, provisional score, freshness gate, generated contract, and bilingual row as one feature.
