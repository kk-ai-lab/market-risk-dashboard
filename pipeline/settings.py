"""Pipeline runtime configuration: pydantic-settings reads .env + config/*.yaml.

Architecture §3.5/§1.3: API keys live only in the local .env (gitignored); keys must never
appear in the frontend/CI. This file is the T01 skeleton: defines the Settings structure +
YAML loading utilities; used by Collectors since T03.
"""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:  # pragma: no cover - import-time only, resolves the lazy-typed annotations
    # Imported here so ruff F821 can resolve the return annotations below; the real import is
    # lazy inside each loader to avoid the module-level cycle (config.models → degrade →
    # settings).
    from pipeline.config.models import (
        NewsSourcesConfig,
        PublicSourcePolicy,
        SourcesConfig,
        ThemesConfig,
        UniverseConfig,
    )

# Project root directory (pipeline/settings.py → parent)
PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Global pipeline configuration.

    Environment variable prefix DATA_ (e.g. DATA_FRED_API_KEY in .env.example).
    """

    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_prefix="DATA_",
        case_sensitive=False,
        extra="ignore",
    )

    fred_api_key: str | None = Field(default=None, description="FRED API key (local .env)")
    eia_api_key: SecretStr | None = Field(default=None, description="EIA API key (local .env or cloud secret)")
    coingecko_api_key: str | None = Field(default=None, description="CoinGecko Demo key")
    fmp_api_key: str | None = Field(default=None, description="FMP free-tier key")
    coinmetrics_enabled: bool = Field(default=False, description="Opt in to Coin Metrics Community BTC/ETH reference rates for noncommercial publication (CC BY-NC 4.0)")
    treasury_enabled: bool = Field(default=False, description="Opt in to the local Treasury daily yield snapshot")
    bls_enabled: bool = Field(default=False, description="Use the local BLS snapshot for supported macro indicators")
    approved_sources_only: bool = Field(default=False, description="Restrict collection and cache replay to config/public_sources.yaml")

    bls_cloud_enabled: bool = Field(default=False, description="Opt in to the durable cloud BLS ledger instead of the local snapshot")
    treasury_cloud_enabled: bool = Field(default=False, description="Opt in to the durable cloud Treasury ledger instead of the local snapshot")
    cloud_state_repo: str | None = Field(default=None, description="Explicit owner/repository for the pre-provisioned cloud-state branch")
    cloud_state_branch: str = Field(default="cloud-state", description="Pre-provisioned shared state branch; never reset while writers run")
    cloud_state_token: SecretStr | None = Field(default=None, description="Credential with contents write access to the cloud-state branch")
    bls_checkpoint_path: str = Field(default="bls/ledger.json", description="Pre-provisioned cloud BLS ledger path")
    treasury_checkpoint_path: str = Field(default="treasury/ledger.json", description="Pre-provisioned cloud Treasury ledger path")

    # Directories (relative to project root)
    project_root: Path = PROJECT_ROOT
    config_dir: Path = PROJECT_ROOT / "config"
    data_dir: Path = PROJECT_ROOT / "public" / "data"
    artifacts_dir: Path = PROJECT_ROOT / "artifacts"

    # ---- YAML config loading ----

    def _load_yaml(self, name: str) -> dict[str, Any]:
        """Read config/{name}.yaml and return a dict; raises ConfigError when the file is missing or invalid."""
        from pipeline.config.models import ConfigError

        path = self.config_dir / f"{name}.yaml"
        try:
            with path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh)
        except OSError as exc:
            raise ConfigError(f"config file could not be read: {path}: {exc}") from exc
        except (UnicodeError, yaml.YAMLError) as exc:
            raise ConfigError(f"config file could not be parsed: {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ConfigError(f"config file must be a YAML mapping: {path}")
        return data

    def load_universe(self) -> dict[str, Any]:
        """Asset universe (config/universe.yaml)."""
        return self._load_yaml("universe")

    def load_risk_model(self) -> dict[str, Any]:
        """Risk model weights/indicators/thresholds (config/risk_model.yaml)."""
        return self._load_yaml("risk_model")

    def load_sources(self) -> dict[str, Any]:
        """Provider registry/degradation/expected frequency (config/sources.yaml)."""
        return self._load_yaml("sources")

    def load_news_sources(self) -> dict[str, Any]:
        """News sources and importance rules (config/news_sources.yaml)."""
        return self._load_yaml("news_sources")

    # ---- Validated config (#102, pipeline/config/models.py) ----
    #
    # The raw loaders above remain for the loose readers (freshness expectations,
    # importance rules). The validated loaders below are what the provider factory, the
    # collectors and the universe use — a typo now raises ConfigError before any provider
    # is constructed, instead of silently disabling a theme.

    def load_sources_config(self) -> "SourcesConfig":
        """Validated sources.yaml (providers/degrade/operations) — raises ConfigError on drift."""
        from pipeline.config.models import SourcesConfig, load_config

        return load_config(self.config_dir / "sources.yaml", SourcesConfig)

    def load_public_source_policy(self) -> PublicSourcePolicy:
        """Validated allowlist for public redistribution and provider/cache access."""
        from pipeline.config.models import PublicSourcePolicy, load_config

        return load_config(self.config_dir / "public_sources.yaml", PublicSourcePolicy)

    def load_universe_config(self) -> "UniverseConfig":
        """Validated universe.yaml — no theme tags (#102)."""
        from pipeline.config.models import UniverseConfig, load_config

        return load_config(self.config_dir / "universe.yaml", UniverseConfig)

    def load_themes_config(self) -> "ThemesConfig":
        """Validated themes.yaml: constituents resolve in universe.yaml and the #86 taxonomy
        guards hold (≤1 primary per symbol, ≤3 theme memberships, pairwise Jaccard ≤0.40)."""
        from pipeline.config.models import ConfigError, ThemesConfig, load_config

        themes = load_config(self.config_dir / "themes.yaml", ThemesConfig)
        universe = self.load_universe_config()
        known = {
            a.symbol
            for pool in (
                universe.us_equities,
                universe.a_share_memory,
                universe.crypto,
                universe.metals,
                universe.oil,
                universe.theme_series,
            )
            for a in pool
        }
        # Collect every theme definition (sectors + themes) in one table for the guards.
        all_defs: dict[str, tuple[str, list]] = {}
        for section in ("sectors", "themes"):
            for key, theme in getattr(themes, section).items():
                all_defs[f"{section}.{key}"] = (section, theme.constituents)

        for label, (section, constituents) in all_defs.items():
            for constituent in constituents:
                if constituent.symbol not in known:
                    raise ConfigError(
                        f"themes.yaml:{label}: constituent {constituent.symbol!r} "
                        f"does not resolve in universe.yaml"
                    )

        validation = themes.validation
        if validation is not None:
            # The #86 guards apply to the THEME taxonomy only — the legacy sector rows
            # (semis/auto) are aggregations over the same universe and must not consume a
            # ticker's primary/secondary budget.
            theme_labels = [label for label, (section, _) in all_defs.items() if section == "themes"]
            memberships: dict[str, list[str]] = {}
            primaries: dict[str, int] = {}
            for label in theme_labels:
                for c in all_defs[label][1]:
                    memberships.setdefault(c.symbol, []).append(label)
                    if c.weight >= 1.0:
                        primaries[c.symbol] = primaries.get(c.symbol, 0) + 1
            for symbol, count in primaries.items():
                if count > validation.max_primaries_per_symbol:
                    raise ConfigError(
                        f"themes.yaml: {symbol!r} is primary in {count} themes "
                        f"(max {validation.max_primaries_per_symbol})"
                    )
            for symbol, labels in memberships.items():
                if len(labels) > validation.max_themes_per_symbol:
                    raise ConfigError(
                        f"themes.yaml: {symbol!r} appears in {len(labels)} themes "
                        f"(max {validation.max_themes_per_symbol}): {', '.join(labels)}"
                    )
            for i, a in enumerate(theme_labels):
                for b in theme_labels[i + 1 :]:
                    members_a = {c.symbol for c in all_defs[a][1]}
                    members_b = {c.symbol for c in all_defs[b][1]}
                    intersection = members_a & members_b
                    union = members_a | members_b
                    if union and len(intersection) / len(union) > validation.max_pairwise_jaccard:
                        raise ConfigError(
                            f"themes.yaml: {a!r} and {b!r} pairwise Jaccard "
                            f"{len(intersection) / len(union):.2f} exceeds "
                            f"{validation.max_pairwise_jaccard} (#86 overlap guard)"
                        )
        return themes

    def load_news_sources_config(self) -> "NewsSourcesConfig":
        """Validated news_sources.yaml — the assets/categories blocks are gone (#102)."""
        from pipeline.config.models import NewsSourcesConfig, load_config

        return load_config(self.config_dir / "news_sources.yaml", NewsSourcesConfig)


_settings_lock = Lock()

if TYPE_CHECKING:
    settings: Settings


def __getattr__(name: str) -> Any:
    if name != "settings":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    with _settings_lock:
        instance = globals().get("settings")
        if instance is None:
            instance = Settings()
            globals()["settings"] = instance
        return instance
