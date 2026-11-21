"""Run report (architecture §3.7 run_report.py → artifacts/logs)."""

from __future__ import annotations

import json
from collections.abc import Collection
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_DIAGNOSTIC_COUNT = 10_000


class RunFailureCode(StrEnum):
    CONFIGURATION_INVALID = "configuration_invalid"
    COLLECTION_EXCEPTION = "collection_exception"
    ISOLATED_DATA_DIR_REQUIRED = "isolated_data_dir_required"
    PIPELINE_EXCEPTION = "pipeline_exception"
    RESULT_REJECTED = "result_rejected"
    DATASET_HEALTH_FAILED = "dataset_health_failed"


class RunRejectionCode(StrEnum):
    RISK_NO_USABLE_INDICATORS = "risk_no_usable_indicators"
    RISK_COMPUTATION_FAILED = "risk_computation_failed"
    RISK_WRITE_FAILED = "risk_write_failed"
    RISK_VALIDATION_FAILED = "risk_validation_failed"


class TreasuryRefreshStatus(StrEnum):
    NOT_CONFIGURED = "not_configured"
    COMPLETE = "complete"
    COMPLETE_DEGRADED = "complete_degraded"
    NO_OBSERVATIONS = "no_observations"
    REFRESH_FAILED = "refresh_failed"
    CHECKPOINT_UNAVAILABLE = "checkpoint_unavailable"


class TreasuryInputEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    status: TreasuryRefreshStatus
    dgs2_observation_count: int = Field(ge=0, le=MAX_DIAGNOSTIC_COUNT)
    dgs10_observation_count: int = Field(ge=0, le=MAX_DIAGNOSTIC_COUNT)


class RiskInputDiagnostics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    treasury: TreasuryInputEvidence
    macro_dgs2_has_value: bool
    macro_dgs10_has_value: bool
    usable_indicator_count: int = Field(ge=0, le=MAX_DIAGNOSTIC_COUNT)
    configured_indicator_count: int = Field(ge=0, le=MAX_DIAGNOSTIC_COUNT)

    @model_validator(mode="after")
    def indicator_counts_are_consistent(self) -> RiskInputDiagnostics:
        if self.usable_indicator_count > self.configured_indicator_count:
            raise ValueError("usable indicators exceed configured indicators")
        return self


def parse_risk_input_diagnostics(value: object) -> RiskInputDiagnostics | None:
    if not isinstance(value, dict):
        return None
    treasury = value.get("treasury")
    if not isinstance(treasury, dict):
        return None
    try:
        parsed_treasury = dict(treasury)
        parsed_treasury["status"] = TreasuryRefreshStatus(parsed_treasury["status"])
        parsed = {**value, "treasury": parsed_treasury}
        return RiskInputDiagnostics.model_validate(parsed, strict=True)
    except (KeyError, TypeError, ValueError):
        return None


def write_run_report(
    artifacts_dir: Path,
    *,
    command: str,
    ok: bool,
    durations: dict[str, float],
    provider_status: dict[str, Any],
    degraded: list[str],
    dataset_counts: dict[str, int],
    error: str | None = None,
    failed_datasets: list[str] | None = None,
    skipped_datasets: list[str] | None = None,
    degraded_datasets: list[str] | None = None,
    failure_code: RunFailureCode | None = None,
    rejection_code: RunRejectionCode | None = None,
    proxy_discounts: list[dict[str, Any]] | None = None,
    risk_evidence: dict[str, Any] | None = None,
    risk_input_diagnostics: RiskInputDiagnostics | None = None,
    written_latest_outputs: Collection[str] | None = None,
) -> Path:
    """Write artifacts/logs/run-report-{ts}.json.

    `failed_datasets` / `skipped_datasets` / `degraded_datasets` name the datasets whose
    outcome fell short of "published and current". `clean` is the single field an
    operator can read to tell a complete run from a partial one without opening a log:
    it is true only when the run succeeded and no dataset appears in any of those lists.

    `degraded` (free-text provider notes) also disqualifies a run. A note there without a
    corresponding entry in `degraded_datasets` still means something went wrong, and
    reporting the run as clean would be the lie this ticket exists to remove.
    """
    logs_dir = artifacts_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    failed = list(failed_datasets or [])
    skipped = list(skipped_datasets or [])
    degraded_names = list(degraded_datasets or [])
    clean = bool(ok) and not failed and not skipped and not degraded_names and not degraded
    report = {
        "command": command,
        "ok": ok,
        "clean": clean,
        "error": error,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "durations_seconds": {k: round(v, 3) for k, v in durations.items()},
        "provider_status": provider_status,
        "degraded": degraded,
        "failed_datasets": failed,
        "skipped_datasets": skipped,
        "degraded_datasets": degraded_names,
        "dataset_counts": dataset_counts,
        # #69: which trust discounts applied to which top-driver indicator, so a 0.64
        # (proxy × degraded provider) is never an unexplained number.
        "proxy_discounts": list(proxy_discounts or []),
        "risk_evidence": dict(risk_evidence or {}),
    }
    if risk_input_diagnostics is not None:
        report["risk_input_diagnostics"] = risk_input_diagnostics.model_dump(mode="json")
    if written_latest_outputs is not None:
        report["written_latest_outputs"] = sorted(written_latest_outputs)
    if failure_code is not None:
        report["failure_code"] = failure_code.value
    if rejection_code is not None:
        report["rejection_code"] = rejection_code.value
    path = logs_dir / f"run-report-{ts}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
