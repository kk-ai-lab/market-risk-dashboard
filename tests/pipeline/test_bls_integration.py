from datetime import UTC, datetime

from pipeline.collectors.macro import MacroCollector
from pipeline.providers.bls import SERIES
from pipeline.settings import Settings
from pipeline.storage.bls_snapshot import refresh_bls_snapshot
from tests.pipeline.test_macro import _all_series_rows, _FakeRegistry


class BatchProvider:
    def get_series_batch(self):
        return {key: ([] if key == "PAYEMS" else [{"date": "2026-10-01", "value": 4.0}]) for key in SERIES}


def test_bls_snapshot_routes_supported_series_without_fred_fallback(tmp_path, monkeypatch):
    snapshot = refresh_bls_snapshot(tmp_path / "bls.json", BatchProvider(), now=datetime(2026, 11, 14, tzinfo=UTC))
    registry = _FakeRegistry(_all_series_rows())
    collector = MacroCollector(registry, Settings(_env_file=None), bls_snapshot=snapshot)
    monkeypatch.setattr(collector, "_collect_fedwatch", lambda: None)
    macro, meta = collector.collect()
    by_key = {item.key: item for group in (macro.inflation, macro.labor) for item in group}
    assert by_key["cpiaucsl"].value == 4.0
    assert by_key["cpiaucsl"].source == "BLS"
    assert by_key["cpiaucsl"].observation_period == "2026-10"
    assert by_key["cpiaucsl"].updated_at is None
    assert by_key["cpiaucsl"].fetched_at == "2026-11-14T00:00:00Z"
    assert by_key["cpiaucsl"].freshness_policy.delayed_after_minutes == 54_000
    assert by_key["cpiaucsl"].freshness_policy.stale_after_minutes == 108_000
    assert by_key["payems"].value is None
    assert by_key["payems"].status == "missing"
    assert not ({call[2] for call in registry.calls} & set(SERIES))
    assert meta["provider"]["provider"] == "mixed"
    assert meta["degraded"]


def test_opt_in_macro_collection_reuses_bls_between_pipeline_runs(tmp_path, monkeypatch):
    from pipeline import run
    from pipeline.providers import bls

    class CountedProvider(BatchProvider):
        calls = 0

        def __init__(self, settings):
            pass

        def get_series_batch(self):
            type(self).calls += 1
            return super().get_series_batch()

    registry = _FakeRegistry(_all_series_rows())
    configured = Settings(_env_file=None, bls_enabled=True, artifacts_dir=tmp_path)
    monkeypatch.setattr(run, "settings", configured)
    monkeypatch.setattr(run, "build_registry", lambda _settings: registry)
    monkeypatch.setattr(bls, "BlsProvider", CountedProvider)
    monkeypatch.setattr(MacroCollector, "_collect_fedwatch", lambda self: None)
    first = run._run_collection("macro-only")
    second = run._run_collection("macro-only")
    assert CountedProvider.calls == 1
    assert first["macro"].inflation[0].source == "BLS"
    assert second["macro"].inflation[0].observation_period == "2026-10"
    assert not ({call[2] for call in registry.calls} & set(SERIES))


def test_corrupt_bls_ledger_preserves_other_macro_series_and_makes_no_bls_request(tmp_path, monkeypatch):
    from pipeline import run
    from pipeline.providers import bls

    class NeverRequest:
        def __init__(self, settings):
            pass

        def get_series_batch(self):
            raise AssertionError("corrupt state must not reset the budget")

    ledger = tmp_path / "bls" / "snapshot.json"
    ledger.parent.mkdir()
    ledger.write_text("{}")
    registry = _FakeRegistry(_all_series_rows())
    monkeypatch.setattr(run, "settings", Settings(_env_file=None, bls_enabled=True, artifacts_dir=tmp_path))
    monkeypatch.setattr(run, "build_registry", lambda _settings: registry)
    monkeypatch.setattr(bls, "BlsProvider", NeverRequest)
    monkeypatch.setattr(MacroCollector, "_collect_fedwatch", lambda self: None)
    result = run._run_collection("macro-only")
    assert result["macro"].rates[0].value is not None
    assert result["macro"].inflation[0].value is None
    assert result["macro_meta"]["provider"]["provider"] == "fred"
    assert result["macro_meta"]["degraded"]
    assert ledger.read_text() == "{}"


def test_bls_history_does_not_merge_into_existing_fred_archive(tmp_path):
    from pipeline.collectors.macro import SERIES_GROUPS
    from pipeline.storage import StorageWriter
    from pipeline.storage.macro_history import write_macro_history

    writer = StorageWriter(tmp_path / "data")
    writer.write_slices("macro/CPIAUCSL", [{"date": "2026-09-01", "value": 100}])
    write_macro_history(writer, {"cpiaucsl": [{"date": "2026-10-01", "value": 2.5}]}, SERIES_GROUPS, series_sources={"cpiaucsl": "BLS"})
    assert writer.read_history("macro/CPIAUCSL", "daily") == [{"date": "2026-09-01", "value": 100}]
    assert writer.read_history("macro/bls/CPIAUCSL", "daily") == [{"date": "2026-10-01", "value": 2.5}]


def test_full_pipeline_writes_bls_sources_and_reuses_snapshot(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace

    from pipeline import run
    from pipeline.providers import bls
    from pipeline.schemas import registry as datasets
    from tests.pipeline.factories import PAYLOAD_BUILDERS

    class CountedProvider(BatchProvider):
        calls = 0

        def __init__(self, settings):
            pass

        def get_series_batch(self):
            type(self).calls += 1
            return super().get_series_batch()

    configured = Settings(_env_file=None, bls_enabled=True, data_dir=tmp_path / "data", artifacts_dir=tmp_path / "artifacts")
    payloads = {key: datasets.require(key).model.model_fields["payload"].annotation.model_validate(builder()) for key, builder in PAYLOAD_BUILDERS.items()}
    market = {key: payloads[key] for key in ("equities", "sectors", "crypto", "commodities")}
    market.update(histories={}, degraded=[], provider_status={}, data_quality=1.0)
    registry = _FakeRegistry(_all_series_rows())
    monkeypatch.setattr(run, "settings", configured)
    monkeypatch.setattr(run, "build_registry", lambda _settings: registry)
    monkeypatch.setattr(bls, "BlsProvider", CountedProvider)
    monkeypatch.setattr(MacroCollector, "_collect_fedwatch", lambda self: None)
    monkeypatch.setattr(run, "MarketCollector", lambda *args: SimpleNamespace(collect=lambda: market))
    for name, key in (("CalendarCollector", "calendar"), ("NewsCollector", "news")):
        monkeypatch.setattr(run, name, lambda *args, key=key: SimpleNamespace(
            collect=lambda: (payloads[key], {"degraded": [], "provider_status": {}, "data_quality": 1.0}),
        ))
    assert run.main(["--full"]) == 0
    assert run.main(["--full"]) == 0
    assert CountedProvider.calls == 1
    macro = json.loads((configured.data_dir / "latest" / "macro.json").read_text())
    cpi = next(item for item in macro["payload"]["inflation"] if item["key"] == "cpiaucsl")
    assert cpi["source"] == "BLS"
    assert cpi["observation_period"] == "2026-10"
    assert cpi["updated_at"] is None
    assert (configured.data_dir / "history" / "macro" / "bls" / "CPIAUCSL" / "daily.json").exists()
    assert (configured.data_dir / "latest" / "risk.json").exists()
    assert (configured.data_dir / "latest" / "dashboard.json").exists()


class FileCheckpoint:
    instances = []
    state_path = None
    uncertain = False

    def __init__(self, owner, repo, branch, token):
        self.closed = False
        self.instances.append(self)
        assert (owner, repo, branch, token) == ("owner", "repo", "test-ledger", "test-secret")

    def read(self, path):
        from pipeline.storage.github_checkpoint import CheckpointSnapshot
        assert path == "state/bls.json"
        return CheckpointSnapshot("a" * 40, "b" * 40, path, self.state_path.read_bytes())

    def write(self, snapshot, content, message):
        from pipeline.storage.github_checkpoint import CheckpointUncertain
        if self.uncertain:
            raise CheckpointUncertain("uncertain fake reservation")
        self.state_path.write_bytes(content)
        return "c" * 40

    def close(self):
        self.closed = True


def _cloud_collection_setup(tmp_path, monkeypatch, **overrides):
    from pipeline import run
    from pipeline.providers import bls
    from pipeline.storage import github_checkpoint

    class CountedProvider(BatchProvider):
        calls = 0

        def __init__(self, settings):
            pass

        def get_series_batch(self):
            type(self).calls += 1
            return super().get_series_batch()

    config = dict(bls_cloud_enabled=True, bls_enabled=True, cloud_state_repo="owner/repo",
                  cloud_state_branch="test-ledger", bls_checkpoint_path="state/bls.json",
                  cloud_state_token="test-secret", artifacts_dir=tmp_path / "local")
    config.update(overrides)
    configured = Settings(_env_file=None, **config)
    registry = _FakeRegistry(_all_series_rows())
    monkeypatch.setattr(run, "settings", configured)
    monkeypatch.setattr(run, "build_registry", lambda _settings: registry)
    monkeypatch.setattr(bls, "BlsProvider", CountedProvider)
    monkeypatch.setattr(github_checkpoint, "GitHubCheckpoint", FileCheckpoint)
    monkeypatch.setattr(FileCheckpoint, "state_path", tmp_path / "durable.json")
    monkeypatch.setattr(FileCheckpoint, "instances", [])
    monkeypatch.setattr(FileCheckpoint, "uncertain", False)
    monkeypatch.setattr(MacroCollector, "_collect_fedwatch", lambda self: None)
    return run, CountedProvider, registry, configured


def test_cloud_bls_fresh_clients_reuse_only_durable_state(tmp_path, monkeypatch):
    from pipeline.storage.cloud_bls import initial_cloud_bls_ledger
    run, provider, registry, configured = _cloud_collection_setup(tmp_path, monkeypatch, bls_enabled=False)
    FileCheckpoint.state_path.write_bytes(initial_cloud_bls_ledger())
    first = run._run_collection("macro-only")
    monkeypatch.setattr(run, "settings", Settings(_env_file=None, **configured.model_dump()))
    second = run._run_collection("macro-only")
    assert provider.calls == 1
    assert len(FileCheckpoint.instances) == 2
    assert all(client.closed for client in FileCheckpoint.instances)
    assert first["macro"].inflation[0].value == second["macro"].inflation[0].value == 4.0
    assert not configured.artifacts_dir.exists()
    assert not ({call[2] for call in registry.calls} & set(SERIES))
    assert "test-secret" not in repr(configured)


def test_cloud_bls_corrupt_or_uncertain_checkpoint_never_requests_bls(tmp_path, monkeypatch):
    from pipeline.storage.cloud_bls import initial_cloud_bls_ledger
    run, provider, registry, configured = _cloud_collection_setup(tmp_path, monkeypatch)
    for content, uncertain in ((b"{}", False), (initial_cloud_bls_ledger(), True)):
        FileCheckpoint.state_path.write_bytes(content)
        monkeypatch.setattr(FileCheckpoint, "uncertain", uncertain)
        result = run._run_collection("macro-only")
        assert result["macro"].inflation[0].source == "BLS"
        assert result["macro"].inflation[0].status == "missing"
        assert result["macro"].rates[0].value is not None
        assert FileCheckpoint.state_path.read_bytes() == content
    assert provider.calls == 0
    assert all(client.closed for client in FileCheckpoint.instances)
    assert not configured.artifacts_dir.exists()
    assert not ({call[2] for call in registry.calls} & set(SERIES))


def test_cloud_bls_missing_config_never_uses_local_snapshot_or_fred(tmp_path, monkeypatch):
    run, provider, registry, configured = _cloud_collection_setup(tmp_path, monkeypatch)
    local_path = configured.artifacts_dir / "bls" / "snapshot.json"
    refresh_bls_snapshot(local_path, BatchProvider(), now=datetime.now(UTC))
    original = local_path.read_bytes()
    for missing in (dict(cloud_state_repo=None), dict(cloud_state_token=None),
                    dict(cloud_state_repo="invalid")):
        monkeypatch.setattr(run, "settings", configured.model_copy(update=missing))
        result = run._run_collection("macro-only")
        assert result["macro"].inflation[0].source == "BLS"
        assert result["macro"].inflation[0].status == "missing"
    assert provider.calls == 0
    assert not FileCheckpoint.instances
    assert local_path.read_bytes() == original
    assert not ({call[2] for call in registry.calls} & set(SERIES))


def test_cloud_bls_close_failure_preserves_verified_snapshot(tmp_path, monkeypatch):
    from pipeline.storage.cloud_bls import initial_cloud_bls_ledger
    from pipeline.storage.github_checkpoint import CheckpointError
    run, provider, registry, _configured = _cloud_collection_setup(tmp_path, monkeypatch)
    FileCheckpoint.state_path.write_bytes(initial_cloud_bls_ledger())

    def failed_close(self):
        raise CheckpointError("fake close failure")

    monkeypatch.setattr(FileCheckpoint, "close", failed_close)
    result = run._run_collection("macro-only")
    assert provider.calls == 1
    assert result["macro"].inflation[0].status != "missing"
    assert result["macro"].inflation[0].value == 4.0
    assert result["macro"].inflation[0].source == "BLS"
    assert not ({call[2] for call in registry.calls} & set(SERIES))


def test_cloud_bls_uncertain_refresh_keeps_trusted_old_snapshot(tmp_path, monkeypatch):
    from pipeline.storage import cloud_bls
    from pipeline.storage.bls_snapshot import begin_bls_attempt

    run, provider, registry, configured = _cloud_collection_setup(tmp_path, monkeypatch)
    fetched_at = datetime(2026, 11, 12, tzinfo=UTC)
    old = refresh_bls_snapshot(tmp_path / "old.json", BatchProvider(), now=fetched_at)
    degraded = begin_bls_attempt(old, datetime(2026, 11, 14, tzinfo=UTC))
    local_path = configured.artifacts_dir / "bls" / "snapshot.json"
    local_path.parent.mkdir(parents=True)
    local_path.write_text("local state must not be consulted")

    def uncertain_refresh(*args, **kwargs):
        raise cloud_bls.CloudBlsRefreshError(degraded)

    monkeypatch.setattr(cloud_bls, "refresh_cloud_bls_snapshot", uncertain_refresh)
    result = run._run_collection("macro-only")
    cpi = result["macro"].inflation[0]
    assert cpi.source == "BLS"
    assert cpi.value == 4.0
    assert cpi.status == "degraded"
    assert cpi.fetched_at == "2026-11-12T00:00:00Z"
    assert cpi.observation_period == "2026-10"
    assert provider.calls == 0
    assert all(client.closed for client in FileCheckpoint.instances)
    assert local_path.read_text() == "local state must not be consulted"
    assert not ({call[2] for call in registry.calls} & set(SERIES))


def test_cloud_bls_completion_uncertainty_preserves_old_pipeline_values(tmp_path, monkeypatch):
    from pipeline.storage import cloud_bls
    from pipeline.storage.github_checkpoint import CheckpointUncertain

    run, provider, registry, configured = _cloud_collection_setup(tmp_path, monkeypatch)
    FileCheckpoint.state_path.write_bytes(cloud_bls.initial_cloud_bls_ledger())
    previous_day = datetime(2026, 11, 12, tzinfo=UTC)
    today = datetime(2026, 11, 13, tzinfo=UTC)
    seed_client = FileCheckpoint("owner", "repo", "test-ledger", "test-secret")
    cloud_bls.refresh_cloud_bls_snapshot(
        seed_client, "state/bls.json", BatchProvider(), now=previous_day, clock=lambda: previous_day,
    )
    seed_client.close()
    original_write = FileCheckpoint.write
    real_refresh = cloud_bls.refresh_cloud_bls_snapshot

    def uncertain_completion(self, snapshot, content, message):
        if message == "Complete daily BLS request":
            raise CheckpointUncertain("completion outcome is unknown")
        return original_write(self, snapshot, content, message)

    def fixed_clock_refresh(*args, **kwargs):
        return real_refresh(*args, **kwargs, now=today, clock=lambda: today)

    monkeypatch.setattr(FileCheckpoint, "write", uncertain_completion)
    monkeypatch.setattr(cloud_bls, "refresh_cloud_bls_snapshot", fixed_clock_refresh)
    first = run._run_collection("macro-only")
    second = run._run_collection("macro-only")
    for result in (first, second):
        cpi = result["macro"].inflation[0]
        assert cpi.source == "BLS"
        assert cpi.value == 4.0
        assert cpi.status == "degraded"
        assert cpi.fetched_at == "2026-11-12T00:00:00Z"
    assert provider.calls == 1
    assert all(client.closed for client in FileCheckpoint.instances)
    assert not configured.artifacts_dir.exists()
    assert not ({call[2] for call in registry.calls} & set(SERIES))
