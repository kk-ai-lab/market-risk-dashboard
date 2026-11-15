"""Local BLS refresh ledger and last usable monthly observations.

This ledger protects concurrent processes that share one filesystem. A cloud runner must
restore it before a run and persist the attempt before the network call to preserve its
budget across machines.
"""
from __future__ import annotations

import fcntl
import json
import os
import tempfile
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal, Protocol

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    FiniteFloat,
    ValidationError,
    model_validator,
)

from pipeline.providers.base import ProviderError
from pipeline.providers.bls import SERIES


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: date
    value: FiniteFloat


class SeriesSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[Observation]
    fetched_at: AwareDatetime | None
    status: Literal["fresh", "degraded", "missing"]

    @model_validator(mode="after")
    def validate_state(self) -> SeriesSnapshot:
        if bool(self.rows) != (self.fetched_at is not None):
            raise ValueError("rows and fetched_at must be present together")
        if self.status == "missing" and self.rows or self.status != "missing" and not self.rows:
            raise ValueError("series status does not match rows")
        if any(row.date.day != 1 for row in self.rows):
            raise ValueError("BLS observations must identify a month")
        dates = [row.date for row in self.rows]
        if dates != sorted(set(dates)):
            raise ValueError("BLS observation months must be unique and sorted")
        return self


class BlsSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1]
    attempted_on: date | None
    attempt_status: Literal["never", "pending", "complete", "failed"]
    series: dict[str, SeriesSnapshot]

    @model_validator(mode="after")
    def validate_state(self) -> BlsSnapshot:
        if set(self.series) != set(SERIES):
            raise ValueError("snapshot must contain all BLS series")
        if (self.attempted_on is None) != (self.attempt_status == "never"):
            raise ValueError("attempt date and status do not match")
        if self.attempt_status == "never" and any(
            snapshot.status != "missing" for snapshot in self.series.values()
        ):
            raise ValueError("unattempted snapshot cannot contain observations")
        if self.attempt_status in {"pending", "failed"} and any(
            snapshot.status == "fresh" for snapshot in self.series.values()
        ):
            raise ValueError("unfinished or failed attempt cannot contain fresh series")
        if any(
            snapshot.status == "fresh"
            and snapshot.fetched_at.astimezone(UTC).date() != self.attempted_on
            for snapshot in self.series.values()
        ):
            raise ValueError("fresh series must be fetched on the UTC attempt date")
        return self


class BatchProvider(Protocol):
    def get_series_batch(self) -> dict[str, list[dict]]: ...


class SnapshotError(Exception):
    """An unreadable snapshot must not silently reset the daily request budget."""


def empty_bls_snapshot() -> BlsSnapshot:
    return BlsSnapshot(version=1, attempted_on=None, attempt_status="never", series={
        key: SeriesSnapshot(rows=[], fetched_at=None, status="missing") for key in SERIES
    })


def _load(path: Path) -> BlsSnapshot:
    try:
        data = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return empty_bls_snapshot()
    except UnicodeError as exc:
        raise SnapshotError("BLS snapshot is invalid; refresh was not attempted") from exc
    try:
        return BlsSnapshot.model_validate_json(data)
    except (ValidationError, ValueError) as exc:
        raise SnapshotError("BLS snapshot is invalid; refresh was not attempted") from exc


def _save(path: Path, state: BlsSnapshot) -> None:
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
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


def validate_snapshot_clock(state: BlsSnapshot, now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must have a timezone")
    now = now.astimezone(UTC)
    if state.attempted_on is not None and state.attempted_on > now.date():
        raise SnapshotError("BLS snapshot has a future attempt date; refresh was not attempted")
    if any(snapshot.fetched_at is not None and snapshot.fetched_at > now for snapshot in state.series.values()):
        raise SnapshotError("BLS snapshot has a future fetch time; refresh was not attempted")
    return now


def begin_bls_attempt(state: BlsSnapshot, now: datetime) -> BlsSnapshot:
    now = validate_snapshot_clock(state, now)
    if state.attempted_on == now.date():
        raise SnapshotError("BLS daily attempt already recorded")
    state = state.model_copy(deep=True)
    for snapshot in state.series.values():
        snapshot.status = "degraded" if snapshot.rows else "missing"
    state.attempted_on = now.date()
    state.attempt_status = "pending"
    return state


def complete_bls_attempt(state: BlsSnapshot, batch: dict[str, list[dict]], now: datetime) -> BlsSnapshot:
    now = validate_snapshot_clock(state, now)
    if state.attempt_status != "pending":
        raise SnapshotError("BLS attempt is not pending")
    state = state.model_copy(deep=True)
    for key in SERIES:
        raw_rows = batch.get(key, [])
        if not raw_rows:
            continue
        try:
            candidate = SeriesSnapshot(rows=raw_rows, fetched_at=now, status="fresh")
            if any(row.date > now.date().replace(day=1) for row in candidate.rows):
                continue
            previous = state.series[key].rows
            first_year = now.year - (4 if key in {"CPIAUCSL", "CPILFESL"} else 5)
            first_month = 2 if key == "PAYEMS" else 1
            window_start = date(first_year, first_month, 1)
            returned_dates = {row.date for row in candidate.rows}
            if previous and (candidate.rows[-1].date < previous[-1].date or any(
                row.date >= window_start and row.date not in returned_dates for row in previous
            )):
                continue
        except (ValidationError, TypeError, ValueError):
            continue
        state.series[key] = candidate
    state.attempt_status = "complete"
    return state


def refresh_bls_snapshot(path: Path, provider: BatchProvider, *, now: datetime) -> BlsSnapshot:
    """Attempt at most once per UTC day while all callers share this ledger and lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = _load(path)
        now = validate_snapshot_clock(state, now)
        if state.attempted_on == now.date():
            return state
        state = begin_bls_attempt(state, now)
        _save(path, state)
        try:
            batch = provider.get_series_batch()
        except ProviderError:
            state.attempt_status = "failed"
            _save(path, state)
            return state
        state = complete_bls_attempt(state, batch, now)
        _save(path, state)
        return state
