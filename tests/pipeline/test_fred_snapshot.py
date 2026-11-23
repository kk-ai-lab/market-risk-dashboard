from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest

from pipeline.collectors.macro import FREQ_SPEC, MacroCollector
from pipeline.providers.base import ProviderError
from pipeline.settings import Settings
from pipeline.storage.cloud_fred import refresh_cloud_fred_snapshot
from pipeline.storage.fred_snapshot import (
    FREQUENCY_NEXT_DAYS,
    FredObservation,
    FredSeriesState,
    FredSnapshot,
    SnapshotError,
    fred_due,
    merge_rows,
    next_publish_after,
    refresh_fred_snapshot,
    series_spec,
)
from pipeline.storage.github_checkpoint import CheckpointSnapshot


def _state(rows=(), last_attempt=None):
    return FredSeriesState(
        rows=[FredObservation(date=day, value=value) for day, value in rows],
        last_attempt_at=last_attempt,
    )


def test_frequency_table_matches_macro_spec():
    assert {
        name: FREQ_SPEC[name]["next_days"] for name in FREQUENCY_NEXT_DAYS
    } == FREQUENCY_NEXT_DAYS


def test_next_publish_after_skips_weekend():
    friday = date(2026, 11, 20)

    assert next_publish_after(friday, "daily", time(12, 0)) == datetime(
        2026, 11, 23, 12, 0, tzinfo=UTC
    )
    assert next_publish_after(friday, "weekly", time(12, 0)) == datetime(
        2026, 11, 28, 12, 0, tzinfo=UTC
    )


def test_first_fill_is_due():
    now = datetime(2026, 11, 20, 16, 0, tzinfo=UTC)

    assert fred_due(_state(), series_spec("VIXCLS"), now)


def test_waits_for_the_next_observation_then_retries_hourly():
    now = datetime(2026, 11, 20, 16, 0, tzinfo=UTC)
    state = _state([(date(2026, 11, 19), 20.0)])
    spec = series_spec("VIXCLS")

    assert fred_due(state, spec, now)
    state.last_attempt_at = now
    assert not fred_due(state, spec, now.replace(minute=30))
    assert fred_due(state, spec, now.replace(hour=17))


def test_a_weekend_does_not_trigger_a_fetch():
    state = _state([(date(2026, 11, 20), 20.0)])
    saturday = datetime(2026, 11, 21, 23, 0, tzinfo=UTC)

    assert not fred_due(state, series_spec("VIXCLS"), saturday)


def test_merge_replaces_a_revision_and_rejects_a_future_row():
    previous = [FredObservation(date=date(2026, 11, 18), value=1.0),
                FredObservation(date=date(2026, 11, 19), value=2.0)]

    merged = merge_rows(previous, [
        {"date": "2026-11-19", "value": 2.5},
        {"date": "2026-11-20", "value": 3.0},
    ], today=date(2026, 11, 20))

    assert [(row.date, row.value) for row in merged] == [
        (date(2026, 11, 18), 1.0), (date(2026, 11, 19), 2.5), (date(2026, 11, 20), 3.0)
    ]
    with pytest.raises(SnapshotError, match="ahead of the current clock"):
        merge_rows(previous, [{"date": "2026-11-21", "value": 4.0}], today=date(2026, 11, 20))


def test_local_snapshot_does_not_fetch_when_no_observation_is_due(tmp_path):
    path = tmp_path / "fred.json"
    calls = []
    now = datetime(2026, 11, 20, 16, 0, tzinfo=UTC)

    def fetch(series_id, start):
        calls.append((series_id, start))
        return [{"date": "2026-11-20", "value": 20.0}]

    refresh_fred_snapshot(path, fetch, now=now, series_ids=["VIXCLS"])
    refresh_fred_snapshot(path, fetch, now=now.replace(minute=30), series_ids=["VIXCLS"])

    assert calls == [("VIXCLS", (now.date() - timedelta(days=5 * 365)).isoformat())]


def test_local_failed_fetch_keeps_last_good_and_bounds_the_retry(tmp_path):
    path = tmp_path / "fred.json"
    now = datetime(2026, 11, 20, 16, 0, tzinfo=UTC)
    refresh_fred_snapshot(
        path, lambda _s, _start: [{"date": "2026-11-19", "value": 19.0}],
        now=now - timedelta(days=1), series_ids=["VIXCLS"],
    )
    calls = []

    def failing(series_id, start):
        calls.append(series_id)
        raise ProviderError("FRED is down")

    after_failure = refresh_fred_snapshot(path, failing, now=now, series_ids=["VIXCLS"])
    assert after_failure.series["VIXCLS"].rows[-1].value == 19.0
    assert after_failure.series["VIXCLS"].access == "failed"

    refresh_fred_snapshot(path, failing, now=now.replace(minute=30), series_ids=["VIXCLS"])
    assert calls == ["VIXCLS"]

    refresh_fred_snapshot(path, failing, now=now.replace(hour=17), series_ids=["VIXCLS"])
    assert calls == ["VIXCLS", "VIXCLS"]


class MemoryCheckpoint:
    def __init__(self, content: bytes = b""):
        self.content = content
        self.writes = 0

    def read(self, path):
        return CheckpointSnapshot("a" * 40, "b" * 40, path, self.content)

    def write(self, snapshot, content, message):
        self.writes += 1
        self.content = content
        return "c" * 40


def test_cloud_reserves_the_attempt_before_the_request():
    now = datetime(2026, 11, 20, 16, 0, tzinfo=UTC)
    checkpoint = MemoryCheckpoint(FredSnapshot().model_dump_json().encode())
    seen = []

    def fetch(series_id, start):
        durable = FredSnapshot.model_validate_json(checkpoint.content)
        seen.append(durable.series[series_id].last_attempt_at)
        return [{"date": "2026-11-20", "value": 21.0}]

    result = refresh_cloud_fred_snapshot(
        checkpoint, "fred/snapshot.json", fetch,
        now=now, series_ids=["VIXCLS"],
    )

    assert seen == [now]
    assert result.series["VIXCLS"].rows[-1].value == 21.0
    assert checkpoint.writes == 2


def test_cloud_failure_keeps_cached_rows_and_does_not_repeat_inside_the_window():
    now = datetime(2026, 11, 20, 16, 0, tzinfo=UTC)
    cached = FredSnapshot(series={
        "VIXCLS": _state([(date(2026, 11, 19), 19.0)]),
    })
    checkpoint = MemoryCheckpoint(cached.model_dump_json().encode())
    calls = []

    def failing(series_id, start):
        calls.append(series_id)
        raise ProviderError("FRED is down")

    result = refresh_cloud_fred_snapshot(
        checkpoint, "fred/snapshot.json", failing,
        now=now, series_ids=["VIXCLS"],
    )
    assert result.series["VIXCLS"].rows[-1].value == 19.0
    assert result.series["VIXCLS"].access == "failed"

    refresh_cloud_fred_snapshot(
        checkpoint, "fred/snapshot.json", failing,
        now=now.replace(minute=30), series_ids=["VIXCLS"],
    )
    assert calls == ["VIXCLS"]


class NoNetworkRegistry:
    def __init__(self):
        self.calls = []

    def call(self, domain, method, key, args=(), kwargs=None):
        self.calls.append((domain, method, key))
        raise AssertionError(f"unexpected provider call: {domain}.{method}")

    def call_provider(self, domain, provider_name, method, key, args=(), kwargs=None, **_options):
        self.calls.append((domain, method, key))
        return {
            "result": [],
            "meta": {"provider": provider_name, "from_cache": False, "degraded": False},
        }

    def providers_for(self, _domain):
        return []

    def resolved_provider(self, domain):
        return None


def test_macro_serves_snapshot_rows_without_a_request(tmp_path):
    now = datetime(2026, 11, 20, 16, 0, tzinfo=UTC)
    settings = Settings(
        _env_file=None,
        approved_sources_only=True,
        fred_api_key="secret",
        project_root=tmp_path,
        config_dir=Settings(_env_file=None).config_dir,
        data_dir=tmp_path / "data",
        artifacts_dir=tmp_path / "artifacts",
    )
    snapshot = FredSnapshot(series={
        "VIXCLS": _state([(date(2026, 11, 19), 18.0), (date(2026, 11, 20), 21.0)],
                         last_attempt=now),
    })
    registry = NoNetworkRegistry()

    macro, meta = MacroCollector(registry, settings, fred_snapshot=snapshot).collect()
    indicators = {
        indicator.key.upper(): indicator
        for group in ("rates", "credit", "volatility", "inflation", "labor", "liquidity", "fx")
        for indicator in getattr(macro, group)
    }

    assert registry.calls == []
    assert indicators["VIXCLS"].value is None
    assert indicators["VIXCLS"].source == "unavailable"
    assert indicators["VIXCLS"].status == "missing"
    assert meta["provider_status"]["fred"]["failed_series"] == []


def test_macro_only_does_not_publish_personal_use_snapshot_rows(tmp_path, monkeypatch):
    from pipeline import run
    from pipeline.storage import bls_snapshot as bls_module
    from pipeline.storage import fred_snapshot as fred_module
    from pipeline.storage import treasury_snapshot as treasury_module
    from tests.pipeline.test_treasury_macro import snapshot as treasury_snapshot

    configured = Settings(
        _env_file=None,
        approved_sources_only=True,
        fred_api_key="secret",
        project_root=tmp_path,
        config_dir=Settings(_env_file=None).config_dir,
        data_dir=tmp_path / "data",
        artifacts_dir=tmp_path / "artifacts",
    )
    fake = FredSnapshot(series={
        "VIXCLS": _state([(date(2026, 11, 20), 21.0)],
                         last_attempt=datetime(2026, 11, 20, 16, 0, tzinfo=UTC)),
    })
    calls = []
    registry = NoNetworkRegistry()

    monkeypatch.setattr(run, "settings", configured)
    monkeypatch.setattr(run, "build_registry", lambda *_args, **_kwargs: registry)
    monkeypatch.setattr(
        treasury_module, "refresh_treasury_snapshot", lambda *_args, **_kwargs: treasury_snapshot()
    )
    monkeypatch.setattr(
        bls_module, "refresh_bls_snapshot", lambda *_args, **_kwargs: bls_module.empty_bls_snapshot()
    )

    def fake_refresh(path, fetch, *, now, series_ids, retry_minutes):
        calls.append((path, tuple(series_ids), retry_minutes))
        return fake

    monkeypatch.setattr(fred_module, "refresh_fred_snapshot", fake_refresh)

    results = run._run_collection("macro-only")

    assert calls == []
    assert results["macro"].volatility[0].value is None
    assert all(not key.startswith("fred_") for _, _, key in registry.calls)
