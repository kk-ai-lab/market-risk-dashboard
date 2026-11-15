import json
from dataclasses import replace

import pytest

from pipeline.storage.github_checkpoint import (
    CheckpointConflict,
    CheckpointError,
    CheckpointSnapshot,
    CheckpointUncertain,
)
from pipeline.storage.publication_checkpoint import (
    DeploymentObservation,
    Generation,
    PublicationCheckpoint,
    PublicationState,
    empty_state,
)


class FakeCAS:
    def __init__(self):
        self.content = empty_state()
        self.head = 0
        self.writes = 0
        self.before_write = None
        self.failure = None

    def read(self, path):
        if self.content is None:
            raise CheckpointError("missing checkpoint")
        return CheckpointSnapshot(f"{self.head:040x}", "0" * 40, path, self.content)

    def write(self, snapshot, content, message):
        if self.before_write:
            callback, self.before_write = self.before_write, None
            callback()
        if snapshot.head_sha != f"{self.head:040x}":
            raise CheckpointConflict("expected head mismatch")
        failure, self.failure = self.failure, None
        if failure == "before":
            raise CheckpointUncertain("request lost")
        if failure == "rejected":
            raise CheckpointError("rejected")
        self.content = content
        self.head += 1
        self.writes += 1
        if failure == "after":
            raise CheckpointUncertain("response lost or malformed")
        return f"{self.head:040x}"


def generation(number=1, parent=None, *, attempt=1, nonce=1):
    return Generation(f"{number:064x}", f"{number + 100:064x}", "a" * 40, parent,
                      attempt, f"00000000-0000-4000-8000-{nonce:012x}",
                      1000 + number, 2000 + number, "sha256:" + f"{number + 300:064x}")


def observation(value=None):
    return DeploymentObservation(value)


@pytest.fixture
def control():
    cas = FakeCAS()
    return cas, PublicationCheckpoint(cas)


def test_prepare_and_observed_deployment_replay(control):
    cas, checkpoint = control
    first = generation()
    checkpoint.prepare(first)
    assert checkpoint.read().candidate == first
    assert checkpoint.read().deployed is None
    checkpoint.prepare(first)
    assert cas.writes == 1
    for unknown in (None, first, observation(), observation(generation(nonce=2))):
        with pytest.raises(CheckpointError):
            checkpoint.mark_deployed(first, observed=unknown)
    checkpoint.mark_deployed(first, observed=observation(first))
    checkpoint.mark_deployed(first, observed=observation(first))
    checkpoint.prepare(first)
    assert checkpoint.read().deployed == first
    assert checkpoint.read().candidate is None
    assert cas.writes == 2


@pytest.mark.parametrize("operation", ["prepare", "deployed", "abandon"])
@pytest.mark.parametrize("failure", ["before", "after", "rejected"])
def test_failures_propagate_and_exact_replay_reconciles(control, operation, failure):
    cas, checkpoint = control
    first = generation()
    if operation != "prepare":
        checkpoint.prepare(first)
    actions = {
        "prepare": lambda: checkpoint.prepare(first),
        "deployed": lambda: checkpoint.mark_deployed(first, observed=observation(first)),
        "abandon": lambda: checkpoint.abandon(first, observed_deployed=observation(),
                                               deployment_terminated=True),
    }
    cas.failure = failure
    with pytest.raises(CheckpointError):
        actions[operation]()
    actual = PublicationCheckpoint(cas).read()
    if failure != "after":
        assert actual.deployed is None
        assert actual.candidate == (first if operation != "prepare" else None)
    actions[operation]()
    state = checkpoint.read()
    assert getattr(state, {"prepare": "candidate", "deployed": "deployed",
                           "abandon": "abandoned"}[operation]) == first
    assert cas.writes == (1 if operation == "prepare" else 2)


def test_competing_same_generation_attempts_never_mistake_winner(control):
    cas, checkpoint = control
    winner, loser = generation(nonce=1), generation(nonce=2)
    cas.before_write = lambda: PublicationCheckpoint(cas).prepare(winner)
    with pytest.raises(CheckpointConflict):
        checkpoint.prepare(loser)
    with pytest.raises(CheckpointError):
        checkpoint.prepare(loser)
    with pytest.raises(CheckpointError):
        checkpoint.mark_deployed(loser, observed=observation(loser))
    assert checkpoint.read().candidate == winner
    assert cas.writes == 1


def test_abandon_aba_old_calls_cannot_affect_new_attempt(control):
    cas, checkpoint = control
    old = generation()
    new = replace(generation(attempt=2, nonce=2), artifact_id=1002)
    checkpoint.prepare(old)
    checkpoint.abandon(old, observed_deployed=observation(), deployment_terminated=True)
    checkpoint.abandon(old, observed_deployed=observation(), deployment_terminated=True)
    with pytest.raises(CheckpointError):
        checkpoint.prepare(old)
    checkpoint.prepare(new)
    for action in (
        lambda: checkpoint.prepare(old),
        lambda: checkpoint.mark_deployed(old, observed=observation(old)),
        lambda: checkpoint.mark_deployed(new, observed=observation(old)),
        lambda: checkpoint.abandon(old, observed_deployed=observation(), deployment_terminated=True),
    ):
        with pytest.raises(CheckpointError):
            action()
    assert checkpoint.read().candidate == new


@pytest.mark.parametrize("operation", ["abandon", "deployed"])
def test_delayed_old_cas_loses_after_same_generation_is_reserved_again(control, operation):
    cas, checkpoint = control
    old, new = generation(), replace(generation(attempt=2, nonce=2), artifact_id=1002)
    checkpoint.prepare(old)

    def advance():
        other = PublicationCheckpoint(cas)
        other.abandon(old, observed_deployed=observation(), deployment_terminated=True)
        other.prepare(new)

    cas.before_write = advance
    with pytest.raises(CheckpointConflict):
        if operation == "abandon":
            checkpoint.abandon(old, observed_deployed=observation(), deployment_terminated=True)
        else:
            checkpoint.mark_deployed(old, observed=observation(old))
    assert checkpoint.read().candidate == new


def test_abandon_requires_explicit_observation_termination_and_tombstone(control):
    cas, checkpoint = control
    first = generation()
    with pytest.raises(CheckpointError):
        checkpoint.abandon(first, observed_deployed=observation(), deployment_terminated=True)
    checkpoint.prepare(first)
    for terminated in (None, False, 1):
        with pytest.raises(CheckpointError):
            checkpoint.abandon(first, observed_deployed=observation(),
                               deployment_terminated=terminated)
    for unknown in (None, first, observation(generation(nonce=2))):
        with pytest.raises(CheckpointError):
            checkpoint.abandon(first, observed_deployed=unknown, deployment_terminated=True)
    assert checkpoint.read().candidate == first


def test_old_and_new_generations_cannot_regress_state(control):
    cas, checkpoint = control
    first = generation()
    second = generation(2, first.generation_id, attempt=2, nonce=2)
    checkpoint.prepare(first)
    checkpoint.mark_deployed(first, observed=observation(first))
    checkpoint.prepare(second)
    with pytest.raises(CheckpointError):
        checkpoint.prepare(first)
    assert checkpoint.mark_deployed(first, observed=observation(first)).candidate == second
    checkpoint.mark_deployed(second, observed=observation(second))
    for action in (
        lambda: checkpoint.prepare(first),
        lambda: checkpoint.mark_deployed(first, observed=observation(first)),
        lambda: checkpoint.prepare(generation(3, attempt=3, nonce=3)),
    ):
        with pytest.raises(CheckpointError):
            action()
    assert checkpoint.read().deployed == second


def test_abandon_after_prior_deployment_requires_same_observed_attempt(control):
    cas, checkpoint = control
    first = generation()
    checkpoint.prepare(first)
    checkpoint.mark_deployed(first, observed=observation(first))
    second = generation(2, first.generation_id, attempt=2, nonce=2)
    checkpoint.prepare(second)
    for wrong in (observation(), observation(replace(first, attempt_id=second.attempt_id))):
        with pytest.raises(CheckpointError):
            checkpoint.abandon(second, observed_deployed=wrong, deployment_terminated=True)
    checkpoint.abandon(second, observed_deployed=observation(first), deployment_terminated=True)
    assert checkpoint.read().deployed == first
    assert checkpoint.read().abandoned == second


@pytest.mark.parametrize("content", [
    None, b"", b"null", b"[]", b"{", b'\xff',
    b'{"version":1,"version":1}',
    b'{"version":true,"candidate":null,"deployed":null}',
    b'{"version":2,"candidate":null,"deployed":null}',
    b'{"version":1,"candidate":null}',
])
def test_missing_or_corrupt_state_never_writes(control, content):
    cas, checkpoint = control
    cas.content = content
    for action in (checkpoint.read, lambda: checkpoint.prepare(generation()),
                   lambda: checkpoint.mark_deployed(generation(), observed=observation(generation())),
                   lambda: checkpoint.abandon(generation(), observed_deployed=observation(),
                                               deployment_terminated=True)):
        with pytest.raises(CheckpointError):
            action()
    assert cas.writes == 0


@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", 2), ("last_attempt", True),
    ("last_attempt", -1), ("last_attempt", 1), ("extra", {}),
    ("candidate", {}), ("abandoned", {}), ("deployed", {}),
])
def test_invalid_state_fields_fail_closed(control, field, value):
    cas, checkpoint = control
    state = json.loads(empty_state())
    state[field] = value
    cas.content = json.dumps(state).encode()
    with pytest.raises(CheckpointError):
        checkpoint.prepare(generation())
    assert cas.writes == 0


@pytest.mark.parametrize("field,value", [
    ("generation_id", "bad"), ("manifest_sha256", None),
    ("producer_revision", "main"), ("parent_id", 42),
    ("parent_id", f"{1:064x}"), ("attempt_id", "not-a-uuid"),
    ("attempt_number", True), ("attempt_number", 0),
])
def test_invalid_generation_identity(field, value):
    with pytest.raises(CheckpointError):
        replace(generation(), **{field: value})


def test_uncertain_competitor_response_does_not_authorize_loser(control):
    cas, checkpoint = control
    winner, loser = generation(nonce=1), generation(nonce=2)

    def competing_write():
        cas.failure = "after"
        PublicationCheckpoint(cas).prepare(winner)

    cas.before_write = competing_write
    with pytest.raises(CheckpointUncertain):
        checkpoint.prepare(loser)
    assert PublicationCheckpoint(cas).read().candidate == winner
    with pytest.raises(CheckpointError):
        checkpoint.prepare(loser)
    assert cas.writes == 1


@pytest.mark.parametrize("field,value", [
    ("artifact_id", None), ("artifact_id", True), ("artifact_id", 0),
    ("artifact_id", -1), ("artifact_id", "1001"), ("artifact_id", 1.0),
    ("workflow_run_id", None), ("workflow_run_id", True), ("workflow_run_id", 0),
    ("workflow_run_id", -1), ("workflow_run_id", "2001"), ("workflow_run_id", 1.0),
    ("artifact_digest", None), ("artifact_digest", "a" * 64),
    ("artifact_digest", "sha256:" + "a" * 63),
    ("artifact_digest", "sha256:" + "a" * 65),
    ("artifact_digest", "SHA256:" + "a" * 64),
    ("artifact_digest", "sha256:" + "A" * 64),
    ("artifact_digest", "sha512:" + "a" * 64),
    ("artifact_digest", "sha256:" + "g" * 64),
])
def test_artifact_identity_requires_strict_github_types(field, value):
    with pytest.raises(CheckpointError):
        replace(generation(), **{field: value})


@pytest.mark.parametrize("field", ["artifact_id", "workflow_run_id", "artifact_digest"])
def test_missing_artifact_identity_fails_closed_on_state_read(control, field):
    cas, checkpoint = control
    checkpoint.prepare(generation())
    state = json.loads(cas.content)
    del state["candidate"][field]
    cas.content = json.dumps(state).encode()
    for action in (checkpoint.read, lambda: checkpoint.prepare(generation())):
        with pytest.raises(CheckpointError):
            action()
    assert cas.writes == 1


@pytest.mark.parametrize("change", [
    {"artifact_id": 9999}, {"workflow_run_id": 9999},
    {"artifact_digest": "sha256:" + "f" * 64},
    {"artifact_id": 2001, "workflow_run_id": 1001},
])
def test_same_generation_with_different_artifact_is_not_a_replay(control, change):
    cas, checkpoint = control
    original = generation()
    different = replace(original, **change)
    checkpoint.prepare(original)
    checkpoint.prepare(original)
    assert checkpoint.read().candidate == original
    for action in (
        lambda: checkpoint.prepare(different),
        lambda: checkpoint.mark_deployed(different, observed=observation(different)),
        lambda: checkpoint.mark_deployed(original, observed=observation(different)),
        lambda: checkpoint.abandon(different, observed_deployed=observation(),
                                   deployment_terminated=True),
    ):
        with pytest.raises(CheckpointError):
            action()
    assert cas.writes == 1
    checkpoint.mark_deployed(original, observed=observation(original))
    for action in (
        lambda: checkpoint.prepare(different),
        lambda: checkpoint.mark_deployed(different, observed=observation(different)),
    ):
        with pytest.raises(CheckpointError):
            action()
    assert cas.writes == 2


def test_artifact_competition_and_uncertain_replay_bind_exact_reference(control):
    cas, checkpoint = control
    winner = generation()
    loser = replace(winner, artifact_id=9999, artifact_digest="sha256:" + "f" * 64)
    cas.before_write = lambda: PublicationCheckpoint(cas).prepare(winner)
    with pytest.raises(CheckpointConflict):
        checkpoint.prepare(loser)
    with pytest.raises(CheckpointError):
        checkpoint.prepare(loser)
    cas.failure = "after"
    with pytest.raises(CheckpointUncertain):
        checkpoint.mark_deployed(winner, observed=observation(winner))
    recovered = PublicationCheckpoint(cas)
    assert recovered.read().deployed == winner
    recovered.mark_deployed(winner, observed=observation(winner))
    with pytest.raises(CheckpointError):
        recovered.mark_deployed(loser, observed=observation(loser))
    assert cas.writes == 2


def test_abandon_tombstone_and_observation_bind_artifact_reference(control):
    cas, checkpoint = control
    original = generation()
    other = replace(original, workflow_run_id=9999)
    checkpoint.prepare(original)
    checkpoint.abandon(original, observed_deployed=observation(), deployment_terminated=True)
    assert checkpoint.read().abandoned == original
    with pytest.raises(CheckpointError):
        checkpoint.abandon(other, observed_deployed=observation(), deployment_terminated=True)
    new = replace(generation(attempt=2, nonce=2), artifact_id=1002)
    checkpoint.prepare(new)
    checkpoint.mark_deployed(new, observed=observation(new))
    pending = replace(generation(2, new.generation_id, attempt=3, nonce=3), artifact_id=1003)
    checkpoint.prepare(pending)
    with pytest.raises(CheckpointError):
        checkpoint.abandon(pending, observed_deployed=observation(replace(new, artifact_id=9999)),
                           deployment_terminated=True)
    assert checkpoint.read().candidate == pending


@pytest.mark.parametrize("retained_state", ["deployed", "abandoned"])
def test_reusing_retained_artifact_for_new_generation_is_rejected(control, retained_state):
    cas, checkpoint = control
    first = generation()
    checkpoint.prepare(first)
    if retained_state == "deployed":
        checkpoint.mark_deployed(first, observed=observation(first))
        parent = first.generation_id
    else:
        checkpoint.abandon(first, observed_deployed=observation(), deployment_terminated=True)
        parent = None
    second = replace(generation(2, parent, attempt=2, nonce=2), artifact_id=first.artifact_id,
                     workflow_run_id=first.workflow_run_id, artifact_digest=first.artifact_digest)
    with pytest.raises(CheckpointError):
        checkpoint.prepare(second)
    assert cas.writes == 2
    assert checkpoint.read().candidate is None


def test_corrupt_candidate_reusing_deployed_artifact_fails_closed(control):
    cas, checkpoint = control
    first = generation()
    checkpoint.prepare(first)
    checkpoint.mark_deployed(first, observed=observation(first))
    second = generation(2, first.generation_id, attempt=2, nonce=2)
    checkpoint.prepare(second)
    state = json.loads(cas.content)
    state["candidate"]["artifact_id"] = first.artifact_id
    cas.content = json.dumps(state).encode()
    with pytest.raises(CheckpointError):
        checkpoint.read()
    assert cas.writes == 3
