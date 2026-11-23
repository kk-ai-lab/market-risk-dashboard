from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Lock

import pytest

from pipeline.providers.base import ProviderError
from pipeline.providers.bls import SERIES
from pipeline.storage.bls_snapshot import SnapshotError
from pipeline.storage.cloud_bls import (
    _decode,
    CloudBlsRefreshError,
    initial_cloud_bls_ledger,
    refresh_cloud_bls_snapshot,
)
from pipeline.storage.github_checkpoint import (
    CheckpointConflict,
    CheckpointError,
    CheckpointSnapshot,
    CheckpointUncertain,
)

NOW = datetime(2026, 11, 14, 12, tzinfo=UTC)


class Store:
    def __init__(self):
        self.content = initial_cloud_bls_ledger()
        self.head = 0
        self.lock = Lock()
        self.writes = 0
        self.on_write = None

    def read(self, path):
        with self.lock:
            return CheckpointSnapshot(str(self.head), "tree", path, self.content)

    def write(self, snapshot, content, message):
        with self.lock:
            self.writes += 1
            if self.on_write:
                self.on_write(self, snapshot, content)
            if snapshot.head_sha != str(self.head):
                raise CheckpointConflict("head changed")
            self.content = content
            self.head += 1
            return str(self.head)


class Provider:
    def __init__(self, fail=False):
        self.calls = 0
        self.fail = fail

    def get_series_batch(self):
        self.calls += 1
        if self.fail:
            raise ProviderError("unavailable")
        return {key: [{"date": "2026-10-01", "value": 2.0}] for key in SERIES}


def refresh(store, provider, when=NOW, **kwargs):
    return refresh_cloud_bls_snapshot(store, "bls.json", provider, now=when,
                                      clock=lambda: when, **kwargs)


def test_once_per_utc_day_and_next_day_refresh():
    store, provider = Store(), Provider()
    state = refresh(store, provider)
    assert state.attempt_status == "complete"
    assert all(series.status == "fresh" for series in state.series.values())
    assert refresh(store, provider) == state
    refresh(store, provider, NOW + timedelta(days=1))
    assert provider.calls == 2


def test_same_day_narrowing_confirms_checkpoint_scrub_before_noop():
    store = Store()
    refresh(store, Provider())
    provider = Provider()

    state = refresh(store, provider, NOW + timedelta(hours=2), series_ids=["UNRATE"])

    persisted = _decode(store.content).snapshot
    assert provider.calls == 0
    assert persisted.series["UNRATE"].status == "fresh"
    assert all(persisted.series[key].status == "missing" for key in set(SERIES) - {"UNRATE"})
    assert state == persisted


def test_empty_bls_selection_scrubs_checkpoint_without_request():
    store = Store()
    refresh(store, Provider())
    provider = Provider()

    state = refresh(store, provider, series_ids=[])

    assert provider.calls == 0
    assert all(series.status == "missing" for series in state.series.values())
    assert all(series.status == "missing" for series in _decode(store.content).snapshot.series.values())


def test_failure_keeps_old_values_and_spends_day():
    store = Store()
    first = refresh(store, Provider())
    provider = Provider(fail=True)
    state = refresh(store, provider, NOW + timedelta(days=1))
    assert state.attempt_status == "failed"
    assert all(series.status == "degraded" for series in state.series.values())
    assert state.series["UNRATE"].rows == first.series["UNRATE"].rows
    assert state.series["UNRATE"].fetched_at == NOW
    refresh(store, provider, NOW + timedelta(days=1))
    assert provider.calls == 1


@pytest.mark.parametrize("error", [CheckpointConflict, CheckpointUncertain, CheckpointError])
def test_reservation_errors_never_call_provider(error):
    store, provider = Store(), Provider()
    def fail(*args):
        raise error("rejected")
    store.on_write = fail
    with pytest.raises(CloudBlsRefreshError) as caught:
        refresh(store, provider)
    assert isinstance(caught.value.__cause__, error)
    assert provider.calls == 0


def test_ambiguous_applied_reservation_is_consumed():
    store, provider = Store(), Provider()
    def apply_then_fail(store, snapshot, content):
        store.content = content
        store.head += 1
        raise CheckpointUncertain("lost response")
    store.on_write = apply_then_fail
    with pytest.raises(CloudBlsRefreshError) as caught:
        refresh(store, provider)
    assert isinstance(caught.value.__cause__, CheckpointUncertain)
    store.on_write = None
    assert refresh(store, provider).attempt_status == "pending"
    assert provider.calls == 0


def test_confirmation_must_match_reservation():
    store, provider = Store(), Provider()
    original_read = store.read
    def read(path):
        snapshot = original_read(path)
        if store.head:
            return CheckpointSnapshot(snapshot.head_sha, "tree", path, initial_cloud_bls_ledger())
        return snapshot
    store.read = read
    with pytest.raises(CheckpointError):
        refresh(store, provider)
    assert provider.calls == 0


def test_crash_after_reservation_does_not_repeat():
    store = Store()
    class Crash:
        def get_series_batch(self):
            raise RuntimeError("crashed")
    with pytest.raises(RuntimeError):
        refresh(store, Crash())
    provider = Provider()
    assert refresh(store, provider).attempt_status == "pending"
    assert provider.calls == 0


def test_completion_conflict_retries_only_persistence():
    store, provider = Store(), Provider()
    def unrelated_commit(store, snapshot, content):
        if store.writes == 2:
            store.head += 1
            raise CheckpointConflict("another file changed")
    store.on_write = unrelated_commit
    assert refresh(store, provider).attempt_status == "complete"
    assert provider.calls == 1
    assert store.writes == 3


def test_uncertain_completion_reconciles_applied_write():
    store, provider = Store(), Provider()
    def apply_then_fail(store, snapshot, content):
        if store.writes == 2:
            store.content = content
            store.head += 1
            raise CheckpointUncertain("lost response")
    store.on_write = apply_then_fail
    assert refresh(store, provider).attempt_status == "complete"
    assert provider.calls == 1
    assert store.writes == 2


def test_failed_completion_does_not_repeat_provider():
    store, provider = Store(), Provider()
    def fail_completion(store, snapshot, content):
        if store.writes > 1:
            raise CheckpointError("unavailable")
    store.on_write = fail_completion
    with pytest.raises(CheckpointError):
        refresh(store, provider)
    store.on_write = None
    state = refresh(store, provider)
    assert state.attempt_status == "pending"
    assert provider.calls == 1


def test_concurrent_reservations_have_one_winner():
    store, provider = Store(), Provider()
    barrier = Barrier(2)
    original_read = store.read
    def read(path):
        snapshot = original_read(path)
        if snapshot.head_sha == "0":
            barrier.wait(timeout=5)
        return snapshot
    store.read = read
    def run():
        try:
            return refresh(store, provider)
        except CloudBlsRefreshError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        states = list(pool.map(lambda _: run(), range(2)))
    assert provider.calls == 1
    assert sum(state is not None for state in states) == 1


@pytest.mark.parametrize("content", [b"{}", b"null", b"not json", b"\xff",
    b'{"version":1,"version":1}', b'{"version":true}', b'{"version":NaN}'])
def test_corruption_fails_closed(content):
    store, provider = Store(), Provider()
    store.content = content
    with pytest.raises(SnapshotError):
        refresh(store, provider)
    assert provider.calls == store.writes == 0


def test_future_and_naive_clocks_fail_closed():
    store, provider = Store(), Provider()
    refresh(store, provider)
    with pytest.raises(SnapshotError):
        refresh(store, provider, NOW - timedelta(days=1))
    with pytest.raises(ValueError):
        refresh(store, provider, NOW.replace(tzinfo=None))
    assert provider.calls == 1


def test_midnight_between_reservation_and_request_consumes_without_call():
    store, provider = Store(), Provider()
    state = refresh_cloud_bls_snapshot(store, "bls.json", provider, now=NOW,
                                       clock=lambda: NOW + timedelta(days=1))
    assert state.attempt_status == "pending"
    assert provider.calls == 0


def test_timezone_uses_utc_day():
    from datetime import timezone
    store, provider = Store(), Provider()
    refresh(store, provider)
    refresh(store, provider, NOW.astimezone(timezone(timedelta(hours=14))))
    assert provider.calls == 1


def test_missing_remote_is_not_initialized():
    store, provider = Store(), Provider()
    def missing(path):
        raise CheckpointError("missing")
    store.read = missing
    with pytest.raises(CheckpointError):
        refresh(store, provider)
    assert store.writes == provider.calls == 0


@pytest.mark.parametrize("attempt_status", ["pending", "failed", "never"])
def test_semantically_corrupt_snapshot_never_calls_provider(attempt_status):
    import json

    store = Store()
    refresh(store, Provider())
    ledger = json.loads(store.content)
    ledger["snapshot"]["attempt_status"] = attempt_status
    if attempt_status == "never":
        ledger["snapshot"]["attempted_on"] = None
        ledger["nonce"] = None
    store.content = json.dumps(ledger).encode()
    previous_writes = store.writes
    provider = Provider()
    with pytest.raises(SnapshotError):
        refresh(store, provider)
    assert provider.calls == 0
    assert store.writes == previous_writes


def test_crashed_cloud_attempt_keeps_old_values_degraded():
    store = Store()
    previous = refresh(store, Provider())

    class Crash:
        def get_series_batch(self):
            raise RuntimeError("crashed")

    tomorrow = NOW + timedelta(days=1)
    with pytest.raises(RuntimeError):
        refresh(store, Crash(), tomorrow)
    provider = Provider()
    state = refresh(store, provider, tomorrow)
    assert state.attempt_status == "pending"
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
def test_cloud_fresh_timestamp_must_match_utc_attempt_date(fetched_at, valid):
    import json

    store = Store()
    refresh(store, Provider())
    ledger = json.loads(store.content)
    ledger["snapshot"]["series"]["UNRATE"]["fetched_at"] = fetched_at
    store.content = json.dumps(ledger).encode()
    provider = Provider()
    previous_writes = store.writes
    if valid:
        assert refresh(store, provider).series["UNRATE"].status == "fresh"
    else:
        with pytest.raises(SnapshotError):
            refresh(store, provider)
    assert provider.calls == 0
    assert store.writes == previous_writes


def test_cloud_complete_degraded_series_can_keep_old_fetch_date():
    store = Store()
    refresh(store, Provider())

    class Empty:
        def get_series_batch(self):
            return {}

    tomorrow = NOW + timedelta(days=1)
    refresh(store, Empty(), tomorrow)
    result = refresh(store, Provider(), tomorrow)
    assert result.attempt_status == "complete"
    assert all(series.status == "degraded" and series.fetched_at == NOW
               for series in result.series.values())


@pytest.mark.parametrize("failure", ["reserve", "confirm_read", "confirm_corrupt", "complete", "complete_read", "complete_corrupt"])
def test_checkpoint_failures_expose_only_trusted_degraded_history(failure):
    store = Store()
    old = refresh(store, Provider())
    previous_writes = store.writes
    original_read = store.read

    def fail_write(store, snapshot, content):
        relative_write = store.writes - previous_writes
        if failure == "reserve" and relative_write == 1:
            raise CheckpointUncertain("reservation outcome unknown")
        if failure.startswith("complete") and relative_write >= 2:
            raise CheckpointUncertain("completion outcome unknown")

    def fail_read(path):
        relative_write = store.writes - previous_writes
        if (failure.startswith("confirm") and relative_write == 1
                or failure in {"complete_read", "complete_corrupt"} and relative_write >= 2):
            if failure.endswith("corrupt"):
                snapshot = original_read(path)
                return CheckpointSnapshot(snapshot.head_sha, snapshot.tree_sha, path, b"{}")
            raise CheckpointUncertain("read unavailable")
        return original_read(path)

    store.on_write = fail_write
    store.read = fail_read
    provider = Provider()
    with pytest.raises(CloudBlsRefreshError) as caught:
        refresh(store, provider, NOW + timedelta(days=1))
    fallback = caught.value.snapshot
    assert fallback.attempted_on == old.attempted_on
    assert all(series.status == "degraded" and series.fetched_at == NOW
               and series.rows == old.series[key].rows
               for key, series in fallback.series.items())
    assert provider.calls == (1 if failure.startswith("complete") else 0)
    store.read = original_read
    store.on_write = None
    if failure != "reserve":
        assert refresh(store, provider, NOW + timedelta(days=1)).attempt_status == "pending"
        assert provider.calls == (1 if failure.startswith("complete") else 0)


def test_initial_corruption_does_not_claim_fallback_snapshot():
    store = Store()
    store.content = b"{}"
    with pytest.raises(SnapshotError) as caught:
        refresh(store, Provider())
    assert not hasattr(caught.value, "snapshot")
