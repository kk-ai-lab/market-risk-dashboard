"""Prepare an isolated approved-source candidate; never deploy or reserve publication."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from pipeline.publication import (
    GenerationSource,
    PublicationError,
    _object,
    _path,
    canonical_json,
    publish_reused_generation,
    verify_generation,
)
from pipeline.settings import Settings
from pipeline.storage.bls_snapshot import SnapshotError as BlsSnapshotError
from pipeline.storage.cloud_bls import _decode as decode_bls
from pipeline.storage.cloud_treasury import _decode as decode_treasury
from pipeline.storage.github_checkpoint import Checkpoint, CheckpointError, GitHubCheckpoint
from pipeline.storage.publication_checkpoint import Generation, PublicationCheckpoint
from pipeline.storage.treasury_snapshot import SnapshotError as TreasurySnapshotError
from pipeline.utils import now_utc

CloudMode = Literal["scheduled", "code-only"]


class CloudRefreshError(ValueError):
    """A candidate cannot safely be prepared from the supplied state."""


class GenerationRestorer(Protocol):
    def restore(self, expected: Generation, store: Path) -> Path: ...


@dataclass(frozen=True)
class CloudPaths:
    work_dir: Path
    publication: str = "publication.json"
    bls: str = "bls/ledger.json"
    treasury: str = "treasury/ledger.json"


@dataclass(frozen=True)
class CloudRefreshResult:
    mode: CloudMode
    application_revision: str
    generation_id: str
    manifest_sha256: str
    generation_dir: Path
    parent_id: str | None


class PagesGenerationRestorer:
    """Read Pages only when pointer, manifest and all files match the checkpoint."""

    def __init__(self, base_url: str, *, transport: httpx.BaseTransport | None = None):
        url = urlsplit(base_url)
        if (
            url.scheme != "https"
            or not url.netloc
            or url.username
            or url.password
            or url.query
            or url.fragment
            or ".." in url.path.split("/")
        ):
            raise CloudRefreshError("site URL must be an HTTPS base URL")
        self.base_url = base_url.rstrip("/")
        self.transport = transport

    def restore(self, expected: Generation, store: Path) -> Path:
        total = 0
        with httpx.Client(
            transport=self.transport, timeout=30, follow_redirects=False, trust_env=False
        ) as client:

            def download(relative: str) -> bytes:
                nonlocal total
                data = bytearray()
                try:
                    url = self.base_url + "/data/" + relative
                    if relative == "current.json":
                        url += "?refresh=" + uuid4().hex
                    with client.stream(
                        "GET", url, headers={"Cache-Control": "no-cache"}
                    ) as response:
                        if response.status_code != 200:
                            raise CloudRefreshError("Pages generation read failed")
                        for chunk in response.iter_bytes():
                            total += len(chunk)
                            data.extend(chunk)
                            if len(data) > 10 * 1024 * 1024 or total > 100 * 1024 * 1024:
                                raise CloudRefreshError("Pages generation exceeds download limit")
                except httpx.HTTPError as exc:
                    raise CloudRefreshError("Pages generation read failed") from exc
                return bytes(data)

            def document(data: bytes) -> dict:
                try:
                    value = json.loads(
                        data,
                        object_pairs_hook=_object,
                        parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)),
                    )
                except (ValueError, UnicodeError) as exc:
                    raise CloudRefreshError("Pages returned invalid JSON") from exc
                if not isinstance(value, dict):
                    raise CloudRefreshError("Pages returned invalid document")
                return value

            pointer = {
                "version": 1,
                "generation_id": expected.generation_id,
                "manifest_sha256": expected.manifest_sha256,
            }
            observed_pointer = document(download("current.json"))
            if type(observed_pointer.get("version")) is not int or observed_pointer != pointer:
                raise CloudRefreshError("Pages pointer does not match deployed checkpoint")
            prefix = "generations/" + expected.generation_id + "/"
            manifest_bytes = download(prefix + "manifest.json")
            if hashlib.sha256(manifest_bytes).hexdigest() != expected.manifest_sha256:
                raise CloudRefreshError("Pages manifest digest mismatch")
            manifest = document(manifest_bytes)
            files = manifest.get("files")
            if (
                type(manifest.get("version")) is not int
                or manifest.get("version") != 1
                or manifest.get("generation_id") != expected.generation_id
                or manifest.get("producer_revision") != expected.producer_revision
                or manifest.get("parent_id") != expected.parent_id
                or not isinstance(files, dict)
                or not files
                or len(files) > 5000
            ):
                raise CloudRefreshError("Pages manifest does not match deployed checkpoint")
            directory = store / "generations" / expected.generation_id
            directory.mkdir(parents=True, exist_ok=False)
            for relative, digest in files.items():
                _path(relative)
                payload = download(prefix + relative)
                if hashlib.sha256(payload).hexdigest() != digest:
                    raise CloudRefreshError("Pages data file digest mismatch")
                target = directory / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(payload)
            (directory / "manifest.json").write_bytes(manifest_bytes)
            verify_generation(directory, manifest_sha256=expected.manifest_sha256)
            (store / "current.json").write_bytes(canonical_json(pointer))
            return directory

    def read_pointer(self) -> dict | None:
        with httpx.Client(
            transport=self.transport, timeout=30, follow_redirects=False, trust_env=False
        ) as client:
            try:
                response = client.get(
                    self.base_url + "/data/current.json?refresh=" + uuid4().hex,
                    headers={"Cache-Control": "no-cache"},
                )
            except httpx.HTTPError as exc:
                raise CloudRefreshError("Pages pointer read failed") from exc
        if response.status_code == 404:
            return None
        if response.status_code != 200 or len(response.content) > 1024 * 1024:
            raise CloudRefreshError("Pages pointer read failed")
        try:
            pointer = json.loads(
                response.content,
                object_pairs_hook=_object,
                parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
            )
        except (ValueError, UnicodeError) as exc:
            raise CloudRefreshError("Pages returned invalid pointer JSON") from exc
        if (
            not isinstance(pointer, dict)
            or set(pointer) != {"version", "generation_id", "manifest_sha256"}
            or type(pointer["version"]) is not int
            or pointer["version"] != 1
            or not isinstance(pointer["generation_id"], str)
            or not isinstance(pointer["manifest_sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", pointer["generation_id"]) is None
            or re.fullmatch(r"[0-9a-f]{64}", pointer["manifest_sha256"]) is None
        ):
            raise CloudRefreshError("Pages returned invalid pointer")
        return pointer

    def has_legacy_data(self) -> bool:
        """Detect the published data layout that predates generation pointers."""
        paths = (
            "latest/dashboard.json",
            "latest/risk.json",
            "metadata/freshness.json",
        )
        with httpx.Client(
            transport=self.transport, timeout=30, follow_redirects=False, trust_env=False
        ) as client:
            for path in paths:
                try:
                    response = client.get(
                        f"{self.base_url}/data/{path}?refresh={uuid4().hex}",
                        headers={"Cache-Control": "no-cache"},
                    )
                except httpx.HTTPError as exc:
                    raise CloudRefreshError("Pages legacy data check failed") from exc
                if response.status_code == 200:
                    return True
                if response.status_code != 404:
                    raise CloudRefreshError("Pages legacy data check failed")
        return False


def _require_provisioned_provider_state(checkpoint: Checkpoint, paths: CloudPaths) -> None:
    decode_bls(checkpoint.read(paths.bls).content)
    decode_treasury(checkpoint.read(paths.treasury).content)


def _verify_checkout(settings: Settings, revision: str) -> None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=settings.project_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if result.returncode or result.stdout.strip() != revision:
        raise CloudRefreshError(
            "Checked-out application revision does not match requested revision"
        )


def _collect_approved(settings: Settings) -> None:
    if not settings.cloud_state_repo or settings.cloud_state_token is None:
        raise CloudRefreshError("Cloud state repository and token are required")
    env = {key: value for key, value in os.environ.items() if not key.startswith("DATA_")}
    env.update(
        {
            "DATA_APPROVED_SOURCES_ONLY": "true",
            "DATA_BLS_CLOUD_ENABLED": "true",
            "DATA_TREASURY_CLOUD_ENABLED": "true",
            "DATA_COINMETRICS_ENABLED": "true",
            "DATA_DATA_DIR": str(settings.data_dir),
            "DATA_ARTIFACTS_DIR": str(settings.artifacts_dir),
            "DATA_CONFIG_DIR": str(settings.config_dir),
            "DATA_PROJECT_ROOT": str(settings.project_root),
            "DATA_CLOUD_STATE_REPO": settings.cloud_state_repo,
            "DATA_CLOUD_STATE_BRANCH": settings.cloud_state_branch,
            "DATA_CLOUD_STATE_TOKEN": settings.cloud_state_token.get_secret_value(),
            "DATA_BLS_CHECKPOINT_PATH": settings.bls_checkpoint_path,
            "DATA_TREASURY_CHECKPOINT_PATH": settings.treasury_checkpoint_path,
        }
    )
    result = subprocess.run(
        [sys.executable, "-m", "pipeline.run", "--full"],
        cwd=settings.project_root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
    )
    if result.returncode:
        raise CloudRefreshError("Approved-source collection or risk calculation failed")


def refresh_cloud(
    *,
    mode: CloudMode,
    app_revision: str,
    checkpoint: Checkpoint,
    paths: CloudPaths,
    settings: Settings,
    restorer: GenerationRestorer,
) -> CloudRefreshResult:
    """Return a local candidate from provisioned state, without publication writes."""
    if (
        mode not in {"scheduled", "code-only"}
        or re.fullmatch(r"[0-9a-f]{40}", app_revision) is None
    ):
        raise CloudRefreshError("Invalid mode or application revision")
    state = PublicationCheckpoint(checkpoint, paths.publication).read()
    if state.candidate is not None:
        raise CloudRefreshError("An unresolved publication attempt requires reconciliation")
    if state.deployed and mode == "scheduled" and app_revision != state.deployed.producer_revision:
        raise CloudRefreshError("Scheduled refresh must keep the deployed application revision")
    _require_provisioned_provider_state(checkpoint, paths)
    _verify_checkout(settings, app_revision)
    workspace = paths.work_dir.resolve() / uuid4().hex
    workspace.mkdir(parents=True, exist_ok=False)
    store = workspace / "store"
    restored = None
    if state.deployed:
        restored = restorer.restore(state.deployed, store)
        manifest = verify_generation(restored, manifest_sha256=state.deployed.manifest_sha256)
        if manifest["generation_id"] != state.deployed.generation_id:
            raise CloudRefreshError("Restored generation does not match checkpoint")
    parent = state.deployed.generation_id if state.deployed else None
    if restored is not None and mode == "code-only":
        pointer = publish_reused_generation(
            store,
            GenerationSource(restored, state.deployed.manifest_sha256),
            producer_revision=app_revision,
            created_at=now_utc(),
        )
    else:
        data = workspace / "data"
        data.mkdir()
        if restored is not None:
            for folder in ("latest", "history", "metadata"):
                shutil.copytree(restored / folder, data / folder)
        isolated = settings.model_copy(
            update={
                "data_dir": data,
                "artifacts_dir": workspace / "artifacts",
                "approved_sources_only": True,
                "bls_cloud_enabled": True,
                "treasury_cloud_enabled": True,
                "bls_checkpoint_path": paths.bls,
                "treasury_checkpoint_path": paths.treasury,
            }
        )
        _collect_approved(isolated)
        _require_provisioned_provider_state(checkpoint, paths)
        from scripts.stage_publication import stage_candidate

        pointer = stage_candidate(
            data, store, parent_id=parent, revision=app_revision, created_at=now_utc()
        )
    directory = store / "generations" / pointer["generation_id"]
    verify_generation(directory, manifest_sha256=pointer["manifest_sha256"])
    return CloudRefreshResult(
        mode, app_revision, pointer["generation_id"], pointer["manifest_sha256"], directory, parent
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("scheduled", "code-only"), required=True)
    parser.add_argument("--app-revision", required=True)
    parser.add_argument("--site-url", required=True)
    parser.add_argument("--work-dir", type=Path, default=Path("artifacts/cloud-refresh"))
    args = parser.parse_args(argv)
    settings = Settings()
    repository = (settings.cloud_state_repo or "").split("/")
    if len(repository) != 2 or settings.cloud_state_token is None:
        parser.error("Cloud state repository and token must be configured")
    checkpoint = GitHubCheckpoint(
        *repository, settings.cloud_state_branch, settings.cloud_state_token.get_secret_value()
    )
    try:
        result = refresh_cloud(
            mode=args.mode,
            app_revision=args.app_revision,
            checkpoint=checkpoint,
            paths=CloudPaths(
                args.work_dir,
                bls=settings.bls_checkpoint_path,
                treasury=settings.treasury_checkpoint_path,
            ),
            settings=settings,
            restorer=PagesGenerationRestorer(args.site_url),
        )
        print(json.dumps({**asdict(result), "generation_dir": str(result.generation_dir)}))
        return 0
    except (
        CloudRefreshError,
        CheckpointError,
        PublicationError,
        BlsSnapshotError,
        TreasurySnapshotError,
        OSError,
        ValueError,
        subprocess.SubprocessError,
    ):
        print("Cloud candidate preparation failed; no publication was performed", file=sys.stderr)
        return 1
    finally:
        checkpoint.close()


if __name__ == "__main__":
    raise SystemExit(main())
