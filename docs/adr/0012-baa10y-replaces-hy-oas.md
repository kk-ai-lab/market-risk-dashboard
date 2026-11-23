# ADR-0012: Replace HY OAS with the Baa-10Y spread as the credit-stress input

- **Status:** Accepted for provisional implementation
- **Date:** 2026-11-22

## Context

The risk model scored credit stress with `hy_oas` (FRED `BAMLH0A0HYM2`, ICE BofA US High
Yield OAS) at weight 10 in `liquidity_credit`, with the 5-year rolling percentile used by
every other macro input. The selected replacement is FRED's [Moody's Baa corporate yield
minus the 10-year Treasury](https://fred.stlouisfed.org/series/BAA10Y). The FRED series
notes include a Moody's copyright and redistribution restriction; FRED's [terms](https://fred.stlouisfed.org/legal/terms/)
state that third-party rights still apply. Kai selected the personal-use route and accepted
that licensing risk for the dashboard. This decision records that choice and does not claim
that the series has an open redistribution license.

The committed calibration replay did not validate the HY input it named. The replay panel
had no HY column, so the recorded "HY" baseline produced the same peak scores as the run
with no credit input at all. The 5-year-percentile Baa-10Y variant was the only credit
variant in that replay whose lead time and peak separation came from the credit column
actually present in the panel.

## Decision

Replace `hy_oas` with `baa10y_spread` (FRED `BAA10Y`) in the liquidity-credit dimension at
the same relative weight of 10. Keep the model's existing method: the 5-year rolling
percentile drives the indicator score, and the cross-asset signal `baa_spread_widening`
fires at the 90th percentile or above rather than at a fixed level.

Set the regime floors to 4.00 for `crisis` and 3.00 for `liquidity_stress`. Bump
`model_version` to `1.2.0` and keep calibration status `provisional`. The replay values and
the regime floors are provisional heuristics, not an empirical refit.

Keep `BAMLH0A0HYM2` out of the risk-score path. It remains in the macro roster for the HY
context tile, with ICE attribution under the separate `fred_ice` personal-use grant. No
payload reader may map it to `baa10y_spread`.

## Consequences

- The score uses the series selected by Kai for the personal-use dashboard, with its
  accepted source-risk recorded in `config/public_sources.yaml`.
- Baa-10Y is investment-grade and reacts later and less than HY OAS. A given credit episode
  therefore scores lower and later than the HY reading would.
- Previous-version history is not a comparison baseline. Daily, weekly and monthly changes
  stay unavailable until matching `1.2.0` rows exist.
- The replay's contribution now comes from the credit column it measures. It does not
  establish that Baa-10Y predicts drawdowns better than HY OAS would, because the panel
  never contained HY OAS.
