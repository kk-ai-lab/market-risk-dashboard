from __future__ import annotations

from types import SimpleNamespace

from pipeline.collectors.market import MarketCollector
from pipeline.providers.base import ProviderError
from pipeline.run import _build_dashboard
from pipeline.schemas.risk import RiskModelResult
from pipeline.settings import Settings
from tests.pipeline.factories import make_risk_payload


class QuoteRegistry:
    def __init__(self, *, fail_primary_index: bool = False, fail_all: bool = False) -> None:
        self.fail_primary_index = fail_primary_index
        self.fail_all = fail_all
        self.calls: list[tuple[str, str]] = []

    def call(self, domain, method, key, args=(), kwargs=None, *, provider_names=None):
        symbol = args[0]
        provider = next(iter(provider_names or {"yfinance"}))
        self.calls.append((provider, symbol))
        if self.fail_all:
            raise ProviderError("quote unavailable")
        if self.fail_primary_index and provider == "yfinance" and symbol == "^GSPC":
            raise ProviderError("primary quote unavailable")
        source = "yahoo" if provider == "yfinance" else "fmp"
        return {
            "result": SimpleNamespace(
                symbol=symbol, change_1d=1.25, source=source, is_proxy=False,
            ),
            "meta": {"provider": provider, "used_fallback": False, "from_cache": False},
        }

    def call_provider(self, domain, provider_name, method, key, args=(), kwargs=None, *, authorization_dataset=None):
        return self.call(domain, method, key, args, kwargs, provider_names={provider_name})


def collector(registry: QuoteRegistry) -> MarketCollector:
    settings = Settings(_env_file=None)
    return MarketCollector(registry, SimpleNamespace(), settings)


def test_configured_instrument_targets_have_dataset_grants() -> None:
    settings = Settings(_env_file=None)
    instruments = settings.load_instruments_config()
    plan = settings.load_public_source_policy().collection_plan()

    indices = [item for item in instruments.instruments.values() if item.kind == "index"]
    sectors = [item for item in instruments.instruments.values() if item.kind == "sector_etf"]
    assert len(indices) == 5
    assert len(sectors) == 11
    for item in instruments.instruments.values():
        dataset = "overview"
        for rung in item.chain:
            grant = plan.grant_for_rung(rung.source, rung.symbol)
            assert grant is not None
            assert dataset in grant.datasets


def test_failed_primary_uses_configured_proxy_and_marks_it() -> None:
    registry = QuoteRegistry(fail_primary_index=True)
    indices, _ = collector(registry)._collect_overview_instruments()
    sp500 = next(row for row in indices if row.key == "sp500")

    assert registry.calls[:2] == [("yfinance", "^GSPC"), ("fmp_quotes", "SPY")]
    assert sp500.symbol == "SPY"
    assert sp500.change_1d == 1.25
    assert sp500.source == "Financial Modeling Prep"
    assert sp500.provider == "fmp_quotes"
    assert sp500.is_proxy is True
    assert sp500.proxy_of == "^GSPC"


def test_missing_quotes_stay_null_for_every_configured_row() -> None:
    indices, sector_etfs = collector(QuoteRegistry(fail_all=True))._collect_overview_instruments()

    assert len(indices) == 5
    assert len(sector_etfs) == 11
    assert all(row.change_1d is None and row.source is None and not row.is_proxy for row in [*indices, *sector_etfs])


def test_dashboard_serializes_every_configured_index_and_sector_etf() -> None:
    settings = Settings(_env_file=None)
    configured = settings.load_instruments_config()
    indices, sector_etfs = collector(QuoteRegistry())._collect_overview_instruments()
    risk = RiskModelResult.model_validate(make_risk_payload())
    payload = _build_dashboard(
        risk_env=SimpleNamespace(payload=risk),
        equities=SimpleNamespace(payload=SimpleNamespace(assets=[])),
        crypto=SimpleNamespace(payload=SimpleNamespace(assets=[])),
        sectors=None,
        calendar=SimpleNamespace(payload=SimpleNamespace(events=[])),
        index_performance=indices,
        sector_etfs=sector_etfs,
    )
    serialized = payload.model_dump(mode="json")

    assert len(serialized["index_performance"]) == sum(
        item.kind == "index" for item in configured.instruments.values()
    ) == 5
    assert len(serialized["sector_etfs"]) == sum(
        item.kind == "sector_etf" for item in configured.instruments.values()
    ) == 11
    assert all(row["change_1d"] == 1.25 and row["source"] for row in serialized["index_performance"])
    assert all(row["change_1d"] == 1.25 and row["provider"] for row in serialized["sector_etfs"])
