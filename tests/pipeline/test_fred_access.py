from __future__ import annotations

import pytest

from pipeline.collectors.macro import SERIES_GROUPS, MacroCollector
from pipeline.fred_access import Configured, NotConfigured, fred_access
from pipeline.providers import build_default_providers
from pipeline.providers.base import ProviderError
from pipeline.providers.fred import SERIES_CATALOG, FredProvider
from pipeline.settings import Settings


class NoNetworkRegistry:
    def __init__(self) -> None:
        self.calls = []
        self.degraded_domains = set()

    def call(self, domain, method, key, args=(), kwargs=None):
        self.calls.append((domain, method, key))
        raise AssertionError(f"unexpected provider call: {domain}.{method}")

    def resolved_provider(self, domain):
        return None


def settings_without_key(tmp_path) -> Settings:
    return Settings(
        _env_file=None,
        approved_sources_only=True,
        fred_api_key=None,
        project_root=tmp_path,
        config_dir=Settings(_env_file=None).config_dir,
        data_dir=tmp_path / "data",
        artifacts_dir=tmp_path / "artifacts",
    )


def test_fred_access_parses_at_the_boundary(monkeypatch):
    monkeypatch.delenv("DATA_FRED_API_KEY", raising=False)
    assert isinstance(fred_access(Settings(_env_file=None, fred_api_key=None)), NotConfigured)
    assert isinstance(fred_access(Settings(_env_file=None, fred_api_key="")), NotConfigured)
    assert isinstance(fred_access(Settings(_env_file=None, fred_api_key="   ")), NotConfigured)
    assert isinstance(fred_access(Settings(_env_file=None, fred_api_key="secret")), Configured)


@pytest.mark.parametrize("key", [None, "", "   "])
def test_absent_key_sends_no_request(tmp_path, monkeypatch, key):
    monkeypatch.delenv("DATA_FRED_API_KEY", raising=False)
    provider = FredProvider(Settings(_env_file=None, fred_api_key=key))

    assert provider._client is None
    with pytest.raises(ProviderError, match="DATA_FRED_API_KEY"):
        provider.get_series("VIXCLS")


def test_absent_key_publishes_not_configured(tmp_path, monkeypatch):
    monkeypatch.delenv("DATA_FRED_API_KEY", raising=False)
    settings = settings_without_key(tmp_path)
    policy = settings.load_public_source_policy()
    fred_series = {series.lower() for series in policy.sources["fred"].macro_series}
    registry = NoNetworkRegistry()

    macro, meta = MacroCollector(registry, settings).collect()
    indicators = {
        indicator.key: indicator
        for group in SERIES_GROUPS
        for indicator in getattr(macro, group)
    }

    assert fred_series
    for key in fred_series:
        assert indicators[key].source == "unavailable"
        assert indicators[key].status == "missing"
        assert indicators[key].value is None
    assert meta["error_code"] == "provider_auth_failed"
    assert "DATA_FRED_API_KEY is not configured; no request was sent" in meta["detail"]
    assert meta["provider_status"]["fred"]["error"] == "not_configured"
    assert registry.calls == []


def test_absent_key_registers_no_fred_provider(tmp_path, monkeypatch):
    monkeypatch.delenv("DATA_FRED_API_KEY", raising=False)
    providers = build_default_providers(settings_without_key(tmp_path))

    assert "fred" not in {provider.name for provider in providers}
    assert {"coinmetrics", "eia", "bea_calendar"} <= {provider.name for provider in providers}


def test_fred_grant_names_only_catalog_series(tmp_path):
    policy = settings_without_key(tmp_path).load_public_source_policy()
    granted = set(policy.sources["fred"].macro_series)

    assert granted
    assert granted <= set(SERIES_CATALOG)
