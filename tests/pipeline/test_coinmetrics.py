import httpx
import pytest
import respx

from pipeline.providers.base import ProviderError
from pipeline.providers.coinmetrics import CM_URL, MAX_BYTES, CoinMetricsProvider
from pipeline.settings import Settings

EXPANDED_ASSETS = (
    ("BTC", "btc", "Bitcoin"),
    ("ETH", "eth", "Ethereum"),
    ("SOL", "sol", "Solana"),
    ("ADA", "ada", "Cardano"),
    ("XRP", "xrp", "XRP"),
    ("DOGE", "doge", "Dogecoin"),
    ("BNB", "bnb", "BNB"),
    ("LINK", "link", "Chainlink"),
    ("LTC", "ltc", "Litecoin"),
    ("AVAX", "avax", "Avalanche"),
)


@pytest.fixture
def provider(tmp_path):
    result = CoinMetricsProvider(Settings(_env_file=None, artifacts_dir=tmp_path))
    yield result
    result._client.close()


def rows():
    return [
        {"asset": "btc", "ReferenceRateUSD": "95000.25", "time": "2020-01-01T00:00:00Z"},
        {"asset": "eth", "ReferenceRateUSD": "3000", "time": "2020-01-01T00:01:00Z"},
    ]


def expanded_rows():
    return [
        {
            "asset": asset_id,
            "ReferenceRateUSD": str(index + 1),
            "time": f"2020-01-01T00:{index:02}:00Z",
        }
        for index, (_, asset_id, _) in enumerate(EXPANDED_ASSETS)
    ]


def test_bulk_reference_rates(provider):
    with respx.mock:
        route = respx.get(CM_URL).respond(200, json={"data": rows()})
        result = provider.get_crypto_market()
        assert route.call_count == 1
        request = route.calls[0].request
        assert dict(request.url.params) == {
            "assets": "btc,eth", "metrics": "ReferenceRateUSD", "frequency": "1h",
            "page_size": "52", "limit_per_asset": "26",
        }
        assert "authorization" not in request.headers
    assert [a["price"] for a in result["assets"]] == [95000.25, 3000.0]
    assert result["assets"][1]["updated_at"] == "2020-01-01T00:01:00Z"
    assert result["assets"][0]["change_1d"] is None
    assert result["market_cap_total"] is None
    assert provider.requires_api_key is False


def test_expanded_reference_rates_keep_catalog_order_and_source_fields(tmp_path):
    requested = [symbol for symbol, _, _ in reversed(EXPANDED_ASSETS)]
    instance = CoinMetricsProvider(Settings(_env_file=None, artifacts_dir=tmp_path), symbols=requested)
    try:
        with respx.mock:
            route = respx.get(CM_URL).respond(200, json={"data": list(reversed(expanded_rows()))})
            result = instance.get_crypto_market()

        assert route.call_count == 1
        assert dict(route.calls[0].request.url.params) == {
            "assets": "btc,eth,sol,ada,xrp,doge,bnb,link,ltc,avax",
            "metrics": "ReferenceRateUSD",
            "frequency": "1h",
            "page_size": "260",
            "limit_per_asset": "26",
        }
        assert [(asset["symbol"], asset["name"]) for asset in result["assets"]] == [
            (symbol, name) for symbol, _, name in EXPANDED_ASSETS
        ]
        assert [asset["updated_at"] for asset in result["assets"]] == [
            f"2020-01-01T00:{index:02}:00Z" for index in range(10)
        ]
        assert all(asset["change_1d"] is None for asset in result["assets"])
        assert all(asset["volume_24h"] is None for asset in result["assets"])
        assert result["market_cap_total"] is None
    finally:
        instance._client.close()


def test_expanded_reference_rates_reject_partial_approved_selection(tmp_path):
    instance = CoinMetricsProvider(
        Settings(_env_file=None, artifacts_dir=tmp_path),
        symbols=[symbol for symbol, _, _ in EXPANDED_ASSETS],
    )
    try:
        with respx.mock:
            respx.get(CM_URL).respond(200, json={"data": expanded_rows()[:-1]})
            with pytest.raises(ProviderError, match="partial approved-symbol data"):
                instance.get_crypto_market()
    finally:
        instance._client.close()


def test_selected_symbol_request_contains_only_the_approved_asset(tmp_path):
    instance = CoinMetricsProvider(Settings(_env_file=None, artifacts_dir=tmp_path), symbols={"ETH"})
    try:
        with respx.mock:
            route = respx.get(CM_URL).respond(200, json={"data": [rows()[1]]})
            result = instance.get_crypto_market()
        assert dict(route.calls[0].request.url.params)["assets"] == "eth"
        assert [asset["symbol"] for asset in result["assets"]] == ["ETH"]
    finally:
        instance._client.close()


def test_hourly_history_calculates_change_from_exact_24_hour_reference(provider):
    history = [
        {"asset": "btc", "ReferenceRateUSD": "100", "time": "2020-01-01T00:00:00Z"},
        {"asset": "btc", "ReferenceRateUSD": "110", "time": "2020-01-02T00:00:00Z"},
        {"asset": "eth", "ReferenceRateUSD": "200", "time": "2020-01-01T01:00:00Z"},
        {"asset": "eth", "ReferenceRateUSD": "210", "time": "2020-01-02T01:00:00Z"},
    ]
    with respx.mock:
        respx.get(CM_URL).respond(200, json={"data": history})
        result = provider.get_crypto_market()

    btc, eth = result["assets"]
    assert (btc["price"], btc["change_1d"], btc["updated_at"]) == (
        110.0, 10.0, "2020-01-02T00:00:00Z"
    )
    assert (eth["price"], eth["change_1d"], eth["updated_at"]) == (
        210.0, 5.0, "2020-01-02T01:00:00Z"
    )


def test_hourly_history_leaves_change_unknown_without_exact_24_hour_reference(provider):
    history = [
        {"asset": "btc", "ReferenceRateUSD": "110", "time": "2020-01-02T00:00:00Z"},
        {"asset": "eth", "ReferenceRateUSD": "210", "time": "2020-01-02T01:00:00Z"},
        {"asset": "eth", "ReferenceRateUSD": "200", "time": "2020-01-01T00:00:00Z"},
    ]
    with respx.mock:
        respx.get(CM_URL).respond(200, json={"data": history})
        result = provider.get_crypto_market()

    assert [asset["change_1d"] for asset in result["assets"]] == [None, None]


@pytest.mark.parametrize("payload,match", [
    ({}, "envelope"), ({"data": []}, "no data"), ({"data": rows()[:1]}, "partial"),
    ({"data": [rows()[0], rows()[0]]}, "duplicate"),
    ({"data": [None]}, "malformed"),
    ({"data": [{"asset": "sol"}]}, "unexpected"),
])
def test_bad_envelopes(provider, payload, match):
    with respx.mock:
        respx.get(CM_URL).respond(200, json=payload)
        with pytest.raises(ProviderError, match=match):
            provider.get_crypto_market()


@pytest.mark.parametrize("value", [None, True, "NaN", "Infinity", "1e999", 0, -1, {}, "oops"])
def test_invalid_price(provider, value):
    data = rows()
    data[0]["ReferenceRateUSD"] = value
    with respx.mock:
        respx.get(CM_URL).respond(200, json={"data": data})
        with pytest.raises(ProviderError, match="price or source time"):
            provider.get_crypto_market()


@pytest.mark.parametrize("value", [None, "oops", "2020-01-01T00:00:00", "2999-01-01T00:00:00Z"])
def test_invalid_time(provider, value):
    data = rows()
    data[0]["time"] = value
    with respx.mock:
        respx.get(CM_URL).respond(200, json={"data": data})
        with pytest.raises(ProviderError, match="source time"):
            provider.get_crypto_market()


def test_redirect_is_not_followed(provider):
    with respx.mock:
        first = respx.get(CM_URL).respond(302, headers={"Location": CM_URL + "/other"})
        target = respx.get(CM_URL + "/other").respond(200, json={"data": rows()})
        with pytest.raises(ProviderError, match="302"):
            provider.get_crypto_market()
        assert first.call_count == 1
        assert target.call_count == 0


@pytest.mark.parametrize("body", [b"broken JSON", b" " * (MAX_BYTES + 1)])
def test_bounded_or_invalid_body(provider, body):
    with respx.mock:
        respx.get(CM_URL).respond(200, content=body)
        with pytest.raises(ProviderError):
            provider.get_crypto_market()


def test_disallowed_host(provider):
    with (
        pytest.raises(ProviderError, match="allowlist"),
        provider._client.stream("GET", "https://example.com"),
    ):
        pass


@pytest.mark.parametrize("status", [429, 500])
def test_http_failure(provider, status):
    with respx.mock:
        respx.get(CM_URL).respond(status, headers={"Retry-After": "5"})
        with pytest.raises(ProviderError) as caught:
            provider.get_crypto_market()
        assert caught.value.cls == ("rate_limited" if status == 429 else "transient")
        if status == 429:
            assert caught.value.retry_after == 5


def test_chunked_body_is_bounded(provider):
    class Chunks(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(20):
                yield b" " * 4096

    with respx.mock:
        respx.get(CM_URL).mock(return_value=httpx.Response(200, stream=Chunks()))
        with pytest.raises(ProviderError, match="size limit"):
            provider.get_crypto_market()


def test_transport_failure_and_health(provider):
    with respx.mock:
        route = respx.get(CM_URL).mock(side_effect=httpx.ReadTimeout("timeout"))
        with pytest.raises(ProviderError):
            provider.get_crypto_market()
        health = provider.health()
        assert health.ok is False
        assert health.error
        assert route.call_count == 2


def test_source_timestamp_preserves_precision_and_normalizes_zone(provider):
    data = rows()
    data[0]["time"] = "2020-01-01T01:00:00.123456+01:00"
    with respx.mock:
        respx.get(CM_URL).respond(200, json={"data": data})
        assert provider.get_crypto_market()["assets"][0]["updated_at"] == "2020-01-01T00:00:00.123456Z"


def test_small_chunks_trigger_best_effort_elapsed_time_check(provider, monkeypatch):
    elapsed = [0.0]
    consumed = []

    class SlowChunks(httpx.SyncByteStream):
        def __iter__(self):
            for index in range(10000):
                elapsed[0] += 11
                consumed.append(index)
                yield b" "

    monkeypatch.setattr("pipeline.providers.coinmetrics.time.monotonic", lambda: elapsed[0])
    with respx.mock:
        respx.get(CM_URL).mock(return_value=httpx.Response(200, stream=SlowChunks()))
        with pytest.raises(ProviderError, match="best-effort elapsed-time budget"):
            provider.get_crypto_market()
    assert consumed == [0, 1]
