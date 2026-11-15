#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from pipeline.legacy_publication import legacy_publication_bytes
from pipeline.publication import PublicationError, verify_generation

ROOT = Path(__file__).resolve().parents[1]


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise PublicationError("Pages pointer has duplicate fields")
        result[key] = value
    return result


def _extract(archive: Path, destination: Path) -> set[str]:
    names: set[str] = set()
    total = 0
    try:
        with tarfile.open(archive, mode="r:") as bundle:
            members = bundle.getmembers()
            if len(members) > 20_000:
                raise PublicationError("Pages artifact has too many entries")
            for member in members:
                name = member.name
                while name.startswith("./"):
                    name = name[2:]
                if name in {"", "."}:
                    if member.isdir():
                        continue
                    raise PublicationError("Pages artifact has an invalid root entry")
                path = PurePosixPath(name)
                if (
                    path.is_absolute()
                    or ".." in path.parts
                    or "\\" in name
                    or str(path) != name
                    or name in names
                ):
                    raise PublicationError("Pages artifact has an unsafe or duplicate path")
                names.add(name)
                target = destination.joinpath(*path.parts)
                if not target.resolve().is_relative_to(destination.resolve()):
                    raise PublicationError("Pages artifact path escapes the extraction directory")
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile() or member.size > 20 * 1024 * 1024:
                    raise PublicationError("Pages artifact contains an unsupported file")
                total += member.size
                if total > 150 * 1024 * 1024:
                    raise PublicationError("Pages artifact exceeds its size limit")
                source = bundle.extractfile(member)
                if source is None:
                    raise PublicationError("Pages artifact file could not be read")
                target.parent.mkdir(parents=True, exist_ok=True)
                with source, target.open("xb") as output:
                    shutil.copyfileobj(source, output)
    except (OSError, tarfile.TarError) as exc:
        raise PublicationError("Pages artifact could not be extracted") from exc
    return {name for name in names if (destination / name).is_file()}


def _inventory(root: Path) -> set[str]:
    if root.is_symlink() or not root.is_dir():
        raise PublicationError("Build directory is invalid")
    files = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise PublicationError("Build contains a symbolic link")
        if path.is_file():
            files.add(path.relative_to(root).as_posix())
        elif not path.is_dir():
            raise PublicationError("Build contains an unsupported entry")
    return files


def verify_pages_artifact(
    archive: Path,
    *,
    generation_id: str,
    manifest_sha256: str,
    producer_revision: str,
    parent_id: str | None,
    dist: Path | None = None,
    generation_dir: Path | None = None,
) -> None:
    if (
        re.fullmatch(r"[0-9a-f]{64}", generation_id) is None
        or re.fullmatch(r"[0-9a-f]{64}", manifest_sha256) is None
        or re.fullmatch(r"[0-9a-f]{40}", producer_revision) is None
    ):
        raise PublicationError("Expected generation identity is invalid")
    with tempfile.TemporaryDirectory(prefix="pages-artifact-") as temporary:
        extracted = Path(temporary) / "site"
        extracted.mkdir()
        files = _extract(archive, extracted)
        if dist is not None:
            expected_files = _inventory(dist)
            if files != expected_files:
                raise PublicationError("Uploaded Pages artifact differs from the build output")
            for relative in files:
                if (extracted / relative).read_bytes() != (dist / relative).read_bytes():
                    raise PublicationError("Uploaded Pages artifact changed a build file")
        try:
            pointer = json.loads(
                (extracted / "data/current.json").read_text(encoding="utf-8"),
                object_pairs_hook=_unique_object,
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
            )
        except (OSError, ValueError, UnicodeError) as exc:
            raise PublicationError(
                "Uploaded Pages artifact has no valid generation pointer"
            ) from exc
        expected_pointer = {
            "version": 1,
            "generation_id": generation_id,
            "manifest_sha256": manifest_sha256,
        }
        if (
            not isinstance(pointer, dict)
            or type(pointer.get("version")) is not int
            or pointer != expected_pointer
        ):
            raise PublicationError("Uploaded Pages artifact points at another generation")
        data_root = extracted / "data"
        if {path.name for path in data_root.iterdir()} != {
            "current.json", "generations", "latest", "history", "metadata"
        }:
            raise PublicationError("Uploaded Pages artifact contains unsupported data files")
        generations_root = data_root / "generations"
        expected_generations = {generation_id}
        if parent_id is not None:
            expected_generations.add(parent_id)
        if {path.name for path in generations_root.iterdir()} != expected_generations:
            raise PublicationError("Uploaded Pages artifact contains an unexpected generation")
        candidate = extracted / "data/generations" / generation_id
        manifest = verify_generation(candidate, manifest_sha256=manifest_sha256)
        if manifest["producer_revision"] != producer_revision or manifest["parent_id"] != parent_id:
            raise PublicationError("Uploaded Pages generation provenance does not match")
        expected_legacy_files = {
            relative
            for relative in manifest["files"]
            if legacy_publication_bytes(
                relative, (candidate / relative).read_bytes()
            ) is not None
        }
        actual_legacy_files = {
            path.relative_to(data_root).as_posix()
            for folder in ("latest", "history", "metadata")
            for path in (data_root / folder).rglob("*")
            if path.is_file()
        }
        if actual_legacy_files != expected_legacy_files:
            raise PublicationError("Legacy Pages paths do not match the current generation")
        for relative in expected_legacy_files:
            original = (candidate / relative).read_bytes()
            expected = legacy_publication_bytes(relative, original)
            if expected is None:
                raise PublicationError("Legacy Pages path exposes an unsupported risk score")
            if (data_root / relative).read_bytes() != expected:
                raise PublicationError("Legacy Pages path differs from its generation projection")
        if generation_dir is not None:
            verify_generation(generation_dir, manifest_sha256=manifest_sha256)
            for path in (candidate / "manifest.json", *candidate.glob("**/*.json")):
                relative = path.relative_to(candidate)
                source = generation_dir / relative
                if not source.is_file() or path.read_bytes() != source.read_bytes():
                    raise PublicationError("Uploaded Pages generation differs from its candidate")
        if parent_id is not None:
            previous = extracted / "data/generations" / parent_id
            verify_generation(previous)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check the uploaded Pages archive against the candidate generation."
    )
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--producer-revision", required=True)
    parser.add_argument("--parent-id", default=None)
    parser.add_argument("--dist", type=Path)
    parser.add_argument("--generation-dir", type=Path)
    args = parser.parse_args()
    try:
        verify_pages_artifact(
            args.archive,
            generation_id=args.generation_id,
            manifest_sha256=args.manifest_sha256,
            producer_revision=args.producer_revision,
            parent_id=args.parent_id,
            dist=args.dist,
            generation_dir=args.generation_dir,
        )
        return 0
    except (OSError, PublicationError, ValueError) as exc:
        print(f"Pages artifact validation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
