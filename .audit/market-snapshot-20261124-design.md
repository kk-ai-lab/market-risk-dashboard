# Market snapshot design

## Goal and observable result

Overview shows a cross-asset market snapshot with at least four real groups: equity market indices, crypto reference prices, commodities, and rates / currencies. Every number shows its unit and observation date or period. Percent changes show their actual period; no monthly average is labeled as a daily close. The Equities page distinguishes broad country indices from individual stock quotes. Unlicensed equities and sector ETFs remain absent with an explicit reason. The risk score remains hidden while evidence is insufficient.

## Baseline

- Local overview before changes: only BTC, ETH, SOL in `Cross-Asset Heatmap`; no prices; 16 licensed-market rows hidden; Global Risk Score hidden.
- Evidence: `artifacts/verify/market-snapshot-20261124/overview/before.yml` and `before.png`.
- Current data snapshot is at `public/data/latest/`; untracked user file `uv.lock` must remain untouched.

## Evidence about candidate sources

- OECD Financial market SDMX dataflow (`DSD_STES@DF_FINMARK`, version 4.0) returns monthly U.S. and China share-price indices on the public OECD API. The live query returned index values for 2026-10, 2026-09, and 2026-08, unit index 2015=100. OECD's indicator page says monthly values are usually simple averages of daily closing values. OECD API use is free. The OECD terms allow data extraction and redistribution with attribution unless dataset metadata identifies third-party restrictions. Preserve the citation and the monthly-average meaning.
- Shanghai EOD terms allow redistribution of next-day End of Day Information without a separate license, but the official query endpoint was not reliably readable in this environment. It is not ready to integrate until stable official access and data shape are independently confirmed.
- SZSE official rules state that exchange trading information belongs to SZSE and may not be used or transmitted without permission. Keep SZSE symbols excluded.
- World Bank Pink Sheet is already approved under CC BY 4.0. The current workbook has additional energy, agricultural, and industrial-metal price series beyond the four configured today. Only selected major rows should be added, preserving monthly-average units and observation periods.
- Yahoo, FRED/Moody's, Cboe VIX, and ICE OAS remain personal-use / restricted and must not enter public output.

## Competing shapes

### A. Extend the old heatmap row

Add `price`, `unit`, and `period` fields to the existing daily-change `DashboardAsset` and include all sources there. This is a smaller diff, but it leaves `change_1d` as the implied center of the view and mixes monthly national indices, commodity averages, crypto reference rates, and yield levels in one type with weak semantics.

### B. Make the home page a market snapshot read model

Add a typed `MarketSnapshotItem` with `key`, `category`, localized name, numeric value, explicit unit, optional percent change plus change period, observation date / period, frequency, and source. The pipeline constructs this projection from the datasets it already owns. Add a separate `EquityMarketIndex` collection in `EquitiesDataset` for OECD's country indices; individual stocks remain represented only by `EquityAsset`. Overview renders grouped snapshot rows rather than calling a heterogeneous collection a heatmap.

## Choice

Choose B. The data contract owns the distinction between a national monthly equity index and a stock, and each displayed value carries its measurement basis. `EquitiesDataset.benchmarks` is the source-of-truth collection for OECD benchmarks; `DashboardPayload.market_snapshot` is a read projection for Overview. No source value is recalculated or inferred without observation history. Crypto percentage change remains absent when Coin Metrics does not supply one. Selected rates are levels only. Commodity month-over-month percentage is shown only where consecutive monthly observations exist.

## Work units and gates

1. Add the OECD provider and strict parsed benchmark contract; verify official API shape and an exact-country allow-list.
2. Expand approved World Bank commodity series with exact workbook-header / unit checks; run the approved-source data refresh and read generated rows.
3. Build the typed dashboard snapshot from equity benchmarks, selected crypto, commodities, Treasury yields, and FX; migrate Overview and Equity pages; add required bilingual labels and source disclosures.
4. Correct Status translations and mixed-domain status so missing equities do not make healthy commodities appear missing.
5. Run source checks, data contract checks, frontend checks, then use the real local UI and inspect emitted immutable data. Do not include restricted market data.

## Open limitations

- OECD provides broad monthly country-level share-price indices, not named U.S. stock prices, A-share individual stocks, or ETF-sector returns.
- SZSE stock and sector data remains unauthorized in the current source set.
- Credit-spread and VIX values remain missing because the known series are restricted.
- BLS calendar remains dependent on the 403-prone feed until an official alternative passes a live source and behavior check.
- Independent review is unavailable in this runtime; if that remains true, mark it explicitly before handback.
