"""Homepage aggregation dashboard.json contract (architecture §2 file list L299 + §3.6).

Isomorphic to the frontend src/schemas/dashboard.ts (Zod):
- payload aggregates key fields from risk/regime/top_drivers/cross_asset/catalysts/sector_performance.
- produced by the pipeline in the --full flow; the frontend Overview page can consume a single file (since T05).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .calendar import CalendarEvent
from .envelope import BaseEnvelope, ContractModel, UTCDateTime
from .risk import DriverContribution, MarketRegime, RiskModelResult


class DashboardAsset(ContractModel):
    """Cross-asset confirmation signal entry (equity/crypto etc.)."""

    asset: str = Field(min_length=1)
    category: str = Field(min_length=1)
    change_1d: float | None = None
    source: str | None = None


class MarketSnapshotItem(ContractModel):
    """A dated, unit-bearing market observation used by the overview."""

    symbol: str = Field(min_length=1)
    label: str = Field(min_length=1)
    label_zh: str | None = None
    category: Literal["equity_index", "crypto", "commodity", "rate", "fx"]
    value: float
    unit: str = Field(min_length=1)
    change_percent: float | None = None
    change_period: str | None = None
    observation_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    observation_period: str | None = Field(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    frequency: Literal["daily", "monthly"]
    source: str = Field(min_length=1)
    source_url: str | None = None
    attribution: str | None = None


class DashboardSector(ContractModel):
    """Localized sector/theme performance entry used by the homepage read model."""

    key: str = Field(min_length=1)
    change_1d: float | None = None


class DashboardInstrument(ContractModel):
    """Overview index or sector ETF row with provider and proxy provenance."""

    key: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    change_1d: float | None = None
    source: str | None = None
    provider: str | None = None
    retrieved_at: UTCDateTime | None = None
    is_proxy: bool = False
    proxy_of: str | None = None
    availability_reason: Literal["public_source_unlicensed", "provider_unavailable"] | None = None


class DashboardPayload(ContractModel):
    """dashboard.json payload (consistent with the frontend Zod strict structure)."""

    risk: RiskModelResult
    regime: MarketRegime
    top_drivers: list[DriverContribution] = Field(default_factory=list)
    cross_asset: list[DashboardAsset] = Field(default_factory=list)
    market_snapshot: list[MarketSnapshotItem] = Field(default_factory=list)
    catalysts: list[CalendarEvent] = Field(default_factory=list)
    sector_performance: list[DashboardSector] = Field(default_factory=list)
    index_performance: list[DashboardInstrument] = Field(default_factory=list)
    sector_etfs: list[DashboardInstrument] = Field(default_factory=list)


class DashboardEnvelope(BaseEnvelope):
    """dashboard.json envelope (strongly typed payload)."""

    payload: DashboardPayload
