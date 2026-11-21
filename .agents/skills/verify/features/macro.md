# Macro indicators

Macro presents rates, credit, inflation, labor, liquidity, FX, volatility, and rate expectation data from the published macro datasets.

## Sub-features

- `macro-route` opens the macro indicator page.
- `macro-sections` shows available indicator groups and their values.
- `macro-history` shows history when its matching history dataset is available.

## How to get to it (user POV)

- Choose `Macro` in `Main navigation`.

## Driving it with playwright-cli

Preconditions:

- The verification instance passes the `../SKILL.md` doctor checks.
- `public/data/latest/macro.json` is present and valid JSON. Check `public/data/history/macro/` before expecting chart history.

- **Open Macro.** Choose `Macro` in `Main navigation`. Run `playwright-cli -s=verify click "getByRole('link', { name: 'Macro' })"`. The URL ends in `#/en/macro` and the page heading is `Macro`.
- **Inspect indicator groups.** Run `playwright-cli -s=verify snapshot`. The page shows populated indicator cards/sections for the available categories (`Rates`, `Credit Spreads`, `Inflation`, `Labor`, `Liquidity`, `FX`, and `Volatility`) or an explicit empty state for a category with no fixture data.
- **Inspect chart data.** Find `Macro Indicator Overview`; if the chart exposes `Show data table`, activate it using its current snapshot ref and verify row labels and values. Do not infer chart contents from the title alone.
- **Proof.** Save snapshot and screenshot after navigation. Record which categories were present and whether the data table/history was available.

## Gotchas

- The time series may be absent even when latest macro cards render. Check the matching file under `public/data/history/macro/` before treating it as an interaction failure.
- Category labels are localized; this recipe uses the English route and names.
- Numbers and freshness change with the published data; verify rendering and units, not fixed values.
