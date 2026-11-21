import json
import tarfile

import pytest

from pipeline.publication import PublicationError, publish_snapshot
from scripts.install_cloud_generation import install_generation
from scripts.verify_pages_artifact import verify_pages_artifact

REVISION = "a" * 40
STAMP = "2026-11-15T12:00:00Z"


def _store(tmp_path):
    store = tmp_path / "store"
    files = {
        "latest/data.json": {"generated_at": STAMP, "value": 3},
        "history/data.json": [{"date": "2026-11-14", "value": 2}],
        "metadata/data.json": {"updated_at": STAMP},
    }
    pointer = publish_snapshot(
        store,
        files,
        parent_id=None,
        producer_revision=REVISION,
        created_at=STAMP,
        validator=lambda _: True,
    )
    return store, pointer


def _site(tmp_path, store):
    dist = tmp_path / "dist"
    (dist / "data").parent.mkdir(parents=True, exist_ok=True)
    (dist / "data").mkdir()
    (dist / "index.html").write_text("dashboard", encoding="utf-8")
    install_generation(store, dist / "data")
    return dist


def _archive(tmp_path, dist, *, extra=None):
    path = tmp_path / "artifact.tar"
    with tarfile.open(path, "w") as archive:
        archive.add(dist, arcname=".")
        if extra is not None:
            archive.add(extra, arcname="../escape.json")
    return path


def _verify(archive, pointer, dist, generation_dir):
    verify_pages_artifact(
        archive,
        generation_id=pointer["generation_id"],
        manifest_sha256=pointer["manifest_sha256"],
        producer_revision=REVISION,
        parent_id=None,
        dist=dist,
        generation_dir=generation_dir,
    )


def test_uploaded_pages_tar_matches_build_and_candidate(tmp_path):
    store, pointer = _store(tmp_path)
    dist = _site(tmp_path, store)
    generation = store / "generations" / pointer["generation_id"]
    _verify(_archive(tmp_path, dist), pointer, dist, generation)


def test_verifier_exposes_the_validated_extraction_for_secret_scanning(tmp_path):
    store, pointer = _store(tmp_path)
    dist = _site(tmp_path, store)
    generation = store / "generations" / pointer["generation_id"]
    payload = tmp_path / "verified-pages-payload"

    verify_pages_artifact(
        _archive(tmp_path, dist),
        generation_id=pointer["generation_id"],
        manifest_sha256=pointer["manifest_sha256"],
        producer_revision=REVISION,
        parent_id=None,
        dist=dist,
        generation_dir=generation,
        extraction_dir=payload,
    )

    assert (payload / "index.html").read_text(encoding="utf-8") == "dashboard"
    assert (payload / "data/current.json").is_file()
    assert not (tmp_path / "escape.json").exists()


def test_verifier_rejects_a_preexisting_extraction_destination(tmp_path):
    store, pointer = _store(tmp_path)
    dist = _site(tmp_path, store)
    generation = store / "generations" / pointer["generation_id"]
    payload = tmp_path / "verified-pages-payload"
    payload.mkdir()

    with pytest.raises(PublicationError, match="extraction destination"):
        verify_pages_artifact(
            _archive(tmp_path, dist),
            generation_id=pointer["generation_id"],
            manifest_sha256=pointer["manifest_sha256"],
            producer_revision=REVISION,
            parent_id=None,
            dist=dist,
            generation_dir=generation,
            extraction_dir=payload,
        )


def test_uploaded_pages_tar_keeps_only_the_immediate_parent_generation(tmp_path):
    store, previous = _store(tmp_path)
    current = publish_snapshot(
        store,
        {
            "latest/data.json": {"generated_at": "2026-11-15T12:15:00Z", "value": 4},
            "history/data.json": [{"date": "2026-11-14", "value": 2}],
            "metadata/data.json": {"updated_at": "2026-11-15T12:15:00Z"},
        },
        parent_id=previous["generation_id"],
        producer_revision=REVISION,
        created_at="2026-11-15T12:15:00Z",
        validator=lambda _: True,
    )
    dist = _site(tmp_path, store)
    generation = store / "generations" / current["generation_id"]
    verify_pages_artifact(
        _archive(tmp_path, dist),
        generation_id=current["generation_id"],
        manifest_sha256=current["manifest_sha256"],
        producer_revision=REVISION,
        parent_id=previous["generation_id"],
        dist=dist,
        generation_dir=generation,
    )


def test_uploaded_pages_tar_accepts_safely_withheld_legacy_risk(tmp_path):
    store = tmp_path / "store"
    files = {
        "latest/risk.json": {
            "payload": {"score": 60, "evidence_state": "insufficient_evidence", "dimensions": []},
        },
        "latest/dashboard.json": {
            "payload": {
                "risk": {"score": 60, "evidence_state": "insufficient_evidence", "dimensions": []}
            },
        },
        "history/risk/30d.json": [
            {"date": "2026-11-13", "total_score": 48, "evidence_state": "complete"},
            {"date": "2026-11-14", "total_score": 50, "evidence_state": "partial"},
            {"date": "2026-11-15", "total_score": 60, "evidence_state": "insufficient_evidence"},
        ],
        "metadata/data.json": {"updated_at": STAMP},
    }
    pointer = publish_snapshot(
        store,
        files,
        parent_id=None,
        producer_revision=REVISION,
        created_at=STAMP,
        validator=lambda _: True,
    )
    dist = _site(tmp_path, store)
    generation = store / "generations" / pointer["generation_id"]

    assert not (dist / "data/latest/risk.json").exists()
    assert not (dist / "data/latest/dashboard.json").exists()
    assert not (dist / "data/history/risk/30d.json").exists()
    _verify(_archive(tmp_path, dist), pointer, dist, generation)


def test_uploaded_pages_tar_rejects_modified_build_file(tmp_path):
    store, pointer = _store(tmp_path)
    dist = _site(tmp_path, store)
    generation = store / "generations" / pointer["generation_id"]
    archive = _archive(tmp_path, dist)
    (dist / "index.html").write_text("changed", encoding="utf-8")
    with pytest.raises(PublicationError, match="changed a build file"):
        _verify(archive, pointer, dist, generation)


def test_uploaded_pages_tar_rejects_a_different_current_pointer(tmp_path):
    store, pointer = _store(tmp_path)
    dist = _site(tmp_path, store)
    current = json.loads((dist / "data/current.json").read_bytes())
    current["generation_id"] = "f" * 64
    (dist / "data/current.json").write_text(json.dumps(current), encoding="utf-8")
    with pytest.raises(PublicationError, match="points at another"):
        verify_pages_artifact(
            _archive(tmp_path, dist),
            generation_id=pointer["generation_id"],
            manifest_sha256=pointer["manifest_sha256"],
            producer_revision=REVISION,
            parent_id=None,
            dist=dist,
        )


def test_uploaded_pages_tar_rejects_parent_traversal(tmp_path):
    store, pointer = _store(tmp_path)
    dist = _site(tmp_path, store)
    generation = store / "generations" / pointer["generation_id"]
    escape = tmp_path / "escape.json"
    escape.write_text("{}", encoding="utf-8")
    with pytest.raises(PublicationError, match="unsafe or duplicate"):
        _verify(_archive(tmp_path, dist, extra=escape), pointer, dist, generation)
