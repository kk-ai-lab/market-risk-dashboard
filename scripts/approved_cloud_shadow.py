from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from pipeline.settings import Settings
from scripts.stage_publication import stage_candidate

ROOT = Path(__file__).resolve().parents[1]


class ShadowError(RuntimeError):
    pass


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ShadowError(f"Shadow output is missing or invalid: {path.name}") from exc
    if not isinstance(value, dict):
        raise ShadowError(f"Shadow output is not a JSON object: {path.name}")
    return value


def _revision() -> str:
    value = os.environ.get("GITHUB_SHA")
    if value is None:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        value = result.stdout.strip() if result.returncode == 0 else ""
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise ShadowError("A full Git revision is required to stage the candidate")
    return value


def _summary(report: dict, freshness: dict, sources: dict, macro: dict, generation_id: str) -> str:
    statuses = Counter(
        str(item.get("status", "unknown"))
        for item in freshness.get("datasets", {}).values()
        if isinstance(item, dict)
    )
    evidence = report.get("risk_evidence") or {}
    state = str(evidence.get("state", "unknown"))
    coverage = evidence.get("effective_coverage")
    coverage_text = f"{coverage * 100:.1f}%" if isinstance(coverage, (int, float)) else "unknown"
    interval = (evidence.get("score_lower_bound"), evidence.get("score_upper_bound"))
    if all(isinstance(value, (int, float)) for value in interval):
        risk_text = f"{state}, {coverage_text} effective coverage, interval {interval[0]:.1f}–{interval[1]:.1f}"
    else:
        risk_text = f"{state}, {coverage_text} effective coverage"
    if state == "insufficient_evidence":
        risk_text += "; point estimate withheld"

    providers = {
        item.get("provider")
        for domain in sources.get("domains", {}).values()
        if isinstance(domain, dict)
        for item in domain.get("providers", [])
        if isinstance(item, dict)
        and isinstance(item.get("provider"), str)
        and item["provider"] not in {"mixed", "unavailable"}
    }
    providers.update(
        indicator["source"]
        for group in macro.values()
        if isinstance(group, list)
        for indicator in group
        if isinstance(indicator, dict)
        and isinstance(indicator.get("source"), str)
        and indicator["source"] != "unavailable"
    )
    provider_names = ", ".join(sorted(providers, key=str.lower)) if providers else "none"
    lines = [
        "## Approved-source cloud shadow",
        "",
        f"- Collection: `ok={report.get('ok')}`, `clean={report.get('clean')}`, "
        f"`{report.get('durations_seconds', {}).get('total', 'unknown')}s`.",
        f"- Providers used: {provider_names}.",
        f"- Dataset statuses: {', '.join(f'{key}={value}' for key, value in sorted(statuses.items()))}.",
        f"- Failed datasets: {', '.join(report.get('failed_datasets', [])) or 'none'}.",
        f"- Policy-excluded datasets: {', '.join(report.get('skipped_datasets', [])) or 'none'}.",
        f"- Risk evidence: {risk_text}.",
        f"- Candidate: `{generation_id}`, validated by Python and Node; not deployed.",
    ]
    dimensions = evidence.get("dimensions", [])
    if dimensions:
        lines.extend(
            ["", "| Risk dimension | Evidence | Coverage | Missing inputs |", "|---|---|---:|---|"]
        )
        for dimension in dimensions:
            if not isinstance(dimension, dict):
                continue
            value = dimension.get("effective_coverage", dimension.get("coverage"))
            percent = f"{value * 100:.1f}%" if isinstance(value, (int, float)) else "unknown"
            missing = ", ".join(dimension.get("missing_indicators", [])) or "none"
            lines.append(
                f"| {dimension.get('key', 'unknown')} | {dimension.get('state', 'unknown')} "
                f"| {percent} | {missing} |"
            )
    return "\n".join(lines) + "\n"


def run_shadow(*, run_process: Callable = subprocess.run) -> tuple[str, Path]:
    revision = _revision()
    created_at = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
    temporary_root = Path(os.environ.get("RUNNER_TEMP", tempfile.gettempdir()))
    root = Path(tempfile.mkdtemp(prefix="approved-cloud-shadow-", dir=temporary_root))
    data_dir = root / "data"
    artifacts_dir = root / "artifacts"
    store = root / "publication"
    env = {key: value for key, value in os.environ.items() if not key.startswith("DATA_")}
    env.update(
        {
            "DATA_APPROVED_SOURCES_ONLY": "true",
            "DATA_COINMETRICS_ENABLED": "true",
            "DATA_BLS_ENABLED": "true",
            "DATA_BLS_CLOUD_ENABLED": "false",
            "DATA_TREASURY_ENABLED": "true",
            "DATA_TREASURY_CLOUD_ENABLED": "false",
            "DATA_DATA_DIR": str(data_dir),
            "DATA_ARTIFACTS_DIR": str(artifacts_dir),
        }
    )
    result = run_process(
        [sys.executable, "-m", "pipeline.run", "--full"],
        cwd=ROOT,
        env=env,
        check=False,
        timeout=600,
    )
    if result.returncode != 0:
        raise ShadowError(f"Approved-source collection failed with exit code {result.returncode}")

    report_paths = sorted((artifacts_dir / "logs").glob("run-report-*.json"))
    if not report_paths:
        raise ShadowError("Collection did not write a run report")
    report = _read_json(report_paths[-1])
    freshness = _read_json(data_dir / "metadata" / "freshness.json")
    sources = _read_json(data_dir / "metadata" / "sources.json")
    macro = _read_json(data_dir / "latest" / "macro.json").get("payload", {})
    pointer = stage_candidate(
        data_dir, store, parent_id=None, revision=revision, created_at=created_at
    )
    used_providers = {
        item.get("provider")
        for domain in sources.get("domains", {}).values()
        if isinstance(domain, dict)
        for item in domain.get("providers", [])
        if isinstance(item, dict)
    }
    policy = Settings(_env_file=None).load_public_source_policy()
    notices = ["# Shadow candidate attribution", ""]
    for source in policy.sources.values():
        if source.provider in used_providers:
            notices.append(
                f"- {source.attribution} License: [{source.license_id}]({source.license_url})."
            )
    (root / "NOTICE.md").write_text("\n".join(notices) + "\n", encoding="utf-8")
    summary = _summary(report, freshness, sources, macro, pointer["generation_id"])

    destination = os.environ.get("GITHUB_STEP_SUMMARY")
    if destination:
        with Path(destination).open("a", encoding="utf-8") as handle:
            handle.write(summary)
    print(summary, end="")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"artifact_path={root}\n")
    return summary, root


def main() -> int:
    try:
        run_shadow()
    except (ShadowError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"Approved-source shadow failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
