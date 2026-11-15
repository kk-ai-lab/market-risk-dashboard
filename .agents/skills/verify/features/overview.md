# Overview risk panorama

The Overview page presents the dashboard's global risk score, market regime, recent changes, cross-asset heatmap, drivers, catalysts, and bilingual AI brief from the published dataset files.

## Sub-features

- `overview-route` loads the English or Chinese overview from the navigation.
- `risk-conclusion` renders a score, level, regime, and daily movement.
- `risk-context` renders the trend, cross-asset heatmap, drivers, and catalyst sections when data exists.
- `ai-brief` renders a validated brief or its explicit unavailable/degraded state.

## How to get to it (user POV)

- Choose `Overview` in the `Main navigation` region.
- Choose the `Risk Dashboard` brand link to return to the overview.
- Switch language with `Switch language`; the current page remains the overview in the selected locale.

## Driving it with playwright-cli

Preconditions:

- The verification instance passes the `../SKILL.md` doctor checks.
- `public/data/latest/dashboard.json`, `risk.json`, `facts.json`, and `analysis.en.json` are present and parse as JSON. For chart proof, `public/data/history/risk/30d.json` is also present.

- **Open Overview.** From another page, choose `Overview` in `Main navigation`. Run `playwright-cli -s=verify click "getByRole('link', { name: 'Overview' })"`. The URL ends in `#/en/overview` and the page heading is `Overview`.
- **Read the risk conclusion.** Run `playwright-cli -s=verify find "Global Risk Score"` and inspect the adjacent score and risk-level text. The same snapshot contains `Market Regime` and `1D change` values; do not hard-code dated numbers.
- **Read the supporting sections.** Run `playwright-cli -s=verify find "Risk Trend (30d)"`, `playwright-cli -s=verify find "Cross-Asset Heatmap"`, and `playwright-cli -s=verify find "Top Risk Drivers"`. Each populated section renders data or its explicit empty state.
- **Inspect the brief state.** Run `playwright-cli -s=verify find "AI Market Brief"`. The page renders the brief or a visible unavailable/degraded explanation; do not treat missing brief content as a valid populated brief.
- **Proof.** Save an accessibility snapshot and screenshot after the user navigation action. Record the visible score, regime, brief state, route, and any console/network errors in `proof.md`.

## Gotchas

- Values change as `public/data/` is updated; assert that a valid value and level render, not a historical fixed score.
- Risk history and the latest risk envelope are separate data files. A healthy score with an empty chart is a chart precondition failure, not proof that the trend works.
- AI brief rendering depends on paired language data and lineage/freshness checks. Capture the user-visible state rather than bypassing its validation.
- `Risk Dashboard` brand returns to the overview; navigation text is localized after switching language.
