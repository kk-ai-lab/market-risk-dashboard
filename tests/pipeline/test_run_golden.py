"""Byte-identical golden lock for the run.py write path (#192).

T6 restructures pipeline/run.py; the acceptance bar is ZERO behavior change. This
test drives _finalize_and_write + _build_dashboard over synthetic factory payloads
(frozen generated_at) and asserts a SHA256 manifest of every file StorageWriter
published - any accidental semantic drift breaks it. The manifest was recorded
against the PRE-refactor tree; regenerate only via scripts/record_golden.py.
The data-only pipeline deliberately removes the two retired AI freshness entries.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.pipeline.golden_support import FROZEN_TS, publish
from tests.pipeline.golden_support import manifest as build_manifest

GOLDEN = Path(__file__).parent / "golden" / "run_manifest.json"


@pytest.fixture()
def data_root(tmp_path: Path) -> Path:
    from pipeline.storage import StorageWriter

    root = tmp_path / "data"
    publish(StorageWriter(root), FROZEN_TS)
    return root


def test_run_write_path_manifest_is_stable(data_root: Path) -> None:
    if not GOLDEN.exists():
        pytest.fail("golden manifest missing; run python scripts/record_golden.py")
    actual = build_manifest(data_root)
    expected = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert actual == expected, (
        "published bytes drifted (#192): "
        f"missing={sorted(set(expected) - set(actual))} "
        f"extra={sorted(set(actual) - set(expected))} "
        f"changed={sorted(k for k in set(actual) & set(expected) if actual[k] != expected[k])}"
    )


def test_dashboard_cross_asset_keeps_the_selected_quote_source() -> None:
    from pipeline.run import _build_dashboard
    from pipeline.schemas.risk import RiskModelResult
    from tests.pipeline.factories import make_risk_payload

    risk = RiskModelResult.model_validate(make_risk_payload())
    equities = SimpleNamespace(payload=SimpleNamespace(assets=[
        SimpleNamespace(symbol="SPY", change_1d=0.2, source="yahoo"),
    ]))
    crypto = SimpleNamespace(payload=SimpleNamespace(assets=[
        SimpleNamespace(symbol="BTC", change_1d=-0.4, source="coinmetrics"),
    ]))
    calendar = SimpleNamespace(payload=SimpleNamespace(events=[]))
    payload = _build_dashboard(
        risk_env=SimpleNamespace(payload=risk), equities=equities, crypto=crypto,
        sectors=None, calendar=calendar,
    )

    assert payload.cross_asset[0].source == "yahoo"
    assert payload.cross_asset[1].source == "coinmetrics"


def test_run_summary_withholds_score_when_evidence_is_insufficient(capsys) -> None:
    from pipeline.run import _print_summary

    risk = SimpleNamespace(payload=SimpleNamespace(
        total_score=70.25,
        evidence_state="insufficient_evidence",
        evidence_coverage=0.1,
        score_lower_bound=7.0,
        score_upper_bound=97.0,
    ))
    _print_summary("full", {"risk": risk, "degraded": []}, 1.0)

    output = capsys.readouterr().out
    assert "withheld (insufficient evidence, 10% coverage, interval 7.0–97.0)" in output
    assert "70.25" not in output
