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
- Treasury values are from `2026-11-20`; a current-year refresh timed out, so those rows remain degraded instead of being marked fresh. BLS says it could not publish October 2026 12-month changes for indexes missing October 2025 data ([official notice](https://www.bls.gov/cpi/notices/2026/12m-changes-202610.htm)). The public cards now mark October 2026 unavailable and identify September 2026 as the latest reported period.
- Only risk model `1.2.0` remains. Its evidence coverage is 37.78%, so the UI withholds the score. The 30-day history contains one date; no synthetic history was added. Risk inputs lack licensed equity and volatility data.
- The snapshot has no AI brief files. The old public history that came from personal-use sources was removed. The operational `feeds/fedwatch-history.json` cache remains because the collector and validator require it.
- The coverage audit passed with zero rights violations, zero missing attributions, and no missing dataset diagnostic details. The publication candidate is not the Pages production generation. Issue #287 still requires a merged deployment and a readback from Pages.
- Browser evidence is in `artifacts/verify/20261123-data-gaps/proof.md`. Overview showed the risk score hidden, only `1.2.0`, and no 30-day trend. Status now explains the specific public-use and provider failures in both locales. The browser logged the known `indicator_scores` warning, plus provider-specific passthrough fields in `metadata/sources.json`; the contract check passed and the fields remain in the JSON.

## Independent review corrections

- Grok 4.7 blocked head `f6ce0c2989441d3e3347114357d03457e4e50bbf` on November 23. It found incorrect New York Fed notices, stale CPI freshness, Treasury 2y/10y cards using T10YIE attribution, Overview null cells without a rights reason, and a coverage audit that missed nested histories.
- The New York Fed requires the reference-rate notice for DFF and SOFR and the general content notice for RRP. The current official text is in the [Terms of Use](https://www.newyorkfed.org/privacy/termsofuse): general attribution at paragraphs 247-250 and the specific reference-rate notice at paragraphs 281-287. The collector formats the general notice year at publish time.
- CPI and core CPI now carry `latest_yoy_period_unavailable` for `2026-10`, display a specific bilingual note, and are marked `degraded` while retaining the `2026-09` values.
- DGS2 and DGS10 now have their own Treasury CC0 attributions. Overview index and sector-ETF null cells now distinguish missing public redistribution permission from a provider miss.
- The coverage audit now checks every nested `daily.json` archive and the 30/90-day sparse bundles for blocked macro series. Month-end timestamp logic uses a named helper without the comment flagged in review.
- A seeded full refresh completed at `2026-11-23T20:23:03Z`. It retained the 20/20 Chinese news translations and FedWatch operational cache. Coverage: 24/28 macro; 260 energy observations; 4 commodities; 4 BEA calendar events; zero unauthorized macro values/history or personal-use source markers; zero AI brief files. Candidate validation passed with zero errors, warnings, and secret-scan hits.
- The first PR run on the prior head had failed Python and frontend checks (61 Python tests and one i18n-key test). The frontend translation-key defect is corrected. Python failures span source-policy and provider fixtures affected by the expanded approved-source contracts; required checks must be green on the final head before merge.

## Follow-through on independent review

- The New York Fed terms, CPI availability, Treasury attribution, Overview rights reason, and nested history audit findings are corrected as described above.
- DOL claims are embargoed before the public release. The DOL OIG documented the weekly report's Thursday 8:30 a.m. Eastern release and its embargo ([2000 OIG audit](https://oig.dol.gov/public/reports/oa/2000/03-00-011-03-315.pdf)); the later OIG review confirms ETA's public-release embargo policy ([2014 OIG audit](https://oig.dol.gov/public/reports/oa/2014/17-14-001-03-315.pdf)). The ETA 539 and ETA 538 paths now reject or omit reports until 8:31 a.m. Eastern on the report's stated release date. The one-minute margin avoids publishing at the scheduled boundary. The DOL newsroom currently lists the weekly report as a public release ([ETA releases](https://www.dol.gov/index.php/newsroom/releases/eta)).
- The Federal Reserve DDP label parser's contract was checked against the live H.4.1 and H.10 pages: each `FreqRequest_n` input is immediately followed by a `<label for="FreqRequest_n">` element. The parser's label association matches both pages.
- The BLS calendar endpoint still returns HTTP 403 to this collector. The official BLS schedule includes date-only holiday rows and publishes release events with times; BLS states that economic releases are scheduled at 8:30 or 10:00 a.m. Eastern ([November 2026 schedule](https://www.bls.gov/schedule/2026/11_sched_list.htm), [dissemination policy](https://www.bls.gov/about-bls/dissemination.htm)). The parser now documents only that it accepts timestamped releases; the actual feed convention remains unverified because the feed cannot be retrieved.
- Treasury's current yield-curve methodology page is [the official methodology page](https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics/treasury-yield-curve-methodology); the stale path has been replaced.
- A fresh independent review is required on the corrected PR head before merge. The earlier 61 Python and one frontend failures were from the previous head; the current branch contains fixture and assertion updates for the approved-source contracts. GitHub CI on the new head remains the merge gate.
