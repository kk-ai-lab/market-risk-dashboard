"""Providers package: factory functions for centralized registration (architecture §1.4 ProviderRegistry)."""

from __future__ import annotations

from pipeline.config.models import ApprovedCollectionPlan, ConfigError
from pipeline.providers.akshare_provider import AkshareProvider
from pipeline.providers.base import (
    BaseProvider,
    HistoryResult,
    ProviderError,
    ProviderHealth,
    ProviderRegistry,
    QuoteResult,
)
from pipeline.providers.bea_calendar import BeaCalendarProvider
from pipeline.providers.coingecko import CoinGeckoProvider
from pipeline.providers.coinmetrics import CoinMetricsProvider
from pipeline.providers.fedwatch import FedWatchProvider
from pipeline.providers.fmp import FmpProvider, FmpQuotesProvider
from pipeline.providers.fred import FredProvider
from pipeline.providers.eia import EiaProvider
from pipeline.providers.fred_calendar import FredCalendarProvider
from pipeline.providers.nasdaq import NasdaqCalendarProvider
from pipeline.providers.rss_news import RssNewsProvider
from pipeline.providers.yahoo import YahooAShareProvider, YahooProvider
from pipeline.settings import Settings

#: config name → provider class. The name in config/sources.yaml:providers is the class
#: identity; the class's own ``domain`` attribute must match the config group it is listed
#: under, or the factory fails loudly (a provider in the wrong domain would silently sit in
#: a degradation chain it does not belong to).
_PROVIDER_CLASSES: dict[str, type[BaseProvider]] = {
    "yfinance": YahooProvider,
    "yfinance_a_share": YahooAShareProvider,
    "fred": FredProvider,
    "eia": EiaProvider,
    "coingecko": CoinGeckoProvider,
    "coinmetrics": CoinMetricsProvider,
    "fmp": FmpProvider,
    "fmp_quotes": FmpQuotesProvider,
    "nasdaq": NasdaqCalendarProvider,
    "fred_calendar": FredCalendarProvider,
    "bea_calendar": BeaCalendarProvider,
    "fedwatch": FedWatchProvider,
    "akshare": AkshareProvider,
    "rss_news": RssNewsProvider,
}


def build_default_providers(settings=None, *, collection_plan: ApprovedCollectionPlan | None = None) -> list[BaseProvider]:
    """Build Providers from config/sources.yaml:providers — order, priority and enabled (C-3).

    The degradation chain used to be a hardcoded list here; it is now declared in config.
    ``enabled: false`` entries are inert (never constructed — `binance_public` is a
    deliberate not-yet-implemented placeholder). An *enabled* entry naming an unknown
    provider, or a provider whose domain disagrees with its config group, raises
    :class:`~pipeline.config.models.ConfigError` before any provider is constructed.
    """
    s = settings or Settings()
    if s.approved_sources_only:
        plan = collection_plan or s.load_public_source_policy().collection_plan()
        providers: list[BaseProvider] = []
        for grant in plan.grants:
            domain = grant.registry_domain
            if domain is None:
                continue
            cls = _PROVIDER_CLASSES.get(grant.provider)
            if cls is None:
                raise ConfigError(
                    f"public_sources.yaml: unknown provider {grant.provider!r}"
                )
            if grant.provider == "coinmetrics" and not grant.market_symbols:
                continue
            provider = (
                CoinMetricsProvider(s, symbols=grant.market_symbols)
                if grant.provider == "coinmetrics" else cls(s)
            )
            if provider.domain != domain:
                raise ConfigError(
                    f"public_sources.yaml: {grant.provider!r} is a {provider.domain!r} "
                    f"provider, not {domain!r}"
                )
            provider.priority = len(providers)
            providers.append(provider)
        return providers
    cfg = s.load_sources_config()
    providers: list[BaseProvider] = []
    for domain, entries in cfg.providers.items():
        if domain == "crypto" and s.coinmetrics_enabled:
            providers.append(CoinMetricsProvider(s))
            continue
        for entry in sorted(entries, key=lambda e: e.priority):
            if not entry.enabled:
                continue
            cls = _PROVIDER_CLASSES.get(entry.name)
            if cls is None:
                raise ConfigError(
                    f"sources.yaml:providers.{domain}: unknown provider {entry.name!r} "
                    f"(implemented: {', '.join(sorted(_PROVIDER_CLASSES))})"
                )
            provider = cls(s)
            if provider.domain != domain:
                raise ConfigError(
                    f"sources.yaml:providers.{domain}: {entry.name!r} is a "
                    f"{provider.domain!r} provider, not {domain!r}"
                )
            provider.priority = entry.priority
            providers.append(provider)
    return providers


def build_registry(settings=None, *, collection_plan: ApprovedCollectionPlan | None = None) -> ProviderRegistry:
    """Build a Registry with all Providers registered."""
    s = settings or Settings()
    plan = collection_plan or (
        s.load_public_source_policy().collection_plan()
        if s.approved_sources_only else None
    )
    registry = ProviderRegistry(s, collection_plan=plan)
    registry.register_all(build_default_providers(s, collection_plan=plan))
    return registry


__all__ = [
    "AkshareProvider",
    "BaseProvider",
    "CoinGeckoProvider",
    "FmpProvider",
    "FmpQuotesProvider",
    "FredCalendarProvider",
    "FedWatchProvider",
    "FredProvider",
    "EiaProvider",
    "HistoryResult",
    "NasdaqCalendarProvider",
    "ProviderError",
    "ProviderHealth",
    "ProviderRegistry",
    "QuoteResult",
    "RssNewsProvider",
    "YahooAShareProvider",
    "YahooProvider",
    "build_default_providers",
    "build_registry",
]
