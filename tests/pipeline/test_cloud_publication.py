import hashlib
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from pipeline.cloud_publication import (
    WORKFLOW_PATH,
    CloudPublicationError,
    CloudPublisher,
    GitHubActionsApi,
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
REVISION = "a" * 40
NEW_REVISION = "b" * 40
STAMP = "2026-11-15T12:00:00Z"
ARTIFACT_DIGEST = "sha256:" + "c" * 64


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
    def __init__(self, *, current_run=50, conclusion="failure", other_runs=None, heads=None):
        self.current_run = current_run
        self.conclusion = conclusion
        self.other_runs = other_runs or []
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
            "head_repository": {"full_name": REPO},
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


def _publisher(state=None, *, pointer=None, actions=None, legacy_data=False):
    checkpoint = MemoryCheckpoint(state or PublicationState())
    api = actions or Actions()
    pages = Pages(pointer, legacy_data=legacy_data)
    return (
        CloudPublisher(PublicationCheckpoint(checkpoint), api, pages, REPO),
        checkpoint,
        api,
        pages,
    )


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
    with pytest.raises(CloudPublicationError, match="Another Pages workflow"):
        publisher.preflight(
            mode="code-only", event_ref="refs/heads/dev", event_sha=REVISION, run_id=50
        )


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
    publisher, _, _, _ = _publisher(
        PublicationState(candidate=candidate, last_attempt=1), actions=actions
    )
    with pytest.raises(CloudPublicationError, match="artifact provenance"):
        publisher.preflight(
            mode="code-only", event_ref="refs/heads/dev", event_sha=REVISION,
            run_id=candidate.workflow_run_id
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


def test_preflight_rejects_queued_or_rerun_event_after_branch_advances():
    actions = Actions(heads={"dev": NEW_REVISION})
    publisher, checkpoint, _, _ = _publisher(actions=actions)

    with pytest.raises(CloudPublicationError, match="no longer the branch head"):
        publisher.preflight(
            mode="code-only", event_ref="refs/heads/dev", event_sha=REVISION, run_id=50
        )

    assert PublicationCheckpoint(checkpoint).read().candidate is None


def test_reserve_and_finish_bind_the_exact_artifact_to_pages_readback(tmp_path):
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
    actions = Actions(current_run=50)
    actions.artifacts[42] = _artifact(42, 50)
    publisher, checkpoint, _, pages = _publisher(actions=actions)
    generation = publisher.reserve(
        generation_dir=generation_dir, artifact_id=42, run_id=50, event_sha=REVISION
    )
    pages.pointer = {
        "version": 1,
        "generation_id": generation.generation_id,
        "manifest_sha256": generation.manifest_sha256,
    }
    deployed = publisher.finish(current_run_id=50, event_sha=REVISION)
    assert deployed == generation
    state = PublicationCheckpoint(checkpoint).read()
    assert state.deployed == generation and state.candidate is None
    assert pages.restored == [generation.generation_id]
