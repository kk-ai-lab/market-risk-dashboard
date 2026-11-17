import json
from datetime import UTC, date, datetime

import pytest

from pipeline.collectors.calendar import CalendarCollector
from pipeline.collectors.macro import DEFAULT_SERIES, SERIES_GROUPS, MacroCollector
from pipeline.collectors.market import MarketCollector
from pipeline.collectors.news import NewsCollector
from pipeline.providers import build_default_providers, build_registry
from pipeline.providers.base import ProviderError
from pipeline.providers.coinmetrics import CoinMetricsProvider
from pipeline.schemas import (
    CommoditiesDataset,
    CryptoDataset,
    EquitiesDataset,
    NewsDataset,
    SectorsDataset,
)
from pipeline.settings import Settings
from pipeline.storage.bls_snapshot import BlsSnapshot, SeriesSnapshot
from pipeline.storage.bls_snapshot import Observation as BlsObservation
from pipeline.universe import AssetUniverse
from pipeline.utils import now_utc


def public_settings(tmp_path, **values):
    return Settings(
        _env_file=None,
        approved_sources_only=True,
        project_root=tmp_path,
        config_dir=Settings(_env_file=None).config_dir,
        data_dir=tmp_path / "data",
        artifacts_dir=tmp_path / "artifacts",
        **values,
    )


class NoNetworkRegistry:
    def __init__(self):
        self.calls = []
        self.degraded_domains = set()

    def call(self, domain, method, key, args=(), kwargs=None):
        self.calls.append((domain, method, key))
        raise AssertionError(f"unexpected provider call: {domain}.{method}")

    def resolved_provider(self, domain):
        return None


def snapshots():
    today = date.today()
    fetched_at = datetime.now(UTC)
    bls_rows = [
        BlsObservation(date=date(today.year - 1, 1, 1), value=100.0),
        BlsObservation(date=date(today.year, 1, 1), value=102.0),
    ]
    bls = BlsSnapshot(
        version=1,
        attempted_on=today,
        attempt_status="complete",
        series={
            key: SeriesSnapshot(rows=bls_rows, fetched_at=fetched_at, status="fresh")
            for key in ("CPIAUCSL", "CPILFESL", "PAYEMS", "UNRATE")
        },
    )
    from tests.pipeline.test_treasury_macro import snapshot as treasury_snapshot

    return bls, treasury_snapshot()


def test_policy_loads_licensed_sources_and_builds_only_approved_providers(tmp_path):
    settings = public_settings(tmp_path)
    policy = settings.load_public_source_policy()

    assert policy.allows_provider("crypto", "coinmetrics")
    assert not policy.allows_provider("crypto", "coingecko")
    assert policy.source_for_macro_series("DGS10")[0] == "treasury"
    assert policy.source_for_macro_series("CPIAUCSL")[0] == "bls"
    assert policy.source_for_macro_series("VIXCLS") is None
    assert policy.sources["coinmetrics"].permitted_use == "noncommercial"

    providers = build_default_providers(settings)
    assert [(provider.name, provider.domain) for provider in providers] == [
        ("coinmetrics", "crypto"), ("bea_calendar", "bea_calendar"),
    ]
    assert isinstance(providers[0], CoinMetricsProvider)
    assert providers[0].symbols == (
        "BTC", "ETH", "SOL", "ADA", "XRP", "DOGE", "BNB", "LINK", "LTC", "AVAX",
    )
    assert policy.sources["coinmetrics"].market_symbols == [
        "BTC", "ETH", "SOL", "ADA", "XRP", "DOGE", "BNB", "LINK", "LTC", "AVAX",
    ]


def test_policy_plan_and_provider_factory_keep_the_selected_market_targets(tmp_path):
    settings = public_settings(tmp_path)
    policy = settings.load_public_source_policy()
    policy.sources["coinmetrics"].market_symbols = ["ETH"]
    plan = policy.collection_plan()

    providers = build_default_providers(settings, collection_plan=plan)

    assert plan.grant_for_dataset("crypto").market_symbols == frozenset({"ETH"})
    assert providers[0].symbols == ("ETH",)


@pytest.mark.parametrize(
    "source,field,value",
    [
        ("bls", "macro_series", ["DGS10"]),
        ("coinmetrics", "market_symbols", ["BTC", "NOPE"]),
    ],
)
def test_policy_rejects_targets_the_provider_cannot_fetch(tmp_path, source, field, value):
    raw = public_settings(tmp_path).load_public_source_policy().model_dump()
    raw["sources"][source][field] = value

    with pytest.raises(ValueError, match="unsupported"):
        type(public_settings(tmp_path).load_public_source_policy()).model_validate(raw)


def test_expanded_grant_does_not_replay_the_old_two_asset_cache(tmp_path, monkeypatch):
    settings = public_settings(tmp_path)
    registry = build_registry(settings)
    registry.cache_dir = tmp_path / "cache"
    registry.cache_dir.mkdir(parents=True)
    registry.max_retries = 0
    provider = registry.providers_for("crypto")[0]
    monkeypatch.setattr(
        provider,
        "get_crypto_market",
        lambda: (_ for _ in ()).throw(ProviderError("offline")),
    )
    legacy_rows = [
        {
            "symbol": symbol,
            "name": name,
            "price": price,
            "change_1d": None,
            "change_1w": None,
            "change_1m": None,
            "market_cap": None,
            "volume_24h": None,
            "source": "coinmetrics",
            "updated_at": "2026-11-15T12:00:00Z",
        }
        for symbol, name, price in (("BTC", "Bitcoin", 100.0), ("ETH", "Ethereum", 10.0))
    ]
    from pipeline.schemas.envelope import SCHEMA_VERSION

    cache = {
        "method": "get_crypto_market",
        "data": {"assets": legacy_rows},
        "fetched_at": now_utc(),
        "provider": "coinmetrics",
        "schema_version": SCHEMA_VERSION,
        "observation_time_basis": "provider",
    }
    (registry.cache_dir / "crypto__coinmetrics__crypto_market.json").write_text(
        json.dumps(cache),
        encoding="utf-8",
    )

    with pytest.raises(ProviderError, match="offline"):
        registry.call("crypto", "get_crypto_market", "crypto_market")


def test_policy_rejects_unapproved_provider_and_legacy_cache_fallback(tmp_path, monkeypatch):
    from pipeline.providers.coingecko import CoinGeckoProvider

    settings = public_settings(tmp_path)
    registry = build_registry(settings)
    registry.cache_dir = tmp_path / "cache"
    registry.max_retries = 0
    registry.cache_dir.mkdir(parents=True)
    provider = registry.providers_for("crypto")[0]
    monkeypatch.setattr(provider, "get_crypto_market", lambda: (_ for _ in ()).throw(ProviderError("offline")))

    with pytest.raises(ValueError, match="public source policy"):
        registry.register("crypto", CoinGeckoProvider(settings))

    legacy_crypto_cache = registry.cache_dir / "crypto__crypto_market.json"
    legacy_crypto_cache.write_text('{"provider":"coingecko","data":{"assets":[]}}')
    legacy_quote_cache = registry.cache_dir / "quotes__quote_SPY.json"
    legacy_quote_cache.write_text('{"provider":"yfinance","data":{}}')

    with pytest.raises(ProviderError):
        registry.call("crypto", "get_crypto_market", "crypto_market")
    with pytest.raises(ProviderError):
        registry.call("quotes", "get_quote", "quote_SPY", args=("SPY",))

    assert legacy_crypto_cache.exists()
    assert legacy_quote_cache.exists()
    assert not (registry.cache_dir / "crypto__coinmetrics__crypto_market.json").exists()


def test_macro_keeps_full_roster_and_never_falls_back_to_fred(tmp_path):
    registry = NoNetworkRegistry()
    bls, treasury = snapshots()
    settings = public_settings(tmp_path)
    macro, meta = MacroCollector(
        registry,
        settings,
        bls_snapshot=bls,
        treasury_snapshot=treasury,
    ).collect()
    indicators = [indicator for group in SERIES_GROUPS for indicator in getattr(macro, group)]
    by_key = {indicator.key.upper(): indicator for indicator in indicators}

    assert [indicator.key.upper() for indicator in indicators] == DEFAULT_SERIES
    assert by_key["CPIAUCSL"].source == "BLS"
    assert by_key["DGS10"].source == "Treasury"
    assert by_key["VIXCLS"].source == "unavailable"
    assert by_key["VIXCLS"].value is None
    assert by_key["VIXCLS"].status == "missing"
    assert macro.fedwatch is None
    assert registry.calls == []
    assert "VIXCLS" in meta["detail"]
    assert meta["degraded"]


def test_market_policy_collects_crypto_without_other_market_requests(tmp_path, monkeypatch):
    from pipeline.providers.base import ProviderRegistry

    settings = public_settings(tmp_path)
    registry = ProviderRegistry(settings)
    registry.cache_dir = tmp_path / "cache"
    registry.max_retries = 0
    from pipeline.providers import build_default_providers

    registry.register_all(build_default_providers(settings))
    provider = registry.providers_for("crypto")[0]
    monkeypatch.setattr(provider, "get_crypto_market", lambda: {
        "assets": [
            {
                "symbol": symbol,
                "name": name,
                "price": 100.0,
                "source": "coinmetrics",
                "updated_at": "2026-11-15T12:00:00Z",
            }
            for symbol, name in [
                ("BTC", "Bitcoin"), ("ETH", "Ethereum"), ("SOL", "Solana"),
                ("ADA", "Cardano"), ("XRP", "XRP"), ("DOGE", "Dogecoin"),
                ("BNB", "BNB"), ("LINK", "Chainlink"), ("LTC", "Litecoin"),
                ("AVAX", "Avalanche"),
            ]
        ],
        "btc_dominance": None,
        "stablecoin_mcap": None,
        "market_cap_total": None,
        "sentiment": None,
    })
    monkeypatch.setattr(MarketCollector, "_collect_history_plan", lambda self: pytest.fail("unapproved history was requested"))

    market = MarketCollector(registry, AssetUniverse.load(settings), settings).collect()

    assert [asset.symbol for asset in market["crypto"].assets] == [
        "BTC", "ETH", "SOL", "ADA", "XRP", "DOGE", "BNB", "LINK", "LTC", "AVAX",
    ]
    assert market["equities"].assets == []
    assert market["commodities"].assets == []
    assert market["sectors"].sectors == []
    assert market["sectors"].themes == []
    assert market["histories"] == {}
    assert market["source_updated_at_by_dataset"]["crypto"] == "2026-11-15T12:00:00Z"
    assert "approved public source" in market["degraded_detail_by_dataset"]["equities"]
    assert market["source_policy_excluded_datasets"] == ["commodities", "equities", "sectors"]


def test_market_collector_rejects_unapproved_coinmetrics_asset(tmp_path, monkeypatch):
    from pipeline.providers.base import ProviderRegistry

    settings = public_settings(tmp_path)
    policy = settings.load_public_source_policy()
    policy.sources["coinmetrics"].market_symbols = ["ETH"]
    plan = policy.collection_plan()
    registry = ProviderRegistry(settings, collection_plan=plan)
    registry.register_all(build_default_providers(settings, collection_plan=plan))
    provider = registry.providers_for("crypto")[0]
    monkeypatch.setattr(provider, "get_crypto_market", lambda: {
        "assets": [
            {"symbol": "BTC", "name": "Bitcoin", "price": 100.0, "source": "coinmetrics", "updated_at": "2026-11-15T12:00:00Z"},
            {"symbol": "ETH", "name": "Ethereum", "price": 10.0, "source": "coinmetrics", "updated_at": "2026-11-15T12:01:00Z"},
        ],
        "btc_dominance": None, "stablecoin_mcap": None, "market_cap_total": None, "sentiment": None,
    })
    monkeypatch.setattr(MarketCollector, "_collect_history_plan", lambda self: pytest.fail("unapproved history was requested"))

    result = MarketCollector(registry, AssetUniverse.load(settings), settings, collection_plan=plan).collect()

    assert result["crypto"].assets == []
    assert result["degraded_by_dataset"]["crypto"]


def test_news_and_calendar_mark_unlicensed_data_missing_without_registry_calls(tmp_path):
    registry = NoNetworkRegistry()
    settings = public_settings(tmp_path)

    news, news_meta = NewsCollector(registry, settings).collect()
    policy = settings.load_public_source_policy()
    policy.sources.pop("bea_calendar")
    calendar, calendar_meta = CalendarCollector(
        registry, settings, collection_plan=policy.collection_plan(),
    ).collect()

    assert news.items == [] and news.total == 0
    assert calendar.events == []
    assert news_meta["provider_outcome"]["provider"] == "unavailable"
    assert calendar_meta["provider_outcome"]["provider"] == "unavailable"
    assert news_meta["error_code"] == "source_policy_excluded"
    assert calendar_meta["error_code"] == "source_policy_excluded"
    assert registry.calls == []


def test_macro_policy_exclusions_have_an_explicit_reason_code(tmp_path):
    registry = NoNetworkRegistry()
    bls, treasury = snapshots()

    _, meta = MacroCollector(
        registry,
        public_settings(tmp_path),
        bls_snapshot=bls,
        treasury_snapshot=treasury,
    ).collect()

    assert meta["error_code"] == "source_policy_excluded"
    assert "VIXCLS" in meta["detail"]
    assert "No approved public source configured" in meta["detail"]


def _run_with_results(monkeypatch, tmp_path, command, results):
    from pipeline import run

    settings = public_settings(tmp_path)
    monkeypatch.setattr(run, "settings", settings)
    monkeypatch.setattr(run, "_run_collection", lambda _: results)
    assert run.main([f"--{command}"]) == 0
    return settings


def test_market_only_persists_policy_reason_and_marks_partial_run(monkeypatch, tmp_path):
    names = ("equities", "crypto", "sectors", "commodities")
    details = {name: f"No approved public source configured for {name}" for name in names}
    meta = {
        "degraded": True,
        "degraded_by_dataset": {name: True for name in names},
        "degraded_detail_by_dataset": details,
        "data_quality": 0.0,
        "data_quality_by_dataset": {name: 0.0 for name in names},
        "source_updated_at_by_dataset": {},
        "source_policy_excluded_datasets": list(names),
        "providers": {name: {"provider": "unavailable"} for name in names},
    }
    results = {
        "equities": EquitiesDataset(),
        "crypto": CryptoDataset(),
        "sectors": SectorsDataset(),
        "commodities": CommoditiesDataset(),
        "market_meta": meta,
        "provider_status": {},
        "durations": {},
        "degraded": list(details.values()),
    }

    settings = _run_with_results(monkeypatch, tmp_path, "market-only", results)

    freshness = json.loads((settings.data_dir / "metadata" / "freshness.json").read_text())
    for name in names:
        assert freshness["datasets"][name]["reason"] == {"code": "source_policy_excluded", "detail": details[name]}
    report = json.loads(next((settings.artifacts_dir / "logs").glob("run-report-*.json")).read_text())
    assert report["clean"] is False
    assert report["failed_datasets"] == []


def test_news_only_persists_policy_reason(monkeypatch, tmp_path):
    detail = "No approved public source configured for news"
    results = {
        "news": NewsDataset(items=[], total=0, updated_at=now_utc()),
        "news_meta": {
            "provider_outcome": {"provider": "unavailable", "used_fallback": False, "from_cache": False},
            "data_quality": 0.0,
            "source_updated_at": None,
            "detail": detail,
            "error_code": "source_policy_excluded",
        },
        "news_degraded": True,
        "provider_status": {},
        "durations": {},
        "degraded": [detail],
    }

    settings = _run_with_results(monkeypatch, tmp_path, "news-only", results)

    freshness = json.loads((settings.data_dir / "metadata" / "freshness.json").read_text())
    assert freshness["datasets"]["news"]["reason"] == {"code": "source_policy_excluded", "detail": detail}


def test_macro_only_persists_policy_reason_and_full_history_roster(monkeypatch, tmp_path):
    bls, treasury = snapshots()
    macro, meta = MacroCollector(
        NoNetworkRegistry(), public_settings(tmp_path), bls_snapshot=bls, treasury_snapshot=treasury,
    ).collect()
    results = {
        "macro": macro,
        "macro_meta": meta,
        "series_history": meta["series_history"],
        "provider_status": meta["provider_status"],
        "durations": {},
        "degraded": meta["degraded"],
    }

    settings = _run_with_results(monkeypatch, tmp_path, "macro-only", results)

    freshness = json.loads((settings.data_dir / "metadata" / "freshness.json").read_text())
    assert freshness["datasets"]["macro"]["reason"]["code"] == "source_policy_excluded"
    assert "VIXCLS" in freshness["datasets"]["macro"]["reason"]["detail"]
    manifest = json.loads((settings.data_dir / "history" / "macro" / "index.json").read_text())
    assert set(manifest["series"]) == set(DEFAULT_SERIES)


def test_cloud_mode_missing_bls_checkpoint_is_explicit_and_never_uses_fred(tmp_path, monkeypatch):
    from pipeline import run
    from tests.pipeline.test_treasury_macro import snapshot as treasury_snapshot

    settings = public_settings(tmp_path, bls_cloud_enabled=True)
    monkeypatch.setattr(run, "settings", settings)
    monkeypatch.setattr(run, "build_registry", lambda *_args, **_kwargs: NoNetworkRegistry())
    monkeypatch.setattr(
        "pipeline.storage.treasury_snapshot.refresh_treasury_snapshot",
        lambda *args, **kwargs: treasury_snapshot(),
    )

    results = run._run_collection("macro-only")

    assert results["macro_meta"]["provider_status"]["bls"]["error"] == (
        "Cloud state repository or token is not configured"
    )
    assert "Source checkpoints degraded: bls" in results["macro_meta"]["detail"]
    assert results["macro_meta"]["provider"]["provider"] == "treasury"


def test_approved_pipeline_refuses_legacy_public_data_output(tmp_path, monkeypatch, capsys):
    from pipeline import run

    project_root = Settings(_env_file=None).project_root
    settings = Settings(
        _env_file=None,
        approved_sources_only=True,
        project_root=project_root,
        config_dir=project_root / "config",
        data_dir=project_root / "public" / "data",
        artifacts_dir=tmp_path / "artifacts",
    )
    monkeypatch.setattr(run, "settings", settings)

    assert run.main(["--full"]) == 1
    assert "requires an isolated data_dir" in capsys.readouterr().err
