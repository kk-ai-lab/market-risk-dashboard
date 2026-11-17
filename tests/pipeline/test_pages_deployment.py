import hashlib
import json
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from pipeline.pages_deployment import (
    GitHubPagesDeploymentApi,
    PagesDeploymentError,
    PagesStatusError,
)
from pipeline.storage.github_checkpoint import CheckpointSnapshot
from pipeline.storage.publication_checkpoint import (
    Generation,
    PublicationCheckpoint,
    empty_state,
)

REPOSITORY = "owner/repo"
BUILD_VERSION = "a" * 40
SITE_URL = "https://example.test/dashboard/"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


class MemoryCheckpoint:
    def __init__(self):
        self.content = empty_state()
        self.head = 0

    def read(self, path):
        return CheckpointSnapshot(f"{self.head:040x}", "b" * 40, path, self.content)

    def write(self, snapshot, content, message):
        assert snapshot.head_sha == f"{self.head:040x}"
        self.content = content
        self.head += 1
        return f"{self.head:040x}"


def _candidate():
    generation = Generation(
        hashlib.sha256(b"generation").hexdigest(),
        hashlib.sha256(b"manifest").hexdigest(),
        BUILD_VERSION,
        None,
        1,
        str(uuid4()),
        123,
        456,
        "sha256:" + "c" * 64,
    )
    checkpoint = MemoryCheckpoint()
    publication = PublicationCheckpoint(checkpoint)
    candidate = publication.prepare(generation).candidate
    assert candidate is not None
    return publication, candidate


def _api(handler, *, oidc_handler=None, **kwargs):
    clock = [0.0]

    def sleep(seconds):
        clock[0] += seconds

    return GitHubPagesDeploymentApi(
        REPOSITORY,
        "github-token",
        transport=httpx.MockTransport(handler),
        oidc_transport=httpx.MockTransport(
            oidc_handler or (lambda _: httpx.Response(200, json={"value": "oidc-token"}))
        ),
        oidc_request_url="https://token.actions.githubusercontent.com/oidc",
        oidc_request_token="runner-token",
        sleep=sleep,
        monotonic=lambda: clock[0],
        poll_interval=5,
        **kwargs,
    )


def test_status_distinguishes_not_found_from_read_failures():
    not_found = _api(lambda _: httpx.Response(404))
    empty_status = _api(lambda _: httpx.Response(200, json={"status": ""}))
    denied = _api(lambda _: httpx.Response(403))
    try:
        assert not_found.status(BUILD_VERSION) is None
        assert empty_status.status(BUILD_VERSION) is None
        with pytest.raises(PagesStatusError):
            denied.status(BUILD_VERSION)
    finally:
        not_found.close()
        empty_status.close()
        denied.close()


def test_api_endpoint_rejects_embedded_credentials():
    with pytest.raises(ValueError, match="GitHub API URL"):
        GitHubPagesDeploymentApi(REPOSITORY, "token", api_url="https://user:pass@example.test")


def test_deploy_cli_imports_without_pydantic():
    script = """
import importlib.abc
import runpy
import sys

class BlockPydantic(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "pydantic" or fullname.startswith("pydantic."):
            raise ModuleNotFoundError("pydantic is blocked in this isolated process")
        return None

sys.meta_path.insert(0, BlockPydantic())
sys.argv = ["pipeline.pages_deployment", "--help"]
try:
    runpy.run_module("pipeline.pages_deployment", run_name="__main__")
except SystemExit as error:
    if error.code != 0:
        raise
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_create_posts_the_exact_reserved_artifact_and_oidc_token():
    def handler(request):
        assert request.method == "POST"
        assert request.headers["Authorization"] == "Bearer github-token"
        assert request.url.path == f"/repos/{REPOSITORY}/pages/deployments"
        assert request.read() == (
            b'{"artifact_id":123,"environment":"github-pages",'
            b'"pages_build_version":"' + BUILD_VERSION.encode() + b'",'
            b'"oidc_token":"oidc-token"}'
        )
        return httpx.Response(200, json={"id": 789})

    api = _api(handler)
    try:
        assert api.create(123, BUILD_VERSION, "oidc-token") == "789"
    finally:
        api.close()


def test_deploy_reserved_claims_once_and_waits_for_exact_deployment():
    publication, candidate = _candidate()
    posts = []

    def handler(request):
        if request.method == "POST":
            posts.append(request)
            assert json.loads(request.read()) == {
                "artifact_id": 123,
                "environment": "github-pages",
                "pages_build_version": candidate.pages_build_version,
                "oidc_token": "oidc-token",
            }
            return httpx.Response(200, json={"id": 789})
        if request.url.path.endswith("/789"):
            return httpx.Response(200, json={"status": "succeed"})
        return httpx.Response(200, json={"status": ""})

    def oidc_handler(request):
        assert request.headers["Authorization"] == "Bearer runner-token"
        assert request.url.query == b""
        return httpx.Response(200, json={"value": "oidc-token"})

    api = _api(handler, oidc_handler=oidc_handler)
    try:
        assert api.deploy_reserved(
            publication,
            run_id=candidate.workflow_run_id,
            artifact_id=candidate.artifact_id,
            build_version=candidate.pages_build_version,
            site_url=SITE_URL,
        ) == SITE_URL
        state = publication.read()
        assert len(posts) == 1
        assert state.candidate.submission is not None
    finally:
        api.close()


def test_ambiguous_create_is_never_posted_again_on_retry():
    publication, candidate = _candidate()
    posts = 0

    def handler(request):
        nonlocal posts
        if request.method == "POST":
            posts += 1
            raise httpx.ConnectError("response lost", request=request)
        if request.url.path.endswith(candidate.pages_build_version) and posts:
            return httpx.Response(200, json={"status": "succeed"})
        return httpx.Response(404)

    api = _api(handler)
    try:
        assert api.deploy_reserved(
            publication,
            run_id=candidate.workflow_run_id,
            artifact_id=candidate.artifact_id,
            build_version=candidate.pages_build_version,
            site_url=SITE_URL,
        ) == SITE_URL
        assert api.deploy_reserved(
            publication,
            run_id=candidate.workflow_run_id,
            artifact_id=candidate.artifact_id,
            build_version=candidate.pages_build_version,
            site_url=SITE_URL,
        ) == SITE_URL
        assert posts == 1
    finally:
        api.close()


def test_ambiguous_create_with_missing_status_fails_closed_without_retry():
    publication, candidate = _candidate()
    creates = 0
    cancels = 0

    def handler(request):
        nonlocal creates, cancels
        if request.method == "POST":
            if request.url.path.endswith("/cancel"):
                cancels += 1
                return httpx.Response(204)
            creates += 1
            raise httpx.ConnectError("response lost", request=request)
        return httpx.Response(404)

    api = _api(handler, max_status_errors=1)
    try:
        with pytest.raises(PagesDeploymentError, match="outcome could not be resolved"):
            api.deploy_reserved(
                publication,
                run_id=candidate.workflow_run_id,
                artifact_id=candidate.artifact_id,
                build_version=candidate.pages_build_version,
                site_url=SITE_URL,
                timeout_seconds=0.001,
            )
        with pytest.raises(PagesDeploymentError, match="outcome remains unresolved"):
            api.deploy_reserved(
                publication,
                run_id=candidate.workflow_run_id,
                artifact_id=candidate.artifact_id,
                build_version=candidate.pages_build_version,
                site_url=SITE_URL,
            )
        assert creates == 1
        assert cancels == 1
    finally:
        api.close()


def test_status_errors_after_ambiguous_create_cancel_and_preserve_pending_state():
    publication, candidate = _candidate()
    creates = 0
    cancels = 0

    def handler(request):
        nonlocal creates, cancels
        if request.method == "POST" and request.url.path.endswith("/cancel"):
            cancels += 1
            return httpx.Response(204)
        if request.method == "POST":
            creates += 1
            raise httpx.ConnectError("response lost", request=request)
        return httpx.Response(503 if creates else 404)

    api = _api(handler, max_status_errors=1)
    try:
        with pytest.raises(PagesDeploymentError, match="outcome could not be resolved"):
            api.deploy_reserved(
                publication,
                run_id=candidate.workflow_run_id,
                artifact_id=candidate.artifact_id,
                build_version=candidate.pages_build_version,
                site_url=SITE_URL,
            )
        assert creates == 1 and cancels == 1
        assert publication.read().candidate.submission is not None
    finally:
        api.close()


def test_definite_rejection_is_persisted_and_never_reposted():
    publication, candidate = _candidate()
    posts = 0

    def handler(request):
        nonlocal posts
        if request.method == "POST":
            posts += 1
            return httpx.Response(422)
        return httpx.Response(404)

    api = _api(handler)
    try:
        with pytest.raises(PagesDeploymentError, match="HTTP 422"):
            api.deploy_reserved(
                publication,
                run_id=candidate.workflow_run_id,
                artifact_id=candidate.artifact_id,
                build_version=candidate.pages_build_version,
                site_url=SITE_URL,
            )
        assert publication.read().candidate.submission.outcome.value == "rejected"
        with pytest.raises(PagesDeploymentError, match="rejected"):
            api.deploy_reserved(
                publication,
                run_id=candidate.workflow_run_id,
                artifact_id=candidate.artifact_id,
                build_version=candidate.pages_build_version,
                site_url=SITE_URL,
            )
        assert posts == 1
    finally:
        api.close()


def test_second_deployer_cannot_post_when_submission_was_already_claimed():
    publication, candidate = _candidate()
    publication.claim_submission(candidate)
    posts = []
    api = _api(lambda request: posts.append(request) or httpx.Response(404))
    try:
        with pytest.raises(PagesDeploymentError, match="outcome remains unresolved"):
            api.deploy_reserved(
                publication,
                run_id=candidate.workflow_run_id,
                artifact_id=candidate.artifact_id,
                build_version=candidate.pages_build_version,
                site_url=SITE_URL,
            )
        assert all(request.method != "POST" for request in posts)
    finally:
        api.close()


def test_deployment_lost_does_not_claim_final_deployment_state():
    publication, candidate = _candidate()
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"status": "deployment_lost"})

    api = _api(handler)
    try:
        with pytest.raises(PagesDeploymentError, match="final status is unknown"):
            api.deploy_reserved(
                publication,
                run_id=candidate.workflow_run_id,
                artifact_id=candidate.artifact_id,
                build_version=candidate.pages_build_version,
                site_url=SITE_URL,
            )
        assert all(request.method != "POST" for request in requests)
        assert publication.read().candidate.submission is not None
    finally:
        api.close()


def test_cancel_signal_cancels_the_exact_reserved_deployment():
    publication, candidate = _candidate()
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"status": "deploying"})
        return httpx.Response(204)

    api = _api(handler)
    api.request_cancel()
    try:
        with pytest.raises(PagesDeploymentError, match="was cancelled"):
            api.deploy_reserved(
                publication,
                run_id=candidate.workflow_run_id,
                artifact_id=candidate.artifact_id,
                build_version=candidate.pages_build_version,
                site_url=SITE_URL,
            )
        assert requests[-1].method == "POST"
        assert requests[-1].url.path.endswith(
            f"/pages/deployments/{candidate.pages_build_version}/cancel"
        )
    finally:
        api.close()


def test_reserved_identity_must_match_before_any_network_call():
    publication, candidate = _candidate()
    calls = []
    api = _api(lambda request: calls.append(request) or httpx.Response(404))
    try:
        with pytest.raises(PagesDeploymentError, match="does not match"):
            api.deploy_reserved(
                publication,
                run_id=candidate.workflow_run_id + 1,
                artifact_id=candidate.artifact_id,
                build_version=candidate.pages_build_version,
                site_url=SITE_URL,
            )
        assert calls == []
    finally:
        api.close()


def test_cancel_during_oidc_does_not_claim_or_submit():
    publication, candidate = _candidate()
    api_ref = []
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(404)

    def oidc_handler(_):
        api_ref[0].request_cancel()
        return httpx.Response(200, json={"value": "oidc-token"})

    api = _api(handler, oidc_handler=oidc_handler)
    api_ref.append(api)
    try:
        with pytest.raises(PagesDeploymentError, match="was cancelled"):
            api.deploy_reserved(
                publication,
                run_id=candidate.workflow_run_id,
                artifact_id=candidate.artifact_id,
                build_version=candidate.pages_build_version,
                site_url=SITE_URL,
            )
        assert all(request.method != "POST" for request in requests)
        assert publication.read().candidate.submission is None
    finally:
        api.close()
