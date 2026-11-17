"""Calendar dataset contract (economic calendar + earnings calendar, architecture §1.3 / §3.5).

Filled by the T03 CalendarCollector; this module only defines the contract.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from .envelope import BaseEnvelope, ContractModel, UTCDateTime

EventType = Literal["economic", "earnings"]
EventImportance = Literal["high", "medium", "low"]
CalendarSource = Literal["earnings", "economic", "bea_calendar"]
CalendarSourceState = Literal["fresh", "empty", "degraded", "not_configured"]


class CalendarEvent(ContractModel):
    #: Provider-stable id. The collector also deduplicates economic rows by exact
    #: title and UTC instant.
    id: str = Field(min_length=1, description="stable dedupe key, e.g. econ-fred-10-2026-08-12")
    type: EventType
    title: str = Field(min_length=1)
    country: str | None = None
    datetime: UTCDateTime
    importance: EventImportance = "medium"
    actual: float | None = None
    forecast: float | None = None
    previous: float | None = None
    unit: str | None = None
    related_assets: list[str] = Field(default_factory=list)
    source: str = Field(min_length=1)


class CalendarSourceStatus(ContractModel):
    source: CalendarSource
    state: CalendarSourceState
    provider: str = Field(min_length=1)
    checked_at: UTCDateTime | None = None
    used_fallback: bool = False
    from_cache: bool = False


class CalendarDataset(ContractModel):
    """calendar.json payload."""

    events: list[CalendarEvent] = Field(default_factory=list)
    source_status: list[CalendarSourceStatus] = Field(default_factory=list)
    updated_at: UTCDateTime | None = None


class CalendarEnvelope(BaseEnvelope):
    payload: CalendarDataset
