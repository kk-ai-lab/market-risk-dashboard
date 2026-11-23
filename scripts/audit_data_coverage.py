#!/usr/bin/env python3
"""Report public-snapshot coverage and catch fields sourced only by personal-use grants."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pipeline.collectors.macro import DEFAULT_SERIES, SERIES_GROUPS
from pipeline.collectors.news import news_translation_coverage
from pipeline.schemas.news import NewsDataset
from pipeline.settings import Settings


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _payload(data_dir: Path, name: str) -> dict[str, Any]:
    envelope = _read_json(data_dir / "latest" / f"{name}.json")
    payload = envelope.get("payload")
    return payload if isinstance(payload, dict) else {}


def _personal_source_markers(value: Any, path: str = "$") -> list[str]:
    if isinstance(value, str):
        normalized = value.casefold()
        return [f"{path}={value}"] if "yahoo" in normalized or "yfinance" in normalized else []
    if isinstance(value, list):
        return [marker for index, item in enumerate(value) for marker in _personal_source_markers(item, f"{path}[{index}]")]
    if isinstance(value, dict):
        return [
            marker
            for key, item in value.items()
            for marker in _personal_source_markers(item, f"{path}.{key}")
        ]
    return []


def _macro_coverage(data_dir: Path, settings: Settings) -> dict[str, Any]:
    payload = _payload(data_dir, "macro")
    public_plan = settings.load_public_source_policy().collection_plan(public_output=True)
    series_grants = {
        series_id: grant
        for grant in public_plan.grants
        for series_id in grant.macro_series
    }
    series: dict[str, dict[str, Any]] = {}
    unauthorized: list[str] = []
    for group in SERIES_GROUPS:
        for row in payload.get(group, []):
            if not isinstance(row, dict):
                continue
            key = str(row.get("key", "")).upper()
            grant = series_grants.get(key)
            value = row.get("value")
            missing_reason = None
            if grant is None:
                missing_reason = "no public-use grant"
                if value is not None:
                    unauthorized.append(key)
            elif value is None:
                missing_reason = str(row.get("status") or "no observation")
            series[key] = {
                "group": group,
                "status": str(row.get("status") or ("missing" if value is None else "unknown")),
                "value": value,
                "source": row.get("source"),
                "observation_date": row.get("observation_date") or row.get("updated_at"),
                "attribution": row.get("attribution"),
                "missing_reason": missing_reason,
            }
    public_series = set(series_grants)
    unauthorized_history = _unauthorized_macro_history(data_dir, set(DEFAULT_SERIES) - public_series)
    missing = sorted(key for key, item in series.items() if item["value"] is None)
    missing_attributions = sorted(
        key for key, item in series.items()
        if item["value"] is not None and not item["attribution"]
    )
    return {
        "expected": len(DEFAULT_SERIES),
        "observed": sum(item["value"] is not None for item in series.values()),
        "missing": missing,
        "series": series,
        "unauthorized_values": sorted(unauthorized),
        "unauthorized_history": sorted(unauthorized_history),
        "missing_attributions": missing_attributions,
    }


def _unauthorized_macro_history(data_dir: Path, unauthorized_series: set[str]) -> list[str]:
    macro_history = data_dir / "history" / "macro"
    found: set[str] = set()
    for archive_path in macro_history.rglob("*.json"):
        series_id = archive_path.parent.name.upper()
        if series_id in unauthorized_series and archive_path.name in {"daily.json", "30d.json", "90d.json"}:
            rows = json.loads(archive_path.read_text(encoding="utf-8"))
            if isinstance(rows, list) and rows:
                found.add(series_id)
        if archive_path.name == "index.json":
            index = json.loads(archive_path.read_text(encoding="utf-8"))
            if series_id in unauthorized_series and isinstance(index, dict) and (index.get("count", 0) or index.get("last_observation")):
                found.add(series_id)
            series_rows = index.get("series", {}) if isinstance(index, dict) else {}
            if isinstance(series_rows, dict):
                for key, row in series_rows.items():
                    normalized_id = str(key).upper()
                    if normalized_id in unauthorized_series and isinstance(row, dict) and (row.get("count", 0) or row.get("last_observation")):
                        found.add(normalized_id)

    for bundle_path in (*macro_history.rglob("*.30d.json"), *macro_history.rglob("*.90d.json")):
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        if not isinstance(bundle, dict):
            continue
        for series_id, rows in bundle.items():
            normalized_id = str(series_id).upper()
            if normalized_id not in unauthorized_series:
                continue
            if (isinstance(rows, dict) and (rows.get("d") or rows.get("v"))) or (isinstance(rows, list) and rows):
                found.add(normalized_id)

    return sorted(found)


def _unwithheld_risk_scores(payload: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    insufficient = payload.get("evidence_state") == "insufficient_evidence"
    if insufficient:
        for key in ("total_score", "risk_level", "trend_1d", "trend_1w", "trend_1m"):
            if payload.get(key) is not None:
                violations.append(f"payload.{key}")
        if payload.get("top_drivers"):
            violations.append("payload.top_drivers")

    dimensions = payload.get("dimensions", [])
    if not isinstance(dimensions, list):
        return violations
    for dimension in dimensions:
        if not isinstance(dimension, dict):
            continue
        key = str(dimension.get("key", "unknown"))
        if dimension.get("evidence_state") == "insufficient_evidence" and dimension.get("score") is not None:
            violations.append(f"dimensions.{key}.score")
        for indicator in dimension.get("indicators", []):
            if not isinstance(indicator, dict):
                continue
            if indicator.get("value") is None and indicator.get("risk_score") is not None:
                violations.append(f"dimensions.{key}.indicators.{indicator.get('key', 'unknown')}.risk_score")
            if indicator.get("value") is None and indicator.get("score_included") is not False:
                violations.append(f"dimensions.{key}.indicators.{indicator.get('key', 'unknown')}.score_included")
    return violations


def _unwithheld_risk_history(data_dir: Path, payload: dict[str, Any]) -> list[str]:
    violations: list[str] = []
    unavailable_indicators = {
        str(indicator.get("key"))
        for dimension in payload.get("dimensions", [])
        if isinstance(dimension, dict)
        for indicator in dimension.get("indicators", [])
        if isinstance(indicator, dict) and indicator.get("value") is None
    }
    for path in (data_dir / "history" / "risk").glob("*.json"):
        if path.name == "index.json":
            continue
        rows = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or row.get("evidence_state") != "insufficient_evidence":
                continue
            prefix = f"{path.name}[{index}]"
            for key in ("total_score", "risk_level"):
                if row.get(key) is not None:
                    violations.append(f"{prefix}.{key}")
            scores = row.get("indicator_scores", {})
            if isinstance(scores, dict):
                for key in unavailable_indicators:
                    if key in scores and scores[key] is not None:
                        violations.append(f"{prefix}.indicator_scores.{key}")
    return violations


def audit(data_dir: Path, settings: Settings) -> dict[str, Any]:
    freshness = _read_json(data_dir / "metadata" / "freshness.json")
    dataset_status = freshness.get("datasets", {})
    if not isinstance(dataset_status, dict):
        raise ValueError("freshness datasets must be an object")

    universe = settings.load_universe_config()
    themes = settings.load_themes_config()
    public_plan = settings.load_public_source_policy().collection_plan(public_output=True)
    equity_payload = _payload(data_dir, "equities")
    sector_payload = _payload(data_dir, "sectors")
    commodity_payload = _payload(data_dir, "commodities")
    energy_payload = _payload(data_dir, "energy")
    calendar_payload = _payload(data_dir, "calendar")
    news_payload = _payload(data_dir, "news")
    news_dataset = NewsDataset.model_validate(news_payload)
    translated_news, total_news = news_translation_coverage(news_dataset)
    risk_payload = _payload(data_dir, "risk")
    dashboard_payload = _payload(data_dir, "dashboard")

    equity_rows = equity_payload.get("assets", [])
    sector_rows = sector_payload.get("sectors", [])
    theme_rows = sector_payload.get("themes", [])
    commodity_rows = commodity_payload.get("assets", [])
    energy_rows = energy_payload.get("observations", [])
    calendar_rows = calendar_payload.get("events", [])
    news_rows = news_payload.get("items", [])
    risk_dimensions = risk_payload.get("dimensions", [])

    source_files = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(data_dir.rglob("*.json"))
        if path != data_dir / "metadata" / "freshness.json"
    ]
    unauthorized_providers = sorted({
        marker
        for source_file in source_files
        for marker in _personal_source_markers(source_file)
    })
    diagnostics_without_detail = sorted(
        name
        for name, item in dataset_status.items()
        if isinstance(item, dict)
        and item.get("status") in {"missing", "stale", "degraded"}
        and not str(item.get("reason", {}).get("detail", "")).strip()
    )

    brief_files = sorted(
        str(path.relative_to(data_dir))
        for path in data_dir.rglob("analysis.*.json")
        if path.is_file()
    )
    macro = _macro_coverage(data_dir, settings)
    public_equity_targets = sorted({
        symbol
        for grant in public_plan.grants
        if "equities" in grant.datasets or "a_share" in grant.datasets
        for symbol in grant.market_symbols | grant.instrument_symbols
    })
    public_sector_grants = [
        grant.source_id for grant in public_plan.grants
        if "sectors" in grant.datasets or "themes" in grant.datasets
    ]
    report = {
        "data_dir": str(data_dir),
        "updated_at": freshness.get("updated_at"),
        "datasets": {
            name: {
                "status": item.get("status"),
                "reason": item.get("reason"),
                "updated_at": item.get("updated_at"),
            }
            for name, item in sorted(dataset_status.items())
            if isinstance(item, dict)
        },
        "macro": macro,
        "equities": {
            "configured_us": len(universe.us_equities),
            "configured_a_shares": len(universe.a_share_memory),
            "published": len(equity_rows) if isinstance(equity_rows, list) else 0,
            "public_grant_targets": public_equity_targets,
            "missing_reason": None if public_equity_targets else "no public-use price-data grant",
        },
        "sectors": {
            "configured_sectors": len(themes.sectors),
            "configured_themes": len(themes.themes),
            "published_sectors": len(sector_rows) if isinstance(sector_rows, list) else 0,
            "published_themes": len(theme_rows) if isinstance(theme_rows, list) else 0,
            "public_source_grants": public_sector_grants,
            "missing_reason": None if public_sector_grants else "no public-use sector-data grant",
        },
        "commodities": len(commodity_rows) if isinstance(commodity_rows, list) else 0,
        "energy_observations": len(energy_rows) if isinstance(energy_rows, list) else 0,
        "calendar_events": len(calendar_rows) if isinstance(calendar_rows, list) else 0,
        "news": {
            "items": len(news_rows) if isinstance(news_rows, list) else 0,
            "translated": translated_news,
            "total": total_news,
            "coverage": translated_news / total_news if total_news else 1.0,
        },
        "risk": {
            "model_version": risk_payload.get("model_version"),
            "evidence_state": risk_payload.get("evidence_state"),
            "evidence_coverage": risk_payload.get("evidence_coverage"),
            "unwithheld_scores": _unwithheld_risk_scores(risk_payload),
            "unwithheld_history_scores": _unwithheld_risk_history(data_dir, risk_payload),
            "dimensions": [
                {
                    "key": row.get("key"),
                    "state": row.get("evidence_state"),
                    "coverage": row.get("coverage"),
                    "missing_indicators": row.get("missing_indicators", []),
                }
                for row in risk_dimensions
                if isinstance(row, dict)
            ] if isinstance(risk_dimensions, list) else [],
        },
        "overview_sector_performance_rows": len(dashboard_payload.get("sector_performance", []))
        if isinstance(dashboard_payload.get("sector_performance"), list) else 0,
        "ai_brief_files": brief_files,
        "diagnostics_without_detail": diagnostics_without_detail,
        "rights_violations": {
            "unauthorized_macro_values": macro["unauthorized_values"],
            "unauthorized_macro_history": macro["unauthorized_history"],
            "unwithheld_risk_scores": _unwithheld_risk_scores(risk_payload),
            "unwithheld_risk_history_scores": _unwithheld_risk_history(data_dir, risk_payload),
            "macro_values_without_attribution": macro["missing_attributions"],
            "personal_use_source_markers": unauthorized_providers,
        },
    }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("public/data"))
    args = parser.parse_args(argv)
    try:
        report = audit(args.data_dir.resolve(), Settings())
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"coverage audit inconclusive: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    violations = report["rights_violations"]
    return 1 if any(violations.values()) or report["diagnostics_without_detail"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
