# Risk score history version boundary

## Problem

Model 1.1.0 adds a scoring input, so its totals are not directly comparable with earlier model totals. Risk history rows currently omit `model_version`; `read_prev_risk` passes yesterday's total and dimensions plus all retained rows to the scoring context, and `_history_trends` compares numeric rows without a model identity. The browser's trend chart likewise draws one line through every date.

## Usage

The risk writer tags each new daily row with the `model_version` from its computed result. The backend selects prior scores and trend history only when a row has the exact active version. The history endpoint remains a plain array. The chart plots a separate line for each explicit version and keeps rows with unknown versions in its accessible details table only.

## Shape

```py
risk_row = {
    "date": today,
    "total_score": result.total_score,
    "model_version": result.model_version,
    ...
}

def read_prev_risk(
    writer: StorageWriter, *, model_version: str, as_of_date: str | None = None
) -> tuple[float | None, dict[str, float] | None, list[dict[str, Any]]]: ...
```

The storage row is a plain JSON mapping with required `model_version` for new writes. Existing public rows without the field remain legacy/unknown and are not treated as matches. `src/schemas/history.ts` accepts the field as optional for backward compatibility.

```ts
export interface RiskTrendPointInput {
  date: string;
  total_score: number | null;
  model_version?: string;
  risk_level?: string;
  confidence?: number;
}
```

Exact version equality is the only score-compatibility rule. `read_prev_risk` filters history before selecting yesterday's value and before supplying rows to the existing 7-day and 30-day trend calculation. The chart groups only explicitly versioned points into separate series; unknown-version values remain in the details table with a localized unknown marker.

## Candidate assessment

| Criterion | Candidate 1: typed legacy/versioned unions and chart cohorts | Candidate 3: versioned history context and null breaks |
|---|---:|---:|
| No cross-version deltas | 5 | 5 |
| No misleading chart continuity | 5 | 4 |
| Legacy JSON remains readable | 5 | 5 |
| Small public surface | 3 | 4 |
| Fits existing pipeline ownership | 3 | 4 |

Candidate 2 produced no artifact after two waits and was stopped. The candidates and cross-judge panel use the same configured parent model family; cross-judge independence was unavailable and is skipped.

## Synthesis decision

Use Candidate 3's row-level version identity and exact-version comparison policy, with Candidate 1's separate chart cohorts and table-only treatment for unknown rows. Adapt both to the repository's real plain-dictionary history writer and `read_prev_risk` tuple rather than introducing the candidates' hypothetical history classes and context API. This keeps the additional boundary explicit while avoiding a new framework around a small JSON row.

The previous score, dimension values, and week/month comparisons all use the filtered rows. If the active version has no comparable previous day, one-day and dimension trends remain unavailable; week/month comparisons likewise return no result until matching-version history is in their existing date windows. Legacy rows are never inferred to have version `1.0.0`.

The frontend receives every historical row unchanged. It groups versioned values into distinct series, inserts date-gap separators within each group, and does not plot rows whose model version is missing. Six symbols crossed with three supported line patterns distinguish up to 18 series while retaining the chart's single accent color. One shared table renderer labels legacy rows as unknown and synthetic separators as data gaps. No public fixtures are rewritten or backfilled.

## Verification limits

No tests are added or run. Static public history predates `model_version`, so the checked-in fixture can validate the unknown-version UI state but cannot demonstrate a rendered Sahm-style row or a populated model 1.1.0 segment. The cloud collection pipeline is not run during UI verification.

## Formula readability decision

The no-comments review found that the formula docstring restated slice arithmetic whose meaning was encoded as `-14`, `[-3:]`, and `range(12)`. Two local shapes were compared. Candidate 1 keeps the month-number map and gives the 3-month average, 12 prior average endpoints, and 15 required observations named constants and one private averaging helper. Candidate 2 groups duplicate months into explicit slots and scans a deque of 15 observations through multiple helpers.

Candidate 1 is the base because the existing module already rejects duplicates by omitting ambiguous months, and named required-month checks preserve that behavior with less machinery. Candidate 2's explicit continuity scan is rejected because a slot type and three helpers add state and boundaries without changing which windows qualify. The implementation keeps the existing public signature and result types, names the 3, 12, and 15-month spans, and computes prior averages ending at `t-12` through `t-1`. It then compares their minimum with the current average ending at `t`. No formula behavior changed.
