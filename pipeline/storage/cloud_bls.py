"""Fail-closed daily BLS reservation on an explicitly provisioned checkpoint."""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from pipeline.providers.base import ProviderError
from pipeline.storage.bls_snapshot import (
    BatchProvider,
    BlsSnapshot,
    SnapshotError,
    begin_bls_attempt,
    complete_bls_attempt,
    empty_bls_snapshot,
    scope_bls_snapshot,
    validate_snapshot_clock,
)
from pipeline.providers.bls import SERIES as BLS_SERIES
from pipeline.storage.github_checkpoint import (
    Checkpoint,
    CheckpointConflict,
    CheckpointError,
    CheckpointSnapshot,
)
from pipeline.storage.strict_json import load_json_rejecting_duplicates

_PRE_CIVPART_BLS_SERIES = frozenset({"CPIAUCSL", "CPILFESL", "PAYEMS", "UNRATE"})


class CloudBlsRefreshError(CheckpointError):
    """Refresh failed after reading a usable snapshot; only old values are safe."""

    def __init__(self, snapshot: BlsSnapshot):
        super().__init__("Cloud BLS refresh failed; retained observations are degraded")
        self.snapshot = snapshot.model_copy(deep=True)
        for series in self.snapshot.series.values():
            series.status = "degraded" if series.rows else "missing"


class CloudBlsLedger(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1]
    nonce: str | None
    snapshot: BlsSnapshot

    @model_validator(mode="after")
    def validate_reservation(self) -> CloudBlsLedger:
        if (self.nonce is None) != (self.snapshot.attempt_status == "never"):
            raise ValueError("reservation identity and attempt state do not match")
        if self.nonce is not None and re.fullmatch(r"[0-9a-f]{32}", self.nonce) is None:
            raise ValueError("invalid reservation identity")
        return self


def initial_cloud_bls_ledger() -> bytes:
    """Return provisionable content. Never create a remote branch or file."""
    return _encode(CloudBlsLedger(version=1, nonce=None, snapshot=empty_bls_snapshot()))


def _encode(ledger: CloudBlsLedger) -> bytes:
    return ledger.model_dump_json().encode("utf-8")


def _decode(content: bytes) -> CloudBlsLedger:
    try:
        raw = load_json_rejecting_duplicates(content)
        if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1:
            raise ValueError("invalid ledger version")
        snapshot = raw.get("snapshot")
        if not isinstance(snapshot, dict) or type(snapshot.get("version")) is not int or snapshot["version"] != 1:
            raise ValueError("invalid snapshot version")
        series = snapshot.get("series")
        if (
            isinstance(series, dict)
            and set(series) == _PRE_CIVPART_BLS_SERIES
            and set(BLS_SERIES) == _PRE_CIVPART_BLS_SERIES | {"CIVPART"}
        ):
            raw = dict(raw)
            snapshot = dict(snapshot)
            snapshot["series"] = {
                **series,
                "CIVPART": {"rows": [], "fetched_at": None, "status": "missing"},
            }
            raw["snapshot"] = snapshot
        return CloudBlsLedger.model_validate_json(json.dumps(raw), strict=True)
    except (ValidationError, ValueError, TypeError, UnicodeError) as exc:
        raise SnapshotError("Cloud BLS ledger is invalid; refresh was not attempted") from exc


def refresh_cloud_bls_snapshot(
    checkpoint: Checkpoint, path: str, provider: BatchProvider, *,
    now: datetime | None = None, clock: Callable[[], datetime] | None = None,
    persistence_attempts: int = 3, series_ids: Iterable[str] | None = None,
) -> BlsSnapshot:
    """Reserve before calling BLS; ambiguous reservations never authorize a call.

    A failed completion write leaves the durable pending reservation consumed. A
    later invocation returns that degraded snapshot rather than repeating BLS.
    """
    if persistence_attempts < 1:
        raise ValueError("persistence_attempts must be positive")
    current_time = clock or (lambda: datetime.now(UTC))
    started = now if now is not None else current_time()
    original = checkpoint.read(path)
    ledger = _decode(original.content)
    started = validate_snapshot_clock(ledger.snapshot, started)
    selected = set(BLS_SERIES if series_ids is None else series_ids)
    scoped = scope_bls_snapshot(ledger.snapshot, selected)
    if scoped != ledger.snapshot:
        ledger = CloudBlsLedger(version=ledger.version, nonce=ledger.nonce, snapshot=scoped)
        checkpoint.write(original, _encode(ledger), "Remove unapproved BLS series")
        original = checkpoint.read(path)
        if _decode(original.content) != ledger:
            raise CheckpointError("BLS policy scope could not be confirmed")
    if not selected or ledger.snapshot.attempted_on == started.date():
        return ledger.snapshot
    try:
        return _refresh_reserved(checkpoint, path, provider, ledger, original, started,
                                 current_time, persistence_attempts, selected)
    except (CheckpointError, SnapshotError) as exc:
        raise CloudBlsRefreshError(ledger.snapshot) from exc


def _refresh_reserved(
    checkpoint: Checkpoint, path: str, provider: BatchProvider,
    ledger: CloudBlsLedger, original: CheckpointSnapshot, started: datetime,
    current_time: Callable[[], datetime], persistence_attempts: int,
    series_ids: set[str],
) -> BlsSnapshot:
    reserved = CloudBlsLedger(version=1, nonce=uuid4().hex,
                              snapshot=begin_bls_attempt(ledger.snapshot, started))
    checkpoint.write(original, _encode(reserved), "Reserve daily BLS request")
    confirmed = checkpoint.read(path)
    durable = _decode(confirmed.content)
    if durable != reserved:
        raise CheckpointError("BLS reservation could not be confirmed")
    request_time = validate_snapshot_clock(reserved.snapshot, current_time())
    if request_time.date() != started.date():
        return reserved.snapshot
    try:
        batch = (
            provider.get_series_batch()
            if series_ids == set(BLS_SERIES)
            else provider.get_series_batch(sorted(series_ids))
        )
        if set(batch) - series_ids:
            raise SnapshotError("BLS provider returned an unrequested dashboard series")
    except ProviderError:
        completed = reserved.snapshot.model_copy(deep=True)
        completed.attempt_status = "failed"
    else:
        completed = complete_bls_attempt(reserved.snapshot, batch, request_time)
    result = CloudBlsLedger(version=1, nonce=reserved.nonce, snapshot=completed)
    for attempt in range(persistence_attempts):
        try:
            checkpoint.write(confirmed, _encode(result), "Complete daily BLS request")
            return completed
        except CheckpointError:
            confirmed = checkpoint.read(path)
            durable = _decode(confirmed.content)
            if durable == result:
                return completed
            if durable != reserved:
                raise CheckpointConflict("BLS reservation changed during completion") from None
            if attempt + 1 == persistence_attempts:
                raise
    raise AssertionError("unreachable")
