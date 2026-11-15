from pipeline.collectors.macro import MacroCollector
from pipeline.settings import Settings
from pipeline.storage.treasury_snapshot import SnapshotError
from tests.pipeline.test_macro import _all_series_rows, _FakeRegistry
from tests.pipeline.test_treasury_macro import snapshot


def test_pipeline_routes_opt_in_treasury_snapshot_without_fred_fallback(tmp_path, monkeypatch):
    from pipeline import run
    from pipeline.providers import treasury
    from pipeline.storage import treasury_snapshot

    registry = _FakeRegistry(_all_series_rows())
    monkeypatch.setattr(run, "settings", Settings(_env_file=None, treasury_enabled=True, artifacts_dir=tmp_path))
    monkeypatch.setattr(run, "build_registry", lambda _: registry)
    monkeypatch.setattr(treasury, "TreasuryProvider", lambda _: object())
    monkeypatch.setattr(treasury_snapshot, "refresh_treasury_snapshot", lambda *args, **kwargs: snapshot())
    monkeypatch.setattr(MacroCollector, "_collect_fedwatch", lambda self: None)
    result = run._run_collection("macro-only")
    maturities = [item for item in result["macro"].rates if item.key in {"dgs2", "dgs10"}]
    assert len(maturities) == 2 and all(item.source == "Treasury" and item.value is not None for item in maturities)
    assert not {"DGS2", "DGS10"} & {call[2] for call in registry.calls}


def test_pipeline_marks_treasury_missing_when_snapshot_is_unreadable(tmp_path, monkeypatch):
    from pipeline import run
    from pipeline.providers import treasury
    from pipeline.storage import treasury_snapshot

    registry = _FakeRegistry(_all_series_rows())
    monkeypatch.setattr(run, "settings", Settings(_env_file=None, treasury_enabled=True, artifacts_dir=tmp_path))
    monkeypatch.setattr(run, "build_registry", lambda _: registry)
    monkeypatch.setattr(treasury, "TreasuryProvider", lambda _: object())
    monkeypatch.setattr(treasury_snapshot, "refresh_treasury_snapshot", lambda *args, **kwargs: (_ for _ in ()).throw(SnapshotError("invalid")))
    monkeypatch.setattr(MacroCollector, "_collect_fedwatch", lambda self: None)
    result = run._run_collection("macro-only")
    maturities = [item for item in result["macro"].rates if item.key in {"dgs2", "dgs10"}]
    assert len(maturities) == 2 and all(item.value is None and item.status == "missing" for item in maturities)
    assert not {"DGS2", "DGS10"} & {call[2] for call in registry.calls}


def test_full_pipeline_publishes_treasury_without_mixing_fred_archive(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from pipeline import run
    from pipeline.providers import treasury
    from pipeline.schemas import registry as datasets
    from pipeline.storage import treasury_snapshot
    from tests.pipeline.factories import PAYLOAD_BUILDERS

    configured = Settings(_env_file=None, treasury_enabled=True, data_dir=tmp_path / "data", artifacts_dir=tmp_path / "artifacts")
    payloads = {key: datasets.require(key).model.model_fields["payload"].annotation.model_validate(builder()) for key, builder in PAYLOAD_BUILDERS.items()}
    market = {key: payloads[key] for key in ("equities", "sectors", "crypto", "commodities")}
    market.update(histories={}, degraded=[], provider_status={}, data_quality=1.0)
    registry = _FakeRegistry(_all_series_rows())
    monkeypatch.setattr(run, "settings", configured)
    monkeypatch.setattr(run, "build_registry", lambda _: registry)
    monkeypatch.setattr(treasury, "TreasuryProvider", lambda _: object())
    monkeypatch.setattr(treasury_snapshot, "refresh_treasury_snapshot", lambda *args, **kwargs: snapshot())
    monkeypatch.setattr(MacroCollector, "_collect_fedwatch", lambda self: None)
    monkeypatch.setattr(run, "MarketCollector", lambda *args: SimpleNamespace(collect=lambda: market))
    for name, key in (("CalendarCollector", "calendar"), ("NewsCollector", "news")):
        monkeypatch.setattr(run, name, lambda *args, key=key: SimpleNamespace(
            collect=lambda: (payloads[key], {"degraded": [], "provider_status": {}, "data_quality": 1.0}),
        ))
    assert run.main(["--full"]) == 0
    macro = json.loads((configured.data_dir / "latest/macro.json").read_text())
    yields = [item for item in macro["payload"]["rates"] if item["key"] in {"dgs2", "dgs10"}]
    assert len(yields) == 2 and all(item["source"] == "Treasury" and item["observation_date"] for item in yields)
    assert (configured.data_dir / "history/macro/treasury/DGS10/daily.json").exists()
    assert not (configured.data_dir / "history/macro/DGS10/daily.json").exists()
    assert (configured.data_dir / "latest/risk.json").exists()
    assert not {"DGS2", "DGS10"} & {call[2] for call in registry.calls}
