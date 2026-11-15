import json
from uuid import uuid4

import httpx
import pytest

import pipeline.cloud_refresh as cloud
from pipeline.publication import canonical_json, publish_snapshot, verify_generation
from pipeline.settings import Settings
from pipeline.storage.cloud_bls import initial_cloud_bls_ledger
from pipeline.storage.cloud_treasury import initial_cloud_treasury_ledger
from pipeline.storage.github_checkpoint import CheckpointError, CheckpointSnapshot
from pipeline.storage.publication_checkpoint import Generation, PublicationState

REVISION = "a" * 40
EVENT_REVISION = "b" * 40
STAMP = "2026-11-15T12:00:00Z"


class Store:
    def __init__(self, state=None):
        state = state or PublicationState()
        self.files = {
            "publication.json": state.encode(),
            "bls/ledger.json": initial_cloud_bls_ledger(),
            "treasury/ledger.json": initial_cloud_treasury_ledger(),
        }
        self.writes = 0

    def read(self, path):
        if path not in self.files:
            raise CheckpointError("missing checkpoint")
        return CheckpointSnapshot("0" * 40, "1" * 40, path, self.files[path])

    def write(self, *args):
        self.writes += 1
        raise AssertionError("collection entrypoint must not write publication state")


@pytest.fixture
def files():
    return {
        "latest/data.json": {"generated_at": STAMP, "fetched_at": STAMP, "value": 4.2},
        "history/data.json": [{"date": "2026-11-14", "value": 1}],
        "metadata/data.json": {"updated_at": STAMP},
    }


def deployed(tmp_path, files):
    root = tmp_path / uuid4().hex
    pointer = publish_snapshot(
        root,
        files,
        parent_id=None,
        producer_revision=REVISION,
        created_at=STAMP,
        validator=lambda _: True,
    )
    generation = Generation(
        pointer["generation_id"],
        pointer["manifest_sha256"],
        REVISION,
        None,
        1,
        str(uuid4()),
        1,
        1,
        "sha256:" + "1" * 64,
    )
    return root, generation


def restorer(root, *, change=None):
    def handle(request):
        relative = request.url.path.removeprefix("/app/data/")
        target = root / relative
        if change:
            replacement = change(relative)
            if replacement is not None:
                return httpx.Response(200, content=replacement)
        if not target.is_file():
            return httpx.Response(404)
        return httpx.Response(200, content=target.read_bytes())

    return cloud.PagesGenerationRestorer(
        "https://example.test/app", transport=httpx.MockTransport(handle)
    )


@pytest.fixture
def setup(monkeypatch, tmp_path):
    monkeypatch.setattr(cloud, "_verify_checkout", lambda *_: None)
    return cloud.CloudPaths(tmp_path / "work"), Settings(_env_file=None)


@pytest.mark.parametrize("mode", ["scheduled", "code-only"])
def test_empty_provisioned_state_bootstraps_only_from_approved_collection(
    mode, setup, monkeypatch, files
):
    paths, settings = setup
    calls = []

    def collect(isolated):
        calls.append(isolated)
        assert (
            isolated.approved_sources_only
            and isolated.bls_cloud_enabled
            and isolated.treasury_cloud_enabled
        )
        assert not any(isolated.data_dir.iterdir())
        for relative, value in files.items():
            target = isolated.data_dir / relative
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(canonical_json(value))

    monkeypatch.setattr(cloud, "_collect_approved", collect)
    import scripts.stage_publication as stage

    monkeypatch.setattr(stage, "validate_candidate", lambda _: True)

    class NoRestore:
        def restore(self, *args):
            raise AssertionError("bootstrap must not restore repository or Pages data")

    state = Store()
    result = cloud.refresh_cloud(
        mode=mode,
        app_revision=REVISION,
        checkpoint=state,
        paths=paths,
        settings=settings,
        restorer=NoRestore(),
    )
    assert len(calls) == 1 and result.parent_id is None and state.writes == 0
    assert verify_generation(result.generation_dir)["producer_revision"] == REVISION


def test_scheduled_revision_rejection_happens_before_download_or_collection(
    tmp_path, files, setup, monkeypatch
):
    root, generation = deployed(tmp_path, files)
    paths, settings = setup
    monkeypatch.setattr(cloud, "_collect_approved", lambda *_: pytest.fail("provider called"))
    with pytest.raises(cloud.CloudRefreshError, match="keep the deployed"):
        cloud.refresh_cloud(
            mode="scheduled",
            app_revision=EVENT_REVISION,
            checkpoint=Store(PublicationState(deployed=generation, last_attempt=1)),
            paths=paths,
            settings=settings,
            restorer=restorer(root),
        )
    assert not paths.work_dir.exists()


def test_code_only_preserves_data_timestamps_and_calls_no_provider(
    tmp_path, files, setup, monkeypatch
):
    root, generation = deployed(tmp_path, files)
    paths, settings = setup
    monkeypatch.setattr(cloud, "_collect_approved", lambda *_: pytest.fail("provider called"))
    import scripts.stage_publication as stage

    monkeypatch.setattr(
        stage, "stage_candidate", lambda *_a, **_kw: pytest.fail("age validation rerun")
    )
    state = Store(PublicationState(deployed=generation, last_attempt=1))
    result = cloud.refresh_cloud(
        mode="code-only",
        app_revision=EVENT_REVISION,
        checkpoint=state,
        paths=paths,
        settings=settings,
        restorer=restorer(root),
    )
    manifest = verify_generation(result.generation_dir)
    assert manifest["producer_revision"] == EVENT_REVISION
    assert manifest["parent_id"] == generation.generation_id
    for relative in files:
        assert (result.generation_dir / relative).read_bytes() == (
            root / "generations" / generation.generation_id / relative
        ).read_bytes()
    assert state.writes == 0


@pytest.mark.parametrize("path", ["publication.json", "bls/ledger.json", "treasury/ledger.json"])
@pytest.mark.parametrize("corrupt", [False, True])
def test_missing_or_corrupt_state_fails_before_collection(path, corrupt, setup, monkeypatch):
    paths, settings = setup
    state = Store()
    if corrupt:
        state.files[path] = b"{}"
    else:
        del state.files[path]
    monkeypatch.setattr(cloud, "_collect_approved", lambda *_: pytest.fail("provider called"))
    with pytest.raises((CheckpointError, cloud.BlsSnapshotError, cloud.TreasurySnapshotError)):
        cloud.refresh_cloud(
            mode="scheduled",
            app_revision=REVISION,
            checkpoint=state,
            paths=paths,
            settings=settings,
            restorer=None,
        )
    assert state.writes == 0 and not paths.work_dir.exists()


@pytest.mark.parametrize("part", ["current.json", "manifest.json", "latest/data.json"])
def test_pages_readback_rejects_pointer_manifest_or_file_corruption(tmp_path, files, part):
    root, generation = deployed(tmp_path, files)
    reader = restorer(root, change=lambda relative: b"{}" if relative.endswith(part) else None)
    with pytest.raises((cloud.CloudRefreshError, ValueError)):
        reader.restore(generation, tmp_path / "restore")


def test_pages_missing_readback_fails_closed(tmp_path, files):
    root, generation = deployed(tmp_path, files)
    (root / "current.json").unlink()
    with pytest.raises(cloud.CloudRefreshError):
        restorer(root).restore(generation, tmp_path / "restore")


def test_pages_read_pointer_uses_cache_busting_and_parses_valid_identity():
    pointer = {"version": 1, "generation_id": "a" * 64, "manifest_sha256": "b" * 64}
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=pointer)

    reader = cloud.PagesGenerationRestorer(
        "https://example.test/app", transport=httpx.MockTransport(handle)
    )
    assert reader.read_pointer() == pointer
    assert requests[0].url.path == "/app/data/current.json"
    assert requests[0].url.params.get("refresh")
    assert requests[0].headers["Cache-Control"] == "no-cache"


@pytest.mark.parametrize(
    "body",
    [
        b'{"version":true,"generation_id":"'
        + b"a" * 64
        + b'","manifest_sha256":"'
        + b"b" * 64
        + b'"}',
        b'{"version":1,"generation_id":"'
        + b"a" * 64
        + b'","manifest_sha256":"'
        + b"b" * 64
        + b'","version":1}',
    ],
)
def test_pages_read_pointer_rejects_wrong_type_and_duplicate_fields(body):
    reader = cloud.PagesGenerationRestorer(
        "https://example.test/app",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=body)),
    )
    with pytest.raises(cloud.CloudRefreshError, match="invalid pointer"):
        reader.read_pointer()


def test_pages_read_pointer_treats_only_an_explicit_404_as_empty():
    reader = cloud.PagesGenerationRestorer(
        "https://example.test/app",
        transport=httpx.MockTransport(lambda _: httpx.Response(404)),
    )
    assert reader.read_pointer() is None


def test_pages_detect_legacy_data_without_treating_a_missing_file_as_success():
    requests = []

    def legacy(request):
        requests.append(request.url.path)
        if request.url.path.endswith("/data/latest/dashboard.json"):
            return httpx.Response(200, json={"payload": {}})
        return httpx.Response(404)

    reader = cloud.PagesGenerationRestorer(
        "https://example.test/app", transport=httpx.MockTransport(legacy)
    )
    assert reader.has_legacy_data()
    assert requests == ["/app/data/latest/dashboard.json"]

    empty = cloud.PagesGenerationRestorer(
        "https://example.test/app",
        transport=httpx.MockTransport(lambda _: httpx.Response(404)),
    )
    assert not empty.has_legacy_data()


def test_unresolved_publication_attempt_fails_before_collection(
    tmp_path, files, setup, monkeypatch
):
    _, generation = deployed(tmp_path, files)
    paths, settings = setup
    monkeypatch.setattr(cloud, "_collect_approved", lambda *_: pytest.fail("provider called"))
    state = Store(PublicationState(candidate=generation, last_attempt=1))
    with pytest.raises(cloud.CloudRefreshError, match="unresolved"):
        cloud.refresh_cloud(
            mode="scheduled",
            app_revision=REVISION,
            checkpoint=state,
            paths=paths,
            settings=settings,
            restorer=None,
        )


def test_confirmed_abandonment_tombstone_allows_a_new_candidate(setup, monkeypatch, files):
    paths, settings = setup
    _, failed = deployed(paths.work_dir, files)
    state = Store(PublicationState(abandoned=failed, last_attempt=1))

    def collect(isolated):
        for relative, value in files.items():
            target = isolated.data_dir / relative
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(canonical_json(value))

    monkeypatch.setattr(cloud, "_collect_approved", collect)
    import scripts.stage_publication as stage

    monkeypatch.setattr(stage, "validate_candidate", lambda _: True)
    result = cloud.refresh_cloud(
        mode="code-only",
        app_revision=REVISION,
        checkpoint=state,
        paths=paths,
        settings=settings,
        restorer=None,
    )
    assert result.parent_id is None


def test_pipeline_adapter_forces_approved_mode_and_shared_cloud_branch(tmp_path, monkeypatch):
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        artifacts_dir=tmp_path / "artifacts",
        cloud_state_repo="owner/repo",
        cloud_state_token="test-token",
        cloud_state_branch="cloud-state",
    )
    monkeypatch.setenv("DATA_APPROVED_SOURCES_ONLY", "false")
    monkeypatch.setenv("DATA_FRED_API_KEY", "must-not-forward")
    observed = []

    def run(args, **kwargs):
        observed.append(kwargs["env"])
        assert args[1:] == ["-m", "pipeline.run", "--full"]
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(cloud.subprocess, "run", run)
    cloud._collect_approved(settings)
    env = observed[0]
    assert env["DATA_APPROVED_SOURCES_ONLY"] == "true"
    assert env["DATA_BLS_CLOUD_ENABLED"] == env["DATA_TREASURY_CLOUD_ENABLED"] == "true"
    assert env["DATA_CLOUD_STATE_BRANCH"] == "cloud-state"
    assert "DATA_FRED_API_KEY" not in env


def test_child_failure_logs_only_allowlisted_collection_summary(tmp_path, monkeypatch, capsys):
    settings = Settings(
        _env_file=None,
        data_dir=tmp_path / "data",
        artifacts_dir=tmp_path / "artifacts",
        cloud_state_repo="owner/repo",
        cloud_state_token="test-token",
        cloud_state_branch="cloud-state",
    )
    report_dir = settings.artifacts_dir / "logs"
    report_dir.mkdir(parents=True)
    (report_dir / "run-report-20261115T120000Z.json").write_text(
        json.dumps(
            {
                "ok": True,
                "clean": False,
                "failed_datasets": ["equities", "report-token-secret"],
                "skipped_datasets": [],
                "degraded_datasets": ["macro"],
                "error": "report-error-secret",
                "degraded": ["provider-note-secret"],
                "provider_status": {"credential": "provider-secret"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cloud.subprocess,
        "run",
        lambda *_args, **_kwargs: type(
            "Result",
            (),
            {
                "returncode": 1,
                "stdout": "child-stdout-secret",
                "stderr": "child-stderr-secret",
            },
        )(),
    )
    diagnostics = cloud.RefreshDiagnostics(enabled=True)
    diagnostics.enter(cloud.RefreshStage.APPROVED_SOURCE_COLLECTION)

    with pytest.raises(cloud.CloudRefreshError, match="Approved-source collection"):
        cloud._collect_approved(settings, diagnostics=diagnostics)
    diagnostics.emit_failure()

    output = capsys.readouterr().err
    events = [json.loads(line) for line in output.splitlines()]
    assert events == [
        {"event": "cloud_refresh.phase", "stage": "approved_source_collection"},
        {
            "event": "cloud_refresh.failure",
            "stage": "approved_source_collection",
            "code": "child_exit_nonzero",
            "exit_code": 1,
            "collection": {
                "ok": True,
                "clean": False,
                "failed_datasets": ["equities"],
                "skipped_datasets": [],
                "degraded_datasets": ["macro"],
            },
        },
    ]
    for secret in (
        "report-token-secret",
        "report-error-secret",
        "provider-note-secret",
        "provider-secret",
        "child-stdout-secret",
        "child-stderr-secret",
    ):
        assert secret not in output


def test_refresh_cloud_diagnostic_keeps_the_failed_phase(setup, monkeypatch, capsys):
    paths, settings = setup
    diagnostics = cloud.RefreshDiagnostics(enabled=True)

    def fail_collection(_settings, *, diagnostics):
        assert diagnostics is not None
        raise cloud.CloudRefreshError("collection failed")

    monkeypatch.setattr(cloud, "_collect_approved", fail_collection)
    with pytest.raises(cloud.CloudRefreshError, match="collection failed"):
        cloud.refresh_cloud(
            mode="code-only",
            app_revision=REVISION,
            checkpoint=Store(),
            paths=paths,
            settings=settings,
            restorer=None,
            diagnostics=diagnostics,
        )
    diagnostics.emit_failure()

    events = [json.loads(line) for line in capsys.readouterr().err.splitlines()]
    assert events[-1] == {
        "event": "cloud_refresh.failure",
        "stage": "approved_source_collection",
        "code": "refresh_failed",
    }


def test_scheduled_collects_with_verified_parent_and_stages_validated_child(
    tmp_path, files, setup, monkeypatch
):
    root, generation = deployed(tmp_path, files)
    paths, settings = setup
    collected = []

    def collect(isolated):
        assert (isolated.data_dir / "latest/data.json").read_bytes() == canonical_json(
            files["latest/data.json"]
        )
        collected.append(isolated.data_dir)
        (isolated.data_dir / "latest/data.json").write_bytes(
            canonical_json({"value": 4.3, "generated_at": STAMP})
        )

    monkeypatch.setattr(cloud, "_collect_approved", collect)
    import scripts.stage_publication as stage

    validated = []

    def validate(candidate):
        validated.append(candidate)
        return True

    monkeypatch.setattr(stage, "validate_candidate", validate)
    state = Store(PublicationState(deployed=generation, last_attempt=1))
    result = cloud.refresh_cloud(
        mode="scheduled",
        app_revision=REVISION,
        checkpoint=state,
        paths=paths,
        settings=settings,
        restorer=restorer(root),
    )
    assert len(collected) == len(validated) == 1
    assert result.parent_id == generation.generation_id and state.writes == 0
    assert verify_generation(result.generation_dir)["producer_revision"] == REVISION


def test_provider_checkpoint_lost_during_collection_prevents_staging(setup, monkeypatch):
    paths, settings = setup
    state = Store()
    monkeypatch.setattr(cloud, "_collect_approved", lambda _: state.files.pop("bls/ledger.json"))
    import scripts.stage_publication as stage

    monkeypatch.setattr(
        stage, "stage_candidate", lambda *_a, **_kw: pytest.fail("staged after checkpoint loss")
    )
    with pytest.raises(CheckpointError):
        cloud.refresh_cloud(
            mode="scheduled",
            app_revision=REVISION,
            checkpoint=state,
            paths=paths,
            settings=settings,
            restorer=None,
        )
    assert state.writes == 0


def test_candidate_validation_failure_does_not_change_publication_state(setup, files, monkeypatch):
    paths, settings = setup
    state = Store()
    before = state.files["publication.json"]

    def collect(isolated):
        for relative, value in files.items():
            target = isolated.data_dir / relative
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(canonical_json(value))

    monkeypatch.setattr(cloud, "_collect_approved", collect)
    import scripts.stage_publication as stage

    monkeypatch.setattr(stage, "validate_candidate", lambda _: False)
    with pytest.raises(cloud.PublicationError):
        cloud.refresh_cloud(
            mode="code-only",
            app_revision=REVISION,
            checkpoint=state,
            paths=paths,
            settings=settings,
            restorer=None,
        )
    assert state.files["publication.json"] == before and state.writes == 0


def test_pages_paths_cannot_escape_generation(tmp_path, files):
    import hashlib
    import json
    from dataclasses import replace

    root, generation = deployed(tmp_path, files)
    manifest_path = root / "generations" / generation.generation_id / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["files"]["latest/../../outside.json"] = "a" * 64
    payload = canonical_json(manifest)
    malformed = replace(generation, manifest_sha256=hashlib.sha256(payload).hexdigest())
    reader = restorer(
        root,
        change=lambda path: (
            canonical_json(
                {
                    "version": 1,
                    "generation_id": generation.generation_id,
                    "manifest_sha256": malformed.manifest_sha256,
                }
            )
            if path == "current.json"
            else payload
            if path.endswith("manifest.json")
            else None
        ),
    )
    with pytest.raises(cloud.PublicationError):
        reader.restore(malformed, tmp_path / "restore")
    assert not (tmp_path / "outside.json").exists()


def test_main_settings_initialization_failure_emits_startup_diagnostic(monkeypatch, capsys):
    def fail_settings():
        raise ValueError("settings-secret")

    monkeypatch.setattr(cloud, "Settings", fail_settings)

    result = cloud.main(
        [
            "--mode",
            "code-only",
            "--app-revision",
            REVISION,
            "--site-url",
            "https://example.test",
        ]
    )

    assert result == 1
    output = capsys.readouterr()
    events = [json.loads(line) for line in output.err.splitlines() if line.startswith("{")]
    assert events == [
        {"event": "cloud_refresh.phase", "stage": "startup"},
        {
            "event": "cloud_refresh.failure",
            "stage": "startup",
            "code": "refresh_failed",
        },
    ]
    assert "settings-secret" not in output.err
    assert "Cloud candidate preparation failed; no publication was performed" in output.err


def test_main_checkpoint_initialization_failure_emits_startup_diagnostic(monkeypatch, capsys):
    settings = Settings(
        _env_file=None,
        cloud_state_repo="owner/repo",
        cloud_state_token="test-token",
    )
    monkeypatch.setattr(cloud, "Settings", lambda: settings)

    def fail_checkpoint(*_args, **_kwargs):
        raise ValueError("checkpoint-secret")

    monkeypatch.setattr(cloud, "GitHubCheckpoint", fail_checkpoint)

    result = cloud.main(
        [
            "--mode",
            "code-only",
            "--app-revision",
            REVISION,
            "--site-url",
            "https://example.test",
        ]
    )

    assert result == 1
    output = capsys.readouterr()
    events = [json.loads(line) for line in output.err.splitlines() if line.startswith("{")]
    assert events == [
        {"event": "cloud_refresh.phase", "stage": "startup"},
        {
            "event": "cloud_refresh.failure",
            "stage": "startup",
            "code": "refresh_failed",
        },
    ]
    assert "checkpoint-secret" not in output.err


def test_main_missing_cloud_config_keeps_argparse_exit(monkeypatch, capsys):
    monkeypatch.setattr(cloud, "Settings", lambda: Settings(_env_file=None))

    with pytest.raises(SystemExit) as error:
        cloud.main(
            [
                "--mode",
                "code-only",
                "--app-revision",
                REVISION,
                "--site-url",
                "https://example.test",
            ]
        )

    assert error.value.code == 2
    output = capsys.readouterr().err
    assert "error: Cloud state repository and token must be configured" in output
