import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from pipeline import cloud_publication
from pipeline.cloud_publication import (
    WORKFLOW_PATH,
    CloudPublicationError,
    CloudPublisher,
    GitHubActionsApi,
    main,
)
from pipeline.cloud_refresh import CloudRefreshError
from pipeline.publication import publish_snapshot
from pipeline.storage.github_checkpoint import CheckpointSnapshot
from pipeline.storage.publication_checkpoint import (
    Generation,
    PublicationCheckpoint,
    PublicationState,
)

REPO = "owner/repo"
REPOSITORY_ID = 1321685294
REVISION = "a" * 40
NEW_REVISION = "b" * 40
STAMP = "2026-11-15T12:00:00Z"
ARTIFACT_DIGEST = "sha256:" + "c" * 64
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class MemoryCheckpoint:
    def __init__(self, state=None):
        state = state or PublicationState()
        self.content = state.encode()
        self.writes = 0

    def read(self, path):
        return CheckpointSnapshot("1" * 40, "2" * 40, path, self.content)

    def write(self, snapshot, content, message):
        assert snapshot.content == self.content
        self.content = content
        self.writes += 1
        return "3" * 40


class Actions:
    def __init__(
        self, *, current_run=50, conclusion="failure", other_runs=None, heads=None,
        deploy_step_skipped=False,
    ):
        self.current_run = current_run
        self.conclusion = conclusion
        self.other_runs = other_runs or []
        self.deploy_step_skipped = deploy_step_skipped
        self.artifacts = {}
        self.heads = {"main": REVISION, "dev": REVISION, **(heads or {})}

    def active_workflow_runs(self, *, current_run_id):
        return [run for run in self.other_runs if run["id"] != current_run_id]

    def workflow_run(self, run_id):
        return {
            "id": run_id,
            "path": WORKFLOW_PATH,
            "status": "completed",
            "conclusion": self.conclusion,
            "head_repository": {"full_name": REPO},
            "head_sha": REVISION,
        }

    def artifact(self, artifact_id):
        return self.artifacts[artifact_id]

    def branch_head(self, branch):
        return self.heads[branch]

    def deployment_action_was_skipped(self, run_id):
        assert run_id > 0
        return self.deploy_step_skipped


class PagesDeployments:
    def __init__(self, status="deployment_failed"):
        self.value = status
        self.revisions = []

    def status(self, revision):
        self.revisions.append(revision)
        return self.value

    def close(self):
        pass


class Pages:
    def __init__(self, pointer, *, legacy_data=False):
        self.pointer = pointer
        self.legacy_data = legacy_data
        self.restored = []

    def read_pointer(self):
        return self.pointer

    def restore(self, expected, store):
        pointer = {
            "version": 1,
            "generation_id": expected.generation_id,
            "manifest_sha256": expected.manifest_sha256,
        }
        if pointer != self.pointer:
            raise CloudRefreshError("Pages pointer mismatch")
        self.restored.append(expected.generation_id)
        Path(store).mkdir(parents=True)
        return Path(store)

    def has_legacy_data(self):
        return self.legacy_data


def _artifact(artifact_id, run_id, *, event_sha=REVISION):
    return {
        "id": artifact_id,
        "name": "github-pages",
        "expired": False,
        "digest": ARTIFACT_DIGEST,
        "workflow_run": {
            "id": run_id,
            "head_sha": event_sha,
            "repository_id": REPOSITORY_ID,
            "head_repository_id": REPOSITORY_ID,
        },
    }


def _generation(*, artifact_id=41, run_id=40, revision=REVISION, parent_id=None, attempt_number=1):
    return Generation(
        hashlib.sha256(uuid4().bytes).hexdigest(),
        hashlib.sha256(uuid4().bytes).hexdigest(),
        revision,
        parent_id,
        attempt_number,
        str(uuid4()),
        artifact_id,
        run_id,
        ARTIFACT_DIGEST,
    )


def _run_python_with_invalid_settings(*arguments):
    environment = {
        key: os.environ[key]
        for key in ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL")
        if key in os.environ
    }
    environment.update(
        {
            "DATA_COINMETRICS_ENABLED": "not-a-bool",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return subprocess.run(
        [sys.executable, *arguments],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _publisher(
    state=None, *, pointer=None, actions=None, legacy_data=False,
    deployment_status="deployment_failed",
):
    checkpoint = MemoryCheckpoint(state or PublicationState())
    api = actions or Actions()
    pages = Pages(pointer, legacy_data=legacy_data)
    deployments = PagesDeployments(deployment_status)
    return (
        CloudPublisher(
            PublicationCheckpoint(checkpoint), api, pages, deployments, REPO, REPOSITORY_ID
        ),
        checkpoint,
        api,
        pages,
    )


def _stub_cli_runtime(monkeypatch, checkpoint, actions, pages):
    actions.close = lambda: None
    checkpoint.close = lambda: None
    monkeypatch.setattr(
        cloud_publication,
        "_runtime",
        lambda: (
            checkpoint,
            actions,
            PagesDeployments(),
            SimpleNamespace(cloud_state_repo=REPO),
            REPOSITORY_ID,
        ),
    )
    monkeypatch.setattr(cloud_publication, "PagesGenerationRestorer", lambda _: pages)


def _preflight_cli_args(*options):
    return [
        "preflight",
        "--mode",
        "code-only",
        "--event-ref",
        "refs/heads/dev",
        "--event-sha",
        REVISION,
        "--run-id",
        "50",
        "--site-url",
        "https://example.test/",
        *options,
    ]


def test_preflight_uses_the_deployed_application_for_scheduled_runs():
    deployed = _generation(artifact_id=39, run_id=38, revision=NEW_REVISION)
    publisher, _, _, _ = _publisher(PublicationState(deployed=deployed, last_attempt=1))
    plan = publisher.preflight(
        mode="scheduled", event_ref="refs/heads/main", event_sha=REVISION, run_id=50
    )
    assert plan.application_revision == NEW_REVISION
    assert plan.resume is None


def test_preflight_resumes_a_pending_candidate_from_the_same_workflow_run():
    candidate = _generation()
    actions = Actions()
    actions.artifacts[candidate.artifact_id] = _artifact(
        candidate.artifact_id, candidate.workflow_run_id
    )
    publisher, _, _, _ = _publisher(
        PublicationState(candidate=candidate, last_attempt=1), actions=actions
    )
    plan = publisher.preflight(
        mode="code-only", event_ref="refs/heads/dev", event_sha=REVISION,
        run_id=candidate.workflow_run_id
    )
    assert plan.application_revision == candidate.producer_revision
    assert plan.resume == candidate


def test_preflight_marks_a_candidate_deployed_when_pages_serves_it():
    candidate = _generation()
    pointer = {
        "version": 1,
        "generation_id": candidate.generation_id,
        "manifest_sha256": candidate.manifest_sha256,
    }
    actions = Actions(conclusion="failure")
    actions.artifacts[candidate.artifact_id] = _artifact(
        candidate.artifact_id, candidate.workflow_run_id
    )
    publisher, checkpoint, _, pages = _publisher(
        PublicationState(candidate=candidate, last_attempt=1), pointer=pointer, actions=actions
    )
    plan = publisher.preflight(
        mode="scheduled", event_ref="refs/heads/main", event_sha=REVISION, run_id=50
    )
    state = PublicationCheckpoint(checkpoint).read()
    assert state.deployed == candidate and state.candidate is None
    assert plan.application_revision == candidate.producer_revision
    assert pages.restored == [candidate.generation_id]


def test_preflight_abandons_a_failed_candidate_when_pages_still_serves_parent():
    deployed = _generation(artifact_id=39, run_id=38, revision=NEW_REVISION)
    candidate = _generation(
        artifact_id=41,
        run_id=40,
        revision=NEW_REVISION,
        parent_id=deployed.generation_id,
        attempt_number=2,
    )
    pointer = {
        "version": 1,
        "generation_id": deployed.generation_id,
        "manifest_sha256": deployed.manifest_sha256,
    }
    actions = Actions(conclusion="failure")
    actions.artifacts[candidate.artifact_id] = _artifact(
        candidate.artifact_id, candidate.workflow_run_id
    )
    publisher, checkpoint, _, pages = _publisher(
        PublicationState(deployed=deployed, candidate=candidate, last_attempt=2),
        pointer=pointer,
        actions=actions,
    )
    plan = publisher.preflight(
        mode="code-only", event_ref="refs/heads/dev", event_sha=REVISION, run_id=50
    )
    state = PublicationCheckpoint(checkpoint).read()
    assert state.deployed == deployed and state.candidate is None and state.abandoned == candidate
    assert plan.application_revision == REVISION
    assert pages.restored == [deployed.generation_id]


@pytest.mark.parametrize("status", ["deploying", "succeed", "deployment_lost", None])
def test_preflight_keeps_candidate_when_pages_does_not_confirm_failed_terminal_status(status):
    deployed = _generation(artifact_id=39, run_id=38, revision=NEW_REVISION)
    candidate = _generation(
        artifact_id=41,
        run_id=40,
        revision=NEW_REVISION,
        parent_id=deployed.generation_id,
        attempt_number=2,
    )
    pointer = {
        "version": 1,
        "generation_id": deployed.generation_id,
        "manifest_sha256": deployed.manifest_sha256,
    }
    actions = Actions(conclusion="failure")
    actions.artifacts[candidate.artifact_id] = _artifact(
        candidate.artifact_id, candidate.workflow_run_id
    )
    publisher, checkpoint, _, _ = _publisher(
        PublicationState(deployed=deployed, candidate=candidate, last_attempt=2),
        pointer=pointer,
        actions=actions,
        deployment_status=status,
    )

    with pytest.raises(CloudPublicationError, match="Pages deployment has not confirmed termination"):
        publisher.preflight(
            mode="code-only", event_ref="refs/heads/dev", event_sha=REVISION, run_id=50
        )

    assert PublicationCheckpoint(checkpoint).read().candidate == candidate


def test_preflight_can_abandon_when_deploy_action_was_skipped_and_no_deployment_exists():
    deployed = _generation(artifact_id=39, run_id=38, revision=NEW_REVISION)
    candidate = _generation(
        artifact_id=41,
        run_id=40,
        revision=NEW_REVISION,
        parent_id=deployed.generation_id,
        attempt_number=2,
    )
    pointer = {
        "version": 1,
        "generation_id": deployed.generation_id,
        "manifest_sha256": deployed.manifest_sha256,
    }
    actions = Actions(conclusion="failure", deploy_step_skipped=True)
    actions.artifacts[candidate.artifact_id] = _artifact(
        candidate.artifact_id, candidate.workflow_run_id
    )
    publisher, checkpoint, _, _ = _publisher(
        PublicationState(deployed=deployed, candidate=candidate, last_attempt=2),
        pointer=pointer,
        actions=actions,
        deployment_status=None,
    )

    publisher.preflight(
        mode="code-only", event_ref="refs/heads/dev", event_sha=REVISION, run_id=50
    )

    state = PublicationCheckpoint(checkpoint).read()
    assert state.candidate is None and state.abandoned == candidate


def test_preflight_keeps_candidate_when_an_earlier_workflow_attempt_started_deploy():
    deployed = _generation(artifact_id=39, run_id=38, revision=NEW_REVISION)
    candidate = _generation(
        artifact_id=41,
        run_id=40,
        revision=NEW_REVISION,
        parent_id=deployed.generation_id,
        attempt_number=2,
    )
    pointer = {
        "version": 1,
        "generation_id": deployed.generation_id,
        "manifest_sha256": deployed.manifest_sha256,
    }
    actions = Actions(conclusion="failure", deploy_step_skipped=False)
    actions.artifacts[candidate.artifact_id] = _artifact(
        candidate.artifact_id, candidate.workflow_run_id
    )
    publisher, checkpoint, _, _ = _publisher(
        PublicationState(deployed=deployed, candidate=candidate, last_attempt=2),
        pointer=pointer,
        actions=actions,
        deployment_status=None,
    )

    with pytest.raises(CloudPublicationError, match="Pages deployment has not confirmed termination"):
        publisher.preflight(
            mode="code-only", event_ref="refs/heads/dev", event_sha=REVISION, run_id=50
        )

    assert PublicationCheckpoint(checkpoint).read().candidate == candidate


def test_preflight_does_not_abandon_a_successful_run_with_stale_pages():
    deployed = _generation(artifact_id=39, run_id=38, revision=NEW_REVISION)
    candidate = _generation(
        artifact_id=41,
        run_id=40,
        revision=NEW_REVISION,
        parent_id=deployed.generation_id,
        attempt_number=2,
    )
    pointer = {
        "version": 1,
        "generation_id": deployed.generation_id,
        "manifest_sha256": deployed.manifest_sha256,
    }
    actions = Actions(conclusion="success")
    actions.artifacts[candidate.artifact_id] = _artifact(
        candidate.artifact_id, candidate.workflow_run_id
    )
    publisher, checkpoint, _, _ = _publisher(
        PublicationState(deployed=deployed, candidate=candidate, last_attempt=2),
        pointer=pointer,
        actions=actions,
    )
    with pytest.raises(CloudPublicationError, match="successful run"):
        publisher.preflight(
            mode="scheduled", event_ref="refs/heads/main", event_sha=REVISION, run_id=50
        )
    assert PublicationCheckpoint(checkpoint).read().candidate == candidate


def test_preflight_fails_closed_on_an_unknown_pages_generation():
    candidate = _generation()
    actions = Actions(conclusion="failure")
    actions.artifacts[candidate.artifact_id] = _artifact(
        candidate.artifact_id, candidate.workflow_run_id
    )
    publisher, checkpoint, _, _ = _publisher(
        PublicationState(candidate=candidate, last_attempt=1),
        pointer={"version": 1, "generation_id": "f" * 64, "manifest_sha256": "1" * 64},
        actions=actions,
    )
    with pytest.raises(CloudPublicationError, match="unknown generation"):
        publisher.preflight(
            mode="code-only", event_ref="refs/heads/dev", event_sha=REVISION, run_id=50
        )
    assert PublicationCheckpoint(checkpoint).read().candidate == candidate


def test_preflight_pauses_while_another_pages_run_is_active():
    actions = Actions(other_runs=[{"id": 49}])
    publisher, _, _, _ = _publisher(actions=actions)
    with pytest.raises(CloudPublicationError, match="Another Pages workflow") as exc_info:
        publisher.preflight(
            mode="code-only", event_ref="refs/heads/dev", event_sha=REVISION, run_id=50
        )
    assert exc_info.value.code.value == "active_pages_run"


def test_empty_checkpoint_refuses_an_unrecorded_pages_generation():
    pointer = {"version": 1, "generation_id": "f" * 64, "manifest_sha256": "1" * 64}
    publisher, checkpoint, _, _ = _publisher(pointer=pointer)

    with pytest.raises(CloudPublicationError, match="uncheckpointed generation"):
        publisher.preflight(
            mode="code-only", event_ref="refs/heads/dev", event_sha=REVISION, run_id=50
        )

    assert PublicationCheckpoint(checkpoint).read().candidate is None


def test_legacy_pages_data_requires_explicit_bootstrap_confirmation():
    publisher, _, _, _ = _publisher(legacy_data=True)

    with pytest.raises(CloudPublicationError, match="explicit bootstrap confirmation"):
        publisher.preflight(
            mode="code-only", event_ref="refs/heads/dev", event_sha=REVISION, run_id=50
        )

    plan = publisher.preflight(
        mode="code-only", event_ref="refs/heads/dev", event_sha=REVISION,
        run_id=50, allow_legacy_bootstrap=True,
    )
    assert plan.application_revision == REVISION


def test_initial_pages_publication_requires_confirmation_even_when_probes_are_empty():
    publisher, _, _, _ = _publisher(legacy_data=False)

    with pytest.raises(CloudPublicationError, match="site appears empty.*explicit bootstrap confirmation"):
        publisher.preflight(
            mode="code-only", event_ref="refs/heads/dev", event_sha=REVISION, run_id=50
        )

    plan = publisher.preflight(
        mode="code-only", event_ref="refs/heads/dev", event_sha=REVISION,
        run_id=50, allow_legacy_bootstrap=True,
    )
    assert plan.application_revision == REVISION


def test_pending_artifact_must_match_the_recorded_workflow_and_digest():
    candidate = _generation()
    actions = Actions()
    wrong = _artifact(candidate.artifact_id, candidate.workflow_run_id)
    wrong["digest"] = "sha256:" + "f" * 64
    actions.artifacts[candidate.artifact_id] = wrong
    publisher, checkpoint, _, _ = _publisher(
        PublicationState(candidate=candidate, last_attempt=1), actions=actions
    )
    with pytest.raises(CloudPublicationError, match="artifact provenance"):
        publisher.preflight(
            mode="code-only", event_ref="refs/heads/dev", event_sha=REVISION,
            run_id=candidate.workflow_run_id
        )
    assert checkpoint.writes == 0


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("id", 43),
        ("name", "other-artifact"),
        ("expired", True),
        ("digest", "sha256:invalid"),
        ("workflow_run.id", 51),
        ("workflow_run.head_sha", NEW_REVISION),
    ],
)
def test_reserve_rejects_artifact_provenance_mismatches_before_checkpoint_write(
    tmp_path, field, value
):
    files = {
        "latest/data.json": {"generated_at": STAMP, "value": 3},
        "history/data.json": [{"date": "2026-11-14", "value": 2}],
        "metadata/data.json": {"updated_at": STAMP},
    }
    pointer = publish_snapshot(
        tmp_path / "store",
        files,
        parent_id=None,
        producer_revision=REVISION,
        created_at=STAMP,
        validator=lambda _: True,
    )
    artifact = _artifact(42, 50)
    target = artifact
    parts = field.split(".")
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value
    actions = Actions()
    actions.artifacts[42] = artifact
    publisher, checkpoint, _, _ = _publisher(actions=actions)

    with pytest.raises(CloudPublicationError, match="artifact provenance"):
        publisher.reserve(
            generation_dir=tmp_path / "store/generations" / pointer["generation_id"],
            artifact_id=42,
            run_id=50,
            event_sha=REVISION,
        )

    assert checkpoint.writes == 0


def test_finish_rejects_artifact_provenance_mismatch_without_checkpoint_write():
    candidate = _generation()
    artifact = _artifact(candidate.artifact_id, candidate.workflow_run_id)
    artifact["workflow_run"]["head_sha"] = NEW_REVISION
    actions = Actions()
    actions.artifacts[candidate.artifact_id] = artifact
    publisher, checkpoint, _, _ = _publisher(
        PublicationState(candidate=candidate, last_attempt=1), actions=actions
    )

    with pytest.raises(CloudPublicationError, match="artifact provenance"):
        publisher.finish(
            current_run_id=candidate.workflow_run_id,
            event_sha=REVISION,
        )

    assert checkpoint.writes == 0


@pytest.mark.parametrize(
    ("field", "repository_id", "head_repository_id"),
    [
        ("repository_id", None, REPOSITORY_ID),
        ("repository_id", True, REPOSITORY_ID),
        ("repository_id", str(REPOSITORY_ID), REPOSITORY_ID),
        ("repository_id", 0, REPOSITORY_ID),
        ("repository_id", -1, REPOSITORY_ID),
        ("repository_id", REPOSITORY_ID + 1, REPOSITORY_ID + 1),
        ("head_repository_id", REPOSITORY_ID, REPOSITORY_ID + 1),
        ("missing_repository_id", None, REPOSITORY_ID),
    ],
)
def test_reserve_rejects_artifact_repository_ids_outside_runner_scope(
    tmp_path, field, repository_id, head_repository_id
):
    files = {
        "latest/data.json": {"generated_at": STAMP, "value": 3},
        "history/data.json": [{"date": "2026-11-14", "value": 2}],
        "metadata/data.json": {"updated_at": STAMP},
    }
    pointer = publish_snapshot(
        tmp_path / "store",
        files,
        parent_id=None,
        producer_revision=REVISION,
        created_at=STAMP,
        validator=lambda _: True,
    )
    artifact = _artifact(42, 50)
    if field.startswith("missing_"):
        artifact["workflow_run"].pop(field.removeprefix("missing_"))
    else:
        artifact["workflow_run"]["repository_id"] = repository_id
    artifact["workflow_run"]["head_repository_id"] = head_repository_id
    actions = Actions()
    actions.artifacts[42] = artifact
    publisher, checkpoint, _, _ = _publisher(actions=actions)

    with pytest.raises(CloudPublicationError, match="artifact provenance"):
        publisher.reserve(
            generation_dir=tmp_path / "store/generations" / pointer["generation_id"],
            artifact_id=42,
            run_id=50,
            event_sha=REVISION,
        )

    assert checkpoint.writes == 0


@pytest.mark.parametrize(
    ("runner_repository", "runner_repository_id"),
    [
        ("other/repo", str(REPOSITORY_ID)),
        (REPO, "not-a-number"),
        (REPO, "0"),
        (None, str(REPOSITORY_ID)),
    ],
)
def test_runtime_rejects_invalid_runner_identity_before_client_construction(
    monkeypatch, runner_repository, runner_repository_id
):
    settings = SimpleNamespace(
        cloud_state_repo=REPO,
        cloud_state_branch="cloud-state",
        cloud_state_token=SimpleNamespace(get_secret_value=lambda: "token"),
    )
    monkeypatch.setattr(cloud_publication, "Settings", lambda: settings)
    if runner_repository is None:
        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    else:
        monkeypatch.setenv("GITHUB_REPOSITORY", runner_repository)
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", runner_repository_id)
    monkeypatch.setattr(
        cloud_publication,
        "GitHubCheckpoint",
        lambda *_: pytest.fail("checkpoint client constructed before identity validation"),
    )
    monkeypatch.setattr(
        cloud_publication,
        "GitHubActionsApi",
        lambda *_: pytest.fail("Actions client constructed before identity validation"),
    )
    monkeypatch.setattr(
        cloud_publication,
        "GitHubPagesDeploymentStatusApi",
        lambda *_: pytest.fail("Pages deployment client constructed before identity validation"),
    )

    with pytest.raises(CloudPublicationError, match="runner repository identity") as error:
        cloud_publication._runtime()

    assert error.value.code.value == "preflight_configuration_invalid"


def test_runtime_returns_validated_repository_id_to_publisher(monkeypatch):
    settings = SimpleNamespace(
        cloud_state_repo=REPO,
        cloud_state_branch="cloud-state",
        cloud_state_token=SimpleNamespace(get_secret_value=lambda: "token"),
    )
    checkpoint = object()
    actions = object()
    deployments = object()
    monkeypatch.setattr(cloud_publication, "Settings", lambda: settings)
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO.upper())
    monkeypatch.setenv("GITHUB_REPOSITORY_ID", str(REPOSITORY_ID))
    monkeypatch.setattr(cloud_publication, "GitHubCheckpoint", lambda *_: checkpoint)
    monkeypatch.setattr(cloud_publication, "GitHubActionsApi", lambda *_: actions)
    monkeypatch.setattr(
        cloud_publication, "GitHubPagesDeploymentStatusApi", lambda *_: deployments
    )

    assert cloud_publication._runtime() == (
        checkpoint, actions, deployments, settings, REPOSITORY_ID
    )


def test_actions_api_lists_only_current_workflow_runs_with_authentication():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200, json={"total_count": 2, "workflow_runs": [{"id": 50}, {"id": 49}]}
        )

    actions = GitHubActionsApi(REPO, "token", transport=httpx.MockTransport(handle))
    assert actions.active_workflow_runs(current_run_id=50) == [{"id": 49}]
    assert requests[0].url.path.endswith(
        "/actions/workflows/" + Path(WORKFLOW_PATH).name + "/runs"
    )
    assert requests[0].url.params["status"] == "in_progress"
    assert requests[0].headers["Authorization"] == "Bearer token"
    actions.close()


def test_actions_api_reads_only_supported_pages_branch_heads():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={"ref": "refs/heads/dev", "object": {"sha": NEW_REVISION}},
        )

    actions = GitHubActionsApi(REPO, "token", transport=httpx.MockTransport(handle))
    assert actions.branch_head("dev") == NEW_REVISION
    assert requests[0].url.path.endswith("/git/ref/heads/dev")
    with pytest.raises(CloudPublicationError, match="Unsupported Pages publication branch"):
        actions.branch_head("features/test")
    assert len(requests) == 1
    actions.close()


def test_actions_api_confirms_deploy_step_was_skipped():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "total_count": 1,
                "jobs": [
                    {
                        "name": "deploy",
                        "conclusion": "failure",
                        "steps": [
                            {
                                "name": "Confirm publication revision is still the branch head",
                                "status": "completed",
                                "conclusion": "failure",
                            },
                            {
                                "name": "Deploy the reserved artifact",
                                "status": "completed",
                                "conclusion": "skipped",
                            },
                        ],
                    }
                ]
            },
        )

    actions = GitHubActionsApi(REPO, "token", transport=httpx.MockTransport(handle))
    assert actions.deployment_action_was_skipped(40)
    assert requests[0].url.path.endswith("/actions/runs/40/jobs")
    assert requests[0].headers["Authorization"] == "Bearer token"
    actions.close()


def test_actions_api_does_not_treat_an_attempted_deploy_step_as_skipped():
    actions = GitHubActionsApi(
        REPO,
        "token",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "total_count": 1,
                    "jobs": [
                        {
                            "name": "deploy",
                            "conclusion": "failure",
                            "steps": [
                                {
                                    "name": "Deploy the reserved artifact",
                                    "status": "completed",
                                    "conclusion": "failure",
                                }
                            ],
                        }
                    ]
                },
            )
        ),
    )
    assert not actions.deployment_action_was_skipped(40)
    actions.close()


def test_actions_api_checks_skipped_evidence_across_all_workflow_attempts():
    requests = []

    def handle(request):
        requests.append(request)
        page = request.url.params["page"]
        if page == "1":
            return httpx.Response(
                200,
                json={
                    "total_count": 2,
                    "jobs": [{"name": "deploy", "conclusion": "skipped"}],
                },
            )
        return httpx.Response(
            200,
            json={
                "total_count": 2,
                "jobs": [
                    {
                        "name": "deploy",
                        "conclusion": "failure",
                        "steps": [
                            {
                                "name": "Deploy the reserved artifact",
                                "status": "completed",
                                "conclusion": "failure",
                            }
                        ],
                    }
                ],
            },
        )

    actions = GitHubActionsApi(REPO, "token", transport=httpx.MockTransport(handle))
    assert not actions.deployment_action_was_skipped(40)
    assert [request.url.params["filter"] for request in requests] == ["all", "all"]
    assert [request.url.params["page"] for request in requests] == ["1", "2"]
    actions.close()


def test_actions_api_does_not_confirm_skipped_deploy_from_truncated_job_pages():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "total_count": 101,
                "jobs": (
                    [{"name": "deploy", "conclusion": "skipped"}]
                    + [{"name": f"build-{index}"} for index in range(99)]
                    if len(requests) == 1
                    else []
                ),
            },
        )

    actions = GitHubActionsApi(REPO, "token", transport=httpx.MockTransport(handle))
    with pytest.raises(CloudPublicationError, match="workflow jobs could not be enumerated"):
        actions.deployment_action_was_skipped(40)
    assert len(requests) == 2
    actions.close()


def test_pages_deployment_status_api_reads_status_by_workflow_revision():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "deployment_failed"})

    api = cloud_publication.GitHubPagesDeploymentStatusApi(
        REPO, "token", transport=httpx.MockTransport(handle)
    )
    assert api.status(REVISION) == "deployment_failed"
    assert requests[0].url.path.endswith(f"/pages/deployments/{REVISION}")
    assert requests[0].headers["Authorization"] == "Bearer token"
    api.close()


def test_pages_deployment_status_api_keeps_unknown_create_outcome_distinct():
    api = cloud_publication.GitHubPagesDeploymentStatusApi(
        REPO, "token", transport=httpx.MockTransport(
            lambda request: httpx.Response(404, json={"message": "Not Found"})
        )
    )
    assert api.status(REVISION) is None
    api.close()


def test_preflight_rejects_queued_or_rerun_event_after_branch_advances():
    actions = Actions(heads={"dev": NEW_REVISION})
    publisher, checkpoint, _, _ = _publisher(actions=actions)

    with pytest.raises(CloudPublicationError, match="no longer the branch head") as exc_info:
        publisher.preflight(
            mode="code-only", event_ref="refs/heads/dev", event_sha=REVISION, run_id=50
        )

    assert exc_info.value.code.value == "event_revision_stale"
    assert PublicationCheckpoint(checkpoint).read().candidate is None


def test_preflight_cli_emits_fixed_code_and_hides_exception_details(monkeypatch, capsys):
    checkpoint = MemoryCheckpoint()
    actions = Actions()

    def fail_to_read_run_list(*, current_run_id):
        try:
            raise ValueError("sentinel chained cause")
        except ValueError as cause:
            raise CloudPublicationError("sentinel exception text") from cause

    actions.active_workflow_runs = fail_to_read_run_list
    _stub_cli_runtime(monkeypatch, checkpoint, actions, Pages(None))

    result = main(_preflight_cli_args())

    stderr = capsys.readouterr().err.splitlines()
    assert result == 1
    assert json.loads(stderr[0]) == {
        "event": "cloud_publication.failure",
        "stage": "preflight",
        "code": "actions_run_list_read_failed",
    }
    assert stderr[1] == "Cloud Pages publication could not be confirmed"
    assert "sentinel" not in "\n".join(stderr)
    assert checkpoint.writes == 0


@pytest.mark.parametrize(
    ("arguments", "expected_event_code"),
    [
        (
            _preflight_cli_args(),
            "preflight_configuration_invalid",
        ),
        (
            [
                "reserve",
                "--generation-dir",
                "/tmp/generation",
                "--artifact-id",
                "41",
                "--run-id",
                "50",
                "--event-sha",
                REVISION,
            ],
            None,
        ),
        (
            [
                "finish",
                "--run-id",
                "50",
                "--event-sha",
                REVISION,
                "--site-url",
                "https://example.test/",
            ],
            None,
        ),
    ],
)
def test_cloud_publication_cli_classifies_invalid_runtime_settings(
    arguments, expected_event_code
):
    result = _run_python_with_invalid_settings(
        "-m", "pipeline.cloud_publication", *arguments
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "Traceback" not in result.stderr
    assert "not-a-bool" not in result.stderr
    lines = result.stderr.splitlines()
    if expected_event_code:
        assert json.loads(lines[0]) == {
            "event": "cloud_publication.failure",
            "stage": "preflight",
            "code": expected_event_code,
        }
        assert lines[1] == "Cloud Pages publication could not be confirmed"
    else:
        assert lines == ["Cloud Pages publication could not be confirmed"]


@pytest.mark.parametrize(
    ("arguments", "expected_returncode"),
    [(["--help"], 0), (["preflight", "--mode", "invalid"], 2)],
)
def test_cloud_publication_cli_parses_arguments_before_settings(
    arguments, expected_returncode
):
    result = _run_python_with_invalid_settings(
        "-m", "pipeline.cloud_publication", *arguments
    )

    assert result.returncode == expected_returncode
    assert "Traceback" not in result.stderr
    assert "not-a-bool" not in result.stderr
    assert "cloud_publication.failure" not in result.stderr


def test_settings_import_is_lazy_and_retries_after_failed_validation():
    code = """
import os
from pydantic import ValidationError
import pipeline.settings as settings_module
from pipeline.settings import Settings

try:
    settings_module.missing_name
except AttributeError:
    pass
else:
    raise AssertionError("unknown attributes must not construct settings")

try:
    settings_module.settings
except ValidationError:
    pass
else:
    raise AssertionError("invalid settings must still fail on access")

os.environ["DATA_COINMETRICS_ENABLED"] = "false"
first = settings_module.settings
assert isinstance(first, Settings)
first.coinmetrics_enabled = True
from pipeline.settings import settings
assert settings is first
assert settings_module.settings is first
assert settings.coinmetrics_enabled is True
"""
    result = _run_python_with_invalid_settings("-c", code)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


def test_preflight_cli_distinguishes_legacy_probe_failure(monkeypatch, capsys):
    checkpoint = MemoryCheckpoint()
    actions = Actions()
    pages = Pages(None)

    def fail_legacy_probe():
        raise CloudRefreshError("sentinel legacy response")

    _stub_cli_runtime(monkeypatch, checkpoint, actions, pages)
    monkeypatch.setattr(pages, "has_legacy_data", fail_legacy_probe)

    result = main(_preflight_cli_args())

    stderr = capsys.readouterr().err.splitlines()
    assert result == 1
    assert json.loads(stderr[0]) == {
        "event": "cloud_publication.failure",
        "stage": "preflight",
        "code": "legacy_data_probe_failed",
    }
    assert stderr[1] == "Cloud Pages publication could not be confirmed"
    assert "sentinel" not in "\n".join(stderr)
    assert checkpoint.writes == 0


def test_preflight_cli_success_outputs_are_unchanged(monkeypatch, capsys, tmp_path):
    checkpoint = MemoryCheckpoint()
    actions = Actions()
    pages = Pages(None)
    output = tmp_path / "github-output.txt"
    _stub_cli_runtime(monkeypatch, checkpoint, actions, pages)
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    result = main(_preflight_cli_args("--allow-legacy-bootstrap"))

    captured = capsys.readouterr()
    assert result == 0
    assert output.read_text() == f"mode=code-only\napp_revision={REVISION}\nresume=false\n"
    assert captured.out == ""
    assert captured.err == ""


def test_cloud_publication_cli_success_outputs_cover_each_command(
    monkeypatch, capsys, tmp_path
):
    candidate = _generation(parent_id="d" * 64)
    plan = SimpleNamespace(
        mode="code-only", application_revision=REVISION, resume=candidate
    )
    publisher = SimpleNamespace(
        preflight=lambda **_: plan,
        reserve=lambda **_: candidate,
        finish=lambda **_: candidate,
    )

    checkpoint = MemoryCheckpoint()
    actions = Actions()
    pages = Pages(None)
    _stub_cli_runtime(monkeypatch, checkpoint, actions, pages)
    monkeypatch.setattr(cloud_publication, "CloudPublisher", lambda *_: publisher)
    output = tmp_path / "github-output.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))

    commands = [
        (
            _preflight_cli_args(),
            "\n".join(
                (
                    "mode=code-only",
                    f"app_revision={REVISION}",
                    "resume=true",
                    f"generation_id={candidate.generation_id}",
                    f"manifest_sha256={candidate.manifest_sha256}",
                    f"producer_revision={candidate.producer_revision}",
                    f"parent_id={candidate.parent_id}",
                    f"artifact_id={candidate.artifact_id}",
                )
            )
            + "\n",
        ),
        (
            [
                "reserve",
                "--generation-dir",
                str(tmp_path),
                "--artifact-id",
                str(candidate.artifact_id),
                "--run-id",
                "50",
                "--event-sha",
                REVISION,
            ],
            "\n".join(
                (
                    f"generation_id={candidate.generation_id}",
                    f"attempt_id={candidate.attempt_id}",
                    f"artifact_id={candidate.artifact_id}",
                )
            )
            + "\n",
        ),
        (
            [
                "finish",
                "--run-id",
                "50",
                "--event-sha",
                REVISION,
                "--site-url",
                "https://example.test/",
            ],
            f"generation_id={candidate.generation_id}\n"
            f"manifest_sha256={candidate.manifest_sha256}\n",
        ),
    ]

    for arguments, expected_output in commands:
        output.write_text("", encoding="utf-8")
        result = main(arguments)
        captured = capsys.readouterr()

        assert result == 0
        assert output.read_text(encoding="utf-8") == expected_output
        assert captured.out == ""
        assert captured.err == ""


def test_reserve_and_finish_cli_failures_do_not_emit_preflight_events(
    monkeypatch, capsys, tmp_path
):
    checkpoint = MemoryCheckpoint()
    actions = Actions()
    pages = Pages(None)
    _stub_cli_runtime(monkeypatch, checkpoint, actions, pages)

    reserve_result = main(
        [
            "reserve",
            "--generation-dir",
            str(tmp_path / "missing-generation"),
            "--artifact-id",
            "42",
            "--run-id",
            "50",
            "--event-sha",
            REVISION,
        ]
    )
    reserve_stderr = capsys.readouterr().err

    finish_result = main(
        [
            "finish",
            "--run-id",
            "50",
            "--event-sha",
            REVISION,
            "--site-url",
            "https://example.test/",
        ]
    )
    finish_stderr = capsys.readouterr().err

    assert reserve_result == 1
    assert reserve_stderr == "Cloud Pages publication could not be confirmed\n"
    assert finish_result == 1
    assert finish_stderr == "Cloud Pages publication could not be confirmed\n"


def test_artifact_api_provenance_flows_through_reserve_resume_and_finish(tmp_path):
    files = {
        "latest/data.json": {"generated_at": STAMP, "value": 3},
        "history/data.json": [{"date": "2026-11-14", "value": 2}],
        "metadata/data.json": {"updated_at": STAMP},
    }
    pointer = publish_snapshot(
        tmp_path / "store",
        files,
        parent_id=None,
        producer_revision=REVISION,
        created_at=STAMP,
        validator=lambda _: True,
    )
    generation_dir = tmp_path / "store/generations" / pointer["generation_id"]
    requests = []

    def handle(request):
        requests.append(request)
        path = request.url.path
        if path.endswith("/actions/artifacts/42"):
            return httpx.Response(200, json=_artifact(42, 50))
        if path.endswith("/actions/workflows/" + Path(WORKFLOW_PATH).name + "/runs"):
            return httpx.Response(200, json={"total_count": 0, "workflow_runs": []})
        if path.endswith("/git/ref/heads/dev"):
            return httpx.Response(
                200, json={"ref": "refs/heads/dev", "object": {"sha": REVISION}}
            )
        if path.endswith("/actions/runs/50"):
            return httpx.Response(
                200,
                json={
                    "id": 50,
                    "path": WORKFLOW_PATH,
                    "status": "completed",
                    "head_repository": {"full_name": REPO},
                    "head_sha": REVISION,
                },
            )
        raise AssertionError(f"Unexpected GitHub API request: {path}")

    actions = GitHubActionsApi(REPO, "token", transport=httpx.MockTransport(handle))
    checkpoint = MemoryCheckpoint()
    pages = Pages(None)
    publisher = CloudPublisher(
        PublicationCheckpoint(checkpoint),
        actions,
        pages,
        PagesDeployments("succeed"),
        REPO,
        REPOSITORY_ID,
    )
    try:
        generation = publisher.reserve(
            generation_dir=generation_dir, artifact_id=42, run_id=50, event_sha=REVISION
        )
    except CloudPublicationError as error:
        reservation = f"rejected: {error}"
    else:
        reservation = f"reserved: {generation.artifact_id}"
    assert reservation == "reserved: 42"
    pages.pointer = {
        "version": 1,
        "generation_id": generation.generation_id,
        "manifest_sha256": generation.manifest_sha256,
    }
    plan = publisher.preflight(
        mode="code-only", event_ref="refs/heads/dev", event_sha=REVISION, run_id=50
    )
    assert plan.application_revision == REVISION
    deployed = publisher.finish(current_run_id=50, event_sha=REVISION)
    assert deployed == generation
    state = PublicationCheckpoint(checkpoint).read()
    assert state.deployed == generation and state.candidate is None
    assert pages.restored == [generation.generation_id]
    assert sum(request.url.path.endswith("/actions/artifacts/42") for request in requests) == 3
    actions.close()
