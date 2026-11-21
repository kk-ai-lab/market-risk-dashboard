from datetime import UTC, datetime

import pytest

from pipeline.collectors.market import MarketCollector
from pipeline.providers import build_default_providers
from pipeline.providers.base import ProviderError, ProviderRegistry
from pipeline.providers.coingecko import CoinGeckoProvider
from pipeline.providers.coinmetrics import CoinMetricsProvider
from pipeline.schemas import CommoditiesDataset, EquitiesDataset, SectorsDataset
from pipeline.settings import Settings
from pipeline.universe import AssetUniverse
from pipeline.validation.freshness import evaluate_freshness


def settings(tmp_path, enabled=True):
    return Settings(_env_file=None, coinmetrics_enabled=enabled, artifacts_dir=tmp_path)


def payload():
    return {"assets": [
        {"symbol": symbol, "name": symbol, "price": 100, "source": "coinmetrics", "updated_at": time}
        for symbol, time in [("BTC", "2026-01-01T00:00:00Z"), ("ETH", "2026-01-01T00:01:00Z")]
    ]}


def registry(tmp_path, monkeypatch):
    s = settings(tmp_path)
    result = ProviderRegistry(s)
    result.cache_dir = tmp_path
    result.max_retries = 0
    provider = CoinMetricsProvider(s)
    monkeypatch.setattr(provider, "get_crypto_market", lambda: (_ for _ in ()).throw(ProviderError("offline")))
    result.register("crypto", provider)
    return result


def test_opt_in_replaces_chain_and_default_is_unchanged(tmp_path):
    enabled = [p for p in build_default_providers(settings(tmp_path)) if p.domain == "crypto"]
    assert [p.name for p in enabled] == ["coinmetrics"]
    assert enabled[0].symbols == ("BTC", "ETH")
    assert [p.name for p in build_default_providers(settings(tmp_path, False)) if p.domain == "crypto"] == ["coingecko"]


def test_no_cross_source_provider_or_cache_fallback(tmp_path, monkeypatch):
    r = registry(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="forbids"):
        r.register("crypto", CoinGeckoProvider(r.settings))
    r._save_last_good("crypto", "crypto_market", "get_crypto_market", payload(), "coingecko")
    with pytest.raises(ProviderError):
        r.call("crypto", "get_crypto_market", "crypto_market")


def test_reference_cache_preserves_source_time_and_is_degraded(tmp_path, monkeypatch):
    r = registry(tmp_path, monkeypatch)
    r._save_last_good("crypto", "crypto_market", "get_crypto_market", payload(), "coinmetrics")
    out = r.call("crypto", "get_crypto_market", "crypto_market")
    assert out["meta"]["from_cache"] and out["meta"]["degraded"]
    assert out["result"]["assets"][0]["updated_at"] == "2026-01-01T00:00:00Z"
    assert out["result"]["assets"][0]["change_1d"] is None


@pytest.mark.parametrize("rows", [[], payload()["assets"][:1], [*payload()["assets"], payload()["assets"][0]]])
def test_partial_cache_is_not_replayed(tmp_path, monkeypatch, rows):
    r = registry(tmp_path, monkeypatch)
    r._save_last_good("crypto", "crypto_market", "get_crypto_market", {"assets": rows}, "coinmetrics")
    with pytest.raises(ProviderError):
        r.call("crypto", "get_crypto_market", "crypto_market")


def test_collector_uses_oldest_reference_observation_and_staleness(tmp_path, monkeypatch):
    r = registry(tmp_path, monkeypatch)
    monkeypatch.setattr(r.providers_for("crypto")[0], "get_crypto_market", payload)
    c = MarketCollector(r, AssetUniverse.load(r.settings), r.settings)
    monkeypatch.setattr(c, "_collect_history_plan", lambda: None)
    monkeypatch.setattr(c, "_collect_equities", EquitiesDataset)
    monkeypatch.setattr(c, "_collect_commodities", CommoditiesDataset)
    monkeypatch.setattr(c, "_collect_sectors", SectorsDataset)
    result = c.collect()
    observed = result["source_updated_at_by_dataset"]["crypto"]
    assert observed == "2026-01-01T00:00:00Z"
    assert evaluate_freshness(observed, 60, datetime(2026, 1, 2, tzinfo=UTC)) == "stale"
    assert [a.symbol for a in result["crypto"].assets] == ["BTC", "ETH"]


@pytest.mark.parametrize("from_cache", [False, True])
def test_market_command_persists_stale_reference_price_consistently(tmp_path, monkeypatch, from_cache):
    import json

    import respx

    from pipeline import run
    from pipeline.providers.coinmetrics import CM_URL

    frozen = "2026-01-01T12:00:00Z"
    s = Settings(_env_file=None, coinmetrics_enabled=True, data_dir=tmp_path / "data", artifacts_dir=tmp_path / "artifacts")
    monkeypatch.setattr(run, "settings", s)
    monkeypatch.setattr("pipeline.validation.freshness.now_utc", lambda: frozen)
    monkeypatch.setattr("pipeline.utils.now_utc", lambda: frozen)
    monkeypatch.setattr("pipeline.storage.outcomes.now_utc", lambda: frozen)
    monkeypatch.setattr(run, "now_utc", lambda: frozen)
    monkeypatch.setattr(MarketCollector, "_collect_history_plan", lambda self: None)
    monkeypatch.setattr(MarketCollector, "_collect_equities", lambda self: EquitiesDataset())
    monkeypatch.setattr(MarketCollector, "_collect_commodities", lambda self: CommoditiesDataset())
    monkeypatch.setattr(MarketCollector, "_collect_sectors", lambda self: SectorsDataset())
    monkeypatch.setattr(CoinGeckoProvider, "get_crypto_market", lambda self: pytest.fail("CoinGecko fallback is forbidden"))
    if from_cache:
        from pipeline.providers import build_registry

        cached_registry = build_registry(s)
        cached_registry.cache_dir = tmp_path / "cache"
        cached_registry.max_retries = 0
        cached = payload()
        cached["assets"][0]["updated_at"] = "2026-01-01T10:00:00Z"
        cached["assets"][1]["updated_at"] = "2026-01-01T11:59:00Z"
        cached_registry._save_last_good("crypto", "crypto_market", "get_crypto_market", cached, "coinmetrics")
        monkeypatch.setattr(run, "build_registry", lambda settings: cached_registry)
    with respx.mock:
        route = respx.get(CM_URL).respond(503 if from_cache else 200, json={"data": [
            {"asset": "btc", "ReferenceRateUSD": "95000", "time": "2026-01-01T10:00:00Z"},
            {"asset": "eth", "ReferenceRateUSD": "3000", "time": "2026-01-01T11:59:00Z"},
        ]})
        assert run.main(["--market-only"]) == 0
        assert route.call_count == 1
    crypto = json.loads((s.data_dir / "latest" / "crypto.json").read_text())
    freshness = json.loads((s.data_dir / "metadata" / "freshness.json").read_text())["datasets"]["crypto"]
    sources = json.loads((s.data_dir / "metadata" / "sources.json").read_text())["domains"]["crypto"]
    assert crypto["source_updated_at"] == "2026-01-01T10:00:00Z"
    expected_status = "degraded" if from_cache else "stale"
    assert crypto["freshness_status"] == freshness["status"] == expected_status
    assert crypto["provenance"]["from_cache"] is from_cache
    assert sources["degraded"] is True
    assert sources["status"] == expected_status
    assert any(p["provider"] == "coinmetrics" and p["used_fallback"] is from_cache for p in sources["providers"])
    assert all(p["provider"] != "coingecko" for p in sources["providers"])


@pytest.mark.parametrize("generated,observed,status", [
    ("2026-01-01T12:00:00Z", "2026-01-01T11:59:00Z", "fresh"),
    ("2026-01-01T10:00:00Z", "2026-01-01T11:59:00Z", "stale"),
    ("2026-01-01T12:00:00Z", None, "stale"),
    ("2026-01-01T12:00:00Z", "2026-01-01T12:01:00Z", "stale"),
])
def test_reference_freshness_requires_valid_observation_and_current_snapshot(generated, observed, status):
    from pipeline.schemas.crypto import CryptoDataset, CryptoEnvelope
    from pipeline.schemas.envelope import assemble_dataset

    result = assemble_dataset(
        CryptoEnvelope, CryptoDataset(), dataset="crypto", provider="coinmetrics", degraded=False,
        generated_at=generated, source_updated_at=observed, row_count=2,
        now=datetime(2026, 1, 1, 12, tzinfo=UTC),
    )
    assert result.envelope.freshness_status == status


@pytest.mark.parametrize("observed,marker", [
    (None, "missing"),
    ("2026-01-01T12:01:00Z", "future"),
])
@pytest.mark.parametrize("rows,degraded,cached,status,code", [
    (0, False, False, "empty", "no_rows_returned"),
    (0, True, False, "missing", "all_providers_failed"),
    (2, True, True, "degraded", "served_from_cache"),
    (2, False, False, "stale", "provider_parse_error"),
])
def test_invalid_reference_time_preserves_outcome_priority(observed, marker, rows, degraded, cached, status, code):
    from pipeline.schemas.crypto import CryptoDataset, CryptoEnvelope
    from pipeline.schemas.envelope import assemble_dataset

    result = assemble_dataset(
        CryptoEnvelope, CryptoDataset(), dataset="crypto", provider="coinmetrics", degraded=degraded,
        generated_at="2026-01-01T12:00:00Z", source_updated_at=observed, row_count=rows,
        from_cache=cached, now=datetime(2026, 1, 1, 12, tzinfo=UTC),
    )
    assert result.envelope.freshness_status == status
    assert result.reason.code == code
    if not degraded and rows:
        assert marker in result.reason.detail
        assert "source_updated_at" in result.reason.detail
        assert "not_collected" not in result.reason.detail
