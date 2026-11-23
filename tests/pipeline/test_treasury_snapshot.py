import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Lock

import pytest

from pipeline.providers.base import ProviderError
from pipeline.providers.treasury import TREASURY_XML
from pipeline.storage.treasury_snapshot import SnapshotError, refresh_treasury_snapshot

NOW = datetime(2026, 11, 15, 12, tzinfo=UTC)


class Provider:
    def __init__(self, now=NOW):
        self.now = now
        self.calls = []
        self.failed = set()
        self.value = 4.2
        self.days = [2, 3]
        self.lock = Lock()

    def get_yield_curve(self, *, year, series_ids=None):
        with self.lock:
            self.calls.append(year)
        if year in self.failed:
            raise ProviderError("unavailable or empty")
        return {"year": year,
                "source": f"{TREASURY_XML}?data=daily_treasury_yield_curve&field_tdr_date_value={year}",
                "checked_at": self.now.isoformat(), "date_semantics": "observation_date",
                "series": {key: [{"date": f"{year}-01-{day:02}", "value": self.value} for day in self.days]
                           for key in (series_ids or ("DGS2", "DGS10"))}}


def test_cold_start_six_calendar_years_and_original_time(tmp_path):
    provider = Provider(NOW - timedelta(seconds=7))
    state = refresh_treasury_snapshot(tmp_path, provider, now=NOW)
    assert provider.calls == list(range(2021, 2027))
    assert not state.degraded
    assert state.required_years == list(range(2021, 2027))
    assert state.years[2026].checked_at == provider.now.isoformat()
    assert state.years[2026].series["DGS2"][0].date.isoformat() == "2026-01-02"
    assert state.years[2026].date_semantics == "observation_date"
    assert "field_tdr_date_value=2026" in state.years[2026].source
    assert len(list(tmp_path.glob("20*.json"))) == 6
    refresh_treasury_snapshot(tmp_path, provider, now=NOW + timedelta(minutes=14))
    assert len(provider.calls) == 6
    provider.now = NOW + timedelta(minutes=15)
    refreshed = refresh_treasury_snapshot(tmp_path, provider, now=provider.now)
    assert not refreshed.degraded
    assert provider.calls == list(range(2021, 2027)) + [2026]


def test_same_day_policy_narrowing_scrubs_local_years_without_request(tmp_path):
    provider = Provider()
    refresh_treasury_snapshot(tmp_path, provider, now=NOW)
    provider.calls.clear()

    state = refresh_treasury_snapshot(tmp_path, provider, now=NOW, series_ids=["DGS10"])

    assert provider.calls == []
    assert set(state.years[2026].series) == {"DGS10"}
    assert set(json.loads((tmp_path / "2026.json").read_text())["data"]["series"]) == {"DGS10"}


def test_empty_treasury_selection_scrubs_local_state_without_request(tmp_path):
    provider = Provider()
    refresh_treasury_snapshot(tmp_path, provider, now=NOW)
    previous_ledger = json.loads((tmp_path / "manifest.json").read_text())
    provider.calls.clear()

    state = refresh_treasury_snapshot(tmp_path, provider, now=NOW, series_ids=[])

    assert provider.calls == []
    assert state.years == {}
    assert not list(tmp_path.glob("[0-9][0-9][0-9][0-9].json"))
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert all(
        not item["has_data"]
        and item["status"] == "excluded"
        and item["attempted_at"] == previous_ledger["years"][year]["attempted_at"]
        for year, item in manifest["years"].items()
    )


def test_empty_selection_reauthorization_preserves_request_cadence(tmp_path):
    provider = Provider()
    refresh_treasury_snapshot(tmp_path, provider, now=NOW)
    provider.calls.clear()
    refresh_treasury_snapshot(tmp_path, provider, now=NOW, series_ids=[])

    state = refresh_treasury_snapshot(tmp_path, provider, now=NOW, series_ids=["DGS10"])

    assert provider.calls == []
    assert state.degraded and state.years == {}
    assert set(state.errors) == set(range(2021, 2027))


def test_treasury_request_persists_only_the_approved_maturity(tmp_path):
    provider = Provider()
    state = refresh_treasury_snapshot(tmp_path, provider, now=NOW, series_ids=["DGS10"])

    assert set(state.years[2026].series) == {"DGS10"}
    assert all(set(year.series) == {"DGS10"} for year in state.years.values())


def test_expanding_treasury_selection_waits_for_cadence_then_backfills(tmp_path):
    provider = Provider()
    refresh_treasury_snapshot(tmp_path, provider, now=NOW, series_ids=["DGS10"])
    provider.calls.clear()

    state = refresh_treasury_snapshot(
        tmp_path, provider, now=NOW + timedelta(seconds=1), series_ids=["DGS2", "DGS10"]
    )
    assert provider.calls == []
    assert state.degraded
    assert all(set(year.series) == {"DGS10"} for year in state.years.values())

    provider.now = NOW + timedelta(days=1)
    state = refresh_treasury_snapshot(
        tmp_path, provider, now=provider.now, series_ids=["DGS2", "DGS10"]
    )

    assert not state.degraded
    assert provider.calls == list(range(2021, 2027))
    assert all(set(year.series) == {"DGS2", "DGS10"} for year in state.years.values())


def test_partial_bootstrap_failure_and_daily_history_retry(tmp_path):
    provider = Provider()
    provider.failed = {2023}
    state = refresh_treasury_snapshot(tmp_path, provider, now=NOW)
    assert state.degraded and 2023 in state.errors and 2023 not in state.years
    provider.failed.clear()
    refresh_treasury_snapshot(tmp_path, provider, now=NOW + timedelta(minutes=15))
    assert provider.calls.count(2023) == 1
    provider.now = NOW + timedelta(days=1)
    state = refresh_treasury_snapshot(tmp_path, provider, now=NOW + timedelta(days=1))
    assert provider.calls.count(2023) == 2
    assert not state.degraded


def test_year_transition_keeps_previous_year_on_empty_current(tmp_path):
    provider = Provider()
    refresh_treasury_snapshot(tmp_path, provider, now=NOW)
    january = datetime(2027, 1, 1, tzinfo=UTC)
    provider.now = january
    provider.failed.add(2027)
    state = refresh_treasury_snapshot(tmp_path, provider, now=january)
    assert provider.calls[-1] == 2027
    assert provider.calls.count(2026) == 1
    assert state.degraded and state.years[2026].series["DGS10"]
    assert 2027 not in state.years
    provider.failed.clear()
    provider.now = datetime(2027, 1, 4, tzinfo=UTC)
    state = refresh_treasury_snapshot(tmp_path, provider, now=provider.now)
    assert not state.degraded and 2027 in state.years


def test_failed_or_regressed_response_preserves_data_and_revision_is_accepted(tmp_path):
    provider = Provider()
    initial = refresh_treasury_snapshot(tmp_path, provider, now=NOW)
    provider.days = [2]
    provider.now = NOW + timedelta(minutes=15)
    state = refresh_treasury_snapshot(tmp_path, provider, now=NOW + timedelta(minutes=15))
    assert state.degraded and "lost historical" in state.errors[2026]
    assert state.years[2026] == initial.years[2026]
    provider.days, provider.value = [2, 3], 4.4
    provider.now = NOW + timedelta(minutes=30)
    state = refresh_treasury_snapshot(tmp_path, provider, now=NOW + timedelta(minutes=30), refresh_history=True)
    assert not state.degraded
    assert state.years[2026].series["DGS2"][0].value == 4.4
    ledger = json.loads((tmp_path / "manifest.json").read_text())
    assert ledger["years"]["2026"]["revisions"] == 4
    assert provider.calls.count(2021) == 1  # same UTC day, already checked
    provider.now = NOW + timedelta(days=1)
    state = refresh_treasury_snapshot(tmp_path, provider, now=NOW + timedelta(days=1), refresh_history=True)
    assert provider.calls.count(2021) == 2
    assert state.years[2021].series["DGS2"][0].value == 4.4


def test_corrupt_or_missing_history_degrades_without_replacing_it(tmp_path):
    provider = Provider()
    refresh_treasury_snapshot(tmp_path, provider, now=NOW)
    (tmp_path / "2023.json").unlink()
    (tmp_path / "2022.json").write_text("broken")
    provider.now = NOW + timedelta(days=1)
    state = refresh_treasury_snapshot(tmp_path, provider, now=NOW + timedelta(days=1))
    assert state.degraded and set(state.errors) == {2022, 2023}
    assert provider.calls.count(2022) == provider.calls.count(2023) == 1
    assert (tmp_path / "2022.json").read_text() == "broken"


def test_clock_rollback_and_missing_manifest_fail_closed(tmp_path):
    provider = Provider()
    refresh_treasury_snapshot(tmp_path, provider, now=NOW)
    with pytest.raises(SnapshotError, match="clock"):
        refresh_treasury_snapshot(tmp_path, provider, now=NOW - timedelta(seconds=1))
    (tmp_path / "manifest.json").unlink()
    with pytest.raises(SnapshotError, match="manifest missing"):
        refresh_treasury_snapshot(tmp_path, provider, now=NOW)
    assert len(provider.calls) == 6


def test_duplicate_concurrent_runs_share_local_lock(tmp_path):
    provider = Provider()
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: refresh_treasury_snapshot(tmp_path, provider, now=NOW), range(4)))
    assert len(provider.calls) == 6
    assert all(result.years == results[0].years and result.errors == results[0].errors for result in results)
    assert sum(result.history_rechecked for result in results) == 1


def test_invalid_provenance_and_regressed_check_time_retain_old_data(tmp_path):
    provider = Provider()
    original = refresh_treasury_snapshot(tmp_path, provider, now=NOW)
    provider.now = NOW - timedelta(seconds=1)
    state = refresh_treasury_snapshot(tmp_path, provider, now=NOW + timedelta(minutes=15))
    assert state.degraded and "request window" in state.errors[2026]
    assert state.years[2026] == original.years[2026]
    original_method = provider.get_yield_curve
    def bad_source(*, year):
        result = original_method(year=year)
        result["source"] = "https://fred.stlouisfed.org/"
        return result
    provider.get_yield_curve = bad_source
    provider.now = NOW + timedelta(minutes=30)
    state = refresh_treasury_snapshot(tmp_path, provider, now=NOW + timedelta(minutes=30))
    assert state.degraded and state.years[2026] == original.years[2026]


def test_interrupted_attempt_is_durable_and_does_not_repeat_immediately(tmp_path):
    provider = Provider()
    original_method = provider.get_yield_curve
    def interrupted(*, year):
        raise RuntimeError("process interrupted")
    provider.get_yield_curve = interrupted
    with pytest.raises(RuntimeError):
        refresh_treasury_snapshot(tmp_path, provider, now=NOW)
    ledger = json.loads((tmp_path / "manifest.json").read_text())
    assert ledger["years"]["2021"]["status"] == "pending"
    provider.get_yield_curve = original_method
    state = refresh_treasury_snapshot(tmp_path, provider, now=NOW)
    assert state.degraded and "pending" in state.errors[2021]
    assert 2021 not in provider.calls
    provider.now = NOW + timedelta(days=1)
    state = refresh_treasury_snapshot(tmp_path, provider, now=NOW + timedelta(days=1))
    assert not state.degraded and provider.calls.count(2021) == 1


def test_lost_manifest_entry_and_invalid_clock_are_not_reset(tmp_path):
    provider = Provider()
    with pytest.raises(ValueError, match="timezone"):
        refresh_treasury_snapshot(tmp_path, provider, now=NOW.replace(tzinfo=None))
    refresh_treasury_snapshot(tmp_path, provider, now=NOW)
    path = tmp_path / "manifest.json"
    ledger = json.loads(path.read_text())
    del ledger["years"]["2021"]
    path.write_text(json.dumps(ledger))
    with pytest.raises(SnapshotError, match="lost a year"):
        refresh_treasury_snapshot(tmp_path, provider, now=NOW)
    assert len(provider.calls) == 6


def test_history_rechecked_reports_actual_requests(tmp_path):
    provider = Provider()
    cold = refresh_treasury_snapshot(tmp_path, provider, now=NOW)
    assert cold.history_rechecked
    skipped = refresh_treasury_snapshot(tmp_path, provider, now=NOW, refresh_history=True)
    assert not skipped.history_rechecked
    provider.now = NOW + timedelta(minutes=15)
    current_only = refresh_treasury_snapshot(tmp_path, provider, now=provider.now, refresh_history=True)
    assert not current_only.history_rechecked
    provider.now = NOW + timedelta(days=1)
    historical = refresh_treasury_snapshot(tmp_path, provider, now=provider.now, refresh_history=True)
    assert historical.history_rechecked


def test_old_response_not_marked_complete_even_when_timestamp_did_not_regress(tmp_path):
    provider = Provider()
    old = refresh_treasury_snapshot(tmp_path, provider, now=NOW)
    state = refresh_treasury_snapshot(tmp_path, provider, now=NOW + timedelta(minutes=15))
    assert state.degraded and "request window" in state.errors[2026]
    assert state.years[2026] == old.years[2026]
    assert json.loads((tmp_path / "manifest.json").read_text())["years"]["2026"]["status"] == "failed"
    provider.now = NOW + timedelta(seconds=1)
    state = refresh_treasury_snapshot(tmp_path, provider, now=NOW + timedelta(hours=1))
    assert state.degraded and state.years[2026] == old.years[2026]


def test_future_response_beyond_explicit_skew_is_rejected(tmp_path):
    provider = Provider(NOW + timedelta(seconds=20))
    old = refresh_treasury_snapshot(tmp_path, provider, now=NOW)
    provider.now = NOW + timedelta(minutes=15)
    provider.now += timedelta(minutes=1)
    state = refresh_treasury_snapshot(tmp_path, provider, now=NOW + timedelta(minutes=15))
    assert state.degraded and state.years[2026] == old.years[2026]


def test_crash_after_atomic_year_commit_recovers_revision_ledger(tmp_path, monkeypatch):
    from pipeline.storage import treasury_snapshot as module
    provider = Provider()
    refresh_treasury_snapshot(tmp_path, provider, now=NOW)
    provider.now, provider.value = NOW + timedelta(minutes=15), 4.7
    real_save = module._save
    def interrupted_manifest(path, state):
        if path.name == "manifest.json" and state.years[2026].status == "complete":
            raise OSError("crash after year commit")
        return real_save(path, state)
    monkeypatch.setattr(module, "_save", interrupted_manifest)
    with pytest.raises(OSError, match="crash"):
        refresh_treasury_snapshot(tmp_path, provider, now=provider.now)
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    committed = json.loads((tmp_path / "2026.json").read_text())
    assert manifest["years"]["2026"]["status"] == "pending"
    assert manifest["years"]["2026"]["revisions"] == 0
    assert committed["ledger"]["revisions"] == 4
    monkeypatch.setattr(module, "_save", real_save)
    calls = len(provider.calls)
    recovered = refresh_treasury_snapshot(tmp_path, provider, now=provider.now)
    assert not recovered.degraded
    assert recovered.years[2026].series["DGS2"][0].value == 4.7
    assert len(provider.calls) == calls
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["years"]["2026"] == committed["ledger"]


def test_crash_before_atomic_year_commit_keeps_old_rows_and_pending(tmp_path, monkeypatch):
    from pipeline.storage import treasury_snapshot as module
    provider = Provider()
    old = refresh_treasury_snapshot(tmp_path, provider, now=NOW)
    provider.now, provider.value = NOW + timedelta(minutes=15), 4.7
    real_save = module._save
    def interrupted_year(path, state):
        if path.name == "2026.json":
            raise OSError("crash before year commit")
        return real_save(path, state)
    monkeypatch.setattr(module, "_save", interrupted_year)
    with pytest.raises(OSError, match="crash"):
        refresh_treasury_snapshot(tmp_path, provider, now=provider.now)
    monkeypatch.setattr(module, "_save", real_save)
    calls = len(provider.calls)
    recovered = refresh_treasury_snapshot(tmp_path, provider, now=provider.now)
    assert recovered.degraded and "pending" in recovered.errors[2026]
    assert recovered.years[2026] == old.years[2026]
    assert len(provider.calls) == calls


@pytest.mark.parametrize("minutes", [0, -1, 1.5, True, "15", 10**30])
def test_refresh_interval_must_be_positive_integer_before_network(tmp_path, minutes):
    provider = Provider()
    with pytest.raises(ValueError):
        refresh_treasury_snapshot(tmp_path, provider, now=NOW, refresh_interval_minutes=minutes)
    assert provider.calls == []
    assert not (tmp_path / "manifest.json").exists()
