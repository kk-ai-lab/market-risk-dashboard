"""Validated config models (#102, decision #90, ADR 0005: config is a fact).

One pydantic model per config file, all loaded through :func:`load_config`, which raises
:class:`ConfigError` **before any provider is constructed**. Every model uses
``extra="forbid"`` so a typo'd key fails loudly instead of silently disabling a theme or
silently keeping a dead knob.

Scope: the configs #102 actually touches — ``sources.yaml``, ``universe.yaml``,
``themes.yaml``, ``news_sources.yaml``. ``risk_model.yaml`` is deliberately not modelled
here: it is already validated by ``pipeline/risk/model.py`` (pinned by
``tests/pipeline/test_config_drift.py``), and modelling it twice would create a second
authoritative shape for the same file.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal, TypeVar
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from pipeline.coinmetrics_assets import COINMETRICS_ASSET_SYMBOLS
from pipeline.degrade import DEFAULT_CACHE_MAX_AGE_HOURS, DEFAULT_DEGRADE_FACTOR

FRED_MACRO_SERIES = frozenset({
    "VIXCLS", "BAA10Y", "DFII10", "DFF", "PCEPILFE", "T5YIFR", "T10YIE", "ICSA",
    "CCSA", "CIVPART", "WALCL", "WRESBAL", "WTREGEN", "RRPONTSYD", "SOFR",
    "DTWEXBGS", "DTWEXAFEGS", "DEXUSEU", "DEXJPUS", "DEXCHUS",
})
FRED_ICE_SERIES = frozenset({"BAMLH0A0HYM2", "BAMLC0A0CM"})
FEDERAL_RESERVE_BOARD_SERIES = frozenset({
    "WALCL", "WRESBAL", "WTREGEN", "DTWEXBGS", "DTWEXAFEGS", "DEXUSEU", "DEXJPUS", "DEXCHUS",
    "IPGMF", "IPG334", "IPG3344",
})
SUPPORTED_TARGETS: dict[str, dict[str, frozenset[str]]] = {
    "bls": {"macro_series": frozenset({"CPIAUCSL", "CPILFESL", "PAYEMS", "UNRATE", "CIVPART"})},
    "treasury": {"macro_series": frozenset({"DGS2", "DGS5", "DGS10", "DFII5", "DFII10", "T5YIFR", "T10YIE"})},
    "nyfed_rates": {"macro_series": frozenset({"DFF", "RRPONTSYD", "SOFR"})},
    "bea_nipa": {"macro_series": frozenset({"PCEPILFE"})},
    "dol_claims": {"macro_series": frozenset({"ICSA", "CCSA"})},
    "federal_reserve_data": {"macro_series": FEDERAL_RESERVE_BOARD_SERIES},
    "fred": {"macro_series": FRED_MACRO_SERIES | FRED_ICE_SERIES},
    "coinmetrics": {"market_symbols": COINMETRICS_ASSET_SYMBOLS},
    "eia": {"energy_series": frozenset({"WCESTUS1"})},
    "world_bank_commodities": {"commodity_series": frozenset({"GC=F", "SI=F", "HG=F", "CL=F"})},
}
TARGET_FIELDS: dict[str, tuple[str, ...]] = {
    "macro": ("macro_series",),
    "news": ("news_feeds",),
    # FRED equities are index closes (index_series); the equity providers use
    # instrument symbols. Either identifies the target a grant authorizes.
    "equities": ("instrument_symbols", "index_series"),
    "sectors": ("instrument_symbols",),
    "crypto": ("market_symbols",),
    "overview": ("instrument_symbols",),
    "energy": ("energy_series",),
    "commodities": ("commodity_series",),
}
PUBLIC_DATASETS = frozenset({"calendar", "commodities", "crypto", "energy", "equities", "macro", "news", "overview", "sectors"})
PermittedUse = Literal["public-domain", "public-redistribution", "noncommercial", "personal-use"]


#: Everything config-related that #102 can raise. Loaders use it so a bad config fails the
#: run at construction time rather than surfacing mid-collection as a confusing provider
#: error.
class ConfigError(Exception):
    """A config file is missing, unparseable, or failed pydantic validation."""


# -------------------------------------------------------------------------------------
# sources.yaml
# -------------------------------------------------------------------------------------


class Expectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    interval_minutes: int = Field(ge=1)
    note: str | None = None


class ProviderEntry(BaseModel):
    """One provider in the degradation chain. ``max_concurrency``/``min_interval_ms`` are
    the per-host token-bucket budget used by the registry's rate limiter (#103/P-1): a
    browser-hostile host like Yahoo gets the tightest budget."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    priority: int = Field(ge=0)
    enabled: bool = True
    max_concurrency: int = Field(default=2, ge=1)
    min_interval_ms: int = Field(default=500, ge=0)


class DegradeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_retries: int = Field(default=2, ge=0)
    backoff_base_seconds: float = Field(default=1.0, gt=0)
    jitter: bool = True
    cache_max_age_hours: float = Field(default=DEFAULT_CACHE_MAX_AGE_HOURS, gt=0)
    data_quality_degrade_factor: float = Field(default=DEFAULT_DEGRADE_FACTOR, gt=0, le=1.0)
    last_good_cache_dir: str = Field(default="artifacts/cache", min_length=1)


class OperationsConfig(BaseModel):
    """Tuning knobs that used to be magic literals in the collectors (M-5).

    Model parameters (4.0/105/1.5, fact-layer slices 15/20/5/8) deliberately stay in Python
    as named constants with a derivation comment — they are model parameters, not operations
    knobs, and moving them would put model math behind a config indirection.
    """

    model_config = ConfigDict(extra="forbid")

    circuit_breaker_threshold: int = Field(default=2, ge=1, description="per-domain failures before the collector fast-degrades")
    calendar_horizon_days: int = Field(default=14, ge=1)
    news_max_items: int = Field(default=50, ge=1)
    news_summary_max_chars: int = Field(default=160, ge=1, description="copyright-boundary summary cap")
    recency_half_life_hours: float = Field(default=48.0, gt=0)


class SourcesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(min_length=1)
    expectations: dict[str, Expectation] = Field(default_factory=dict)
    providers: dict[str, list[ProviderEntry]] = Field(default_factory=dict)
    degrade: DegradeConfig = Field(default_factory=DegradeConfig)
    operations: OperationsConfig = Field(default_factory=OperationsConfig)


class PublicDataSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    registry_domain: str | None = None
    datasets: list[str] = Field(default_factory=list)
    methods: list[Literal["get_quote", "get_history"]] = Field(default_factory=list)
    macro_series: list[str] = Field(default_factory=list)
    commodity_series: list[str] = Field(default_factory=list)
    market_symbols: list[str] = Field(default_factory=list)
    energy_series: list[str] = Field(default_factory=list)
    news_feeds: list[str] = Field(default_factory=list)
    index_series: list[str] = Field(default_factory=list)
    instrument_symbols: list[str] = Field(default_factory=list)
    series_attribution: dict[str, str] = Field(default_factory=dict)
    license_id: str = Field(min_length=1)
    license_url: str = Field(min_length=1)
    attribution: str = Field(min_length=1)
    permitted_use: PermittedUse
    accepted_by: str | None = None
    accepted_on: date | None = None
    cadence_minutes: int = Field(ge=1)

    @field_validator("macro_series", "market_symbols", "energy_series", "commodity_series", mode="before")
    @classmethod
    def _canonical_targets(cls, values: object) -> object:
        if not isinstance(values, list):
            return values
        return [value.strip().upper() if isinstance(value, str) else value for value in values]


@dataclass(frozen=True)
class SourceGrant:
    source_id: str
    provider: str
    registry_domain: str | None
    datasets: frozenset[str]
    methods: frozenset[str]
    macro_series: frozenset[str]
    commodity_series: frozenset[str]
    market_symbols: frozenset[str]
    energy_series: frozenset[str]
    news_feeds: frozenset[str]
    index_series: frozenset[str]
    instrument_symbols: frozenset[str]
    series_attribution: dict[str, str]
    license_id: str
    license_url: str
    attribution: str
    permitted_use: str
    accepted_by: str | None
    accepted_on: date | None
    cadence_minutes: int

    @property
    def targets(self) -> frozenset[str]:
        return (
            self.macro_series
            | self.market_symbols
            | self.energy_series
            | self.commodity_series
            | self.news_feeds
            | self.index_series
            | self.instrument_symbols
        )


@dataclass(frozen=True)
class ApprovedCollectionPlan:
    grants: tuple[SourceGrant, ...]

    def grants_for(self, provider: str, dataset: str) -> tuple[SourceGrant, ...]:
        return tuple(
            grant for grant in self.grants
            if grant.provider == provider and dataset in grant.datasets
        )

    def grant_for_dataset(self, dataset: str) -> SourceGrant | None:
        matches = tuple(grant for grant in self.grants if dataset in grant.datasets)
        if len(matches) > 1:
            raise ConfigError(f"public_sources.yaml: multiple grants for dataset {dataset!r}")
        return matches[0] if matches else None

    def grant_for(self, provider: str, dataset: str) -> SourceGrant | None:
        matches = self.grants_for(provider, dataset)
        if len(matches) > 1:
            raise ConfigError(f"public_sources.yaml: multiple grants for {provider!r}/{dataset!r}")
        return matches[0] if matches else None

    def macro_grant_for(self, series_id: str) -> SourceGrant | None:
        return next((grant for grant in self.grants if series_id in grant.macro_series), None)

    def energy_grant_for(self, series_id: str) -> SourceGrant | None:
        return next((grant for grant in self.grants if series_id in grant.energy_series), None)

    def news_feeds(self) -> frozenset[str]:
        return frozenset(feed for grant in self.grants for feed in grant.news_feeds)

    def grant_for_rung(
        self,
        source: str,
        symbol: str,
        *,
        dataset: str | None = None,
        method: str | None = None,
    ) -> SourceGrant | None:
        return next(
            (
                grant for grant in self.grants
                if source in (grant.source_id, grant.provider)
                and symbol in grant.targets
                and (dataset is None or dataset in grant.datasets)
                and (method is None or not grant.methods or method in grant.methods)
            ),
            None,
        )

    def allows_dataset(self, dataset: str) -> bool:
        return any(dataset in grant.datasets for grant in self.grants)

    def allows_provider(self, domain: str, provider: str) -> bool:
        return any(
            grant.registry_domain == domain and grant.provider == provider
            for grant in self.grants
        )

    def providers_for_domain(self, domain: str) -> tuple[str, ...]:
        return tuple(
            grant.provider for grant in self.grants
            if grant.registry_domain == domain
        )


class InstrumentRung(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    proxy_of: str | None = None


class InstrumentChain(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["index", "sector_etf", "crypto"]
    chain: list[InstrumentRung] = Field(min_length=1)


class InstrumentsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(min_length=1)
    instruments: dict[str, InstrumentChain] = Field(default_factory=dict)


class PublicSourcePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(min_length=1)
    sources: dict[str, PublicDataSource]

    def collection_plan(self, *, public_output: bool = False) -> ApprovedCollectionPlan:
        """Build a grant plan, optionally excluding personal-use-only data from publication.

        ``approved_sources_only`` writes an isolated generation intended for public
        distribution. Personal-use grants remain available to non-public, local collection
        flows, but must not authorize data written to that generation.
        """
        return ApprovedCollectionPlan(tuple(
            SourceGrant(
                source_id=source_id,
                provider=source.provider,
                registry_domain=source.registry_domain,
                datasets=frozenset(source.datasets),
                methods=frozenset(source.methods),
                macro_series=frozenset(source.macro_series),
                commodity_series=frozenset(source.commodity_series),
                market_symbols=frozenset(source.market_symbols),
                energy_series=frozenset(source.energy_series),
                news_feeds=frozenset(source.news_feeds),
                index_series=frozenset(source.index_series),
                instrument_symbols=frozenset(source.instrument_symbols),
                series_attribution=dict(source.series_attribution),
                license_id=source.license_id,
                license_url=source.license_url,
                attribution=source.attribution,
                permitted_use=source.permitted_use,
                accepted_by=source.accepted_by,
                accepted_on=source.accepted_on,
                cadence_minutes=source.cadence_minutes,
            )
            for source_id, source in self.sources.items()
            if not (public_output and source.permitted_use == "personal-use")
        ))

    def validate_instrument_chains(self, config: InstrumentsConfig) -> None:
        grants = self.collection_plan().grants
        for key, instrument in config.instruments.items():
            for rung in instrument.chain:
                if instrument.kind == "crypto":
                    target_is_granted = any(
                        rung.source in (grant.source_id, grant.provider)
                        and rung.symbol in grant.market_symbols
                        and "crypto" in grant.datasets
                        for grant in grants
                    )
                else:
                    target_is_granted = any(
                        rung.source in (grant.source_id, grant.provider)
                        and rung.symbol in grant.instrument_symbols
                        and "overview" in grant.datasets
                        and (not grant.methods or "get_quote" in grant.methods)
                        for grant in grants
                    )
                if not target_is_granted:
                    raise ConfigError(
                        f"instruments.yaml: {key} rung {rung.source}/{rung.symbol} "
                        "has no public source grant"
                    )

    @model_validator(mode="after")
    def _source_targets_are_unambiguous(self) -> PublicSourcePolicy:
        macro_series: set[str] = set()
        news_feeds: set[str] = set()
        provider_domains: dict[str, str | None] = {}
        provider_targets: dict[tuple[str, str], set[str]] = {}
        target_fields = (
            "macro_series", "market_symbols", "energy_series", "commodity_series",
            "news_feeds", "index_series", "instrument_symbols",
        )
        for source_id, source in self.sources.items():
            if not source_id or not source.datasets:
                raise ValueError("each public source needs an id and at least one dataset")
            unknown_datasets = set(source.datasets) - PUBLIC_DATASETS
            if unknown_datasets:
                raise ValueError(f"{source_id} has unsupported datasets: {sorted(unknown_datasets)}")
            previous_domain = provider_domains.setdefault(source.provider, source.registry_domain)
            if previous_domain != source.registry_domain:
                raise ValueError(f"{source.provider} has conflicting registry domains")
            for field in target_fields:
                values = getattr(source, field)
                if len(values) != len(set(values)):
                    raise ValueError(f"{source_id} repeats a value in {field}")
            for dataset in source.datasets:
                fields = TARGET_FIELDS.get(dataset)
                if fields is None:
                    continue
                targets = [target for field in fields for target in getattr(source, field)]
                if not targets:
                    raise ValueError(
                        f"{source_id} authorizes {dataset} without {' or '.join(fields)}"
                    )
                overlap = provider_targets.setdefault((source.provider, dataset), set()) & set(targets)
                if overlap:
                    raise ValueError(
                        f"{source.provider} has overlapping grants for {dataset}: {sorted(overlap)}"
                    )
                provider_targets[(source.provider, dataset)].update(targets)
            if source.macro_series and "macro" not in source.datasets:
                raise ValueError(f"{source_id} configures macro_series outside the macro dataset")
            if source.market_symbols and "crypto" not in source.datasets:
                raise ValueError(f"{source_id} configures market symbols outside the crypto dataset")
            if source.energy_series and "energy" not in source.datasets:
                raise ValueError(f"{source_id} configures energy_series outside the energy dataset")
            if source.commodity_series and "commodities" not in source.datasets:
                raise ValueError(f"{source_id} configures commodity_series outside the commodities dataset")
            if source.news_feeds and "news" not in source.datasets:
                raise ValueError(f"{source_id} configures news_feeds outside the news dataset")
            if source.instrument_symbols and not ({"equities", "overview", "sectors"} & set(source.datasets)):
                raise ValueError(f"{source_id} configures instrument_symbols outside the equities, overview or sectors dataset")
            if source.methods and (
                not source.instrument_symbols
                or source.registry_domain != "quotes"
                or len(source.methods) != len(set(source.methods))
            ):
                raise ValueError(f"{source_id} methods require quote-domain instrument targets and unique operations")
            if source.index_series and "equities" not in source.datasets:
                raise ValueError(f"{source_id} configures index_series outside the equities dataset")
            unknown_attribution = set(source.series_attribution) - set(source.macro_series)
            if unknown_attribution:
                raise ValueError(
                    f"{source_id} attributes series it does not grant: {sorted(unknown_attribution)}"
                )
            if any(not value.strip() for value in source.series_attribution.values()):
                raise ValueError(f"{source_id} has an empty series attribution")
            supported = SUPPORTED_TARGETS.get(source.provider, {})
            for field in ("macro_series", "market_symbols", "energy_series", "commodity_series"):
                allowed = supported.get(field, frozenset())
                if not set(getattr(source, field)).issubset(allowed):
                    label = field.replace("_", " ")
                    raise ValueError(f"{source_id} has {label} unsupported by {source.provider}")
            if "crypto" in source.datasets and not supported.get("market_symbols"):
                raise ValueError(f"{source_id} requires a supported crypto provider and explicit market symbols")
            if source.provider == "eia" and (
                set(source.datasets) != {"energy"} or set(source.energy_series) != {"WCESTUS1"}
            ):
                raise ValueError(f"{source_id} must grant only energy series WCESTUS1")
            if source.permitted_use == "personal-use" and not (
                source.accepted_by and source.accepted_on
            ):
                raise ValueError(f"{source_id} personal-use grant requires accepted_by and accepted_on")
            if source.permitted_use != "public-domain" and not source.attribution.strip():
                raise ValueError(f"{source_id} requires attribution")
            if macro_series.intersection(source.macro_series):
                raise ValueError("public source macro series must have one owner")
            if news_feeds.intersection(source.news_feeds):
                raise ValueError("public source news feeds must have one owner")
            macro_series.update(source.macro_series)
            news_feeds.update(source.news_feeds)
        return self

    def allows_provider(self, domain: str, provider: str) -> bool:
        return any(
            source.registry_domain == domain and source.provider == provider
            for source in self.sources.values()
        )

    def providers_for_domain(self, domain: str) -> tuple[str, ...]:
        return tuple(
            source.provider for source in self.sources.values()
            if source.registry_domain == domain
        )

    def source_for_macro_series(self, series_id: str) -> tuple[str, PublicDataSource] | None:
        return next(
            ((source_id, source) for source_id, source in self.sources.items()
             if series_id in source.macro_series),
            None,
        )

    def allows_dataset(self, dataset: str) -> bool:
        return any(dataset in source.datasets for source in self.sources.values())


# -------------------------------------------------------------------------------------
# universe.yaml
# -------------------------------------------------------------------------------------


class UniverseAsset(BaseModel):
    """One asset-pool entry. No ``theme`` field: membership lives in themes.yaml (D-8)."""

    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1)
    name: str = Field(min_length=1)
    name_zh: str | None = None
    sector: str = "other"


class UniverseConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = "1.0.0"
    us_equities: list[UniverseAsset] = Field(default_factory=list)
    a_share_memory: list[UniverseAsset] = Field(default_factory=list)
    crypto: list[UniverseAsset] = Field(default_factory=list)
    metals: list[UniverseAsset] = Field(default_factory=list)
    oil: list[UniverseAsset] = Field(default_factory=list)
    # #93: theme-series-only assets — fetched for basket percentile series, never rendered
    # as equity cards (the collector's `all_equities()` stays us + a_share).
    theme_series: list[UniverseAsset] = Field(default_factory=list)


# -------------------------------------------------------------------------------------
# themes.yaml
# -------------------------------------------------------------------------------------


class ThemeProxy(BaseModel):
    """An optional market proxy for a theme (#86: 11 ETF-proxied themes, 9 basket themes)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["etf", "basket"]
    symbol: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _symbol_matches_kind(self) -> ThemeProxy:
        if self.kind == "etf" and self.symbol is None:
            raise ValueError("ETF theme proxy requires a symbol")
        if self.kind == "basket" and self.symbol is not None:
            raise ValueError("basket theme proxy must not define a symbol")
        return self


class ThemePercentile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lookback_sessions: int = Field(default=252, ge=1)
    window_sessions: int = Field(default=20, ge=1)
    min_observations: int = Field(default=100, ge=1)


class ThemeConstituent(BaseModel):
    """``weight``: 1.0 primary, 0.5 secondary (#86). Must resolve in universe.yaml."""

    model_config = ConfigDict(extra="forbid")

    symbol: str = Field(min_length=1)
    weight: float = Field(default=1.0, gt=0)


class ThemeDef(BaseModel):
    """One theme (#93/#86). ``proxy`` with ``kind: etf`` publishes the ETF's own series;
    ``kind: basket`` (or no proxy) builds an equal-weight series from constituents.
    ``weight`` 1.0 primary / 0.5 secondary."""

    model_config = ConfigDict(extra="forbid")

    proxy: ThemeProxy | None = None
    percentile: ThemePercentile | None = None
    constituents: list[ThemeConstituent] = Field(default_factory=list)

    @model_validator(mode="after")
    def _constituents_are_unique(self) -> ThemeDef:
        symbols = [constituent.symbol for constituent in self.constituents]
        duplicates = sorted({symbol for symbol in symbols if symbols.count(symbol) > 1})
        if duplicates:
            raise ValueError(f"theme constituents must be unique: {', '.join(duplicates)}")
        return self

    @property
    def series_symbol(self) -> str | None:
        """Return the configured ETF series symbol, or ``None`` for a basket series."""
        return self.proxy.symbol if self.proxy is not None and self.proxy.kind == "etf" else None


def theme_history_symbols(theme: ThemeDef) -> set[str]:
    """Return the non-CN symbols required to build one theme's history series."""
    if theme.series_symbol is not None:
        return {theme.series_symbol}
    return {constituent.symbol for constituent in theme.constituents if not constituent.symbol.endswith((".SH", ".SZ"))}


class ValidationConfig(BaseModel):
    """#93/#86 taxonomy guards — enforced at config load (hard fails)."""

    model_config = ConfigDict(extra="forbid")

    max_primaries_per_symbol: int = Field(default=1, ge=1)
    max_themes_per_symbol: int = Field(default=3, ge=1)
    max_pairwise_jaccard: float = Field(default=0.40, gt=0, le=1.0)


class ThemesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(min_length=1)
    #: Global percentile defaults (window/lookback/min_obs); per-theme overrides win.
    percentile: ThemePercentile | None = None
    validation: ValidationConfig | None = None
    sectors: dict[str, ThemeDef] = Field(default_factory=dict)
    themes: dict[str, ThemeDef] = Field(default_factory=dict)


# -------------------------------------------------------------------------------------
# news_sources.yaml
# -------------------------------------------------------------------------------------


def _validate_public_hostname(host: str | None, field: str) -> None:
    """Reject malformed or obviously local destinations at the config boundary."""
    if not host:
        raise ValueError(f"{field} must include a hostname")
    normalized = host.lower()
    if normalized == "localhost" or normalized.endswith((".localhost", ".local")):
        raise ValueError(f"{field} must not target a local hostname: {host!r}")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        try:
            ascii_host = host.encode("idna").decode("ascii").lower()
        except UnicodeError as exc:
            raise ValueError(f"{field} is not a valid hostname: {host!r}") from exc
        labels = ascii_host.split(".")
        if len(ascii_host) > 253 or len(labels) < 2 or any(
            not part
            or len(part) > 63
            or part[0] == "-"
            or part[-1] == "-"
            or any(not (char.isalnum() or char == "-") for char in part)
            for part in labels
        ):
            raise ValueError(f"{field} is not a valid hostname: {host!r}") from None
    else:
        if not ip.is_global:
            raise ValueError(f"{field} must not target a non-public IP: {host!r}")


def _validate_source_url(url: str, field: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise ValueError(f"news source URL must be https: {url!r}")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError(f"{field} must not contain URL credentials")
    if parsed.fragment:
        raise ValueError(f"{field} must not contain a URL fragment")
    try:
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(f"{field} has an invalid host or port: {url!r}") from exc
    _validate_public_hostname(hostname, field)


class NewsSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    url: str = Field(min_length=1)
    lang: Literal["en", "zh"] = "en"
    weight: int = Field(default=1, ge=0)
    category: str | None = None
    requires_declared_ua: bool = False
    enabled: bool = True
    # #124: ordered fallback URL chain — a dead primary advances to the first fallback that
    # serves; one try per URL per run attempt. Every URL is https-only (S-3) and a chain
    # holds no exact duplicates (config-is-a-fact, ADR-0005).
    fallback_urls: list[str] = Field(default_factory=list)
    # S-3: a relay source may redirect only to its explicit forwarding destinations.
    trust: Literal["relay"] | None = None
    # S-3: relay redirects are explicit destinations, never an arbitrary host voucher.
    redirect_hosts: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _chain_is_https_and_duplicate_free(self) -> NewsSource:
        seen: set[str] = set()
        for url in self.chain_urls:
            _validate_source_url(url, "news source URL")
            if url in seen:
                raise ValueError(f"duplicate URL in news source chain: {url!r}")
            seen.add(url)
        for host in self.redirect_hosts:
            _validate_public_hostname(host, "relay redirect host")
        if self.trust == "relay" and not self.redirect_hosts:
            raise ValueError("relay source must declare redirect_hosts")
        if self.trust != "relay" and self.redirect_hosts:
            raise ValueError("redirect_hosts are only valid for relay sources")
        return self

    @property
    def chain_urls(self) -> list[str]:
        """Primary URL first, then each fallback — the ordered chain the provider walks
        (#124). One shape for the validator, the S-3 allowlist, and the fetch loop."""
        return [self.url, *self.fallback_urls]


class NewsImportance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_weight: float = Field(default=30, ge=0)
    keyword_weight: float = Field(default=30, ge=0)
    asset_hit_weight: float = Field(default=20, ge=0)
    recency_weight: float = Field(default=20, ge=0)
    keywords: dict[str, list[str]] = Field(default_factory=dict)


class NewsSourcesConfig(BaseModel):
    """No ``assets``/``categories`` blocks: asset hits come from the universe (D-8), and the
    category list was dead config (no consumer ever read it)."""

    model_config = ConfigDict(extra="forbid")

    version: str = "1.0.0"
    sources: list[NewsSource] = Field(default_factory=list)
    importance: NewsImportance = Field(default_factory=NewsImportance)


# -------------------------------------------------------------------------------------
# The single loader
# -------------------------------------------------------------------------------------

M = TypeVar("M", bound=BaseModel)


def load_config(path: Path, model: type[M]) -> M:
    """Load and validate one config file. Raises :class:`ConfigError` on any failure."""
    if not path.exists():
        raise ConfigError(f"config file missing: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"config file is not valid YAML: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"config file must be a YAML mapping: {path}")
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"config file failed validation ({path}): {exc}") from exc
