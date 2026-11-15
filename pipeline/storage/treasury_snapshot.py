"""Opt-in local Treasury CMT cache, never a FRED merge or production default.

Six calendar-year files cover a rolling five-year lookback; consumers must trim
observation dates themselves. Historical years are retained, not presumed immutable:
``refresh_history=True`` explicitly rechecks them at most once per UTC day. Normal
runs only poll the current year (15-minute minimum), plus unfinished bootstrap
requests once/day. A local flock coordinates one filesystem, NOT separate runners.
Restore/persist this directory externally if runner continuity is required.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import parse_qs, urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, FiniteFloat, model_validator

from pipeline.providers.base import ProviderError
from pipeline.providers.treasury import SERIES, TREASURY_XML


class SnapshotError(Exception):
    """Invalid local state or a backwards clock prevents refresh."""


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    value: FiniteFloat


class TreasuryYear(BaseModel):
    model_config = ConfigDict(extra="forbid")
    year: int
    source: str
    checked_at: str
    date_semantics: Literal["observation_date"]
    series: dict[str, list[Observation]]

    @model_validator(mode="after")
    def validate_year(self) -> TreasuryYear:
        stamp = datetime.fromisoformat(self.checked_at)
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("checked_at must include a timezone")
        url, official = urlsplit(self.source), urlsplit(TREASURY_XML)
        if (url.scheme, url.netloc, url.path) != (official.scheme, official.netloc, official.path) or url.fragment:
            raise ValueError("source must be the official Treasury year feed")
        if parse_qs(url.query) != {"data": ["daily_treasury_yield_curve"], "field_tdr_date_value": [str(self.year)]}:
            raise ValueError("source must identify the requested year")
        if set(self.series) != set(SERIES):
            raise ValueError("both Treasury maturities are required")
        for rows in self.series.values():
            dates = [row.date for row in rows]
            if not dates or dates != sorted(set(dates)) or any(day.year != self.year or day > stamp.date() for day in dates):
                raise ValueError("invalid, duplicate, future, or unordered observations")
        return self


class YearLedger(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempted_at: AwareDatetime | None = None
    status: Literal["never", "pending", "complete", "failed"] = "never"
    error: str | None = None
    has_data: bool = False
    revisions: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_state(self) -> YearLedger:
        if (self.attempted_at is None) != (self.status == "never"):
            raise ValueError("attempt time and state disagree")
        if self.status == "complete" and not self.has_data:
            raise ValueError("complete year must contain data")
        return self


class YearCommit(BaseModel):
    """One atomic commit couples observations, request identity and revision count."""
    model_config = ConfigDict(extra="forbid")
    data: TreasuryYear
    ledger: YearLedger

    @model_validator(mode="after")
    def validate_commit(self) -> YearCommit:
        if self.ledger.status != "complete" or not self.ledger.has_data:
            raise ValueError("year commit must be complete")
        return self


class Ledger(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    years: dict[int, YearLedger] = Field(default_factory=dict)


class TreasurySnapshot(BaseModel):
    """Rows retain original dates and retrieval timestamps, not publication times.

    ``years`` may include older retained years; ``required_years`` identifies the
    six-year coverage window. Errors never cause historical rows to be deleted.
    """
    years: dict[int, TreasuryYear]
    required_years: list[int]
    degraded: bool
    errors: dict[int, str]
    history_rechecked: bool  # At least one historical network request in this call.


class YearProvider(Protocol):
    def get_yield_curve(self, *, year: int) -> dict: ...


def _read(path: Path, model):
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SnapshotError(f"Unreadable Treasury snapshot: {path.name}") from exc


def _save(path: Path, state: BaseModel) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
            temporary = stream.name
            json.dump(state.model_dump(mode="json"), stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def refresh_treasury_snapshot(directory: Path, provider: YearProvider, *, now: datetime,
                              refresh_history: bool = False) -> TreasurySnapshot:
    """Return usable local data with explicit gaps/failures; never silently reset state.

    Historical removal/regression rejects the response while retaining the entire
    previous year. Changed values are accepted as revisions. An empty new year is
    a degraded refresh, retaining the previous year until new observations exist.
    Interrupted attempts remain degraded and observe the same retry interval.
    Each year atomically commits its data and complete ledger; a pending manifest
    is recovered from that commit after a crash, without repeating the request.
    Missing/corrupt committed files fail closed (no silent historical re-fetch).
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must have a timezone")
    now = now.astimezone(UTC)
    started = time.monotonic()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        manifest = directory / "manifest.json"
        ledger = _read(manifest, Ledger) if manifest.exists() else Ledger()
        if not manifest.exists() and any(directory.glob("[0-9][0-9][0-9][0-9].json")):
            raise SnapshotError("Treasury manifest missing beside existing year files")
        for path in directory.glob("[0-9][0-9][0-9][0-9].json"):
            if int(path.stem) not in ledger.years:
                raise SnapshotError("Treasury manifest lost a year entry")
        required = list(range(now.year - 5, now.year + 1))
        years = {}
        errors = {}
        recovered = False
        history_rechecked = False
        for year, entry in ledger.years.items():
            if entry.attempted_at is not None and entry.attempted_at > now:
                raise SnapshotError("Treasury attempt clock moved backwards")
            path = directory / f"{year}.json"
            if entry.has_data or path.exists():
                try:
                    commit = _read(path, YearCommit)
                    saved, committed = commit.data, commit.ledger
                    if saved.year != year:
                        raise SnapshotError("Treasury year file mismatch")
                    if committed.attempted_at > now:
                        raise SnapshotError("Treasury commit clock moved backwards")
                    if entry.attempted_at == committed.attempted_at:
                        if entry.status == "pending":
                            ledger.years[year] = committed
                            recovered = True
                        elif entry != committed:
                            raise SnapshotError("Treasury year/manifest commit mismatch")
                    elif entry.attempted_at is None or committed.attempted_at > entry.attempted_at:
                        raise SnapshotError("Treasury year has an unrecorded attempt")
                    elif entry.status not in {"pending", "failed"} or entry.revisions != committed.revisions:
                        raise SnapshotError("Treasury year/manifest commit mismatch")
                    years[year] = saved
                except SnapshotError as exc:
                    if "clock moved backwards" in str(exc):
                        raise
                    errors[year] = str(exc)
        if recovered:
            _save(manifest, ledger)
        for year in required:
            entry = ledger.years.setdefault(year, YearLedger())
            if year in errors:
                continue
            attempted = entry.attempted_at
            due = attempted is None or (now - attempted >= timedelta(minutes=15) if year == now.year else attempted.date() < now.date())
            requested = year == now.year or not entry.has_data or refresh_history
            if not (due and requested):
                if entry.status != "complete":
                    errors[year] = entry.error or f"Treasury attempt {entry.status}"
                continue
            entry.attempted_at, entry.status, entry.error = now, "pending", None
            _save(manifest, ledger)
            try:
                if year < now.year:
                    history_rechecked = True
                request_start = now + timedelta(seconds=time.monotonic() - started)
                result = provider.get_yield_curve(year=year)
                request_end = now + timedelta(seconds=time.monotonic() - started)
                candidate = TreasuryYear.model_validate({key: result[key] for key in ("year", "source", "checked_at", "date_semantics", "series")})
                if candidate.year != year:
                    raise ValueError("Treasury returned a different year")
                if any(row.date > now.date() for rows in candidate.series.values() for row in rows):
                    raise ValueError("Treasury returned future observations")
                checked = datetime.fromisoformat(candidate.checked_at)
                # now anchors the caller's clock; monotonic elapsed includes lock
                # waits and network time. Permit a documented 30-second skew only.
                skew = timedelta(seconds=30)
                if not request_start - skew <= checked <= request_end + skew:
                    raise ValueError("Treasury retrieval time is outside this request window")
                previous = years.get(year)
                revisions = 0
                if previous:
                    if checked <= datetime.fromisoformat(previous.checked_at):
                        raise ValueError("Treasury retrieval time regressed or did not advance")
                    for key in SERIES:
                        old = {row.date: row.value for row in previous.series[key]}
                        new = {row.date: row.value for row in candidate.series[key]}
                        if not old.keys() <= new.keys():
                            raise ValueError("Treasury response lost historical observations")
                        revisions += sum(new[day] != value for day, value in old.items())
                committed = entry.model_copy(deep=True)
                committed.revisions += revisions
                committed.has_data, committed.status = True, "complete"
                _save(directory / f"{year}.json", YearCommit(data=candidate, ledger=committed))
                years[year] = candidate
                ledger.years[year] = entry = committed
            except (ProviderError, ValueError, KeyError, TypeError) as exc:
                entry.status, entry.error = "failed", str(exc)
                errors[year] = entry.error
            _save(manifest, ledger)
        return TreasurySnapshot(years=years, required_years=required,
                                degraded=bool(errors) or any(year not in years for year in required),
                                errors=errors, history_rechecked=history_rechecked)
