from datetime import date

import pytest

from pipeline.collectors.macro import SERIES_GROUPS, MacroCollector
from pipeline.macro_history import treasury_window_rows
from pipeline.providers.treasury import TREASURY_XML
from pipeline.risk.model import RiskModel, _curve_value
from pipeline.schemas import MacroDataset, MacroIndicator
from pipeline.storage.macro_history import write_macro_history
from pipeline.storage.treasury_snapshot import TreasurySnapshot, TreasuryYear
from pipeline.storage.writer import StorageWriter
from tests.pipeline.test_macro import _all_series_rows, _FakeRegistry


def snapshot(*, degraded=False, empty=False):
    today = date.today()
    return TreasurySnapshot(years={} if empty else {today.year: TreasuryYear(
        year=today.year, source=f"{TREASURY_XML}?data=daily_treasury_yield_curve&field_tdr_date_value={today.year}",
        checked_at=f"{today.isoformat()}T12:00:00Z", date_semantics="observation_date",
        series={"DGS10": [{"date": today.isoformat(), "value": 4.2}], "DGS2": [{"date": today.isoformat(), "value": 3.9}]},
    )}, required_years=[today.year], degraded=degraded, errors={}, history_rechecked=False)


@pytest.mark.parametrize("degraded,empty", [(False, False), (True, False), (True, True)])
def test_treasury_never_requests_fred_maturities(tmp_path, degraded, empty):
    registry = _FakeRegistry(_all_series_rows())
    collector = MacroCollector(registry, treasury_snapshot=snapshot(degraded=degraded, empty=empty))
    collector._collect_fedwatch = lambda: None
    data, meta = collector.collect()
    maturities = [item for item in data.rates if item.key in {"dgs10", "dgs2"}]
    assert len(maturities) == 2
    assert not {"DGS10", "DGS2"} & {call[2] for call in registry.calls}
    assert all(item.source == "Treasury" and item.updated_at is None for item in maturities)
    assert all(item.status == ("missing" if empty else "degraded" if degraded else "fresh") for item in maturities)
    assert all(item.observation_date == (None if empty else date.today().isoformat()) for item in maturities)
    assert all(item.fetched_at == (None if empty else f"{date.today().isoformat()}T12:00:00Z") for item in maturities)
    if empty:
        assert all(item.freshness_policy is None for item in maturities)
    else:
        assert all(item.freshness_policy.stale_after_minutes == 10_080 for item in maturities)
    assert meta["series_sources"]["dgs10"] == "Treasury"
    writer = StorageWriter(tmp_path)
    write_macro_history(writer, meta["series_history"], SERIES_GROUPS, series_sources=meta["series_sources"])
    assert (writer.history_dir / "macro/treasury/DGS10/daily.json").exists() != empty
    assert not (writer.history_dir / "macro/DGS10/daily.json").exists()
    result = RiskModel().score({"macro": data, "series_history": meta["series_history"]})
    risk = {item.key: item for dimension in result.dimensions for item in dimension.indicators}
    assert risk["dgs10"].source == "Treasury"
    assert risk["yield_curve_10y2y"].value == (None if empty else pytest.approx(0.3))
    if not empty:
        assert risk["yield_curve_10y2y"].source == "Treasury"
        assert risk["yield_curve_10y2y"].observation_date == date.today().isoformat()
        assert risk["yield_curve_10y2y"].updated_at is None


def test_missing_newly_approved_treasury_series_degrades_without_crashing():
    data = snapshot(degraded=True)
    data.years[date.today().year].series.pop("DGS2")
    collector = MacroCollector(_FakeRegistry(_all_series_rows()), treasury_snapshot=data)
    collector._collect_fedwatch = lambda: None

    macro, meta = collector.collect()

    dgs2 = next(item for item in macro.rates if item.key == "dgs2")
    assert dgs2.status == "missing"
    assert meta["series_history"]["dgs2"] == []


@pytest.mark.parametrize("source,day", [("FRED", "2026-11-12"), ("Treasury", "2026-11-11"), ("Treasury", None)])
def test_curve_rejects_mixed_source_or_observation_day(source, day):
    macro = MacroDataset(rates=[
        MacroIndicator(key="dgs10", label="10Y", value=4.2, source="Treasury", observation_date="2026-11-12"),
        MacroIndicator(key="dgs2", label="2Y", value=3.9, source=source, observation_date=day),
    ])
    assert _curve_value({"macro": macro}) is None


def test_curve_history_only_pairs_same_source_and_day():
    history = {
        "dgs10": [{"date": "2026-11-10", "value": 4.2, "source": "Treasury"}, {"date": "2026-11-11", "value": 4.3, "source": "Treasury"}],
        "dgs2": [{"date": "2026-11-10", "value": 3.9, "source": "FRED"}, {"date": "2026-11-11", "value": 4.0, "source": "Treasury"}, {"date": "2026-11-12", "value": 4.1, "source": "Treasury"}],
    }
    assert RiskModel()._indicator_history({"series_history": history}, "yield_curve_10y2y") == [0.3]


def test_curve_requires_both_legs():
    macro = MacroDataset(rates=[MacroIndicator(
        key="dgs10", label="10Y", value=4.2, source="Treasury", observation_date="2026-11-12",
    )])
    assert _curve_value({"macro": macro}) is None
    assert RiskModel()._indicator_history({"series_history": {
        "dgs10": [{"date": "2026-11-12", "value": 4.2, "source": "Treasury"}],
    }}, "yield_curve_10y2y") is None


def test_treasury_dates_do_not_gain_publication_timestamps():
    data = snapshot()
    data.years[date.today().year].checked_at = f"{date.today().isoformat()}T13:00:00+01:00"
    collector = MacroCollector(_FakeRegistry(_all_series_rows()), treasury_snapshot=data)
    collector._collect_fedwatch = lambda: None
    macro, _ = collector.collect()
    assert macro.rates[0].updated_at is None
    assert macro.rates[0].fetched_at == f"{date.today().isoformat()}T12:00:00Z"


def test_sparse_treasury_history_uses_calendar_window_and_keeps_archive(tmp_path):
    import json

    rows = [
        {"date": "2020-11-14", "value": 1.0, "source": "Treasury"},
        {"date": "2021-11-14", "value": 2.0, "source": "Treasury"},
        {"date": "2021-11-15", "value": 3.0, "source": "Treasury"},
        {"date": "2025-11-14", "value": 4.0, "source": "Treasury"},
    ]
    ctx = {"series_history": {"dgs10": rows, "dgs2": [dict(row, value=0.5) for row in rows]}, "_as_of_date": "2026-11-15"}
    model = RiskModel()
    assert model._indicator_history(ctx, "dgs10") == [3.0, 4.0]
    assert model._indicator_history(ctx, "yield_curve_10y2y") == [2.5, 3.5]
    model.percentile_window_years = 2
    assert model._indicator_history(ctx, "dgs10") == [4.0]
    writer = StorageWriter(tmp_path)
    write_macro_history(writer, ctx["series_history"], SERIES_GROUPS,
                        series_sources={"dgs10": "Treasury", "dgs2": "Treasury"},
                        percentile_window_years=2, as_of_date=date(2026, 11, 15))
    archive = json.loads((writer.history_dir / "macro/treasury/DGS10/daily.json").read_text())
    bundle = json.loads((writer.history_dir / "macro/rates.90d.json").read_text())
    assert len(archive) == 4
    assert "DGS10" not in bundle


def test_treasury_calendar_window_clamps_leap_anniversary():
    rows = [{"date": day, "value": 1, "source": "Treasury"} for day in
            ["2019-02-27", "2019-02-28", "2019-03-01", "2024-03-01"]]
    assert [row["date"] for row in treasury_window_rows(rows, years=5, as_of_date=date(2024, 2, 29))] == ["2019-02-28", "2019-03-01"]
