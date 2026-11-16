from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal
from urllib.parse import quote
from uuid import uuid4

import httpx

from pipeline.cloud_refresh import CloudRefreshError, PagesGenerationRestorer
from pipeline.publication import PublicationError, verify_generation
from pipeline.settings import Settings
from pipeline.storage.github_checkpoint import (
    CheckpointError,
    GitHubCheckpoint,
)
from pipeline.storage.publication_checkpoint import (
    DeploymentObservation,
    Generation,
    PublicationCheckpoint,
    PublicationState,
)

WORKFLOW_PATH = ".github/workflows/deploy-pages.yml"
CloudMode = Literal["scheduled", "code-only"]


class CloudPublicationError(ValueError):
    """A Pages attempt cannot be safely prepared or reconciled."""


class PreflightFailureCode(StrEnum):
    ACTIVE_PAGES_RUN = "active_pages_run"
    ACTIONS_RUN_LIST_READ_FAILED = "actions_run_list_read_failed"
    ACTIONS_RUN_LIST_INVALID = "actions_run_list_invalid"
    BRANCH_HEAD_READ_FAILED = "branch_head_read_failed"
    BRANCH_HEAD_INVALID = "branch_head_invalid"
    EVENT_REVISION_STALE = "event_revision_stale"
    PUBLICATION_CHECKPOINT_READ_FAILED = "publication_checkpoint_read_failed"
    PUBLICATION_CHECKPOINT_TRANSITION_UNCONFIRMED = "publication_checkpoint_transition_unconfirmed"
    PUBLICATION_CHECKPOINT_OPERATION_FAILED = "publication_checkpoint_operation_failed"
    PAGES_POINTER_READ_FAILED = "pages_pointer_read_failed"
    LEGACY_DATA_PROBE_FAILED = "legacy_data_probe_failed"
    UNCHECKPOINTED_PAGES_GENERATION = "uncheckpointed_pages_generation"
    LEGACY_BOOTSTRAP_REQUIRED = "legacy_bootstrap_required"
    WORKFLOW_RUN_READ_FAILED = "workflow_run_read_failed"
    WORKFLOW_RUN_INVALID = "workflow_run_invalid"
    CANDIDATE_ARTIFACT_READ_FAILED = "candidate_artifact_read_failed"
    CANDIDATE_ARTIFACT_INVALID = "candidate_artifact_invalid"
    CANDIDATE_REVISION_MISMATCH = "candidate_revision_mismatch"
    PAGES_GENERATION_UNKNOWN = "pages_generation_unknown"
    PRIOR_RUN_OUTCOME_NOT_ABANDONABLE = "prior_run_outcome_not_abandonable"
    CANDIDATE_GENERATION_RESTORE_FAILED = "candidate_generation_restore_failed"
    DEPLOYED_GENERATION_RESTORE_FAILED = "deployed_generation_restore_failed"
    PREFLIGHT_CONFIGURATION_INVALID = "preflight_configuration_invalid"
    PREFLIGHT_INPUT_INVALID = "preflight_input_invalid"
    PREFLIGHT_IO_FAILED = "preflight_io_failed"
    PREFLIGHT_TRANSPORT_FAILED = "preflight_transport_failed"
    PAGES_OPERATION_FAILED = "pages_operation_failed"
    GENERATION_VERIFICATION_FAILED = "generation_verification_failed"
    PREFLIGHT_FAILURE_UNCLASSIFIED = "preflight_failure_unclassified"


class CloudPreflightError(CloudPublicationError):
    def __init__(self, message: str, *, code: PreflightFailureCode):
        super().__init__(message)
        self.code = code


class GitHubActionsApi:
    def __init__(
        self, repository: str, token: str, *, transport: httpx.BaseTransport | None = None
    ):
        parts = repository.split("/")
        if len(parts) != 2 or any(re.fullmatch(r"[A-Za-z0-9_.-]+", part) is None for part in parts):
            raise ValueError("Invalid GitHub repository")
        self.repository = repository
        self._root = "https://api.github.com/repos/" + quote(repository, safe="/")
        self._client = httpx.Client(
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

    def close(self) -> None:
        self._client.close()

    def _get(
        self,
        path: str,
        *,
        params: dict | None = None,
        read_failure_code: PreflightFailureCode,
        invalid_response_code: PreflightFailureCode,
    ) -> dict:
        try:
            response = self._client.get(self._root + path, params=params)
        except httpx.HTTPError as exc:
            raise CloudPreflightError(
                "GitHub Actions state could not be read", code=read_failure_code
            ) from exc
        if response.status_code != 200:
            raise CloudPreflightError(
                "GitHub Actions state could not be read", code=read_failure_code
            )
        try:
            value = response.json()
        except ValueError as exc:
            raise CloudPreflightError(
                "GitHub returned invalid Actions metadata", code=invalid_response_code
            ) from exc
        if not isinstance(value, dict):
            raise CloudPreflightError(
                "GitHub returned invalid Actions metadata", code=invalid_response_code
            )
        return value

    def artifact(self, artifact_id: int) -> dict:
        if type(artifact_id) is not int or artifact_id < 1:
            raise CloudPreflightError(
                "Invalid Pages artifact ID", code=PreflightFailureCode.CANDIDATE_ARTIFACT_INVALID
            )
        return self._get(
            f"/actions/artifacts/{artifact_id}",
            read_failure_code=PreflightFailureCode.CANDIDATE_ARTIFACT_READ_FAILED,
            invalid_response_code=PreflightFailureCode.CANDIDATE_ARTIFACT_INVALID,
        )

    def workflow_run(self, run_id: int) -> dict:
        if type(run_id) is not int or run_id < 1:
            raise CloudPreflightError(
                "Invalid workflow run ID", code=PreflightFailureCode.WORKFLOW_RUN_INVALID
            )
        return self._get(
            f"/actions/runs/{run_id}",
            read_failure_code=PreflightFailureCode.WORKFLOW_RUN_READ_FAILED,
            invalid_response_code=PreflightFailureCode.WORKFLOW_RUN_INVALID,
        )

    def branch_head(self, branch: str) -> str:
        if branch not in {"main", "dev"}:
            raise CloudPreflightError(
                "Unsupported Pages publication branch",
                code=PreflightFailureCode.PREFLIGHT_INPUT_INVALID,
            )
        reference = self._get(
            "/git/ref/heads/" + quote(branch, safe=""),
            read_failure_code=PreflightFailureCode.BRANCH_HEAD_READ_FAILED,
            invalid_response_code=PreflightFailureCode.BRANCH_HEAD_INVALID,
        )
        target = reference.get("object")
        sha = target.get("sha") if isinstance(target, dict) else None
        if (
            reference.get("ref") != f"refs/heads/{branch}"
            or not isinstance(sha, str)
            or re.fullmatch(r"[0-9a-f]{40}", sha) is None
        ):
            raise CloudPreflightError(
                "Current Pages branch revision could not be verified",
                code=PreflightFailureCode.BRANCH_HEAD_INVALID,
            )
        return sha

    def active_workflow_runs(self, *, current_run_id: int) -> list[dict]:
        workflow_id = Path(WORKFLOW_PATH).name
        path = "/actions/workflows/" + quote(workflow_id, safe="") + "/runs"
        value = self._get(
            path,
            params={"status": "in_progress", "per_page": 100},
            read_failure_code=PreflightFailureCode.ACTIONS_RUN_LIST_READ_FAILED,
            invalid_response_code=PreflightFailureCode.ACTIONS_RUN_LIST_INVALID,
        )
        runs = value.get("workflow_runs")
        if (
            not isinstance(runs, list)
            or type(value.get("total_count")) is not int
            or value["total_count"] > len(runs)
        ):
            raise CloudPreflightError(
                "Active Pages runs could not be enumerated",
                code=PreflightFailureCode.ACTIONS_RUN_LIST_INVALID,
            )
        if any(
            not isinstance(run, dict) or type(run.get("id")) is not int or run["id"] < 1
            for run in runs
        ):
            raise CloudPreflightError(
                "Active Pages runs could not be enumerated",
                code=PreflightFailureCode.ACTIONS_RUN_LIST_INVALID,
            )
        return [run for run in runs if run["id"] != current_run_id]


@dataclass(frozen=True)
class PublicationPlan:
    mode: CloudMode
    application_revision: str
    resume: Generation | None = None


class CloudPublisher:
    def __init__(
        self,
        publication: PublicationCheckpoint,
        actions: GitHubActionsApi,
        pages: PagesGenerationRestorer,
        repository: str,
    ):
        self.publication = publication
        self.actions = actions
        self.pages = pages
        self.repository = repository

    def _assert_no_other_pages_run(self, run_id: int) -> None:
        try:
            other_runs = self.actions.active_workflow_runs(current_run_id=run_id)
        except CloudPreflightError:
            raise
        except CloudPublicationError as exc:
            raise CloudPreflightError(
                "Active Pages runs could not be read",
                code=PreflightFailureCode.ACTIONS_RUN_LIST_READ_FAILED,
            ) from exc
        if other_runs:
            raise CloudPreflightError(
                "Another Pages workflow is still running",
                code=PreflightFailureCode.ACTIVE_PAGES_RUN,
            )

    def _validate_run(self, run: dict, run_id: int, *, require_completed: bool) -> None:
        head_repository = run.get("head_repository")
        repository_name = (
            head_repository.get("full_name") if isinstance(head_repository, dict) else None
        )
        if (
            run.get("id") != run_id
            or run.get("path") != WORKFLOW_PATH
            or not isinstance(repository_name, str)
            or repository_name.casefold() != self.repository.casefold()
            or require_completed
            and run.get("status") != "completed"
        ):
            raise CloudPreflightError(
                "Publication run provenance does not match",
                code=PreflightFailureCode.WORKFLOW_RUN_INVALID,
            )

    def _validate_artifact(self, artifact: dict, generation: Generation, event_sha: str) -> None:
        workflow_run = artifact.get("workflow_run")
        head_repository = (
            workflow_run.get("head_repository") if isinstance(workflow_run, dict) else None
        )
        repository_name = (
            head_repository.get("full_name") if isinstance(head_repository, dict) else None
        )
        if (
            artifact.get("id") != generation.artifact_id
            or artifact.get("name") != "github-pages"
            or artifact.get("expired") is not False
            or artifact.get("digest") != generation.artifact_digest
            or not isinstance(workflow_run, dict)
            or workflow_run.get("id") != generation.workflow_run_id
            or workflow_run.get("head_sha") != event_sha
            or not isinstance(repository_name, str)
            or repository_name.casefold() != self.repository.casefold()
        ):
            raise CloudPreflightError(
                "Pages artifact provenance does not match its reservation",
                code=PreflightFailureCode.CANDIDATE_ARTIFACT_INVALID,
            )

    def _read_publication_state_for_preflight(self) -> PublicationState:
        try:
            return self.publication.read()
        except CheckpointError as exc:
            raise CloudPreflightError(
                "Publication checkpoint could not be read",
                code=PreflightFailureCode.PUBLICATION_CHECKPOINT_READ_FAILED,
            ) from exc

    def _read_pages_pointer_for_preflight(self) -> dict | None:
        try:
            return self.pages.read_pointer()
        except CloudRefreshError as exc:
            raise CloudPreflightError(
                "Pages pointer could not be read",
                code=PreflightFailureCode.PAGES_POINTER_READ_FAILED,
            ) from exc

    def _probe_legacy_data_for_preflight(self) -> bool:
        try:
            return self.pages.has_legacy_data()
        except CloudRefreshError as exc:
            raise CloudPreflightError(
                "Legacy Pages data could not be checked",
                code=PreflightFailureCode.LEGACY_DATA_PROBE_FAILED,
            ) from exc

    def _restore_generation_for_preflight(
        self,
        generation: Generation,
        destination: Path,
        *,
        code: PreflightFailureCode,
    ) -> None:
        try:
            self.pages.restore(generation, destination)
        except (CloudRefreshError, PublicationError, OSError, ValueError) as exc:
            raise CloudPreflightError(
                "Pages generation could not be restored", code=code
            ) from exc

    @staticmethod
    def _pointer_for(generation: Generation | None) -> dict | None:
        if generation is None:
            return None
        return {
            "version": 1,
            "generation_id": generation.generation_id,
            "manifest_sha256": generation.manifest_sha256,
        }

    def _transition(self, operation, expected: PublicationState) -> PublicationState:
        try:
            operation()
        except CheckpointError:
            observed = self.publication.read()
            if observed != expected:
                raise
        observed = self.publication.read()
        if observed != expected:
            raise CloudPreflightError(
                "Publication checkpoint transition was not confirmed",
                code=PreflightFailureCode.PUBLICATION_CHECKPOINT_TRANSITION_UNCONFIRMED,
            )
        return observed

    def _reconcile(
        self, state: PublicationState, candidate: Generation, *, current_run_id: int
    ) -> PublicationState:
        previous_run = self.actions.workflow_run(candidate.workflow_run_id)
        self._validate_run(previous_run, candidate.workflow_run_id, require_completed=True)
        self._assert_no_other_pages_run(current_run_id)
        live_pointer = self._read_pages_pointer_for_preflight()
        candidate_pointer = self._pointer_for(candidate)
        deployed_pointer = self._pointer_for(state.deployed)
        if live_pointer == candidate_pointer:
            with tempfile.TemporaryDirectory(prefix="pages-reconcile-") as directory:
                self._restore_generation_for_preflight(
                    candidate,
                    Path(directory) / "site",
                    code=PreflightFailureCode.CANDIDATE_GENERATION_RESTORE_FAILED,
                )
            expected = PublicationState(deployed=candidate, last_attempt=state.last_attempt)
            try:
                return self._transition(
                    lambda: self.publication.mark_deployed(
                        candidate, observed=DeploymentObservation(candidate)
                    ),
                    expected,
                )
            except CheckpointError as exc:
                raise CloudPreflightError(
                    "Publication checkpoint transition was not confirmed",
                    code=PreflightFailureCode.PUBLICATION_CHECKPOINT_TRANSITION_UNCONFIRMED,
                ) from exc
        if live_pointer != deployed_pointer:
            raise CloudPreflightError(
                "Pages serves an unknown generation; publication is paused",
                code=PreflightFailureCode.PAGES_GENERATION_UNKNOWN,
            )
        if state.deployed is not None:
            with tempfile.TemporaryDirectory(prefix="pages-reconcile-") as directory:
                self._restore_generation_for_preflight(
                    state.deployed,
                    Path(directory) / "site",
                    code=PreflightFailureCode.DEPLOYED_GENERATION_RESTORE_FAILED,
                )
        if previous_run.get("conclusion") not in {
            "failure",
            "cancelled",
            "timed_out",
            "startup_failure",
            "action_required",
            "neutral",
            "skipped",
            "stale",
        }:
            raise CloudPreflightError(
                "Pages still serves the previous generation after a successful run",
                code=PreflightFailureCode.PRIOR_RUN_OUTCOME_NOT_ABANDONABLE,
            )
        expected = PublicationState(
            deployed=state.deployed, abandoned=candidate, last_attempt=state.last_attempt
        )
        try:
            return self._transition(
                lambda: self.publication.abandon(
                    candidate,
                    observed_deployed=DeploymentObservation(state.deployed),
                    deployment_terminated=True,
                ),
                expected,
            )
        except CheckpointError as exc:
            raise CloudPreflightError(
                "Publication checkpoint transition was not confirmed",
                code=PreflightFailureCode.PUBLICATION_CHECKPOINT_TRANSITION_UNCONFIRMED,
            ) from exc

    def preflight(
        self, *, mode: CloudMode, event_ref: str, event_sha: str, run_id: int,
        allow_legacy_bootstrap: bool = False,
    ) -> PublicationPlan:
        if (
            mode not in {"scheduled", "code-only"}
            or re.fullmatch(r"[0-9a-f]{40}", event_sha) is None
            or event_ref not in {"refs/heads/main", "refs/heads/dev"}
        ):
            raise CloudPreflightError(
                "Invalid publication mode or event revision",
                code=PreflightFailureCode.PREFLIGHT_INPUT_INVALID,
            )
        self._assert_no_other_pages_run(run_id)
        branch = event_ref.removeprefix("refs/heads/")
        if self.actions.branch_head(branch) != event_sha:
            raise CloudPreflightError(
                "Publication event is no longer the branch head",
                code=PreflightFailureCode.EVENT_REVISION_STALE,
            )
        state = self._read_publication_state_for_preflight()
        if state.deployed is None and state.candidate is None:
            if self._read_pages_pointer_for_preflight() is not None:
                raise CloudPreflightError(
                    "Pages serves an uncheckpointed generation; migrate its checkpoint first",
                    code=PreflightFailureCode.UNCHECKPOINTED_PAGES_GENERATION,
                )
            legacy_data_found = self._probe_legacy_data_for_preflight()
            if not allow_legacy_bootstrap:
                detail = "legacy Pages data was found" if legacy_data_found else "the site appears empty"
                raise CloudPreflightError(
                    f"Initial Pages publication ({detail}) requires explicit bootstrap confirmation",
                    code=PreflightFailureCode.LEGACY_BOOTSTRAP_REQUIRED,
                )
        if state.candidate is not None:
            candidate = state.candidate
            if candidate.workflow_run_id == run_id:
                run = self.actions.workflow_run(run_id)
                self._validate_run(run, run_id, require_completed=False)
                self._validate_artifact(
                    self.actions.artifact(candidate.artifact_id), candidate, event_sha
                )
                expected_revision = (
                    state.deployed.producer_revision
                    if mode == "scheduled" and state.deployed
                    else event_sha
                )
                if candidate.producer_revision != expected_revision:
                    raise CloudPreflightError(
                        "Pending candidate does not match the active application revision",
                        code=PreflightFailureCode.CANDIDATE_REVISION_MISMATCH,
                    )
                return PublicationPlan(mode, candidate.producer_revision, candidate)
            state = self._reconcile(state, candidate, current_run_id=run_id)
        app_revision = (
            state.deployed.producer_revision
            if mode == "scheduled" and state.deployed is not None
            else event_sha
        )
        return PublicationPlan(mode, app_revision)

    def reserve(
        self, *, generation_dir: Path, artifact_id: int, run_id: int, event_sha: str
    ) -> Generation:
        state = self.publication.read()
        if state.candidate is not None:
            raise CloudPublicationError("Another publication candidate is pending")
        manifest = verify_generation(generation_dir)
        artifact = self.actions.artifact(artifact_id)
        digest = artifact.get("digest")
        run = artifact.get("workflow_run")
        head_repository = run.get("head_repository") if isinstance(run, dict) else None
        repository_name = (
            head_repository.get("full_name") if isinstance(head_repository, dict) else None
        )
        if (
            not isinstance(digest, str)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
            or artifact.get("name") != "github-pages"
            or artifact.get("expired") is not False
            or not isinstance(run, dict)
            or run.get("id") != run_id
            or run.get("head_sha") != event_sha
            or not isinstance(repository_name, str)
            or repository_name.casefold() != self.repository.casefold()
        ):
            raise CloudPublicationError("Uploaded Pages artifact could not be verified")
        candidate = Generation(
            generation_id=manifest["generation_id"],
            manifest_sha256=hashlib.sha256(
                (generation_dir / "manifest.json").read_bytes()
            ).hexdigest(),
            producer_revision=manifest["producer_revision"],
            parent_id=manifest["parent_id"],
            attempt_number=state.last_attempt + 1,
            attempt_id=str(uuid4()),
            artifact_id=artifact_id,
            workflow_run_id=run_id,
            artifact_digest=digest,
        )
        expected = PublicationState(
            deployed=state.deployed, candidate=candidate, last_attempt=candidate.attempt_number
        )
        self._transition(lambda: self.publication.prepare(candidate), expected)
        return candidate

    def finish(self, *, current_run_id: int, event_sha: str) -> Generation:
        state = self.publication.read()
        candidate = state.candidate
        if candidate is None or candidate.workflow_run_id != current_run_id:
            if state.deployed is not None and state.deployed.workflow_run_id == current_run_id:
                candidate = state.deployed
            else:
                raise CloudPublicationError("No candidate belongs to this workflow run")
        run = self.actions.workflow_run(current_run_id)
        self._validate_run(run, current_run_id, require_completed=False)
        self._validate_artifact(self.actions.artifact(candidate.artifact_id), candidate, event_sha)
        with tempfile.TemporaryDirectory(prefix="pages-verify-") as directory:
            self.pages.restore(candidate, Path(directory) / "site")
        if state.candidate is not None:
            expected = PublicationState(deployed=candidate, last_attempt=state.last_attempt)
            self._transition(
                lambda: self.publication.mark_deployed(
                    candidate, observed=DeploymentObservation(candidate)
                ),
                expected,
            )
        return candidate


def _runtime() -> tuple[GitHubCheckpoint, GitHubActionsApi, Settings]:
    settings = Settings()
    repository = settings.cloud_state_repo
    token = settings.cloud_state_token.get_secret_value() if settings.cloud_state_token else None
    if not repository or not token:
        raise CloudPreflightError(
            "Cloud checkpoint repository and token are required",
            code=PreflightFailureCode.PREFLIGHT_CONFIGURATION_INVALID,
        )
    checkpoint_parts = repository.split("/")
    if len(checkpoint_parts) != 2:
        raise CloudPreflightError(
            "Cloud checkpoint repository must use owner/repo",
            code=PreflightFailureCode.PREFLIGHT_CONFIGURATION_INVALID,
        )
    checkpoint = GitHubCheckpoint(*checkpoint_parts, settings.cloud_state_branch, token)
    return checkpoint, GitHubActionsApi(repository, token), settings


def _write_outputs(values: dict[str, object]) -> None:
    output = os.environ.get("GITHUB_OUTPUT")
    lines = "".join(f"{key}={value}\n" for key, value in values.items())
    if output:
        with Path(output).open("a", encoding="utf-8") as file:
            file.write(lines)
    else:
        sys.stdout.write(lines)


def _preflight_failure_code(exc: BaseException) -> PreflightFailureCode:
    if isinstance(exc, CloudPreflightError):
        return exc.code
    if isinstance(exc, CheckpointError):
        return PreflightFailureCode.PUBLICATION_CHECKPOINT_OPERATION_FAILED
    if isinstance(exc, CloudRefreshError):
        return PreflightFailureCode.PAGES_OPERATION_FAILED
    if isinstance(exc, PublicationError):
        return PreflightFailureCode.GENERATION_VERIFICATION_FAILED
    if isinstance(exc, CloudPublicationError):
        return PreflightFailureCode.PREFLIGHT_FAILURE_UNCLASSIFIED
    if isinstance(exc, httpx.HTTPError):
        return PreflightFailureCode.PREFLIGHT_TRANSPORT_FAILED
    if isinstance(exc, OSError):
        return PreflightFailureCode.PREFLIGHT_IO_FAILED
    if isinstance(exc, ValueError):
        return PreflightFailureCode.PREFLIGHT_INPUT_INVALID
    return PreflightFailureCode.PREFLIGHT_FAILURE_UNCLASSIFIED


def _emit_preflight_failure(code: PreflightFailureCode) -> None:
    print(
        json.dumps(
            {
                "event": "cloud_publication.failure",
                "stage": "preflight",
                "code": code.value,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        file=sys.stderr,
        flush=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser("preflight")
    preflight.add_argument("--mode", choices=("scheduled", "code-only"), required=True)
    preflight.add_argument("--event-ref", required=True)
    preflight.add_argument("--event-sha", required=True)
    preflight.add_argument("--run-id", type=int, required=True)
    preflight.add_argument("--allow-legacy-bootstrap", action="store_true")
    preflight.add_argument("--site-url", required=True)
    reserve = commands.add_parser("reserve")
    reserve.add_argument("--generation-dir", type=Path, required=True)
    reserve.add_argument("--artifact-id", type=int, required=True)
    reserve.add_argument("--run-id", type=int, required=True)
    reserve.add_argument("--event-sha", required=True)
    finish = commands.add_parser("finish")
    finish.add_argument("--run-id", type=int, required=True)
    finish.add_argument("--event-sha", required=True)
    finish.add_argument("--site-url", required=True)
    args = parser.parse_args(argv)
    checkpoint = actions = None
    try:
        checkpoint, actions, settings = _runtime()
        publication = PublicationCheckpoint(checkpoint)
        site_url = getattr(args, "site_url", None)
        pages = PagesGenerationRestorer(site_url) if site_url else None
        publisher = CloudPublisher(publication, actions, pages, settings.cloud_state_repo)
        if args.command == "preflight":
            plan = publisher.preflight(
                mode=args.mode, event_ref=args.event_ref, event_sha=args.event_sha,
                run_id=args.run_id, allow_legacy_bootstrap=args.allow_legacy_bootstrap,
            )
            values: dict[str, object] = {
                "mode": plan.mode,
                "app_revision": plan.application_revision,
                "resume": str(plan.resume is not None).lower(),
            }
            if plan.resume is not None:
                values.update(
                    {
                        "generation_id": plan.resume.generation_id,
                        "manifest_sha256": plan.resume.manifest_sha256,
                        "producer_revision": plan.resume.producer_revision,
                        "parent_id": plan.resume.parent_id or "",
                        "artifact_id": plan.resume.artifact_id,
                    }
                )
            _write_outputs(values)
        elif args.command == "reserve":
            generation = publisher.reserve(
                generation_dir=args.generation_dir,
                artifact_id=args.artifact_id,
                run_id=args.run_id,
                event_sha=args.event_sha,
            )
            _write_outputs(
                {
                    "generation_id": generation.generation_id,
                    "attempt_id": generation.attempt_id,
                    "artifact_id": generation.artifact_id,
                }
            )
        else:
            generation = publisher.finish(current_run_id=args.run_id, event_sha=args.event_sha)
            _write_outputs(
                {
                    "generation_id": generation.generation_id,
                    "manifest_sha256": generation.manifest_sha256,
                }
            )
        return 0
    except (
        CheckpointError,
        CloudPublicationError,
        CloudRefreshError,
        PublicationError,
        OSError,
        ValueError,
        httpx.HTTPError,
    ) as exc:
        if args.command == "preflight":
            _emit_preflight_failure(_preflight_failure_code(exc))
        print("Cloud Pages publication could not be confirmed", file=sys.stderr)
        return 1
    finally:
        if actions is not None:
            actions.close()
        if checkpoint is not None:
            checkpoint.close()


if __name__ == "__main__":
    raise SystemExit(main())
