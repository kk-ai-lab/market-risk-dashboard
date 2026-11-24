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
        SimpleNamespace(symbol=symbol, change_1d=-0.4, source="coinmetrics")
        for symbol in (
            "AVAX", "DOGE", "SOL", "XRP", "BTC", "LTC", "ETH", "ADA", "LINK", "BNB",
        )
    ]))
    calendar = SimpleNamespace(payload=SimpleNamespace(events=[]))
    payload = _build_dashboard(
        risk_env=SimpleNamespace(payload=risk), equities=equities, crypto=crypto,
        sectors=None, calendar=calendar,
    )

    assert payload.cross_asset[0].source == "yahoo"
    assert payload.cross_asset[1].source == "coinmetrics"
    assert [asset.asset for asset in payload.cross_asset if asset.category == "crypto"] == [
        "BTC", "ETH", "SOL",
    ]


def test_dashboard_market_snapshot_covers_indices_crypto_commodities_rates_and_fx() -> None:
    from pipeline.run import _build_dashboard
    from pipeline.schemas.risk import RiskModelResult
    from tests.pipeline.factories import make_risk_payload

    risk = RiskModelResult.model_validate(make_risk_payload())
    equities = SimpleNamespace(payload=SimpleNamespace(
        assets=[],
        benchmarks=[SimpleNamespace(
            symbol="OECD:USA", name="United States share index", name_zh="美国综合股指",
            value=225.0, unit="index, 2015=100", change_1m=1.2,
            observation_period="2026-10", source="OECD", source_url="https://data-explorer.oecd.org/",
            attribution="OECD citation", market="US",
        )],
    ))
    crypto = SimpleNamespace(payload=SimpleNamespace(assets=[SimpleNamespace(
        symbol="BTC", name="Bitcoin", price=90000.0, change_1d=-0.8,
        source="Coin Metrics", updated_at="2026-11-24T12:00:00Z",
    )]))
    commodities = SimpleNamespace(payload=SimpleNamespace(assets=[SimpleNamespace(
        symbol="WB:BRENT", name="Brent Crude", name_zh="布伦特原油", price=70.2,
        price_unit="$/bbl", currency="USD", change_1m=4.1, observation_period="2026-10",
        updated_at="2026-10-31T00:00:00Z", frequency="monthly", source="World Bank Pink Sheet",
    )]))
    macro = SimpleNamespace(payload=SimpleNamespace(
        rates=[SimpleNamespace(key="dgs10", label="10-Year Treasury Yield", value=4.2,
            unit="pct", observation_date="2026-11-20", observation_period=None,
            source="Treasury", attribution=None)],
        fx=[SimpleNamespace(key="dexuseu", label="USD per EUR", value=0.92,
            unit="usd_per_eur", observation_date="2026-11-20", observation_period=None,
            source="Federal Reserve Board", attribution=None)],
    ))
    payload = _build_dashboard(
        risk_env=SimpleNamespace(payload=risk), equities=equities, crypto=crypto,
        commodities=commodities, macro=macro, sectors=None,
        calendar=SimpleNamespace(payload=SimpleNamespace(events=[])),
    )

    assert {item.category for item in payload.market_snapshot} == {
        "equity_index", "crypto", "commodity", "rate", "fx",
    }
    bitcoin = next(item for item in payload.market_snapshot if item.symbol == "BTC")
    assert bitcoin.value == 90000.0 and bitcoin.unit == "USD" and bitcoin.change_period == "24h"
    brent = next(item for item in payload.market_snapshot if item.symbol == "WB:BRENT")
    assert brent.value == 70.2 and brent.unit == "$/bbl" and brent.observation_period == "2026-10"


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
