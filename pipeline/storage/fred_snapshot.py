"""Durable FRED observations with an observation-keyed fetch gate.

A FRED series is fetched only once its next observation could have been published,
then retried hourly while it is overdue. The stored rows are the last good values,
so a failed fetch degrades the tile instead of emptying it.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    FiniteFloat,
    ValidationError,
    model_validator,
)

from pipeline.providers.base import ProviderError

#: First fill. The risk model's percentile window is five years.
LOOKBACK_DAYS = 5 * 365
#: Overlap on incremental fills; absorbs small upstream revisions.
OVERLAP_DAYS = 14
#: Jitter guards against a publish stamp that is slightly early.
JITTER = timedelta(hours=2)
DEFAULT_RETRY_MINUTES = 60
DEFAULT_PUBLISH_UTC = time(12, 0)

#: Publish stamps read from the FRED series pages on 2026-11-20.
PUBLISH_UTC: dict[str, time] = {
    "VIXCLS": time(14, 30),
    "BAA10Y": time(22, 0),
}

#: Weekly and monthly series cannot be keyed to a next business day; these are the
#: same upper bounds as ``pipeline.collectors.macro.FREQ_SPEC[*]["next_days"]``.
#: ``test_fred_snapshot.py`` pins the two tables together.
FREQUENCY_NEXT_DAYS: dict[str, int] = {"weekly": 8, "monthly": 32}


class SnapshotError(Exception):
    """An unreadable snapshot must not be mistaken for an empty one."""


class FredObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    value: FiniteFloat


class FredSeriesState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rows: list[FredObservation] = []
    last_attempt_at: AwareDatetime | None = None
    access: Literal["available", "not_configured", "failed"] = "available"

    @model_validator(mode="after")
    def validate_rows(self) -> FredSeriesState:
        dates = [row.date for row in self.rows]
        if dates != sorted(set(dates)):
            raise ValueError("FRED observation dates must be unique and sorted")
        return self


class FredSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    series: dict[str, FredSeriesState] = {}


@dataclass(frozen=True)
class SeriesSpec:
    frequency: str
    publish_utc: time = DEFAULT_PUBLISH_UTC
    jitter: timedelta = JITTER
    retry_minutes: int = DEFAULT_RETRY_MINUTES


def series_spec(series_id: str, *, retry_minutes: int = DEFAULT_RETRY_MINUTES) -> SeriesSpec:
    from pipeline.providers.fred import SERIES_CATALOG

    frequency = SERIES_CATALOG.get(series_id, {}).get("frequency", "daily")
    return SeriesSpec(
        frequency=frequency,
        publish_utc=PUBLISH_UTC.get(series_id, DEFAULT_PUBLISH_UTC),
        retry_minutes=retry_minutes,
    )


def next_publish_after(last_obs: date, frequency: str, publish_utc: time) -> datetime:
    """Earliest instant the observation after ``last_obs`` could be published."""
    if frequency == "daily":
        day = last_obs + timedelta(days=1)
        while day.weekday() >= 5:
            day += timedelta(days=1)
    elif frequency in FREQUENCY_NEXT_DAYS:
        day = last_obs + timedelta(days=FREQUENCY_NEXT_DAYS[frequency])
    else:
        raise ValueError(f"unsupported FRED frequency: {frequency}")
    return datetime.combine(day, publish_utc, tzinfo=UTC)


def fred_due(state: FredSeriesState, spec: SeriesSpec, now: datetime) -> bool:
    """True when a fetch may reveal a new observation, or when a retry is allowed."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must have a timezone")
    now = now.astimezone(UTC)
    if not state.rows:
        expected = now
    else:
        expected = next_publish_after(state.rows[-1].date, spec.frequency, spec.publish_utc) - spec.jitter
    if now < expected:
        return False
    if state.last_attempt_at is None:
        return True
    return now - state.last_attempt_at >= timedelta(minutes=spec.retry_minutes)


def merge_rows(
    previous: Iterable[FredObservation],
    fetched: Iterable[dict | FredObservation],
    *,
    today: date,
) -> list[FredObservation]:
    """Merge by date; a fetched row replaces the same date and future dates are rejected."""
    merged = {row.date: row for row in previous}
    for raw in fetched:
        row = raw if isinstance(raw, FredObservation) else FredObservation.model_validate(raw)
        if row.date > today:
            raise SnapshotError("FRED returned an observation ahead of the current clock")
        merged[row.date] = row
    return [merged[key] for key in sorted(merged)]


def empty_fred_snapshot(series_ids: Iterable[str]) -> FredSnapshot:
    return FredSnapshot(series={
        series_id: FredSeriesState() for series_id in series_ids
    })


def scope_fred_snapshot(state: FredSnapshot, series_ids: Iterable[str]) -> FredSnapshot:
    selected = set(series_ids)
    if not selected:
        raise SnapshotError("FRED selection is empty")
    scoped = state.model_copy(deep=True)
    for series_id in set(scoped.series) - selected:
        del scoped.series[series_id]
    for series_id in selected - set(scoped.series):
        scoped.series[series_id] = FredSeriesState()
    return scoped


def validate_clock(state: FredSnapshot, now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must have a timezone")
    now = now.astimezone(UTC)
    for series_id, series in state.series.items():
        if series.last_attempt_at is not None and series.last_attempt_at > now:
            raise SnapshotError(f"FRED snapshot has a future attempt for {series_id}")
        if any(row.date > now.date() for row in series.rows):
            raise SnapshotError(f"FRED snapshot has a future observation for {series_id}")
    return now


def _start_for(state: FredSeriesState, now: date) -> str:
    if not state.rows:
        return (now - timedelta(days=LOOKBACK_DAYS)).isoformat()
    return (state.rows[-1].date - timedelta(days=OVERLAP_DAYS)).isoformat()


def fetch_due(
    snapshot: FredSnapshot,
    fetch: Callable[[str, str], list[dict]],
    *,
    now: datetime,
    retry_minutes: int = DEFAULT_RETRY_MINUTES,
    only: Iterable[str] | None = None,
) -> FredSnapshot:
    """Return the snapshot after fetching the selected series.

    ``only`` skips the due check, for callers that already reserved the attempt.
    Raises on non-provider errors.
    """
    now = validate_clock(snapshot, now)
    completed = snapshot.model_copy(deep=True)
    targets = sorted(snapshot.series if only is None else set(only))
    for series_id in targets:
        state = completed.series[series_id]
        if only is None:
            spec = series_spec(series_id, retry_minutes=retry_minutes)
            if not fred_due(state, spec, now):
                continue
        start = _start_for(state, now.date())
        state.last_attempt_at = now
        try:
            rows = fetch(series_id, start)
        except ProviderError:
            state.access = "failed"
            continue
        state.rows = merge_rows(state.rows, rows, today=now.date())
        state.access = "available"
    return completed


def _load(path: Path) -> FredSnapshot:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return FredSnapshot()
    except UnicodeError as exc:
        raise SnapshotError("FRED snapshot is invalid; refresh was not attempted") from exc
    try:
        return FredSnapshot.model_validate_json(raw)
    except (ValidationError, ValueError) as exc:
        raise SnapshotError("FRED snapshot is invalid; refresh was not attempted") from exc


def _save(path: Path, state: FredSnapshot) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as stream:
        json.dump(state.model_dump(mode="json"), stream, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())
        temporary = stream.name
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def refresh_fred_snapshot(
    path: Path,
    fetch: Callable[[str, str], list[dict]],
    *,
    now: datetime,
    series_ids: Iterable[str],
    retry_minutes: int = DEFAULT_RETRY_MINUTES,
) -> FredSnapshot:
    """Attempt each due series at most once per retry window, across processes.

    The attempt time is persisted before the request, so a crash cannot turn a
    failed fetch into an immediate retry loop.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        durable = _load(path)
        state = scope_fred_snapshot(durable, series_ids)
        now = validate_clock(state, now)
        due = [
            series_id for series_id in state.series
            if fred_due(
                state.series[series_id],
                series_spec(series_id, retry_minutes=retry_minutes),
                now,
            )
        ]
        if not due:
            if state != durable:
                _save(path, state)
            return state
        for series_id in due:
            state.series[series_id].last_attempt_at = now
        _save(path, state)
        state = fetch_due(state, fetch, now=now, retry_minutes=retry_minutes, only=due)
        _save(path, state)
        return state
