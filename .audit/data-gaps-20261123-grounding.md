# Data gap design grounding

## Goal

Fill every dashboard data gap that can be filled with a documented, authorized source. Keep data that lacks a public-use grant explicitly missing. Do not add paid subscriptions, credentials, or AI-written market briefs.

## Live baseline

- App revision: `d09f8258f1b4d416511a1082e9a42ef7c43420cd`.
- Pages generation: `c8ede6b22d7d2d846ff61cb299831cd904d4376d0205c88369f6d07859aa03a2`, created `2026-11-23T17:02:16Z` by the successful scheduled run `45222975022`.
- Macro: 28 entries, 22 `missing` (FRED), 2 `stale`, 4 `fresh`; `DATA_FRED_API_KEY` was not configured, and the run sent no FRED request.
- Equities: 0 assets. Sectors: 0 sectors and 0 themes. `metadata/freshness.json` reports `source_policy_excluded` for both.
- Commodities: 0 assets, `source_policy_excluded`. Energy: 0 observations, `provider_auth_failed` because `DATA_EIA_API_KEY` is absent.
- News: 20 Federal Reserve items, 0/20 `title_zh`, 0/20 `summary_zh`; bilingual coverage is degraded.
- Calendar: 4 BEA events. It is fresh but lacks other official release calendars.
- Risk: model `1.2.0`, evidence coverage `0.5389`; missing inputs are macro 3/5, liquidity-credit 4/4, volatility 1/2.
- Overview has 5 index rows and 11 sector ETF rows. `config/public_sources.yaml` classifies their Yahoo source as `personal-use`. Do not extend it to the public Equities/Sectors/A-share pages or present personal-use data as redistributable.
- The latest generation contains no analysis/AI-brief file. The dashboard domain guide says the public application does not generate or consume AI-written briefs.

## User source boundary

An earlier explicit instruction requires free sources with confirmed public redistribution rights for Equities, Sectors, and A-shares; everything else stays missing. The latest request says to fill all gaps, so seek every authorized free source, but do not treat “free API access” or “publicly viewable” as a redistribution grant. Do not use personal-use-only feeds for public output. Record rights, attribution, covered fields, and cadence in the source grant before enabling collection.

Prior source-rights research, to read without modifying: `/Users/kai/Desktop/my-repo/market-risk-dashboard/docs/operations/public-market-data-redistribution-research.md`.

## Repository boundaries to inspect

- `config/public_sources.yaml` owns source grants.
- `pipeline/config/models.py` validates grants and builds the approved collection plan.
- Provider adapters under `pipeline/providers/` cross the network boundary.
- Collectors under `pipeline/collectors/` map provider data into published dataset schemas.
- `pipeline/validation/` and `scripts/validate-json.mjs` gate candidate snapshots.
- `src/lib/api.ts` and `src/pages/` read published latest datasets.
- `pipeline/cloud_refresh.py` and `.github/workflows/deploy-pages.yml` collect and publish scheduled snapshots.

## Existing ideas not accepted without review

- `origin/fix/refresh-source-policy` and its local ancestor add Yahoo personal-use coverage for indexes/sector ETF proxies. This is not permission for public redistribution and must not be copied into the public Equities/Sectors grant.
- An EIA API key and a FRED API key are absent. Prefer official public bulk sources where rights and stable field mapping are confirmed; do not print or synthesize credentials.

## Completion predicate

For every user-visible empty/stale/degraded field in the live dataset contracts, the output either contains a current value with source, observation time, attribution, and allowed public-use status, or remains missing with a specific source-rights/key/coverage reason. No licensed field is silently published, no missing value is called fresh, the candidate passes source-policy and schema validators, and the published Pages generation is read back and matches the candidate.


## Final candidate refresh

- Refreshed the isolated approved-source directory with `DATA_APPROVED_SOURCES_ONLY=true DATA_DATA_DIR=/private/tmp/mrd-approved-snapshot-20261123-final/data uv run python -m pipeline.run --full`. It completed successfully at `2026-11-23T19:43:47Z` after 166 seconds.
- The candidate contains 24 of 28 macro series. `BAA10Y`, `BAMLC0A0CM`, `BAMLH0A0HYM2`, and `VIXCLS` remain null because their available source grant does not permit public redistribution. All observed macro values have attribution; the audit found no unauthorized values, unauthorized history, or Yahoo/yfinance markers.
- Equities remain empty for five configured US symbols and eleven A-shares. Sectors remain empty for two configured sectors and twenty themes. No public redistribution grant was found for those price series.
- EIA has 260 weekly energy observations. World Bank Pink Sheet has four monthly benchmark averages. Both feeds are documented with source, units, and cadence.
- The calendar has four BEA events. The official BLS iCalendar request returned HTTP 403. News has twenty Federal Reserve items and Chinese translations for all twenty. The SEC RSS source was skipped because `DATA_SEC_CONTACT_EMAIL` is not configured.
- Treasury values are from `2026-11-20`; a current-year refresh timed out, so those rows remain degraded instead of being marked fresh. The BLS CPI and core CPI observations remain `2026-09`, after the October CPI release could not report a 12-month change because October 2025 data was unavailable during the federal funding lapse.
- Only risk model `1.2.0` remains. Its evidence coverage is 37.78%, so the UI withholds the score. The 30-day history contains one date; no synthetic history was added. Risk inputs lack licensed equity and volatility data.
- The snapshot has no AI brief files. The old public history that came from personal-use sources was removed. The operational `feeds/fedwatch-history.json` cache remains because the collector and validator require it.
- The coverage audit passed with zero rights violations, zero missing attributions, and no missing dataset diagnostic details. The publication candidate is not the Pages production generation. Issue #287 still requires a merged deployment and a readback from Pages.
- Browser evidence is in `artifacts/verify/20261123-data-gaps/proof.md`. Overview showed the risk score hidden, only `1.2.0`, and no 30-day trend. Status now explains the specific public-use and provider failures in both locales. The browser logged the known `indicator_scores` warning, plus provider-specific passthrough fields in `metadata/sources.json`; the contract check passed and the fields remain in the JSON.
