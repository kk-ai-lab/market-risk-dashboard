# Public data gap design decision

Date: 2026-11-23
Baseline revision: `d09f8258f1b4d416511a1082e9a42ef7c43420cd`
Tracking issue: https://github.com/kk-ai-lab/market-risk-dashboard/issues/287

## Arena selection

Two independent designs proposed extending the current grant → provider → collector flow and deriving target-level coverage from its existing rosters. Both rejected a new normalized observation database or a second central target catalogue for this bounded data repair. I selected the shared design and grafted one additional safeguard from candidate 2: code-only publication reuse must change when collection or schema code changes, even if YAML does not.

The configured cross-judge roles all inherit the parent model family, so a same-family cross-judge would not be independent and was skipped.

## Applied principles

- **Attack the premise:** “fill every gap” means fill every gap with an authorized value; it does not authorize personal-use quotes or grant market-data rights. Equity, sector and A-share prices remain excluded until exact public redistribution rights are documented.
- **Build the lever:** add a reusable coverage audit that reads the public snapshot and reports each missing target and reason, rather than relying on page-level impressions.
- **Sequence verifiable units:** prove each source and field contract independently (EIA weekly CSV, World Bank monthly prices, official BLS schedule, primary-source macro rates) before allowing it into a candidate.
- **Fix root causes:** use no-key official files/APIs where terms allow redistribution, and include collector/schema code in the cloud collection fingerprint so code-only deploys cannot re-serve a stale snapshot after a data-pipeline change.
- **Boundary discipline:** keep transport in provider adapters, data meaning in collectors/contracts, rights in `public_sources.yaml`, and display labels/disclosures in the frontend.
- **Explain the number:** distinguish observed monthly benchmark averages from live commodity futures, and keep observation period separate from retrieval time.
- **Prove it works:** verify each provider parser against the live official endpoint, validate the assembled candidate, publish only the approved snapshot, and read back the current pointer and manifest.

## Chosen implementation shape

Keep the current source grant → provider → collector architecture. Add only the provider and contract changes needed to cover verified public sources. Keep unlicensed fields explicitly blocked. The coverage audit is derived from the existing schemas and configs, not a second source of truth.

## Evidence and scope

- EIA publishes WPSR CSV at `https://ir.eia.gov/wpsr/psw00.csv`; its public reuse terms permit distributing its data with source/date attribution. This replaces the API-key-only route for the same WCESTUS1 field.
- The World Bank Commodity Markets catalog identifies the Pink Sheet dataset as public and CC BY 4.0, updated monthly. Use monthly observations only and label them as such.
- BLS publishes a maintained iCalendar feed for upcoming releases and states that its publications are public domain, with citation requested.
- Treasury Daily Treasury Par and Real Yield Curve datasets are public-domain/CC0; T10YIE is derived transparently as same-date nominal 10-year par yield minus real 10-year par yield.
- New York Fed terms permit copying and distributing reference-rate data, but require a specific disclaimer. Add EFFR/DFF and SOFR only with that notice displayed on their cards.
- The live FRED key is absent, and the FRED API requires a key. A FRED key alone would not override third-party data rights. Keep the remaining series missing until primary data sources and their redistribution terms are confirmed.
- The configured Yahoo source is personal-use and is not expanded to Equities, Sectors, A-shares or commodities.

## Completion check

For each displayed field, report either a value from an authorized source with source/observation period/cadence, or an honest missing reason. No monthly average is presented as a live quote. No personal-only data enters the candidate. The current public generation is verified against the generated manifest after publication.
