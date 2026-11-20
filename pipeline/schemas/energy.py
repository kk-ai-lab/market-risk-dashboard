from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import Field, model_validator

from .envelope import BaseEnvelope, ContractModel, UTCDateTime

ENERGY_OBSERVATION_WINDOW_DAYS = 14


class EnergyObservation(ContractModel):
    """One reported weekly U.S. crude inventory observation."""

    week_ending: date
    value: float = Field(ge=0.0, description="thousand barrels")


class EnergyDataset(ContractModel):
    """Latest 260-or-fewer weekly observations for EIA series WCESTUS1."""

    series_id: Literal["WCESTUS1"] = "WCESTUS1"
    unit: Literal["thousand_barrels"] = "thousand_barrels"
    observations: list[EnergyObservation] = Field(default_factory=list, max_length=260)
    collection_status: Literal["not_configured", "available", "empty", "degraded"]
    observation_age: Literal["unknown", "within_window", "overdue"] = Field(
        description="Age at last_attempt_at; clients recompute this from week_ending at render time."
    )
    last_attempt_at: UTCDateTime
    last_successful_fetch_at: UTCDateTime | None = None

    @model_validator(mode="after")
    def _consistent_timeline_and_status(self) -> EnergyDataset:
        dates = [row.week_ending for row in self.observations]
        if dates != sorted(set(dates)):
            raise ValueError("energy observations must be unique and sorted by week_ending")

        has_rows = bool(self.observations)
        if self.collection_status == "available" and (not has_rows or not self.last_successful_fetch_at):
            raise ValueError("available energy data requires observations and a successful retrieval time")
        if self.collection_status in {"not_configured", "empty"} and has_rows:
            raise ValueError(f"{self.collection_status} energy data cannot contain observations")
        if self.collection_status == "not_configured" and self.last_successful_fetch_at:
            raise ValueError("not_configured energy data cannot claim a successful retrieval")
        if self.collection_status == "empty" and not self.last_successful_fetch_at:
            raise ValueError("empty energy data requires a successful retrieval time")

        expected_age: str = "unknown"
        if has_rows:
            latest = dates[-1]
            attempt_date = datetime.fromisoformat(self.last_attempt_at.replace("Z", "+00:00")).date()
            days_old = (attempt_date - latest).days
            if 0 <= days_old <= ENERGY_OBSERVATION_WINDOW_DAYS:
                expected_age = "within_window"
            elif days_old > ENERGY_OBSERVATION_WINDOW_DAYS:
                expected_age = "overdue"
        if self.observation_age != expected_age:
            raise ValueError("observation_age must match the latest observation and 14-day dashboard rule")
        return self


class EnergyEnvelope(BaseEnvelope):
    payload: EnergyDataset
