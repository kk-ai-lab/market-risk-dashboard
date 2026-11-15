import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from pipeline.publication import (
    PublicationError,
    canonical_json,
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
