"""Local immutable snapshot publication. The lock coordinates one filesystem only."""

import fcntl
import hashlib
import json
import os
import re
import tempfile
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

VERSION = 1
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class PublicationError(ValueError):
    """A candidate or an existing publication failed verification."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _path(value: str) -> str:
    if not isinstance(value, str):
        raise PublicationError("Path must be a string")
    path = PurePosixPath(value)
    if (path.is_absolute() or ".." in path.parts or "\\" in value
            or str(path) != value or len(path.parts) < 2
            or path.parts[0] not in {"latest", "history", "metadata"}
            or any(not re.fullmatch(r"[A-Za-z0-9._-]+", part) for part in path.parts)
            or path.suffix != ".json"):
        raise PublicationError(f"Invalid data path: {value!r}")
    return value


def _object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise PublicationError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _read(path: Path, *, object_only: bool = True) -> Any:
    try:
        if path.is_symlink():
            raise PublicationError(f"Symbolic link: {path}")
        value = json.loads(path.read_bytes(), object_pairs_hook=_object,
                           parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
        if not isinstance(value, (dict, list)) or object_only and not isinstance(value, dict):
            raise PublicationError(f"Invalid JSON document: {path}")
        return value
    except (OSError, ValueError) as exc:
        raise PublicationError(f"Invalid JSON file: {path}") from exc


def _digest(value: Any) -> bool:
    return isinstance(value, str) and bool(_DIGEST.fullmatch(value))


def _timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})", value):
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).utcoffset() is not None
    except ValueError:
        return False


def verify_generation(directory: Path, *, manifest_sha256: str | None = None) -> dict:
    """Check identity, all hashes, strict paths, and the complete file inventory."""
    directory = Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise PublicationError("Generation must be a real directory")
    manifest = _read(directory / "manifest.json")
    if manifest_sha256 is not None and _sha((directory / "manifest.json").read_bytes()) != manifest_sha256:
        raise PublicationError("Manifest hash mismatch")
    if set(manifest) != {"version", "generation_id", "parent_id", "producer_revision", "created_at", "files"}:
        raise PublicationError("Invalid manifest fields")
    generation_id = manifest["generation_id"]
    identity = {key: value for key, value in manifest.items() if key != "generation_id"}
    if (manifest["version"] != VERSION or not _digest(generation_id)
            or _sha(canonical_json(identity)) != generation_id
            or directory.name != generation_id
            or (manifest["parent_id"] is not None and not _digest(manifest["parent_id"]))
            or not isinstance(manifest["producer_revision"], str) or not manifest["producer_revision"]
            or not _timestamp(manifest["created_at"])
            or not isinstance(manifest["files"], dict)):
        raise PublicationError("Invalid manifest identity")
    files = manifest["files"]
    for relative, digest in files.items():
        _path(relative)
        if not _digest(digest):
            raise PublicationError("Invalid file digest")
    if {PurePosixPath(p).parts[0] for p in files} != {"latest", "history", "metadata"}:
        raise PublicationError("Candidate must contain latest, history and metadata JSON")
    actual = set()
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise PublicationError("Symbolic links are forbidden")
        if path.is_file():
            actual.add(path.relative_to(directory).as_posix())
        elif not path.is_dir():
            raise PublicationError("Unsupported filesystem entry")
    if actual != {"manifest.json", *files}:
        raise PublicationError("Generation file inventory mismatch")
    for relative, digest in files.items():
        path = directory / relative
        if _sha(path.read_bytes()) != digest:
            raise PublicationError(f"File hash mismatch: {relative}")
        _read(path, object_only=False)
    return manifest


def read_current(root: Path) -> dict | None:
    root = Path(root)
    pointer = root / "current.json"
    if not pointer.exists() and not pointer.is_symlink():
        return None
    current = _read(pointer)
    if (set(current) != {"version", "generation_id", "manifest_sha256"}
            or current["version"] != VERSION or not _digest(current["generation_id"])
            or not _digest(current["manifest_sha256"])):
        raise PublicationError("Invalid current pointer")
    generations = root / "generations"
    if generations.is_symlink():
        raise PublicationError("Symbolic generation directory")
    verify_generation(generations / current["generation_id"],
                      manifest_sha256=current["manifest_sha256"])
    return current


def load_candidate_files(source: Path) -> dict[str, dict | list]:
    """Load only public dataset directories, rejecting links and malformed JSON."""
    source = Path(source)
    if source.is_symlink() or not source.is_dir():
        raise PublicationError("Candidate must be a real directory")
    files: dict[str, dict | list] = {}
    for name in ("latest", "history", "metadata"):
        directory = source / name
        if directory.is_symlink() or not directory.is_dir():
            raise PublicationError(f"Candidate directory missing: {name}")
        for path in directory.rglob("*"):
            if path.is_symlink():
                raise PublicationError("Symbolic links are forbidden")
            if path.is_dir():
                continue
            if not path.is_file():
                raise PublicationError("Unsupported filesystem entry")
            relative = _path(path.relative_to(source).as_posix())
            files[relative] = _read(path, object_only=False)
    return files


def publish_snapshot(root: Path, files: Mapping[str, dict | list], *, parent_id: str | None,
                     producer_revision: str, created_at: str,
                     validator: Callable[[Path], bool]) -> dict:
    """Validate a staged candidate then atomically switch current with parent checking.

    The validator receives the staged generation directory and must return True.
    Old generations remain available. This is not a deployment checkpoint or a
    cross-runner lock, and callers must not mutate published generations.
    """
    root = Path(root)
    if root.is_symlink():
        raise PublicationError("Symbolic publication root")
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".publication.lock"
    if lock_path.is_symlink():
        raise PublicationError("Symbolic lock")
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        current = read_current(root)
        payloads = {_path(path): canonical_json(value) for path, value in files.items()}
        identity = {"version": VERSION, "parent_id": parent_id,
                    "producer_revision": producer_revision, "created_at": created_at,
                    "files": {path: _sha(data) for path, data in payloads.items()}}
        generation_id = _sha(canonical_json(identity))
        manifest = {**identity, "generation_id": generation_id}
        manifest_bytes = canonical_json(manifest)
        pointer = {"version": VERSION, "generation_id": generation_id,
                   "manifest_sha256": _sha(manifest_bytes)}
        if current != pointer and (current["generation_id"] if current else None) != parent_id:
            raise PublicationError("Stale parent")
        generations = root / "generations"
        if generations.is_symlink():
            raise PublicationError("Symbolic generation directory")
        generations.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".publication-", dir=root) as temporary:
            staged = Path(temporary) / generation_id
            staged.mkdir()
            for relative, data in payloads.items():
                target = staged / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            (staged / "manifest.json").write_bytes(manifest_bytes)
            verify_generation(staged)
            if validator(staged) is not True:
                raise PublicationError("Candidate validator did not succeed")
            verify_generation(staged, manifest_sha256=pointer["manifest_sha256"])
            destination = generations / generation_id
            if destination.exists() or destination.is_symlink():
                verify_generation(destination, manifest_sha256=pointer["manifest_sha256"])
            else:
                os.replace(staged, destination)
            if current != pointer:
                temporary_pointer = Path(temporary) / "current.json"
                temporary_pointer.write_bytes(canonical_json(pointer))
                os.replace(temporary_pointer, root / "current.json")
        return pointer
