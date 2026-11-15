"""Opt-in Coin Metrics Community USD reference rates.

Community data is CC BY-NC 4.0: confirm noncommercial eligibility and provide
Coin Metrics attribution before enabling publication. ReferenceRateUSD is a
reference price, not an exchange execution price. Coverage/licensing:
https://docs.coinmetrics.io/service-and-support/coverage
"""

from __future__ import annotations

import json
import math
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from pipeline.providers.base import BaseProvider, ProviderError, ProviderHealth, guarded_client

CM_URL = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"
MAX_BYTES = 64 * 1024
PARAMS = {
    "assets": "btc,eth", "metrics": "ReferenceRateUSD", "frequency": "1m",
    "page_size": 2, "limit_per_asset": 1,
}


class CoinMetricsProvider(BaseProvider):
    name = "coinmetrics"
    domain = "crypto"
    hosts = ("community-api.coinmetrics.io",)
    requires_api_key = False

    def __init__(self, settings=None) -> None:
        super().__init__(settings)
        self._client = guarded_client(set(self.hosts), timeout=10.0)

    def health(self) -> ProviderHealth:
        started = time.monotonic()
        try:
            self.get_crypto_market()
            error = None
        except Exception as exc:
            error = str(exc)[:200]
        return ProviderHealth(
            provider=self.name, ok=error is None, error=error, checked_at=None,
            latency_ms=round((time.monotonic() - started) * 1000, 1),
        )

    def get_crypto_market(self) -> dict[str, Any]:
        """Fetch BTC and ETH atomically; missing metrics remain explicitly unknown."""
        started = time.monotonic()
        try:
            with self._client.stream("GET", CM_URL, params=PARAMS, follow_redirects=False) as response:
                if response.status_code != 200:
                    raise ProviderError.from_exception(
                        httpx.HTTPStatusError("Coin Metrics HTTP failure", request=response.request, response=response),
                        detail=f"Coin Metrics HTTP {response.status_code}",
                    )
                body = bytearray()
                # This elapsed-time check is best effort, not a hard deadline:
                # network reads and decompression may block before yielding a chunk.
                for chunk in response.iter_bytes():
                    if len(body) + len(chunk) > MAX_BYTES:
                        raise ProviderError("Coin Metrics response exceeds size limit")
                    if time.monotonic() - started > 20:
                        raise ProviderError("Coin Metrics response exceeds best-effort elapsed-time budget")
                    body.extend(chunk)
            payload = json.loads(body)
        except (httpx.HTTPError, ValueError, UnicodeError) as exc:
            raise ProviderError.from_exception(exc, detail="Coin Metrics request or JSON failed") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ProviderError("Coin Metrics malformed data envelope")
        rows = payload["data"]
        if not rows:
            raise ProviderError("Coin Metrics no data")
        assets = {}
        now = datetime.now(UTC)
        for row in rows:
            if not isinstance(row, dict) or row.get("asset") not in ("btc", "eth"):
                raise ProviderError("Coin Metrics malformed or unexpected asset")
            asset = row["asset"]
            if asset in assets:
                raise ProviderError("Coin Metrics duplicate asset")
            raw_price, raw_time = row.get("ReferenceRateUSD"), row.get("time")
            try:
                if isinstance(raw_price, bool) or not isinstance(raw_price, (str, int, float)):
                    raise ValueError("invalid price")
                price = float(raw_price)
                if not math.isfinite(price) or price <= 0:
                    raise ValueError("invalid price")
                if not isinstance(raw_time, str):
                    raise ValueError("missing time")
                observed = datetime.fromisoformat(raw_time.replace("Z", "+00:00"))
                if observed.tzinfo is None or observed > now:
                    raise ValueError("invalid or future time")
            except (ValueError, OverflowError) as exc:
                raise ProviderError("Coin Metrics invalid price or source time") from exc
            assets[asset] = {
                "symbol": asset.upper(), "name": "Bitcoin" if asset == "btc" else "Ethereum",
                "price": price, "source": self.name,
                "updated_at": observed.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "change_1d": None, "change_1w": None, "change_1m": None,
                "market_cap": None, "volume_24h": None,
            }
        if set(assets) != {"btc", "eth"}:
            raise ProviderError("Coin Metrics partial BTC/ETH data")
        return {
            "assets": [assets["btc"], assets["eth"]], "btc_dominance": None,
            "stablecoin_mcap": None, "market_cap_total": None, "sentiment": None,
        }
