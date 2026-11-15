import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest

from pipeline.storage.cloud_treasury import (
    CloudTreasuryPolicyError,
    CloudTreasuryRefreshError,
    _decode,
    initial_cloud_treasury_ledger,
    refresh_cloud_treasury_snapshot,
)
from pipeline.storage.github_checkpoint import (
    CheckpointConflict,
    CheckpointError,
    CheckpointSnapshot,
    CheckpointUncertain,
)
from pipeline.storage.treasury_snapshot import SnapshotError
from tests.pipeline.test_cloud_bls import Store
from tests.pipeline.test_treasury_snapshot import NOW, Provider


def store():
    result = Store()
    result.content = initial_cloud_treasury_ledger()
    return result


def refresh(state, provider, when=NOW, **kwargs):
    return refresh_cloud_treasury_snapshot(
        state, "treasury/ledger.json", provider, now=when, **kwargs
    )


def test_fresh_runner_restores_six_years_and_request_cadence():
    state, first = store(), Provider()
    original = refresh(state, first)
    assert first.calls == list(range(2021, 2027))
    second = Provider()
    assert refresh(state, second).years == original.years
    assert second.calls == []
    second.now = NOW + timedelta(minutes=15)
    assert refresh(state, second, second.now).years == original.years
    assert second.calls == []
    second.now = NOW + timedelta(days=1)
    result = refresh(state, second, second.now)
    assert second.calls == [2026]
    assert result.years[2025].checked_at == original.years[2025].checked_at
    assert result.years[2026].checked_at == second.now.isoformat()


def test_same_day_narrowing_confirms_remote_scrub_before_noop():
    state = store()
    refresh(state, Provider())
    provider = Provider()

    result = refresh(state, provider, series_ids=["DGS10"])

    durable = _decode(state.content)
    assert provider.calls == []
    assert set(durable.years[2026].data.series) == {"DGS10"}
    assert set(result.years[2026].series) == {"DGS10"}


def test_unconfirmed_policy_scrub_blocks_treasury_request():
    state = store()
    refresh(state, Provider())
    provider = Provider()
    state.on_write = lambda *_args: (_ for _ in ()).throw(CheckpointError("write denied"))

    with pytest.raises(CloudTreasuryPolicyError):
        refresh(state, provider, series_ids=["DGS10"])

    assert provider.calls == []
    assert set(_decode(state.content).years[2026].data.series) == {"DGS2", "DGS10"}


def test_empty_treasury_selection_removes_remote_year_data_without_request():
    state = store()
    refresh(state, Provider())
    provider = Provider()

    result = refresh(state, provider, series_ids=[])

    assert provider.calls == []
    assert result.years == {}
    assert _decode(state.content).years == {}
    assert all(
        entry.status == "excluded" and entry.attempted_at is not None
        for entry in _decode(state.content).ledger.years.values()
    )


def test_empty_selection_reauthorization_does_not_repeat_consumed_requests():
    state, first = store(), Provider()
    refresh(state, first)
    refresh(state, Provider(), series_ids=[])
    provider = Provider()

    result = refresh(state, provider, series_ids=["DGS10"])

    assert provider.calls == []
    assert result.degraded and result.years == {}
    assert set(result.errors) == set(range(2021, 2027))


def test_each_network_request_has_confirmed_durable_pending_reservation():
    state = store()
    provider = Provider()
    original_call = provider.get_yield_curve

    def get_yield_curve(*, year):
        raw = json.loads(state.content)
        assert raw["nonce"]
        assert raw["ledger"]["years"][str(year)]["status"] == "pending"
        assert raw["ledger"]["years"][str(year)]["attempted_at"] == NOW.isoformat().replace(
            "+00:00", "Z"
        )
        return original_call(year=year)

    provider.get_yield_curve = get_yield_curve
    assert not refresh(state, provider).degraded


@pytest.mark.parametrize("error", [CheckpointConflict, CheckpointUncertain, CheckpointError])
def test_reservation_rejection_never_contacts_treasury(error):
    state, provider = store(), Provider()

    def fail(*args):
        raise error("rejected")

    state.on_write = fail
    with pytest.raises(CloudTreasuryRefreshError):
        refresh(state, provider)
    assert provider.calls == []


def test_applied_uncertain_reservation_is_consumed_on_fresh_runner():
    state, provider = store(), Provider()

    def applied(state, previous, content):
        state.content = content
        state.head += 1
        raise CheckpointUncertain("response lost")

    state.on_write = applied
    with pytest.raises(CloudTreasuryRefreshError):
        refresh(state, provider)
    assert provider.calls == []
    state.on_write = None
    result = refresh(state, provider)
    assert result.degraded and 2021 in result.errors
    assert provider.calls == list(range(2022, 2027))


def test_failed_completion_retains_remote_reservation_and_prior_observations():
    state = store()
    first = refresh(state, Provider())
    later = NOW + timedelta(days=1)
    provider = Provider(later)
    provider.value = 5.0

    def fail_completion(state, previous, content):
        raw = json.loads(content)
        if raw["ledger"]["years"]["2026"]["status"] == "complete":
            raise CheckpointUncertain("completion lost")

    state.on_write = fail_completion
    with pytest.raises(CloudTreasuryRefreshError) as caught:
        refresh(state, provider, later)
    assert caught.value.snapshot.degraded
    assert caught.value.snapshot.years == first.years
    assert provider.calls == [2026]
    state.on_write = None
    result = refresh(state, Provider(later), later)
    assert result.degraded and result.years == first.years


def test_provider_failure_is_durable_and_retains_last_observation():
    state = store()
    initial = refresh(state, Provider())
    later = NOW + timedelta(days=1)
    provider = Provider(later)
    provider.failed = {2026}
    result = refresh(state, provider, later)
    assert result.degraded and result.years == initial.years
    assert json.loads(state.content)["ledger"]["years"]["2026"]["status"] == "failed"
    refresh(state, provider, later)
    assert provider.calls == [2026]


@pytest.mark.parametrize(
    "content",
    [b"", b"null", b"{}", b'{"version":1,"version":1}', b'{"version":true,"ledger":{},"years":{}}'],
)
def test_invalid_remote_state_never_bootstraps(content):
    state, provider = store(), Provider()
    state.content = content
    with pytest.raises(SnapshotError):
        refresh(state, provider)
    assert provider.calls == [] and state.writes == 0


def test_missing_remote_checkpoint_never_bootstraps():
    state, provider = store(), Provider()

    def missing(path):
        raise CheckpointError("checkpoint absent")

    state.read = missing
    with pytest.raises(CheckpointError):
        refresh(state, provider)
    assert provider.calls == [] and state.writes == 0


def test_missing_committed_year_fails_closed():
    state = store()
    refresh(state, Provider())
    raw = json.loads(state.content)
    del raw["years"]["2025"]
    state.content = json.dumps(raw).encode()
    provider = Provider(NOW + timedelta(days=1))
    with pytest.raises(SnapshotError):
        refresh(state, provider, provider.now)
    assert provider.calls == []


def test_changed_confirmation_does_not_authorize_network():
    state, provider = store(), Provider()
    read = state.read

    def changed(path):
        result = read(path)
        if state.head:
            return CheckpointSnapshot(
                result.head_sha, result.tree_sha, path, initial_cloud_treasury_ledger()
            )
        return result

    state.read = changed
    with pytest.raises(CloudTreasuryRefreshError):
        refresh(state, provider)
    assert provider.calls == []


def test_concurrent_runners_only_winner_calls_provider():
    state, barrier = store(), Barrier(2)
    read = state.read

    def concurrent_read(path):
        result = read(path)
        if result.head_sha == "0":
            barrier.wait(timeout=5)
        return result

    state.read = concurrent_read
    providers = [Provider(), Provider()]

    def run(provider):
        try:
            return refresh(state, provider)
        except CloudTreasuryRefreshError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, providers))
    assert sum(result is not None for result in results) == 1
    assert sorted(len(provider.calls) for provider in providers) == [0, 6]


def test_remote_clock_regression_fails_before_network():
    state = store()
    refresh(state, Provider())
    provider = Provider()
    with pytest.raises(SnapshotError):
        refresh(state, provider, NOW - timedelta(seconds=1))
    assert provider.calls == []


def test_crash_after_reservation_is_consumed_on_next_runner():
    state, provider = store(), Provider()

    def crash(*, year):
        raise KeyboardInterrupt

    provider.get_yield_curve = crash
    with pytest.raises(KeyboardInterrupt):
        refresh(state, provider)
    next_provider = Provider()
    result = refresh(state, next_provider)
    assert result.degraded and 2021 in result.errors
    assert next_provider.calls == list(range(2022, 2027))


def test_revision_is_committed_and_regression_retains_confirmed_year():
    state = store()
    first = refresh(state, Provider())
    provider = Provider(NOW + timedelta(days=1))
    provider.value = 5.0
    revised = refresh(state, provider, provider.now)
    assert revised.years[2026] != first.years[2026]
    assert json.loads(state.content)["ledger"]["years"]["2026"]["revisions"] == 4
    provider.now += timedelta(days=1)
    provider.days = [2]
    rejected = refresh(state, provider, provider.now)
    assert rejected.degraded and rejected.years == revised.years
    assert json.loads(state.content)["ledger"]["years"]["2026"]["status"] == "failed"


def test_future_remote_retrieval_timestamp_fails_closed():
    state = store()
    refresh(state, Provider())
    raw = json.loads(state.content)
    raw["years"]["2026"]["data"]["checked_at"] = (NOW + timedelta(days=2)).isoformat()
    state.content = json.dumps(raw).encode()
    provider = Provider()
    with pytest.raises(SnapshotError):
        refresh(state, provider)
    assert provider.calls == []


def test_cloud_interval_comes_from_selected_source_policy(tmp_path):
    import yaml

    from pipeline.settings import Settings

    settings = Settings(_env_file=None)
    policy = settings.load_public_source_policy().model_dump()
    policy["sources"]["treasury"]["cadence_minutes"] = 2880
    (tmp_path / "public_sources.yaml").write_text(yaml.safe_dump(policy))
    configured = Settings(_env_file=None, config_dir=tmp_path)
    state, provider = store(), Provider()
    refresh(state, provider, settings=configured)
    provider.calls.clear()
    provider.now = NOW + timedelta(days=1)
    refresh(state, provider, provider.now, settings=configured)
    assert provider.calls == []
    provider.now = NOW + timedelta(days=2)
    refresh(state, provider, provider.now, settings=configured)
    assert provider.calls == [2026]
