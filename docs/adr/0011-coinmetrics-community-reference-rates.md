# Expand approved Coin Metrics Community reference rates

The public dashboard owner confirmed noncommercial use. [Coin Metrics Community Data](https://docs.coinmetrics.io/packages/coin-metrics-community-data) is available under [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/), which permits noncommercial redistribution with attribution, a license link, and disclosure of changes. The UI displays attribution and the license link whenever it renders Coin Metrics data.

The approved cloud source grant selects BTC, ETH, SOL, ADA, XRP, DOGE, BNB, LINK, LTC, and AVAX. A single immutable `pipeline/coinmetrics_assets.py` catalog owns each ticker, Coin Metrics asset ID, display name, and output order. `pipeline/config/models.py` derives the provider-supported symbol set from that catalog. `config/public_sources.yaml` remains the narrower publication grant. A symbol cannot pass policy validation unless the provider can map and parse it.

`CoinMetricsProvider` sends one batched `ReferenceRateUSD` request per collection attempt and requires one valid observation for every symbol in the active grant. It keeps BTC, ETH, and SOL first, independent of config set order or response row order. The ordinary local provider default stays BTC and ETH. Approved cloud collection passes its full explicit grant. The local opt-in and the public grant therefore do not silently change together.

The provider timestamp is the source Calculation Time defined by the [Reference Rate documentation](https://docs.coinmetrics.io/coin-metrics-prices/coin-metrics-prices/reference-rate-metrics), not the runner fetch time. The collector keeps that timestamp and marks cache replay as degraded. A code-only Pages deployment reuses the existing public data generation. A policy change alone does not publish ten new observations; verify a successful approved collection and read back the public generation before describing expanded coverage as live.

These reference rates are market-wide composite prices, not exchange last trades. The Community source does not provide the dashboard's 24-hour changes, market capitalization, or trading volume for this response. Those fields remain null. The crypto expansion does not add score inputs or change risk weights. `risk_context.py` selects BTC by symbol, and `run.py` keeps BTC, ETH, and SOL as the Overview crypto rows regardless of the full Themes page order.

The Themes page names all ten approved assets in English and Simplified Chinese. Its disclosure states that the prices are reference rates and that 24-hour changes are unavailable.
