"""Fail-closed FRED reservation on an explicitly provisioned checkpoint.

The attempt time is written before the request. A crash or a lost completion write
leaves the reservation consumed, so the retry window bounds requests across runners.
"""
from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from pydantic import ValidationError

from pipeline.storage.fred_snapshot import (
    DEFAULT_RETRY_MINUTES,
    FredSnapshot,
    SnapshotError,
    fetch_due,
    scope_fred_snapshot,
    series_spec,
    validate_clock,
    fred_due,
)
from pipeline.storage.github_checkpoint import (
    Checkpoint,
    CheckpointConflict,
    CheckpointError,
    CheckpointSnapshot,
)
from pipeline.storage.strict_json import load_json_rejecting_duplicates


class CloudFredRefreshError(CheckpointError):
    """A fetch or completion write failed after a usable snapshot was read."""

    def __init__(self, snapshot: FredSnapshot):
        super().__init__("Cloud FRED refresh failed; retained observations are degraded")
        self.snapshot = snapshot.model_copy(deep=True)


def _encode(snapshot: FredSnapshot) -> bytes:
    return snapshot.model_dump_json().encode("utf-8")


def _decode(content: bytes) -> FredSnapshot:
    try:
        raw = load_json_rejecting_duplicates(content)
        if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1:
            raise ValueError("invalid snapshot version")
        return FredSnapshot.model_validate_json(json.dumps(raw), strict=True)
    except (ValidationError, ValueError, TypeError, UnicodeError) as exc:
        raise SnapshotError("Cloud FRED snapshot is invalid; refresh was not attempted") from exc


def refresh_cloud_fred_snapshot(
    checkpoint: Checkpoint,
    path: str,
    fetch: Callable[[str, str], list[dict]],
    *,
    now: datetime,
    series_ids: Iterable[str],
    retry_minutes: int = DEFAULT_RETRY_MINUTES,
    persistence_attempts: int = 3,
) -> FredSnapshot:
    """Reserve due series, fetch them, then persist one completion write."""
    if persistence_attempts < 1:
        raise ValueError("persistence_attempts must be positive")
    original = checkpoint.read(path)
    snapshot = scope_fred_snapshot(_decode(original.content), series_ids)
    started = validate_clock(snapshot, now)
    due = [
        series_id for series_id in snapshot.series
        if fred_due(
            snapshot.series[series_id],
            series_spec(series_id, retry_minutes=retry_minutes),
            started,
        )
    ]
    if not due:
        return snapshot
    reserved = snapshot.model_copy(deep=True)
    for series_id in due:
        reserved.series[series_id].last_attempt_at = started
    try:
        checkpoint.write(original, _encode(reserved), "Reserve FRED observation fetches")
        confirmed = checkpoint.read(path)
        if _decode(confirmed.content) != reserved:
            raise CheckpointConflict("FRED reservation could not be confirmed")
        completed = fetch_due(
            reserved, fetch, now=started, retry_minutes=retry_minutes, only=due
        )
        return _complete(checkpoint, path, confirmed, reserved, completed, persistence_attempts)
    except (CheckpointError, SnapshotError) as exc:
        raise CloudFredRefreshError(reserved) from exc


def _complete(
    checkpoint: Checkpoint,
    path: str,
    confirmed: CheckpointSnapshot,
    reserved: FredSnapshot,
    completed: FredSnapshot,
    persistence_attempts: int,
) -> FredSnapshot:
    for attempt in range(persistence_attempts):
        try:
            checkpoint.write(confirmed, _encode(completed), "Complete FRED observation fetches")
            return completed
        except CheckpointError:
            durable = checkpoint.read(path)
            observed = _decode(durable.content)
            if observed == completed:
                return completed
            if observed != reserved:
                raise CheckpointConflict("FRED reservation changed during completion") from None
            if attempt + 1 == persistence_attempts:
                raise
            confirmed = durable
    raise AssertionError("unreachable")
