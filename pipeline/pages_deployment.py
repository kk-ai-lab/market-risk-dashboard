from __future__ import annotations

import argparse
import os
import re
import signal
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from pipeline.storage.github_checkpoint import CheckpointError, GitHubCheckpoint
from pipeline.storage.publication_checkpoint import (
    PublicationCheckpoint,
    SubmissionOutcome,
)

TERMINAL_FAILURES = {
    "deployment_failed",
    "deployment_perms_error",
    "deployment_content_failed",
    "deployment_cancelled",
    "failed",
    "cancelled",
}
DEFINITIVE_REJECTIONS = {400, 401, 403, 404, 422}


class PagesDeploymentError(RuntimeError):
    pass


class PagesStatusError(PagesDeploymentError):
    pass


class PagesCreateRejected(PagesDeploymentError):
    def __init__(self, status_code: int):
        super().__init__(f"Pages rejected deployment request with HTTP {status_code}")
        self.status_code = status_code


class PagesCreateOutcomeUnknown(PagesDeploymentError):
    pass


def _repository_parts(repository: str) -> tuple[str, str]:
    parts = repository.split("/")
    if len(parts) != 2 or any(re.fullmatch(r"[A-Za-z0-9_.-]+", part) is None for part in parts):
        raise ValueError("Invalid GitHub repository")
    return parts[0], parts[1]


def _deployment_id(value: object) -> str:
    if type(value) is int and value > 0:
        return str(value)
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        return value
    raise PagesDeploymentError("GitHub returned an invalid Pages deployment ID")


class GitHubPagesDeploymentApi:
    def __init__(
        self,
        repository: str,
        token: str,
        *,
        api_url: str = "https://api.github.com",
        transport: httpx.BaseTransport | None = None,
        oidc_transport: httpx.BaseTransport | None = None,
        oidc_request_url: str | None = None,
        oidc_request_token: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        poll_interval: float = 5,
        max_status_errors: int = 10,
    ):
        owner, name = _repository_parts(repository)
        parsed_api_url = urlsplit(api_url)
        if (parsed_api_url.scheme != "https" or parsed_api_url.hostname is None
                or parsed_api_url.username is not None or parsed_api_url.password is not None
                or parsed_api_url.query or parsed_api_url.fragment):
            raise ValueError("Invalid GitHub API URL")
        if not token or any(char in token for char in "\r\n"):
            raise ValueError("Invalid GitHub token")
        if poll_interval <= 0 or max_status_errors < 1:
            raise ValueError("Invalid deployment polling limits")
        self._root = (
            f"{api_url.rstrip('/')}/repos/{quote(owner, safe='')}/{quote(name, safe='')}"
        )
        self._api = httpx.Client(
            transport=transport,
            timeout=30,
            follow_redirects=False,
            trust_env=False,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        self._oidc = httpx.Client(
            transport=oidc_transport,
            timeout=30,
            follow_redirects=False,
            trust_env=False,
        )
        self._oidc_request_url = oidc_request_url
        self._oidc_request_token = oidc_request_token
        self._sleep = sleep
        self._monotonic = monotonic
        self._poll_interval = poll_interval
        self._max_status_errors = max_status_errors
        self._cancel_requested = False

    def close(self) -> None:
        self._api.close()
        self._oidc.close()

    def request_cancel(self, *_: object) -> None:
        self._cancel_requested = True

    def _request_oidc_token(self) -> str:
        request_url = self._oidc_request_url or os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
        request_token = self._oidc_request_token or os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
        if not request_url or not request_token:
            raise PagesDeploymentError("GitHub Actions OIDC credentials are unavailable")
        parsed = urlsplit(request_url)
        if (parsed.scheme != "https" or parsed.hostname is None
                or parsed.username is not None or parsed.password is not None or parsed.fragment):
            raise PagesDeploymentError("GitHub Actions OIDC endpoint is invalid")
        try:
            response = self._oidc.get(
                request_url,
                headers={"Authorization": f"Bearer {request_token}", "Accept": "application/json"},
            )
        except httpx.HTTPError as exc:
            raise PagesDeploymentError("GitHub Actions OIDC token could not be read") from exc
        if response.status_code != 200:
            raise PagesDeploymentError("GitHub Actions OIDC token could not be read")
        try:
            value = response.json()
        except ValueError as exc:
            raise PagesDeploymentError("GitHub Actions OIDC response is invalid") from exc
        token = value.get("value") if isinstance(value, dict) else None
        if not isinstance(token, str) or not token or any(char in token for char in "\r\n"):
            raise PagesDeploymentError("GitHub Actions OIDC response is invalid")
        return token

    def status(self, deployment_id: str) -> str | None:
        identity = _deployment_id(deployment_id)
        try:
            response = self._api.get(
                self._root + "/pages/deployments/" + quote(identity, safe="")
            )
        except httpx.HTTPError as exc:
            raise PagesStatusError("GitHub Pages deployment status could not be read") from exc
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise PagesStatusError("GitHub Pages deployment status could not be read")
        try:
            value = response.json()
        except ValueError as exc:
            raise PagesStatusError("GitHub Pages deployment status is invalid") from exc
        status = value.get("status") if isinstance(value, dict) else None
        if not isinstance(status, str) or not status or len(status) > 64:
            raise PagesStatusError("GitHub Pages deployment status is invalid")
        return status

    def create(self, artifact_id: int, build_version: str, oidc_token: str) -> str:
        if type(artifact_id) is not int or artifact_id < 1:
            raise PagesDeploymentError("Invalid Pages artifact ID")
        if re.fullmatch(r"[0-9a-f]{40}", build_version) is None:
            raise PagesDeploymentError("Invalid Pages build version")
        try:
            response = self._api.post(
                self._root + "/pages/deployments",
                json={
                    "artifact_id": artifact_id,
                    "environment": "github-pages",
                    "pages_build_version": build_version,
                    "oidc_token": oidc_token,
                },
            )
        except httpx.HTTPError as exc:
            raise PagesCreateOutcomeUnknown("Pages create request outcome is unknown") from exc
        if response.status_code in DEFINITIVE_REJECTIONS:
            raise PagesCreateRejected(response.status_code)
        if response.status_code != 200:
            raise PagesCreateOutcomeUnknown("Pages create request outcome is unknown")
        try:
            value = response.json()
        except ValueError as exc:
            raise PagesCreateOutcomeUnknown("Pages create response is invalid") from exc
        try:
            return _deployment_id(value.get("id") if isinstance(value, dict) else None)
        except PagesDeploymentError as exc:
            raise PagesCreateOutcomeUnknown("Pages create response is invalid") from exc

    def cancel(self, deployment_id: str) -> None:
        identity = _deployment_id(deployment_id)
        try:
            response = self._api.post(
                self._root + "/pages/deployments/" + quote(identity, safe="") + "/cancel"
            )
        except httpx.HTTPError as exc:
            raise PagesDeploymentError("GitHub Pages deployment could not be cancelled") from exc
        if response.status_code not in {204, 404}:
            raise PagesDeploymentError("GitHub Pages deployment could not be cancelled")

    @staticmethod
    def _validate_site_url(site_url: str) -> str:
        parsed = urlsplit(site_url)
        if (parsed.scheme != "https" or parsed.hostname is None
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment
                or any(ord(char) < 32 for char in site_url)):
            raise PagesDeploymentError("Invalid GitHub Pages URL")
        return site_url.rstrip("/") + "/"

    def _wait_for_status(
        self,
        deployment_id: str,
        *,
        timeout_seconds: float,
        first_status: str | None = None,
    ) -> None:
        deadline = self._monotonic() + timeout_seconds
        status = first_status
        errors = 0
        while True:
            if self._cancel_requested:
                with suppress(PagesDeploymentError):
                    self.cancel(deployment_id)
                raise PagesDeploymentError("GitHub Pages deployment was cancelled")
            if status == "succeed":
                return
            if status in TERMINAL_FAILURES:
                raise PagesDeploymentError("GitHub Pages deployment failed")
            if status == "deployment_lost":
                raise PagesDeploymentError("GitHub Pages deployment final status is unknown")
            if self._monotonic() >= deadline:
                with suppress(PagesDeploymentError):
                    self.cancel(deployment_id)
                raise PagesDeploymentError("GitHub Pages deployment timed out")
            if status is None or status not in {"succeed", *TERMINAL_FAILURES, "deployment_lost"}:
                try:
                    status = self.status(deployment_id)
                    errors = 0
                except PagesStatusError:
                    errors += 1
                    if errors >= self._max_status_errors:
                        with suppress(PagesDeploymentError):
                            self.cancel(deployment_id)
                        raise PagesDeploymentError("GitHub Pages status polling failed") from None
            self._sleep(min(self._poll_interval, max(0, deadline - self._monotonic())))

    def _wait_for_appearance(self, build_version: str, timeout_seconds: float = 30) -> str:
        deadline = self._monotonic() + timeout_seconds
        errors = 0
        while self._monotonic() < deadline:
            if self._cancel_requested:
                with suppress(PagesDeploymentError):
                    self.cancel(build_version)
                raise PagesDeploymentError("GitHub Pages deployment was cancelled")
            try:
                status = self.status(build_version)
                errors = 0
            except PagesStatusError as exc:
                errors += 1
                last_error = exc
                if errors >= self._max_status_errors:
                    break
                status = None
            if status is not None:
                self._wait_for_status(
                    build_version,
                    timeout_seconds=max(1, deadline - self._monotonic()),
                    first_status=status,
                )
                return build_version
            self._sleep(min(self._poll_interval, max(0, deadline - self._monotonic())))
        with suppress(PagesDeploymentError):
            self.cancel(build_version)
        if errors:
            raise PagesDeploymentError("Pages create outcome could not be resolved") from last_error
        raise PagesDeploymentError("Pages create outcome could not be resolved")

    def deploy_reserved(
        self,
        publication: PublicationCheckpoint,
        *,
        run_id: int,
        artifact_id: int,
        build_version: str,
        site_url: str,
        timeout_seconds: float = 600,
    ) -> str:
        if type(run_id) is not int or run_id < 1:
            raise PagesDeploymentError("Invalid workflow run ID")
        if type(artifact_id) is not int or artifact_id < 1:
            raise PagesDeploymentError("Invalid Pages artifact ID")
        output_url = self._validate_site_url(site_url)
        state = publication.read()
        candidate = state.candidate
        if (candidate is None or candidate.workflow_run_id != run_id
                or candidate.artifact_id != artifact_id
                or candidate.pages_build_version != build_version):
            raise PagesDeploymentError("Pages deployment does not match the reserved candidate")
        status = self.status(build_version)
        if status is not None:
            if candidate.submission is None:
                publication.claim_submission(candidate)
            self._wait_for_status(build_version, timeout_seconds=timeout_seconds, first_status=status)
            return output_url
        if candidate.submission is not None:
            if candidate.submission.outcome is SubmissionOutcome.REJECTED:
                raise PagesDeploymentError("Pages rejected the reserved candidate")
            raise PagesDeploymentError("Pages create outcome remains unresolved")
        oidc_token = self._request_oidc_token()
        if self._cancel_requested:
            raise PagesDeploymentError("GitHub Pages deployment was cancelled")
        claim = publication.claim_submission(candidate)
        if not claim.acquired:
            status = self.status(build_version)
            if status is None:
                raise PagesDeploymentError("Pages create outcome remains unresolved")
            self._wait_for_status(build_version, timeout_seconds=timeout_seconds, first_status=status)
            return output_url
        claimed = claim.candidate
        if claimed.submission is None:
            raise PagesDeploymentError("Pages submission claim could not be confirmed")
        try:
            deployment_id = self.create(artifact_id, build_version, oidc_token)
        except PagesCreateRejected as exc:
            publication.mark_submission_rejected(
                claimed, claimed.submission.submission_id
            )
            raise PagesDeploymentError(str(exc)) from exc
        except PagesCreateOutcomeUnknown:
            self._wait_for_appearance(build_version)
            return output_url
        self._wait_for_status(deployment_id, timeout_seconds=timeout_seconds)
        return output_url


def _write_page_url(site_url: str) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    line = f"page_url={site_url}\n"
    if output:
        with Path(output).open("a", encoding="utf-8") as file:
            file.write(line)
    else:
        sys.stdout.write(line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pipeline.pages_deployment")
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--artifact-id", type=int, required=True)
    parser.add_argument("--build-version", required=True)
    parser.add_argument("--site-url", required=True)
    args = parser.parse_args(argv)
    checkpoint = pages = None
    try:
        repository = os.environ["GITHUB_REPOSITORY"]
        state_repository = os.environ.get("DATA_CLOUD_STATE_REPO", repository)
        state_branch = os.environ["DATA_CLOUD_STATE_BRANCH"]
        state_token = os.environ["DATA_CLOUD_STATE_TOKEN"]
        api_token = os.environ["GITHUB_TOKEN"]
        owner, state_name = _repository_parts(state_repository)
        checkpoint = GitHubCheckpoint(owner, state_name, state_branch, state_token)
        publication = PublicationCheckpoint(checkpoint)
        pages = GitHubPagesDeploymentApi(
            repository,
            api_token,
            api_url=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
        )
        signal.signal(signal.SIGINT, pages.request_cancel)
        signal.signal(signal.SIGTERM, pages.request_cancel)
        site_url = pages.deploy_reserved(
            publication,
            run_id=args.run_id,
            artifact_id=args.artifact_id,
            build_version=args.build_version,
            site_url=args.site_url,
        )
        _write_page_url(site_url)
        return 0
    except (KeyError, OSError, ValueError, CheckpointError, PagesDeploymentError):
        print("GitHub Pages deployment could not be confirmed", file=sys.stderr)
        return 1
    finally:
        if pages is not None:
            pages.close()
        if checkpoint is not None:
            checkpoint.close()


if __name__ == "__main__":
    raise SystemExit(main())
