"""Fail-closed daily BLS reservation on an explicitly provisioned checkpoint."""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Literal, Protocol
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
    validate_snapshot_clock,
)
from pipeline.storage.github_checkpoint import (
    CheckpointConflict,
    CheckpointError,
    CheckpointSnapshot,
)


class CloudBlsRefreshError(CheckpointError):
    """Refresh failed after reading a usable snapshot; only old values are safe."""

    def __init__(self, snapshot: BlsSnapshot):
        super().__init__("Cloud BLS refresh failed; retained observations are degraded")
        self.snapshot = snapshot.model_copy(deep=True)
        for series in self.snapshot.series.values():
            series.status = "degraded" if series.rows else "missing"


class Checkpoint(Protocol):
    def read(self, path: str) -> CheckpointSnapshot: ...
    def write(self, snapshot: CheckpointSnapshot, content: bytes, message: str) -> str: ...


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
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate ledger field")
            result[key] = value
        return result

    try:
        raw = json.loads(content, object_pairs_hook=unique_pairs,
                         parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
        if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1:
            raise ValueError("invalid ledger version")
        snapshot = raw.get("snapshot")
        if not isinstance(snapshot, dict) or type(snapshot.get("version")) is not int or snapshot["version"] != 1:
            raise ValueError("invalid snapshot version")
        return CloudBlsLedger.model_validate_json(json.dumps(raw), strict=True)
    except (ValidationError, ValueError, TypeError, UnicodeError) as exc:
        raise SnapshotError("Cloud BLS ledger is invalid; refresh was not attempted") from exc


def refresh_cloud_bls_snapshot(
    checkpoint: Checkpoint, path: str, provider: BatchProvider, *,
    now: datetime | None = None, clock: Callable[[], datetime] | None = None,
    persistence_attempts: int = 3,
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
    if ledger.snapshot.attempted_on == started.date():
        return ledger.snapshot
    try:
        return _refresh_reserved(checkpoint, path, provider, ledger, original, started,
                                 current_time, persistence_attempts)
    except (CheckpointError, SnapshotError) as exc:
        raise CloudBlsRefreshError(ledger.snapshot) from exc


def _refresh_reserved(
    checkpoint: Checkpoint, path: str, provider: BatchProvider,
    ledger: CloudBlsLedger, original: CheckpointSnapshot, started: datetime,
    current_time: Callable[[], datetime], persistence_attempts: int,
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
        batch = provider.get_series_batch()
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
