# Data, UX, and motion verification

Run date: 2026-11-24
Local app: http://127.0.0.1:4173/market-risk-dashboard/

## Data and publication

- Refreshed approved first-party data into `public/data`. The latest macro snapshot has 27 of 31 values; four credit/volatility series remain withheld because their configured grants do not allow public redistribution. Equities, A-share quotes, sector/theme quotes remain absent for the same rights reason. The scoped coverage audit reports no personal-use values or histories, no unwithheld risk scores, and no AI brief files. This audit checks its listed paths and configured grants; it is not a blanket legal determination.
- Latest G.17 rows include October 2026 manufacturing, computer/electronics, and semiconductor output. DOL claims show the latest published report: 203k initial claims for Nov 14 and 1,763k continued claims for Nov 7.
- Staged the exact repository snapshot with `uv run python scripts/stage_publication.py --candidate public/data --store /private/tmp/mrd-ux-motion-exact-final-20261124/store --revision codex-free-data-ux-motion --created-at 2026-11-24T11:03:00Z`. Candidate validation passed. Generation `2b24398d85868d47df5a287359cf87967d7152a9391ef563724b385814c92d84`, manifest `541867012c227934de81498fc0be147c8e10c176dcfa7d08b708bbbffde9c67a`; all 144 listed files match their SHA-256 values, with no missing files or mismatches.
- `uv run python scripts/audit_data_coverage.py --data-dir public/data` completed. It reports 27/31 macro values, 20/20 translated news items, no AI brief files, no unwithheld risk score/history, no personal-use-only values/history, no unauthorized grants in its checks, and three expected warnings for absent market history bundles. BLS calendar data remains unavailable because its public feed returns HTTP 403.

## Automated checks

- `uv run --extra dev pytest -q`: passed, 1,199 tests.
- `npm test -- --run`: passed after the final Equities empty-state regression test was added. The suite emits expected jsdom error-state and React act diagnostics from existing asynchronous route tests.
- Passed: `npm run check:contracts`, `npm run check:i18n`, `npm run check:data`, `npm run lint`, and `npm run build`.
- Build warning: the lazy ECharts chunk is 629.27 kB minified (210.80 kB gzip). It is loaded on demand; the warning is non-blocking.

## Browser checks

- Manually drove Overview, Macro, Equities, Sectors, News, Calendar, Risk Lab, and Status on the local Pages-base URL. At 360px, 390px, and 1440px each route had no horizontal overflow. Risk score remains hidden at 37.78% evidence coverage. Equity empty state states the redistribution restriction, and no blank US-equities section appears.
- At 360px, Macro shows the independent-scale explanation and screen-reader text with start/end dates, values, and units for each sparkline. Desktop Macro shows separate unit axes. Dark-to-light theme switching was exercised while Macro remained open.
- Risk Lab was opened and its theme control switched to light without navigating away. The current snapshot has insufficient risk evidence, so no RiskTrendChart canvas renders; the chart's live color response to a theme change is code-reviewed but not visually verified with populated risk history.
- Reduced motion was enabled at 390px; interactive transitions resolved to 0.01ms and ECharts animation is disabled. Stable route transitions had zero layout-shift entries. A 120-frame scroll sample at 1440px measured 16.69ms mean, 18.5ms p95, 19.4ms maximum, and zero tasks over 50ms. Sample was taken after the page settled, excluding initial-load shifts.
- Screenshots and accessibility snapshots are in `artifacts/verify/20261124-data-ux-motion/`.

## Review findings

- First independent review found the Equities page hid the available memory proxy when equities were empty and showed an empty US equities heading when only A-share assets existed. Both were fixed; frontend regression coverage now checks both states. Final independent review is pending.

- Final independent review: PASS by `gpt-6.1-terra`. It verified the two Equities corrections, test sequencing, the 144-file snapshot hash match, and the rights-based gaps.
