#!/usr/bin/env python3

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

from pipeline.legacy_publication import legacy_publication_bytes
from pipeline.publication import PublicationError, read_current, verify_generation

ROOT = Path(__file__).resolve().parents[1]


def install_generation(store: Path, public_data: Path) -> str:
    store = Path(store)
    public_data = public_data.absolute()
    if store.is_symlink() or not store.is_dir() or public_data.is_symlink():
        raise PublicationError("Publication store and Pages data path must be real directories")
    store = store.resolve()
    try:
        pointer = read_current(store)
    except (OSError, ValueError, UnicodeError) as exc:
        raise PublicationError("Publication pointer is invalid") from exc
    if pointer is None:
        raise PublicationError("Publication pointer is invalid")
    generations = store / "generations"
    if generations.is_symlink() or not generations.is_dir():
        raise PublicationError("Publication generations directory is invalid")
    source_generations = sorted(generations.iterdir())
    if not source_generations or len(source_generations) > 2:
        raise PublicationError("Publication store must contain one or two generations")
    for generation in source_generations:
        if generation.is_symlink() or not generation.is_dir():
            raise PublicationError("Publication generation is invalid")
        verify_generation(
            generation,
            manifest_sha256=pointer["manifest_sha256"]
            if generation.name == pointer["generation_id"]
            else None,
        )
    if pointer["generation_id"] not in {path.name for path in source_generations}:
        raise PublicationError("Current generation is absent from the store")
    parent_id = verify_generation(generations / pointer["generation_id"])["parent_id"]
    expected_generations = {pointer["generation_id"]}
    if parent_id is not None:
        expected_generations.add(parent_id)
    if expected_generations != {path.name for path in source_generations}:
        raise PublicationError("Current generation parent is absent from the store")
    entries = {path.name for path in store.iterdir()}
    if entries not in (
        {"current.json", "generations"},
        {"current.json", "generations", ".publication.lock"},
    ):
        raise PublicationError("Publication store contains unsupported files")
    lock = store / ".publication.lock"
    if lock.exists() and (lock.is_symlink() or not lock.is_file()):
        raise PublicationError("Publication lock is invalid")
    public_data.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".cloud-data-", dir=public_data.parent) as temporary:
        staged = Path(temporary) / "data"
        staged.mkdir()
        for directory in ("latest", "history", "metadata"):
            (staged / directory).mkdir()
        shutil.copyfile(store / "current.json", staged / "current.json")
        shutil.copytree(generations, staged / "generations", symlinks=False)
        current_generation = generations / pointer["generation_id"]
        current_manifest = verify_generation(
            current_generation, manifest_sha256=pointer["manifest_sha256"]
        )
        for relative in current_manifest["files"]:
            source = current_generation / relative
            content = legacy_publication_bytes(relative, source.read_bytes())
            if content is None:
                continue
            target = staged / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        if public_data.exists():
            if not public_data.is_dir():
                raise PublicationError("Pages data destination is not a directory")
            shutil.rmtree(public_data)
        staged.rename(public_data)
    return pointer["generation_id"]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install a verified immutable generation into the Pages data tree."
    )
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--public-data", type=Path, default=ROOT / "public" / "data")
    args = parser.parse_args()
    try:
        print(install_generation(args.store, args.public_data))
        return 0
    except (OSError, PublicationError, ValueError) as exc:
        print(f"Pages generation install failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
