from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from pipeline.providers.base import ProviderError
from pipeline.providers.bls import SERIES
from pipeline.storage.bls_snapshot import SnapshotError, refresh_bls_snapshot

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)


class Provider:
    def __init__(self, rows=None, fail=False):
        self.calls = 0
        self.rows = rows if rows is not None else {
            key: [{"date": "2026-10-01", "value": 2.0}] for key in SERIES
        }
        self.fail = fail

    def get_series_batch(self):
        self.calls += 1
        if self.fail:
            raise ProviderError("unavailable")
        return self.rows


def test_same_day_reuses_snapshot_across_instances(tmp_path):
    path = tmp_path / "bls.json"
    provider = Provider()
    first = refresh_bls_snapshot(path, provider, now=NOW)
    again = refresh_bls_snapshot(path, provider, now=NOW + timedelta(hours=2))
    assert provider.calls == 1
    assert first == again
    assert first.series["UNRATE"].status == "fresh"
    assert first.series["UNRATE"].fetched_at == NOW


def test_same_day_policy_narrowing_scrubs_unapproved_series_without_request(tmp_path):
    path = tmp_path / "bls.json"
    refresh_bls_snapshot(path, Provider(), now=NOW)
    provider = Provider()

    state = refresh_bls_snapshot(path, provider, now=NOW + timedelta(hours=2), series_ids=["UNRATE"])

    assert provider.calls == 0
    assert state.series["UNRATE"].status == "fresh"
    assert all(state.series[key].status == "missing" for key in set(SERIES) - {"UNRATE"})


def test_bls_request_and_snapshot_follow_the_approved_series_selection(tmp_path):
    path = tmp_path / "bls.json"

    class SelectedProvider:
        def __init__(self):
            self.requests = []

        def get_series_batch(self, series_ids=None):
            self.requests.append(tuple(series_ids))
            return {key: [{"date": "2026-10-01", "value": 2.0}] for key in series_ids}

    provider = SelectedProvider()
    state = refresh_bls_snapshot(path, provider, now=NOW, series_ids=["UNRATE"])

    assert provider.requests == [("UNRATE",)]
    assert state.series["UNRATE"].status == "fresh"
    assert all(state.series[key].status == "missing" for key in set(SERIES) - {"UNRATE"})


def test_failure_and_empty_series_preserve_last_observation_time(tmp_path):
    path = tmp_path / "bls.json"
    first = Provider()
    refresh_bls_snapshot(path, first, now=NOW)
    rows = {key: [{"date": "2026-10-01", "value": 2.0}, {"date": "2026-11-01", "value": 3.0}] for key in SERIES}
    rows["UNRATE"] = []
    second = refresh_bls_snapshot(path, Provider(rows), now=NOW + timedelta(days=1))
    assert second.series["UNRATE"].status == "degraded"
    assert second.series["UNRATE"].fetched_at == NOW
    assert second.series["PAYEMS"].status == "fresh"
    assert second.series["PAYEMS"].fetched_at == NOW + timedelta(days=1)
    failed_provider = Provider(fail=True)
    third = refresh_bls_snapshot(path, failed_provider, now=NOW + timedelta(days=2))
    assert third.attempt_status == "failed"
    assert all(item.status == "degraded" for item in third.series.values())
    refresh_bls_snapshot(path, failed_provider, now=NOW + timedelta(days=2, hours=1))
    assert failed_provider.calls == 1


def test_crash_after_attempt_does_not_repeat_request(tmp_path):
    class Crash:
        def get_series_batch(self):
            raise RuntimeError("interrupted")

    path = tmp_path / "bls.json"
    with pytest.raises(RuntimeError):
        refresh_bls_snapshot(path, Crash(), now=NOW)
    provider = Provider()
    recovered = refresh_bls_snapshot(path, provider, now=NOW + timedelta(hours=1))
    assert recovered.attempt_status == "pending"
    assert all(item.status == "missing" for item in recovered.series.values())
    assert provider.calls == 0


def test_corrupt_or_future_state_does_not_reset_budget(tmp_path):
    path = tmp_path / "bls.json"
    path.write_text("not json")
    provider = Provider()
    with pytest.raises(SnapshotError):
        refresh_bls_snapshot(path, provider, now=NOW)
    assert provider.calls == 0
    path.unlink()
    refresh_bls_snapshot(path, provider, now=NOW + timedelta(days=1))
    with pytest.raises(SnapshotError):
        refresh_bls_snapshot(path, provider, now=NOW)
    assert provider.calls == 1


def test_valid_json_with_missing_fields_does_not_reset_budget(tmp_path):
    path = tmp_path / "bls.json"
    path.write_text("{}")
    provider = Provider()
    with pytest.raises(SnapshotError):
        refresh_bls_snapshot(path, provider, now=NOW)
    assert provider.calls == 0


def test_invalid_encoding_does_not_reset_budget(tmp_path):
    path = tmp_path / "bls.json"
    path.write_bytes(b"\xff")
    provider = Provider()
    with pytest.raises(SnapshotError):
        refresh_bls_snapshot(path, provider, now=NOW)
    assert provider.calls == 0


def test_simultaneous_callers_share_daily_attempt(tmp_path):
    path = tmp_path / "bls.json"
    provider = Provider()
    with ThreadPoolExecutor(max_workers=4) as executor:
        states = list(executor.map(lambda _: refresh_bls_snapshot(path, provider, now=NOW), range(4)))
    assert provider.calls == 1
    assert all(state.attempt_status == "complete" for state in states)


def test_invalid_or_future_data_cannot_replace_good_history(tmp_path):
    path = tmp_path / "bls.json"
    refresh_bls_snapshot(path, Provider(), now=NOW)
    rows = {key: [{"date": "2027-01-01", "value": 2}] for key in SERIES}
    rows["PAYEMS"] = [{"date": "2026-10-01", "value": float("nan")}]
    state = refresh_bls_snapshot(path, Provider(rows), now=NOW + timedelta(days=1))
    assert all(item.status == "degraded" for item in state.series.values())
    assert all(item.fetched_at == NOW for item in state.series.values())


def test_short_or_older_series_does_not_replace_full_history(tmp_path):
    path = tmp_path / "bls.json"
    history = [{"date": f"{year}-{month:02}-01", "value": float(month)} for year in (2024, 2025) for month in range(1, 13)]
    refresh_bls_snapshot(path, Provider({key: history for key in SERIES}), now=NOW)
    rows = {key: [{"date": "2025-12-01", "value": 2.0}] for key in SERIES}
    rows["PAYEMS"] = [{"date": "2024-01-01", "value": 2.0}]
    state = refresh_bls_snapshot(path, Provider(rows), now=NOW + timedelta(days=1))
    assert all(item.status == "degraded" for item in state.series.values())
    assert all(len(item.rows) == 24 for item in state.series.values())
    assert all(item.fetched_at == NOW for item in state.series.values())


def test_repeated_small_truncations_do_not_reduce_history(tmp_path):
    path = tmp_path / "bls.json"
    rows = [{"date": f"{year}-{month:02}-01", "value": float(month)} for year in (2024, 2025) for month in range(1, 13)]
    refresh_bls_snapshot(path, Provider({key: rows for key in SERIES}), now=NOW)
    first = refresh_bls_snapshot(path, Provider({key: rows[12:] for key in SERIES}), now=NOW + timedelta(days=1))
    second = refresh_bls_snapshot(path, Provider({key: rows[-1:] for key in SERIES}), now=NOW + timedelta(days=2))
    assert all(item.status == "degraded" and len(item.rows) == 24 for item in first.series.values())
    assert all(item.status == "degraded" and len(item.rows) == 24 for item in second.series.values())


def test_older_latest_month_is_rejected_even_with_all_other_months(tmp_path):
    path = tmp_path / "bls.json"
    first_rows = [{"date": "2020-01-01", "value": 2}]
    refresh_bls_snapshot(path, Provider({key: first_rows for key in SERIES}), now=NOW)
    older = {key: [{"date": "2019-12-01", "value": 1}] for key in SERIES}
    state = refresh_bls_snapshot(path, Provider(older), now=NOW + timedelta(days=1))
    assert all(item.status == "degraded" and item.rows[-1].date.isoformat() == "2020-01-01" for item in state.series.values())


@pytest.mark.parametrize("attempt_status", ["pending", "failed", "never"])
def test_inconsistent_fresh_state_fails_closed(tmp_path, attempt_status):
    import json

    path = tmp_path / "bls.json"
    refresh_bls_snapshot(path, Provider(), now=NOW)
    state = json.loads(path.read_text())
    state["attempt_status"] = attempt_status
    if attempt_status == "never":
        state["attempted_on"] = None
    path.write_text(json.dumps(state))
    provider = Provider()
    with pytest.raises(SnapshotError):
        refresh_bls_snapshot(path, provider, now=NOW)
    assert provider.calls == 0


@pytest.mark.parametrize("failed", [False, True])
def test_pending_or_failed_attempt_preserves_degraded_old_values(tmp_path, failed):
    path = tmp_path / "bls.json"
    previous = refresh_bls_snapshot(path, Provider(), now=NOW)

    class Interrupted:
        def get_series_batch(self):
            if failed:
                raise ProviderError("unavailable")
            raise RuntimeError("crashed")

    if failed:
        refresh_bls_snapshot(path, Interrupted(), now=NOW + timedelta(days=1))
    else:
        with pytest.raises(RuntimeError):
            refresh_bls_snapshot(path, Interrupted(), now=NOW + timedelta(days=1))
    provider = Provider()
    state = refresh_bls_snapshot(path, provider, now=NOW + timedelta(days=1))
    assert state.attempt_status == ("failed" if failed else "pending")
    assert provider.calls == 0
    for key, series in state.series.items():
        assert series.status == "degraded"
        assert series.rows == previous.series[key].rows
        assert series.fetched_at == previous.series[key].fetched_at


@pytest.mark.parametrize("fetched_at,valid", [
    ("2026-11-13T23:00:00Z", False),
    ("2026-11-14T00:30:00+02:00", False),
    ("2026-11-13T23:30:00-02:00", True),
])
def test_fresh_timestamp_must_match_utc_attempt_date(tmp_path, fetched_at, valid):
    import json

    path = tmp_path / "bls.json"
    refresh_bls_snapshot(path, Provider(), now=NOW)
    state = json.loads(path.read_text())
    state["series"]["UNRATE"]["fetched_at"] = fetched_at
    path.write_text(json.dumps(state))
    provider = Provider()
    if valid:
        result = refresh_bls_snapshot(path, provider, now=NOW)
        assert result.series["UNRATE"].status == "fresh"
    else:
        with pytest.raises(SnapshotError):
            refresh_bls_snapshot(path, provider, now=NOW)
    assert provider.calls == 0


def test_complete_degraded_series_can_keep_old_fetch_date(tmp_path):
    path = tmp_path / "bls.json"
    refresh_bls_snapshot(path, Provider(), now=NOW)
    tomorrow = NOW + timedelta(days=1)
    refresh_bls_snapshot(path, Provider(rows={}), now=tomorrow)
    result = refresh_bls_snapshot(path, Provider(), now=tomorrow)
    assert result.attempt_status == "complete"
    assert all(series.status == "degraded" and series.fetched_at == NOW
               for series in result.series.values())
