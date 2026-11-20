from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final


@dataclass(frozen=True, slots=True)
class CoinMetricsAsset:
    symbol: str
    asset_id: str
    name: str


COINMETRICS_ASSETS: Final[tuple[CoinMetricsAsset, ...]] = (
    CoinMetricsAsset("BTC", "btc", "Bitcoin"),
    CoinMetricsAsset("ETH", "eth", "Ethereum"),
    CoinMetricsAsset("SOL", "sol", "Solana"),
    CoinMetricsAsset("ADA", "ada", "Cardano"),
    CoinMetricsAsset("XRP", "xrp", "XRP"),
    CoinMetricsAsset("DOGE", "doge", "Dogecoin"),
    CoinMetricsAsset("BNB", "bnb", "BNB"),
    CoinMetricsAsset("LINK", "link", "Chainlink"),
    CoinMetricsAsset("LTC", "ltc", "Litecoin"),
    CoinMetricsAsset("AVAX", "avax", "Avalanche"),
)

COINMETRICS_BY_SYMBOL: Final[Mapping[str, CoinMetricsAsset]] = MappingProxyType(
    {asset.symbol: asset for asset in COINMETRICS_ASSETS}
)
COINMETRICS_BY_ASSET_ID: Final[Mapping[str, CoinMetricsAsset]] = MappingProxyType(
    {asset.asset_id: asset for asset in COINMETRICS_ASSETS}
)
COINMETRICS_ASSET_SYMBOLS: Final[frozenset[str]] = frozenset(COINMETRICS_BY_SYMBOL)
