#!/usr/bin/env python3
"""Create a locally validated publication; this command does not deploy it."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)

from pipeline.collectors.macro import SERIES_GROUPS
from pipeline.publication import (
    PublicationError,
    load_candidate_files,
    publish_snapshot,
)
from pipeline.schemas.envelope import UTCDateTime
from pipeline.schemas.metadata import FreshnessDocument, SourcesDocument
from pipeline.schemas.risk import MarketRegime, RiskEvidenceState, RiskLevel
from pipeline.validation.ci_checks import run_all

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_CLIENT_FILES = frozenset({
    *(f"history/risk/{slice_name}.json" for slice_name in ("daily", "30d", "90d")),
    "metadata/freshness.json",
    "metadata/sources.json",
    "metadata/schema-version.json",
    "history/macro/index.json",
    *(f"history/macro/{group}.{slice_name}.json" for group in SERIES_GROUPS for slice_name in ("30d", "90d")),
})


class RiskHistoryPoint(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow", allow_inf_nan=False)
    date: str = Field(min_length=1)
    total_score: float = Field(ge=0, le=100)
    risk_level: RiskLevel | None = None
    regime: MarketRegime | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    dim_scores: dict[str, float] | None = None
    dim_evidence_states: dict[str, RiskEvidenceState] | None = None
    evidence_state: RiskEvidenceState | None = None

    @field_validator("risk_level", "regime", "confidence", "dim_scores", "dim_evidence_states", "evidence_state", mode="before")
    @classmethod
    def reject_explicit_null(cls, value):
        if value is None:
            raise ValueError("optional history fields cannot be null")
        return value

    @model_validator(mode="after")
    def validate_dimension_evidence(self):
        if self.dim_evidence_states is not None:
            available = {key for key, state in self.dim_evidence_states.items() if state != "insufficient_evidence"}
            if set(self.dim_scores or {}) != available:
                raise ValueError("dimension scores do not match evidence states")
        return self


class SchemaVersion(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow")
    schema_version: str = Field(min_length=1)
    updated_at: UTCDateTime | None = None

    @field_validator("updated_at", mode="before")
    @classmethod
    def reject_explicit_null(cls, value):
        if value is None:
            raise ValueError("updated_at cannot be null")
        return value


def validate_client_contracts(path: Path) -> bool:
    try:
        risk_payload = json.loads((path / "latest/risk.json").read_text(encoding="utf-8"))["payload"]
        dashboard_risk = json.loads((path / "latest/dashboard.json").read_text(encoding="utf-8"))["payload"]["risk"]
        if risk_payload != dashboard_risk:
            return False
        for slice_name in ("daily", "30d", "90d"):
            risk = json.loads((path / f"history/risk/{slice_name}.json").read_text(encoding="utf-8"))
            TypeAdapter(list[RiskHistoryPoint]).validate_python(risk)
        FreshnessDocument.model_validate_json((path / "metadata/freshness.json").read_bytes())
        SourcesDocument.model_validate_json((path / "metadata/sources.json").read_bytes())
        SchemaVersion.model_validate_json((path / "metadata/schema-version.json").read_bytes())
    except (OSError, ValueError, ValidationError, KeyError, TypeError):
        return False
    return True


def validate_candidate(path: Path) -> bool:
    if any(not (path / relative).is_file() for relative in REQUIRED_CLIENT_FILES):
        return False
    if not validate_client_contracts(path):
        return False
    report = run_all(path)
    if report.errors or report.blocking_warnings:
        return False
    try:
        result = subprocess.run(
            ["node", str(ROOT / "scripts" / "validate-json.mjs"), "--data-dir", str(path)],
            cwd=ROOT, capture_output=True, text=True, check=False, timeout=90,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def stage_candidate(source: Path, store: Path, *, parent_id: str | None, revision: str, created_at: str) -> dict:
    if source.is_symlink():
        raise PublicationError("Candidate must not be a symbolic link")
    if store.resolve().is_relative_to(source.resolve()):
        raise PublicationError("Publication store must be outside candidate data")
    return publish_snapshot(store, load_candidate_files(source), parent_id=parent_id,
                            producer_revision=revision, created_at=created_at, validator=validate_candidate)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--created-at", required=True, help="Fixed ISO timestamp for repeatable retries")
    parser.add_argument("--parent", default=None)
    args = parser.parse_args()
    try:
        pointer = stage_candidate(args.candidate, args.store, parent_id=args.parent,
                                  revision=args.revision, created_at=args.created_at)
    except (PublicationError, OSError, ValueError) as exc:
        print(f"Publication staging failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(pointer, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
