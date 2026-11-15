"""Versioned publication control state, without market data or deployment actions.

Pages deployment and this checkpoint are not atomic. A caller must independently
observe the exact deployed generation before marking it deployed. This module
only validates state transitions; it does not obtain or authenticate observations
or enforce single-flight Pages deployment. External deployment races remain.
Provision the checkpoint explicitly with ``empty_state()`` before use. Missing or
corrupt state is never recreated. After an uncertain write, read and replay the
same operation to reconcile; never infer deployment from a prepared candidate.
Artifact references are control metadata, not source data. Callers must verify
artifact availability (including expiry/deletion), archive digest, manifest,
workflow/repository/code provenance and deployment observations externally.
Recording an artifact identity does not establish its trust or durability.
Before enabling publication, external validation must verify the artifact digest
and archive content against the manifest/generation and repository/run/code
provenance, plus the deployment observation. End-to-end verification is still
required. Checks against retained identities cannot prove uniqueness across all
historical artifacts or prove archive-to-manifest-to-generation provenance.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from uuid import UUID

from pipeline.storage.github_checkpoint import (
    CheckpointError,
    CheckpointSnapshot,
    GitHubCheckpoint,
)


@dataclass(frozen=True)
class Generation:
    """Generation metadata bound to one reservation (counter plus random UUID)."""

    generation_id: str
    manifest_sha256: str
    producer_revision: str
    parent_id: str | None
    attempt_number: int
    attempt_id: str
    artifact_id: int
    workflow_run_id: int
    artifact_digest: str

    def __post_init__(self) -> None:
        for identifier in (self.artifact_id, self.workflow_run_id):
            if type(identifier) is not int or identifier < 1:
                raise CheckpointError("Artifact and workflow run IDs must be positive integers")
        if (not isinstance(self.artifact_digest, str)
                or re.fullmatch(r"sha256:[0-9a-f]{64}", self.artifact_digest) is None):
            raise CheckpointError("Artifact digest must use GitHub's sha256:hex format")
        if type(self.attempt_number) is not int or self.attempt_number < 1:
            raise CheckpointError("Invalid attempt number")
        try:
            attempt = UUID(self.attempt_id)
            if attempt.version != 4 or str(attempt) != self.attempt_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError) as exc:
            raise CheckpointError("Attempt ID must be a canonical random UUID4") from exc
        for digest in (self.generation_id, self.manifest_sha256):
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise CheckpointError("Invalid generation digest")
        if (not isinstance(self.producer_revision, str)
                or re.fullmatch(r"[0-9a-f]{40}", self.producer_revision) is None):
            raise CheckpointError("Producer revision must be a full Git commit SHA")
        if (self.parent_id is not None
                and (not isinstance(self.parent_id, str)
                     or re.fullmatch(r"[0-9a-f]{64}", self.parent_id) is None)):
            raise CheckpointError("Invalid generation parent")
        if self.parent_id == self.generation_id:
            raise CheckpointError("Generation cannot parent itself")


@dataclass(frozen=True)
class DeploymentObservation:
    """Explicit confirmed observation; None means confirmed empty, never unknown.

    A populated identity includes the attempt and exact artifact reference, which
    must be independently verified against the deployment. Reusing a generation's
    manifest alone cannot prove its attempt or the artifact that supplied it.
    """

    deployed: Generation | None

    def __post_init__(self) -> None:
        if self.deployed is not None and not isinstance(self.deployed, Generation):
            raise CheckpointError("Invalid deployment observation")


@dataclass(frozen=True)
class PublicationState:
    deployed: Generation | None = None
    candidate: Generation | None = None
    abandoned: Generation | None = None
    last_attempt: int = 0

    def __post_init__(self) -> None:
        if any(value is not None and not isinstance(value, Generation)
               for value in (self.deployed, self.candidate, self.abandoned)):
            raise CheckpointError("Invalid publication generation")
        attempts = [item.attempt_number for item in (self.deployed, self.candidate, self.abandoned)
                    if item is not None]
        if (type(self.last_attempt) is not int or self.last_attempt < 0
                or self.last_attempt != max(attempts, default=0)
                or len(set(attempts)) != len(attempts)
                or (self.candidate is not None and self.abandoned is not None)):
            raise CheckpointError("Invalid attempt sequence")
        pending = self.candidate or self.abandoned
        if pending is not None:
            if self.deployed is not None and pending.artifact_id == self.deployed.artifact_id:
                raise CheckpointError("Attempt reuses deployed artifact identity")
            if pending.attempt_number != self.last_attempt:
                raise CheckpointError("Pending attempt is not the latest")
            if pending.parent_id != (self.deployed.generation_id if self.deployed else None):
                raise CheckpointError("Attempt does not extend deployed generation")
        if self.candidate is not None:
            if self.candidate.parent_id != (self.deployed.generation_id if self.deployed else None):
                raise CheckpointError("Candidate does not extend deployed generation")
            if self.deployed and self.candidate.generation_id == self.deployed.generation_id:
                raise CheckpointError("Candidate repeats deployed generation")

    def encode(self) -> bytes:
        return json.dumps({"version": 1, **asdict(self)}, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode()


def empty_state() -> bytes:
    """Return explicit provisioning content; this function performs no write."""
    return PublicationState().encode()


def _object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise CheckpointError("Duplicate publication state key")
        result[key] = value
    return result


def _decode(content: bytes) -> PublicationState:
    try:
        value = json.loads(content, object_pairs_hook=_object)
        if (not isinstance(value, dict) or set(value) != {"version", "candidate", "deployed", "abandoned", "last_attempt"}
                or type(value["version"]) is not int or value["version"] != 1):
            raise CheckpointError("Unsupported publication state")
        generations = {}
        for key in ("candidate", "deployed", "abandoned"):
            item = value[key]
            if item is not None:
                if not isinstance(item, dict) or set(item) != set(Generation.__dataclass_fields__):
                    raise CheckpointError("Invalid publication generation fields")
                item = Generation(**item)
            generations[key] = item
        return PublicationState(**generations, last_attempt=value["last_attempt"])
    except (ValueError, TypeError, UnicodeError) as exc:
        raise CheckpointError("Invalid publication state") from exc


class PublicationCheckpoint:
    def __init__(self, checkpoint: GitHubCheckpoint, path: str = "publication.json"):
        self._checkpoint = checkpoint
        self._path = path

    def _read(self) -> tuple[CheckpointSnapshot, PublicationState]:
        snapshot = self._checkpoint.read(self._path)
        return snapshot, _decode(snapshot.content)

    def read(self) -> PublicationState:
        return self._read()[1]

    def prepare(self, generation: Generation) -> PublicationState:
        """Reserve one candidate using expected-head CAS; never replace a pending one.

        A caller may proceed to deployment only when the returned candidate
        exactly matches its generation. An already deployed replay is no permit.
        External coordination must prevent delayed or concurrent deployments.
        Use a fresh UUID4 and last_attempt + 1 for each new deployment attempt.
        Replays must reuse both values, including after an uncertain CAS result.
        """
        if not isinstance(generation, Generation):
            raise CheckpointError("Expected a generation identity")
        snapshot, state = self._read()
        if generation == state.candidate:
            return state
        if state.candidate is not None:
            raise CheckpointError("Another candidate is pending")
        if generation == state.deployed:
            return state
        if generation.parent_id != (state.deployed.generation_id if state.deployed else None):
            raise CheckpointError("Stale publication parent")
        if state.deployed and generation.generation_id == state.deployed.generation_id:
            raise CheckpointError("Generation identity changed")
        for retained in (state.deployed, state.abandoned):
            if retained is not None and generation.artifact_id == retained.artifact_id:
                raise CheckpointError("Artifact ID is already bound to another reservation")
        if generation.attempt_number != state.last_attempt + 1:
            raise CheckpointError("Attempt must advance the checkpoint sequence")
        updated = PublicationState(deployed=state.deployed, candidate=generation,
                                   last_attempt=generation.attempt_number)
        self._checkpoint.write(snapshot, updated.encode(), "Prepare publication candidate")
        return updated

    def mark_deployed(self, generation: Generation, *, observed: DeploymentObservation) -> PublicationState:
        """Record caller-supplied observation only for the exact reserved generation.

        The observation must describe what Pages actually serves, including its
        manifest digest and bound artifact/attempt provenance. A successful upload
        or workflow alone is not observation.
        """
        if (not isinstance(generation, Generation)
                or not isinstance(observed, DeploymentObservation)
                or observed.deployed != generation):
            raise CheckpointError("Deployment observation does not match generation")
        snapshot, state = self._read()
        if state.deployed == generation:
            return state
        if state.candidate != generation:
            raise CheckpointError("Observed generation is not the pending candidate")
        updated = PublicationState(deployed=generation, last_attempt=state.last_attempt)
        self._checkpoint.write(snapshot, updated.encode(), "Record observed publication deployment")
        return updated

    def abandon(self, generation: Generation, *, observed_deployed: DeploymentObservation,
                deployment_terminated: bool = False) -> PublicationState:
        """Release a failed candidate only after external deployment reconciliation.

        The caller must prove that this candidate's deployment has terminated and
        cannot arrive later, then freshly observe the exact currently deployed
        identity (or positively establish no deployment for an empty baseline).
        Unknown observation must not be represented as DeploymentObservation(None).
        This module cannot verify these facts. If either is unknown, do not call
        this transition. A cancelled workflow alone need not prove termination.
        """
        if not isinstance(generation, Generation) or deployment_terminated is not True:
            raise CheckpointError("Deployment termination must be established externally")
        snapshot, state = self._read()
        if (not isinstance(observed_deployed, DeploymentObservation)
                or observed_deployed.deployed != state.deployed):
            raise CheckpointError("Observed deployment differs from checkpoint")
        if generation.parent_id != (state.deployed.generation_id if state.deployed else None):
            raise CheckpointError("Stale abandonment parent")
        if state.candidate is None:
            if state.abandoned == generation:
                return state
            raise CheckpointError("No tombstone proves this attempt was abandoned")
        if state.candidate != generation:
            raise CheckpointError("Cannot abandon another candidate")
        updated = PublicationState(deployed=state.deployed, abandoned=generation,
                                   last_attempt=state.last_attempt)
        self._checkpoint.write(snapshot, updated.encode(), "Abandon terminated publication candidate")
        return updated
