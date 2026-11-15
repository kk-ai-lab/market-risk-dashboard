"""CoinGecko bulk-market request contract (#193)."""

from __future__ import annotations

from pathlib import Path
import json

import httpx
import pytest
import respx

from pipeline.providers.coingecko import CG_BASE, CoinGeckoProvider, _observation_time
from pipeline.providers.base import ProviderError, ProviderRegistry
from pipeline.settings import Settings


def test_crypto_market_uses_one_bulk_market_request(tmp_path: Path) -> None:
    provider = CoinGeckoProvider(Settings(_env_file=None, artifacts_dir=tmp_path))
    provider.api_key = "test-key"
    with respx.mock:
        markets = respx.get(f"{CG_BASE}/coins/markets").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {
                        "id": "bitcoin",
                        "symbol": "btc",
                        "name": "Bitcoin",
                        "current_price": 100.0,
                        "price_change_percentage_24h": 1.0,
                        "price_change_percentage_7d": 2.0,
                        "price_change_percentage_30d": 3.0,
                        "market_cap": 1000.0,
                        "total_volume": 100.0,
                        "last_updated": "2026-08-03T09:30:00.123Z",
                    }
                ],
            )
        )
        global_data = respx.get(f"{CG_BASE}/global").mock(
            return_value=httpx.Response(
                200,
                json={
                    "data": {
                        "market_cap_percentage": {"btc": 50.0},
                        "stablecoin_market_cap": 200.0,
                        "total_market_cap": {"usd": 2000.0},
                    }
                },
            )
        )
        result = provider.get_crypto_market()

    assert markets.call_count == 1
    assert global_data.call_count == 1
    assert result["assets"][0]["symbol"] == "BTC"
    assert result["assets"][0]["change_1m"] == 3.0
    assert result["assets"][0]["updated_at"] == "2026-08-03T09:30:00Z"


def test_crypto_market_keeps_missing_observation_time_unknown(tmp_path: Path) -> None:
    provider = CoinGeckoProvider(Settings(_env_file=None, artifacts_dir=tmp_path))
    provider.api_key = "test-key"
    with respx.mock:
        respx.get(f"{CG_BASE}/coins/markets").mock(return_value=httpx.Response(
            200, json=[{"id": "bitcoin", "name": "Bitcoin", "current_price": 100.0}]
        ))
        respx.get(f"{CG_BASE}/global").mock(return_value=httpx.Response(200, json={"data": {}}))
        result = provider.get_crypto_market()
    assert result["assets"][0]["updated_at"] is None


@pytest.mark.parametrize("value", [None, "not-a-date", "2026-08-03T09:30:00", "2999-01-01T00:00:00Z"])
def test_invalid_or_future_observation_time_is_unknown(value) -> None:
    assert _observation_time(value) is None


def test_legacy_cached_fetch_time_is_not_replayed_as_observation_time(tmp_path: Path, monkeypatch) -> None:
    settings = Settings(_env_file=None, artifacts_dir=tmp_path)
    registry = ProviderRegistry(settings)
    registry.cache_dir = tmp_path
    provider = CoinGeckoProvider(settings)
    monkeypatch.setattr(provider, "get_crypto_market", lambda: (_ for _ in ()).throw(ProviderError("unavailable")))
    registry.register("crypto", provider)
    payload = {"assets": [{"symbol": "BTC", "name": "Bitcoin", "price": 100, "source": "coingecko", "updated_at": "2026-08-03T09:30:00Z"}]}
    registry._save_last_good("crypto", "crypto_market", "get_crypto_market", payload, "coingecko")
    path = registry._cache_path("crypto", "crypto_market")
    cached = json.loads(path.read_text(encoding="utf-8"))
    cached.pop("observation_time_basis")
    path.write_text(json.dumps(cached), encoding="utf-8")
    result = registry.call("crypto", "get_crypto_market", "crypto_market")
    assert result["meta"]["from_cache"] is True
    assert result["result"]["assets"][0]["price"] == 100
    assert result["result"]["assets"][0]["updated_at"] is None
    registry._save_last_good("crypto", "crypto_market", "get_crypto_market", payload, "coingecko")
    result = registry.call("crypto", "get_crypto_market", "crypto_market")
    assert result["result"]["assets"][0]["updated_at"] == "2026-08-03T09:30:00Z"
