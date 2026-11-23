"""Durable Treasury year snapshots with a confirmed CAS before every request.

The checkpoint must be provisioned explicitly. A temporary local snapshot reuses
Treasury's validation and cadence; it never substitutes for missing remote state.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterable, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from pipeline.settings import Settings
from pipeline.storage.github_checkpoint import Checkpoint, CheckpointError
from pipeline.storage.strict_json import load_json_rejecting_duplicates
from pipeline.storage.treasury_snapshot import (
    Ledger,
    SnapshotError,
    TreasurySnapshot,
    YearCommit,
    YearProvider,
    refresh_treasury_snapshot,
)
from pipeline.providers.treasury import SERIES as TREASURY_SERIES


class CloudTreasuryLedger(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    nonce: str | None = None
    ledger: Ledger
    years: dict[int, YearCommit]

    @model_validator(mode="after")
    def validate_files(self) -> CloudTreasuryLedger:
        if self.nonce is not None and re.fullmatch(r"[0-9a-f]{32}", self.nonce) is None:
            raise ValueError("invalid reservation identity")
        if self.ledger.years and self.nonce is None:
            raise ValueError("missing reservation identity")
        if set(self.years) != {year for year, entry in self.ledger.years.items() if entry.has_data}:
            raise ValueError("Treasury year commits do not match manifest")
        for year, commit in self.years.items():
            entry = self.ledger.years[year]
            if commit.data.year != year:
                raise ValueError("Treasury year identity mismatch")
            if commit.ledger.attempted_at == entry.attempted_at:
                if entry != commit.ledger:
                    raise ValueError("Treasury year commit mismatch")
            elif (
                entry.attempted_at is None
                or commit.ledger.attempted_at > entry.attempted_at
                or entry.status not in {"pending", "failed"}
                or entry.revisions != commit.ledger.revisions
            ):
                raise ValueError("Treasury year reservation mismatch")
        return self


class CloudTreasuryRefreshError(CheckpointError):
    """Checkpoint failure after a valid read; only confirmed observations are safe."""

    def __init__(self, state: CloudTreasuryLedger, now: datetime):
        super().__init__("Cloud Treasury checkpoint refresh failed")
        required = list(range(now.year - 5, now.year + 1))
        self.snapshot = TreasurySnapshot(
            years={year: commit.data for year, commit in state.years.items()},
            required_years=required,
            degraded=True,
            errors={year: "Cloud Treasury checkpoint refresh failed" for year in required},
            history_rechecked=False,
        )


class CloudTreasuryPolicyError(CheckpointError):
    """A narrowed Treasury checkpoint could not be confirmed before collection."""


def initial_cloud_treasury_ledger() -> bytes:
    """Provisionable empty state; does not create a remote file or branch."""
    return CloudTreasuryLedger(ledger=Ledger(), years={}).model_dump_json().encode("utf-8")


def _decode(content: bytes) -> CloudTreasuryLedger:
    try:
        raw = load_json_rejecting_duplicates(content)
        if not isinstance(raw, dict) or type(raw.get("version")) is not int or raw["version"] != 1:
            raise ValueError("invalid checkpoint version")
        ledger = raw.get("ledger")
        if (
            not isinstance(ledger, dict)
            or type(ledger.get("version")) is not int
            or ledger["version"] != 1
        ):
            raise ValueError("invalid manifest version")
        return CloudTreasuryLedger.model_validate_json(json.dumps(raw), strict=True)
    except (ValidationError, ValueError, TypeError, UnicodeError) as exc:
        raise SnapshotError("Cloud Treasury checkpoint invalid; refresh was not attempted") from exc


def refresh_cloud_treasury_snapshot(
    checkpoint: Checkpoint,
    path: str,
    provider: YearProvider,
    *,
    now: datetime | None = None,
    refresh_history: bool = False,
    series_ids: Iterable[str] | None = None,
    refresh_interval_minutes: int | None = None,
    settings: Settings | None = None,
) -> TreasurySnapshot:
    """Restore remote state, reserve each due year, then persist its response.

    Observation validation reuses the local snapshot. Current-year polling follows
    the Treasury source-policy cadence; unfinished/rechecked history is at most daily.
    A failed/uncertain CAS never authorizes a request. No automatic retry can repeat
    a request: an applied pending reservation remains consumed across runners.
    """
    if series_ids is None or refresh_interval_minutes is None:
        policy = (settings or Settings()).load_public_source_policy()
        source = policy.sources.get("treasury")
        if source is None or source.provider != "treasury":
            if series_ids is None:
                raise ValueError("Treasury source policy is not configured")
        else:
            if series_ids is None:
                series_ids = source.macro_series
            if refresh_interval_minutes is None:
                refresh_interval_minutes = source.cadence_minutes
    refresh_interval_minutes = refresh_interval_minutes or 15
    now = now if now is not None else datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must have a timezone")
    now = now.astimezone(UTC)
    confirmed = checkpoint.read(path)
    durable = _decode(confirmed.content)
    selected = set(TREASURY_SERIES if series_ids is None else series_ids)
    if not selected.issubset(TREASURY_SERIES):
        raise SnapshotError("Treasury selection contains an unsupported series")
    scope_pending = any(
        not set(commit.data.series).issubset(selected)
        for commit in durable.years.values()
    )
    for commit in durable.years.values():
        if datetime.fromisoformat(commit.data.checked_at) > now + timedelta(seconds=30) or any(
            row.date > now.date() for rows in commit.data.series.values() for row in rows
        ):
            raise SnapshotError("Cloud Treasury observations are ahead of the current clock")
    with TemporaryDirectory(prefix="treasury-checkpoint-") as directory_name:
        directory = Path(directory_name)
        (directory / "manifest.json").write_text(durable.ledger.model_dump_json(), encoding="utf-8")
        for year, commit in durable.years.items():
            (directory / f"{year}.json").write_text(commit.model_dump_json(), encoding="utf-8")

        def persist() -> None:
            nonlocal confirmed, durable, scope_pending
            candidate = CloudTreasuryLedger(
                nonce=uuid4().hex,
                ledger=Ledger.model_validate_json((directory / "manifest.json").read_bytes()),
                years={
                    int(file.stem): YearCommit.model_validate_json(file.read_bytes())
                    for file in directory.glob("[0-9][0-9][0-9][0-9].json")
                },
            )
            try:
                checkpoint.write(
                    confirmed,
                    candidate.model_dump_json().encode("utf-8"),
                    "Checkpoint Treasury year request",
                )
                observed = checkpoint.read(path)
                if _decode(observed.content) != candidate:
                    raise CheckpointError("Treasury checkpoint reservation could not be confirmed")
            except CheckpointError as exc:
                if scope_pending:
                    raise CloudTreasuryPolicyError(
                        "Treasury policy scope could not be confirmed"
                    ) from exc
                raise
            confirmed, durable = observed, candidate
            scope_pending = False

        try:
            return refresh_treasury_snapshot(
                directory,
                provider,
                now=now,
                refresh_history=refresh_history,
                refresh_interval_minutes=refresh_interval_minutes,
                series_ids=series_ids,
                persist=persist,
            )
        except CloudTreasuryPolicyError:
            raise
        except CheckpointError as exc:
            raise CloudTreasuryRefreshError(durable, now) from exc
