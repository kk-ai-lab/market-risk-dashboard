"""Calibration harness tests (#72 — sign convention + heuristic-fallback labelling).

Previously ZERO coverage for `pipeline/risk/calibration.py`. The harness is a
decision-making tool (does the risk model warn early enough?), so its sign convention
and its honesty about which scoring path it measures are both load-bearing.
"""

from __future__ import annotations

import copy
import json
import statistics
from pathlib import Path

import pytest

from pipeline.risk.calibration import (
    _forward_outcome,
    composite_score,
    evaluate_segment,
    normalize_calibration_panel,
    replay_production_path,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

DATES = [f"2026-01-{d:02d}" for d in range(1, 11)]
# SPX rises to a peak at index 4, then declines (the crash).
SPX_PEAK_AT_4 = [100.0, 102.0, 104.0, 106.0, 108.0, 105.0, 100.0, 95.0, 90.0, 85.0]


def test_lead_time_sign_convention() -> None:
    vix = [15.0, 30.0, 40.0, 45.0, 50.0, 45.0, 40.0, 35.0, 30.0, 25.0]
    baa = [1.5, 1.7, 2.5, 3.0, 3.4, 3.2, 3.0, 2.8, 2.6, 2.4]

    result = evaluate_segment(DATES, vix, baa, SPX_PEAK_AT_4, "2008")
    lead = result["early_warning_days_vs_peak"]
    assert lead == 2, f"warned 2 days before the peak, got {lead!r}"
    assert lead > 0


def test_late_warning_yields_negative_lead_time() -> None:
    vix = [15.0, 15.0, 15.0, 15.0, 15.0, 20.0, 40.0, 45.0, 50.0, 55.0]
    baa = [1.4, 1.4, 1.4, 1.4, 1.4, 1.6, 3.0, 3.2, 3.4, 3.5]

    result = evaluate_segment(DATES, vix, baa, SPX_PEAK_AT_4, "2008")
    lead = result["early_warning_days_vs_peak"]
    assert lead == -2, f"warned 2 days after the peak, got {lead!r}"
    assert lead < 0


def test_reported_forward_vol_uses_the_requested_daily_return_horizon() -> None:
    spx = [100.0, 98.0, 97.0, 95.0, 98.0, 96.0, 94.0]
    returns = [(spx[index + 1] - spx[index]) / spx[index] for index in range(5)]
    expected = round(statistics.stdev(returns) * 100.0, 2)

    result = evaluate_segment(DATES[: len(spx)], [15.0] * len(spx), [1.5] * len(spx), spx, "2008")

    assert result["forward_daily_return_std_pct"]["5d"] == expected


def test_output_labelled_heuristic_fallback() -> None:
    vix = [15.0, 30.0, 40.0, 45.0, 50.0, 45.0, 40.0, 35.0, 30.0, 25.0]
    baa = [1.5, 1.7, 2.5, 3.0, 3.4, 3.2, 3.0, 2.8, 2.6, 2.4]

    result = evaluate_segment(DATES, vix, baa, SPX_PEAK_AT_4, "2008")
    assert result["scoring_path"] == "heuristic_fallback", (
        "the harness must label every output as the heuristic fallback path, "
        f"got {result['scoring_path']!r}"
    )
    # The composite score is itself built from the heuristic table.
    assert composite_score(25.0, 3.0, -10.0) is not None


def test_published_artifacts_unaffected() -> None:
    """AC: running the harness writes nothing under public/data (#72)."""
    public_data = REPO_ROOT / "public" / "data"

    def snapshot() -> set[str]:
        return {p.relative_to(public_data).as_posix() for p in public_data.rglob("*") if p.is_file()}

    before = snapshot()
    vix = [15.0, 30.0, 40.0, 45.0, 50.0, 45.0, 40.0, 35.0, 30.0, 25.0]
    baa = [1.5, 1.7, 2.5, 3.0, 3.4, 3.2, 3.0, 2.8, 2.6, 2.4]
    evaluate_segment(DATES, vix, baa, SPX_PEAK_AT_4, "2008")
    composite_score(25.0, 3.0, -10.0)
    assert snapshot() == before, "the calibration harness must not write under public/data"


def test_report_legend_matches_numbers() -> None:
    """The report defines positive and negative lead time relative to the SPX peak."""
    text = (REPO_ROOT / "docs" / "calibration-report.md").read_text(encoding="utf-8")
    assert "positive = before the peak" in text.lower()
    assert "negative = after the peak" in text.lower()
    # The scope must say the harness evaluates the heuristic FALLBACK path, not production.
    assert "heuristic fallback" in text.lower() or "fallback" in text.lower()


def test_offline_calibration_fetches_baa10y_not_hy_oas(tmp_path, monkeypatch) -> None:
    from scripts import calibration as calibration_script

    requested: list[str] = []

    def fetch_fred(series_id: str, start: str, end: str) -> dict[str, float]:
        requested.append(series_id)
        return {start: 2.0, end: 3.0}

    def fetch_spx(start: str, end: str) -> dict[str, float]:
        return {start: 100.0, end: 90.0}

    monkeypatch.setattr(calibration_script, "_fetch_fred_map", fetch_fred)
    monkeypatch.setattr(calibration_script, "_fetch_spx_map", fetch_spx)

    output = tmp_path / "calibration.md"
    assert calibration_script.run_calibration(output) == 0
    report = output.read_text(encoding="utf-8")

    assert requested.count("BAA10Y") == 3
    assert "BAMLH0A0HYM2" not in requested
    assert "Baa-10Y spread" in report
    assert "Input observations: VIXCLS 2, BAA10Y 2, SPX 2, common dates 2" in report
    assert "Common dates:" in report


def test_offline_calibration_skips_windows_without_all_inputs(tmp_path, monkeypatch) -> None:
    from scripts import calibration as calibration_script

    monkeypatch.setattr(
        calibration_script,
        "_fetch_fred_map",
        lambda series_id, start, end: {} if series_id == "BAA10Y" else {start: 20.0},
    )
    monkeypatch.setattr(calibration_script, "_fetch_spx_map", lambda start, end: {start: 100.0})

    output = tmp_path / "calibration.md"
    assert calibration_script.run_calibration(output) == 1
    report = output.read_text(encoding="utf-8")

    assert "skipped; no complete input dates" in report
    assert "BAA10Y 0" in report
    assert "Score range:" not in report


def _production_panel() -> dict:
    return json.loads((REPO_ROOT / "tests" / "fixtures" / "calibration_panel.json").read_text(encoding="utf-8"))


def test_production_replay_is_deterministic_and_point_in_time() -> None:
    panel = _production_panel()
    first = replay_production_path(panel)
    second = replay_production_path(panel)

    assert first == second
    assert first["input_fingerprint"].startswith("sha256:")
    assert first["calibration_policy"]["decisions"]["confidence_formula"]["coverage"] == "retain"
    assert first["point_in_time_policy"]["future_observations_excluded_from_score"] is True
    assert set(first["regime_counts"]) == {"calm", "tightening", "crisis"}
    assert "production_mixed_percentile_heuristic" in first["path_counts"]
    assert first["metrics"]["horizons"]["20"]["coverage"]["outcome_available"] > 0
    assert first["metrics"]["horizons"]["20"]["event_discrimination"]
    assert first["metrics"]["horizons"]["5"]["event_discrimination"] != first["metrics"]["horizons"]["30"]["event_discrimination"]
    assert set(first["observations"][0]["dimension_scores"]) == {
        "macro", "liquidity_credit", "equity_structure", "volatility", "cross_asset", "trend"
    }
    assert all(row["max_history_date"] == row["date"] for row in first["observations"])


def test_cross_asset_replay_publishes_diagnostics_without_weight_changes() -> None:
    panel = _production_panel()
    spy = panel["market"]["SPY"]
    # Add deterministic proxy histories to the fixture without changing its forward SPY
    # outcomes. The replay must evaluate the signals at each point in time and retain the
    # policy gate rather than silently adding their weight to the score.
    panel["market"]["XLY"] = [value * (1.0 + (index % 5) * 0.001) for index, value in enumerate(spy)]
    panel["market"]["XLP"] = [value * (1.0 + 0.002) for value in spy]
    panel["market"]["HYG"] = [value * (1.0 - (index % 4) * 0.001) for index, value in enumerate(spy)]
    panel["market"]["IEF"] = [value * (1.0 + 0.001) for value in spy]

    artifact = replay_production_path(panel)

    assert artifact["artifact_version"] == "1.2.0"
    assert artifact["cross_asset_policy"]["production_scoring"] == "diagnostic_only"
    assert artifact["metrics"]["cross_asset_signals"]["cyclicals_defensives_relative"]["20"]["evaluated"] > 0
    assert artifact["metrics"]["cross_asset_signals"]["hy_treasury_relative"]["20"]["evaluated"] > 0
    assert all(
        signal["production_scoring"] is False
        for signal in artifact["observations"][0]["cross_asset_signals"]
        if signal["key"] in {"cyclicals_defensives_relative", "hy_treasury_relative"}
    )


def test_replay_observes_the_commodity_confirmation_signals_when_the_panel_carries_them() -> None:
    """Copper/gold closes make two more production signals observable in the replay.

    Before the commodity seam existed, ``_point_in_time_context`` always handed the risk
    context an empty commodities dataset, so ``copper_down``/``gold_up`` were permanently
    unobserved: the calibration evidence for the cross_asset dimension covered 5 of its 8
    production signals, and the two missing ones could never be evaluated. The seam makes the
    inputs measurable, which now moves the score. model_version 1.0.1 removed the constant
    50.0 fallback and made ``cross_asset_confirmation`` a real scored indicator, so the
    cross_asset dimension tracks the confirmation proportion (risk_score = proportion × 100)
    rather than a frozen constant. Adding commodity closes changes that proportion, so the two
    replays legitimately differ.
    """
    panel = _production_panel()
    baseline_row = replay_production_path(copy.deepcopy(panel))["observations"][-1]

    spy = panel["market"]["SPY"]
    panel["market"]["GC=F"] = [value * (1.0 + (index % 7) * 0.0005) for index, value in enumerate(spy)]
    panel["market"]["HG=F"] = [value * (1.0 - (index % 5) * 0.0005) for index, value in enumerate(spy)]

    row = replay_production_path(panel)["observations"][-1]
    signals = {signal["key"]: signal for signal in row["cross_asset_signals"]}

    def observed(observation: dict) -> int:
        return sum(1 for signal in observation["cross_asset_signals"] if signal["triggered"] is not None)

    assert signals["copper_down"]["triggered"] is not None
    assert signals["gold_up"]["triggered"] is not None
    assert observed(baseline_row) == 5
    assert observed(row) == 7
    # The confirmation input moved, and so does the dimension score: the real 1.0.1 mapping is
    # risk_score = confirmation_proportion × 100, not the old frozen 50.0. Adding commodity closes
    # changes the observable signal set (and therefore the proportion), so the two replays differ
    # and neither equals the former constant fallback.
    assert row["cross_asset_confirmation"] != baseline_row["cross_asset_confirmation"]
    assert row["dimension_scores"]["cross_asset"] == pytest.approx(row["cross_asset_confirmation"] * 100)
    assert baseline_row["dimension_scores"]["cross_asset"] == pytest.approx(baseline_row["cross_asset_confirmation"] * 100)
    assert row["dimension_scores"]["cross_asset"] != 50.0
    assert baseline_row["dimension_scores"]["cross_asset"] != 50.0
    assert row["dimension_scores"]["cross_asset"] != baseline_row["dimension_scores"]["cross_asset"]


def test_production_replay_does_not_use_future_values() -> None:
    panel = _production_panel()
    baseline = replay_production_path(panel)
    changed = copy.deepcopy(panel)
    # The first evaluated row is index 60; changing rows after it must not alter its score.
    changed["macro"]["vixcls"][119] += 1000
    changed["market"]["SPY"][119] = 1.0
    replayed = replay_production_path(changed)

    assert replayed["observations"][0]["total_score"] == baseline["observations"][0]["total_score"]
    assert replayed["observations"][0]["indicator_path_counts"] == baseline["observations"][0]["indicator_path_counts"]
    assert replayed["input_fingerprint"] != baseline["input_fingerprint"]


def test_production_panel_validation_rejects_shape_and_order_errors() -> None:
    panel = _production_panel()
    panel["dates"][1] = panel["dates"][0]
    with pytest.raises(ValueError, match="unique"):
        normalize_calibration_panel(panel)

    panel = _production_panel()
    panel["market"]["SPY"].pop()
    with pytest.raises(ValueError, match="match dates"):
        normalize_calibration_panel(panel)


def test_forward_outcome_uses_running_peak_for_drawdown() -> None:
    outcome = _forward_outcome([100.0, 130.0, 110.0], 0, 2)

    assert outcome is not None
    assert outcome["max_drawdown"] == pytest.approx(-0.153846)
