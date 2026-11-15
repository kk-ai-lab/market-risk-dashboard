import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from pipeline.publication import (
    GenerationSource,
    PublicationError,
    canonical_json,
    publish_reused_generation,
    publish_snapshot,
    read_current,
    verify_generation,
)


@pytest.fixture
def files():
    return {"latest/data.json": {"value": 1}, "history/data.json": [{"date": "2026-11-14", "value": 1}], "metadata/data.json": {"value": 1}}


def publish(root, files, parent=None, **kwargs):
    return publish_snapshot(root, files, parent_id=parent, producer_revision="fixture-revision",
                            created_at="2026-11-14T12:00:00Z", validator=lambda _: True, **kwargs)


def source_generation(root: Path, payloads: dict[str, bytes]) -> Path:
    digests = {path: hashlib.sha256(data).hexdigest() for path, data in payloads.items()}
    identity = {
        "version": 1,
        "parent_id": None,
        "producer_revision": "source-revision",
        "created_at": "2026-11-14T12:00:00Z",
        "files": digests,
    }
    generation_id = hashlib.sha256(canonical_json(identity)).hexdigest()
    manifest = {**identity, "generation_id": generation_id}
    manifest_bytes = canonical_json(manifest)
    directory = root / "generations" / generation_id
    for relative, data in payloads.items():
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    (directory / "manifest.json").write_bytes(manifest_bytes)
    pointer = {
        "version": 1,
        "generation_id": generation_id,
        "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
    }
    (root / "current.json").write_bytes(canonical_json(pointer))
    return directory


def test_two_generations_and_idempotence(tmp_path, files):
    first = publish(tmp_path, files)
    assert publish(tmp_path, files) == first
    files["latest/data.json"] = {"value": 2}
    second = publish(tmp_path, files, first["generation_id"])
    assert read_current(tmp_path) == second
    generations = tmp_path / "generations"
    assert len(list(generations.iterdir())) == 2
    manifest = verify_generation(generations / second["generation_id"])
    assert manifest["parent_id"] == first["generation_id"]
    assert verify_generation(generations / first["generation_id"])


@pytest.mark.parametrize("validator", [lambda _: False, lambda _: None])
def test_failed_validator_preserves_pointer(tmp_path, files, validator):
    first = publish(tmp_path, files)
    before = (tmp_path / "current.json").read_bytes()
    with pytest.raises(PublicationError):
        publish_snapshot(tmp_path, files, parent_id=first["generation_id"],
                         producer_revision="new", created_at="2026-11-14T12:00:00Z", validator=validator)
    assert (tmp_path / "current.json").read_bytes() == before
    assert len(list((tmp_path / "generations").iterdir())) == 1


def test_validator_exception_and_mutation(tmp_path, files):
    first = publish(tmp_path, files)

    def mutate(path):
        (path / "latest/data.json").write_text('{}')
        return True

    with pytest.raises(PublicationError):
        publish_snapshot(tmp_path, files, parent_id=first["generation_id"],
                         producer_revision="new", created_at="2026-11-14T12:00:00Z", validator=mutate)
    assert read_current(tmp_path) == first

    def fail(_):
        raise RuntimeError("fixture failure")

    with pytest.raises(RuntimeError):
        publish_snapshot(tmp_path, files, parent_id=first["generation_id"],
                         producer_revision="new", created_at="2026-11-14T12:00:00Z", validator=fail)
    assert read_current(tmp_path) == first


@pytest.mark.parametrize("bad", ["../bad.json", "/tmp/bad.json", "latest/../bad.json",
                                 "latest//bad.json", "latest/./bad.json", "other/bad.json"])
def test_malicious_candidate_paths(tmp_path, files, bad):
    files[bad] = {}
    with pytest.raises(PublicationError):
        publish(tmp_path, files)
    assert not (tmp_path / "current.json").exists()


@pytest.mark.parametrize("corruption", ["hash", "extra", "symlink", "duplicate", "manifest"])
def test_corruption_rejected(tmp_path, files, corruption):
    first = publish(tmp_path, files)
    generation = tmp_path / "generations" / first["generation_id"]
    if corruption == "hash":
        (generation / "latest/data.json").write_text('{}')
    elif corruption == "extra":
        (generation / "unexpected.json").write_text('{}')
    elif corruption == "symlink":
        (generation / "latest/data.json").unlink()
        (generation / "latest/data.json").symlink_to(generation / "history/data.json")
    elif corruption == "duplicate":
        (generation / "manifest.json").write_text('{"files":{},"files":{}}')
    else:
        manifest = json.loads((generation / "manifest.json").read_text())
        manifest["files"]["../bad.json"] = "0" * 64
        (generation / "manifest.json").write_bytes(canonical_json(manifest))
    with pytest.raises(PublicationError):
        read_current(tmp_path)
    with pytest.raises(PublicationError):
        verify_generation(generation)


def test_stale_parent(tmp_path, files):
    first = publish(tmp_path, files)
    files["latest/data.json"] = {"value": 2}
    with pytest.raises(PublicationError, match="Stale parent"):
        publish(tmp_path, files)
    assert read_current(tmp_path) == first


def test_concurrent_conflict(tmp_path, files):
    first = publish(tmp_path, files)

    def update(value):
        candidate = {**files, "latest/data.json": {"value": value}}
        try:
            return publish(tmp_path, candidate, first["generation_id"])
        except PublicationError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(update, [2, 3]))
    assert sum(isinstance(result, dict) for result in results) == 1
    winner = next(result for result in results if isinstance(result, dict))
    assert read_current(tmp_path) == winner


def test_atomic_pointer_failure_preserves_old(tmp_path, files, monkeypatch):
    import pipeline.publication as publication

    first = publish(tmp_path, files)
    replace = publication.os.replace

    def fail_pointer(source, destination):
        if destination == tmp_path / "current.json":
            raise OSError("fixture pointer failure")
        replace(source, destination)

    monkeypatch.setattr(publication.os, "replace", fail_pointer)
    files["latest/data.json"] = {"value": 2}
    with pytest.raises(OSError):
        publish(tmp_path, files, first["generation_id"])
    assert read_current(tmp_path) == first


def test_reused_generation_preserves_source_bytes_and_retries_after_pointer_switch(
    tmp_path, monkeypatch
):
    import pipeline.publication as publication

    payloads = {
        "latest/data.json": b'{ "value" : 1 }\n',
        "history/data.json": b'[ { "date" : "2026-11-14", "value" : 1 } ]\n',
        "metadata/data.json": b'{"source": "fixture"}\n',
    }
    destination = tmp_path / "destination"
    source_dir = source_generation(destination, payloads)
    source_manifest = verify_generation(source_dir)
    source = GenerationSource(
        source_dir,
        hashlib.sha256((source_dir / "manifest.json").read_bytes()).hexdigest(),
    )
    replace = publication.os.replace

    def replace_then_lose_ack(source_path, destination_path):
        replace(source_path, destination_path)
        if destination_path == destination / "current.json":
            raise OSError("caller lost the pointer-switch acknowledgement")

    monkeypatch.setattr(publication.os, "replace", replace_then_lose_ack)
    arguments = {
        "producer_revision": "new-revision",
        "created_at": "2026-11-15T12:00:00Z",
    }
    with pytest.raises(OSError, match="acknowledgement"):
        publish_reused_generation(destination, source, **arguments)

    expected = read_current(destination)
    assert expected["generation_id"] != source_manifest["generation_id"]
    monkeypatch.undo()
    assert publish_reused_generation(destination, source, **arguments) == expected
    copied = destination / "generations" / expected["generation_id"]
    assert verify_generation(copied)["parent_id"] == source_manifest["generation_id"]
    for relative, data in payloads.items():
        assert (copied / relative).read_bytes() == data


def test_reused_generation_rejects_source_bytes_changed_after_validation(tmp_path, monkeypatch):
    import pipeline.publication as publication

    payloads = {
        "latest/data.json": b'{ "value" : 1 }\n',
        "history/data.json": b'[{"date":"2026-11-14","value":1}]',
        "metadata/data.json": b'{"source":"fixture"}',
    }
    source_dir = source_generation(tmp_path / "source", payloads)
    source = GenerationSource(
        source_dir,
        hashlib.sha256((source_dir / "manifest.json").read_bytes()).hexdigest(),
    )
    source_file = source_dir / "latest/data.json"
    verify = publication.verify_generation
    mutated = False

    def verify_then_mutate(*args, **kwargs):
        nonlocal mutated
        manifest = verify(*args, **kwargs)
        if not mutated:
            source_file.write_bytes(b'{"value":2}')
            mutated = True
        return manifest

    monkeypatch.setattr(publication, "verify_generation", verify_then_mutate)
    with pytest.raises(PublicationError, match="changed during reuse"):
        publish_reused_generation(
            tmp_path / "destination",
            source,
            producer_revision="new-revision",
            created_at="2026-11-15T12:00:00Z",
        )
    assert not (tmp_path / "destination" / "current.json").exists()
