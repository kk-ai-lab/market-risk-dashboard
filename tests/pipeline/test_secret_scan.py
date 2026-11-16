"""Publish secret scanner coverage contract tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_SCRIPT = REPO_ROOT / "scripts" / "scan-secrets.mjs"
MAX_FILE_BYTES = 2 * 1024 * 1024


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=root, check=True)
    data = root / "public" / "data" / "latest.json"
    data.parent.mkdir(parents=True)
    data.write_text("{}", encoding="utf-8")
    _track(root, data)


def _track(root: Path, path: Path) -> None:
    path.relative_to(root)
    subprocess.run(["git", "add", str(path)], cwd=root, check=True)


def _scan(
    root: Path, *arguments: str, environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("PYTHON_EXECUTABLE", sys.executable)
    if environment:
        env.update(environment)
    return subprocess.run(
        ["node", str(SCAN_SCRIPT), "--root", str(root), *arguments],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _compress(payload: bytes, method: str) -> bytes:
    script = (
        "const z=require('node:zlib');"
        "const chunks=[];"
        f"process.stdin.on('data',chunk=>chunks.push(chunk)).on('end',()=>process.stdout.write(z.{method}(Buffer.concat(chunks))));"
    )
    result = subprocess.run(
        ["node", "-e", script],
        input=payload,
        capture_output=True,
        check=True,
    )
    return result.stdout


def test_secret_scan_fails_closed_for_an_oversized_tracked_file(tmp_path: Path) -> None:
    """An unscanned tracked file is a failed gate, not a clean result."""
    _init_repo(tmp_path)
    oversized = tmp_path / "oversized.bin"
    oversized.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    _track(tmp_path, oversized)

    result = _scan(tmp_path)

    assert result.returncode == 1
    assert "1 skipped" in result.stdout
    assert "scan coverage incomplete" in result.stdout


def test_secret_scan_accepts_a_file_at_the_size_limit(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    boundary = tmp_path / "boundary.txt"
    boundary.write_bytes(b"x" * MAX_FILE_BYTES)
    _track(tmp_path, boundary)

    result = _scan(tmp_path)

    assert result.returncode == 0
    assert "1 skipped" not in result.stdout


@pytest.mark.parametrize("payload", [b"valid text\xff", b"valid\x00text"])
def test_secret_scan_fails_closed_for_invalid_text_bytes(tmp_path: Path, payload: bytes) -> None:
    _init_repo(tmp_path)
    invalid = tmp_path / "invalid.txt"
    invalid.write_bytes(payload)
    _track(tmp_path, invalid)

    result = _scan(tmp_path)

    assert result.returncode == 1
    assert "1 skipped" in result.stdout
    assert "scan coverage incomplete" in result.stdout


@pytest.mark.parametrize("payload_kind", ["invalid-utf8", "nul", "oversized"])
def test_secret_scan_rejects_invalid_or_oversized_dotenv(
    tmp_path: Path, payload_kind: str
) -> None:
    _init_repo(tmp_path)
    payload = {
        "invalid-utf8": b"invalid\xff",
        "nul": b"invalid\x00text",
        "oversized": b"x" * (MAX_FILE_BYTES + 1),
    }[payload_kind]
    (tmp_path / ".env").write_bytes(b"DATA_API_KEY=" + payload + b"\n")

    result = _scan(tmp_path)

    assert result.returncode == 1
    assert "scan coverage incomplete" in result.stdout


def test_repository_scan_covers_untracked_public_data_without_echoing_hits(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    data = tmp_path / "public" / "data" / "new.json"
    data.write_text('{"api_key":"synthetic_secret_value_123456"}', encoding="utf-8")

    result = _scan(tmp_path)

    assert result.returncode == 1
    assert "pattern match" in result.stdout
    assert str(data.relative_to(tmp_path)) in result.stdout
    assert "synthetic_secret_value_123456" not in result.stdout


def test_repository_scan_ignores_short_noncredential_values(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    data = tmp_path / "public" / "data" / "latest.json"
    data.write_text('{"api_key":".."}', encoding="utf-8")

    result = _scan(tmp_path)

    assert result.returncode == 0


def test_repository_scan_keeps_coverage_of_dist_and_run_logs(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    bundle = tmp_path / "dist" / "assets" / "app.js"
    bundle.parent.mkdir(parents=True)
    bundle.write_text('const access_token="synthetic_bundle_secret_123456";', encoding="utf-8")
    log = tmp_path / "artifacts" / "logs" / "run.log"
    log.parent.mkdir(parents=True)
    log.write_text("token=synthetic_log_secret_123456", encoding="utf-8")

    result = _scan(tmp_path)

    assert result.returncode == 1
    assert "dist/assets/app.js" in result.stdout
    assert "artifacts/logs/run.log" in result.stdout
    assert "synthetic_bundle_secret_123456" not in result.stdout
    assert "synthetic_log_secret_123456" not in result.stdout


@pytest.mark.parametrize("layout", ["missing", "empty"])
def test_repository_scan_requires_a_nonempty_data_tree(tmp_path: Path, layout: str) -> None:
    _init_repo(tmp_path)
    data = tmp_path / "public" / "data" / "latest.json"
    subprocess.run(["git", "rm", "--cached", "--quiet", str(data)], cwd=tmp_path, check=True)
    data.unlink()
    if layout == "missing":
        data.parent.rmdir()

    result = _scan(tmp_path)

    assert result.returncode == 1
    assert "scan coverage incomplete" in result.stdout


def test_artifact_scan_uses_data_and_bundle_pattern_tiers(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    artifact = tmp_path / "dist"
    (artifact / "data").mkdir(parents=True)
    (artifact / "assets").mkdir()
    (artifact / "data" / "current.json").write_text(
        '{"api_key":"synthetic_data_secret_123456"}', encoding="utf-8"
    )
    (artifact / "assets" / "app.js").write_text(
        "const password = true; const access_token = 'placeholder';", encoding="utf-8"
    )

    result = _scan(tmp_path, "--artifact-root", str(artifact))

    assert result.returncode == 1
    assert "data/current.json" in result.stdout
    assert "synthetic_data_secret_123456" not in result.stdout
    assert "assets/app.js" not in result.stdout


def test_artifact_scan_keeps_repository_env_literals_and_scans_late_logs(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    secret = "synthetic_literal_secret_value_123456"
    (tmp_path / ".env").write_text(f"DATA_API_KEY={secret}\n", encoding="utf-8")
    artifact = tmp_path / "dist"
    (artifact / "data").mkdir(parents=True)
    (artifact / "data" / "current.json").write_text(
        '{"marker":"synthetic_literal_secret_value_123456"}', encoding="utf-8"
    )
    late_log = tmp_path / "artifacts" / "logs" / "late.log"
    late_log.parent.mkdir(parents=True)
    late_log.write_text("api_key=synthetic_late_log_secret_123456", encoding="utf-8")

    result = _scan(tmp_path, "--artifact-root", str(artifact))

    assert result.returncode == 1
    assert "literal env value (masked)" in result.stdout
    assert "artifacts/logs/late.log" in result.stdout
    assert secret not in result.stdout
    assert "synthetic_late_log_secret_123456" not in result.stdout


def test_artifact_scan_reads_dotenv_export_and_inline_comment(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    secret = "synthetic_exported_literal_secret_123456"
    (tmp_path / ".env").write_text(
        f'export DATA_API_KEY = "{secret}" # local test key\n', encoding="utf-8"
    )
    artifact = tmp_path / "dist"
    (artifact / "data").mkdir(parents=True)
    (artifact / "data" / "current.json").write_text(
        '{"marker":"synthetic_exported_literal_secret_123456"}', encoding="utf-8"
    )

    result = _scan(tmp_path, "--artifact-root", str(artifact))

    assert result.returncode == 1
    assert "literal env value (masked)" in result.stdout
    assert secret not in result.stdout


@pytest.mark.parametrize("base_first", [True, False])
def test_artifact_scan_resolves_dotenv_values_before_process_environment(
    tmp_path: Path, base_first: bool
) -> None:
    _init_repo(tmp_path)
    file_value = "synthetic_file_base_value_123456"
    environment_value = "synthetic_env_base_value_123456"
    dotenv_lines = [
        f"BASE_VALUE={file_value}",
        "DATA_API_KEY=${BASE_VALUE}",
    ]
    if not base_first:
        dotenv_lines.reverse()
    (tmp_path / ".env").write_text(
        "\n".join(dotenv_lines) + "\n", encoding="utf-8"
    )
    secret = file_value if base_first else environment_value
    artifact = tmp_path / "dist"
    (artifact / "data").mkdir(parents=True)
    (artifact / "data" / "current.json").write_text(
        f'{{"marker":"{secret}"}}', encoding="utf-8"
    )

    result = _scan(
        tmp_path,
        "--artifact-root",
        str(artifact),
        environment={"BASE_VALUE": environment_value},
    )

    assert result.returncode == 1
    assert "literal env value (masked)" in result.stdout
    assert secret not in result.stdout
    assert file_value not in result.stdout
    assert environment_value not in result.stdout


def test_artifact_scan_redacts_configured_literals_in_finding_paths(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    secret = "synthetic_path_secret_value_123456"
    (tmp_path / ".env").write_text(f"DATA_API_KEY={secret}\n", encoding="utf-8")
    artifact = tmp_path / "dist"
    (artifact / "data").mkdir(parents=True)
    (artifact / "data" / f"{secret}.json").write_text(
        f'{{"marker":"{secret}"}}', encoding="utf-8"
    )

    result = _scan(tmp_path, "--artifact-root", str(artifact))

    assert result.returncode == 1
    assert "literal env value (masked)" in result.stdout
    assert "[REDACTED].json" in result.stdout
    assert secret not in result.stdout


@pytest.mark.parametrize(
    ("dotenv", "secret"),
    [
        (
            "BASE_VALUE=synthetic_first_value_123456\n"
            "DATA_API_KEY=${BASE_VALUE}\n"
            "BASE_VALUE=synthetic_second_value_123456\n",
            "synthetic_first_value_123456",
        ),
        ("'DATA_API_KEY'=synthetic_quoted_key_value_123456\n", "synthetic_quoted_key_value_123456"),
        (
            'DATA_API_KEY="synthetic\\\'quote_secret_123456"\n',
            "synthetic'quote_secret_123456",
        ),
    ],
)
def test_artifact_scan_matches_python_dotenv_secret_resolution(
    tmp_path: Path, dotenv: str, secret: str
) -> None:
    _init_repo(tmp_path)
    (tmp_path / ".env").write_text(dotenv, encoding="utf-8")
    artifact = tmp_path / "dist"
    (artifact / "data").mkdir(parents=True)
    (artifact / "data" / "current.json").write_text(
        json.dumps({"marker": secret}), encoding="utf-8"
    )

    result = _scan(tmp_path, "--artifact-root", str(artifact))

    assert result.returncode == 1
    assert "literal env value (masked)" in result.stdout
    assert secret not in result.stdout


@pytest.mark.parametrize(
    "encoded_secret",
    [
        "synthetic%20runtime%2Fsecret%20value%20123456",
        "synthetic%20runtime%2fsecret%20value%20123456",
    ],
)
def test_artifact_scan_checks_runtime_credentials(
    tmp_path: Path, encoded_secret: str
) -> None:
    _init_repo(tmp_path)
    secret = "synthetic runtime/secret value 123456"
    artifact = tmp_path / "dist"
    (artifact / "data").mkdir(parents=True)
    (artifact / "data" / "current.json").write_text(
        json.dumps({"marker": encoded_secret}), encoding="utf-8"
    )

    result = _scan(
        tmp_path,
        "--artifact-root",
        str(artifact),
        environment={"DATA_CLOUD_STATE_TOKEN": secret},
    )

    assert result.returncode == 1
    assert "literal env value (masked)" in result.stdout
    assert secret not in result.stdout


def test_scanner_requires_and_rechecks_the_explicit_cloud_run_log_root(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    artifact = tmp_path / "dist"
    (artifact / "data").mkdir(parents=True)
    (artifact / "data" / "current.json").write_text("{}", encoding="utf-8")
    run_logs = tmp_path / "runner" / "workspace" / "artifacts" / "logs"
    run_logs.mkdir(parents=True)
    (run_logs / "run-report.json").write_text(
        "token=synthetic_cloud_log_secret_123456", encoding="utf-8"
    )

    result = _scan(
        tmp_path, "--artifact-root", str(artifact), "--run-logs-root", str(run_logs)
    )

    assert result.returncode == 1
    assert "synthetic_cloud_log_secret_123456" not in result.stdout
    assert "run-report.json" in result.stdout


def test_scanner_fails_closed_when_the_configured_cloud_log_root_is_missing(
    tmp_path: Path,
) -> None:
    _init_repo(tmp_path)
    artifact = tmp_path / "dist"
    (artifact / "data").mkdir(parents=True)
    (artifact / "data" / "current.json").write_text("{}", encoding="utf-8")

    result = _scan(
        tmp_path,
        "--artifact-root",
        str(artifact),
        "--run-logs-root",
        str(tmp_path / "missing-run-logs"),
    )

    assert result.returncode == 1
    assert "scan coverage incomplete" in result.stdout


@pytest.mark.parametrize(
    ("suffix", "method"), [(".gz", "gzipSync"), (".br", "brotliCompressSync")]
)
def test_artifact_scan_decodes_compressed_data_and_bundle_assets(
    tmp_path: Path, suffix: str, method: str
) -> None:
    _init_repo(tmp_path)
    artifact = tmp_path / "dist"
    (artifact / "data").mkdir(parents=True)
    (artifact / "assets").mkdir()
    (artifact / "data" / "current.json").write_text("{}", encoding="utf-8")
    clean = b'{"state":"clean"}'
    (artifact / "data" / f"clean.json{suffix}").write_bytes(_compress(clean, method))
    secret = b'{"api_key":"synthetic_compressed_secret_123456"}'
    (artifact / "data" / f"payload.json{suffix}").write_bytes(_compress(secret, method))
    bundle = b'const access_token="synthetic_compressed_bundle_secret_123456";'
    (artifact / "assets" / f"app.js{suffix}").write_bytes(_compress(bundle, method))

    result = _scan(tmp_path, "--artifact-root", str(artifact))

    assert result.returncode == 1
    assert "payload.json" in result.stdout
    assert "app.js" in result.stdout
    assert "synthetic_compressed_secret_123456" not in result.stdout
    assert "synthetic_compressed_bundle_secret_123456" not in result.stdout


@pytest.mark.parametrize(
    ("suffix", "method"), [(".gz", "gzipSync"), (".br", "brotliCompressSync")]
)
def test_artifact_scan_fails_closed_for_oversized_decompressed_text(
    tmp_path: Path, suffix: str, method: str
) -> None:
    _init_repo(tmp_path)
    artifact = tmp_path / "dist"
    (artifact / "data").mkdir(parents=True)
    (artifact / "data" / "current.json").write_text("{}", encoding="utf-8")
    expanded = b"x" * (MAX_FILE_BYTES + 1)
    (artifact / f"large.json{suffix}").write_bytes(_compress(expanded, method))

    result = _scan(tmp_path, "--artifact-root", str(artifact))

    assert result.returncode == 1
    assert "scan coverage incomplete" in result.stdout


@pytest.mark.parametrize(
    ("suffix", "method"), [(".gz", "gzipSync"), (".br", "brotliCompressSync")]
)
def test_artifact_scan_accepts_the_decompressed_size_boundary(
    tmp_path: Path, suffix: str, method: str
) -> None:
    _init_repo(tmp_path)
    artifact = tmp_path / "dist"
    (artifact / "data").mkdir(parents=True)
    (artifact / "data" / "current.json").write_text("{}", encoding="utf-8")
    (artifact / "data" / f"boundary.json{suffix}").write_bytes(
        _compress(b"x" * MAX_FILE_BYTES, method)
    )

    result = _scan(tmp_path, "--artifact-root", str(artifact))

    assert result.returncode == 0
    assert "0 skipped" in result.stdout


@pytest.mark.parametrize("suffix", [".gz", ".br"])
def test_artifact_scan_fails_closed_for_malformed_compressed_text(
    tmp_path: Path, suffix: str
) -> None:
    _init_repo(tmp_path)
    artifact = tmp_path / "dist"
    (artifact / "data").mkdir(parents=True)
    (artifact / "data" / "current.json").write_text("{}", encoding="utf-8")
    (artifact / "data" / f"malformed.json{suffix}").write_bytes(b"not compressed")

    result = _scan(tmp_path, "--artifact-root", str(artifact))

    assert result.returncode == 1
    assert "scan coverage incomplete" in result.stdout


@pytest.mark.parametrize("layout", ["missing-root", "missing-data", "empty-data"])
def test_artifact_scan_fails_closed_without_a_nonempty_pages_data_tree(
    tmp_path: Path, layout: str
) -> None:
    _init_repo(tmp_path)
    artifact = tmp_path / "dist"
    if layout != "missing-root":
        artifact.mkdir()
    if layout == "empty-data":
        (artifact / "data").mkdir()
    if layout == "missing-data":
        (artifact / "index.html").write_text("dashboard", encoding="utf-8")
    if layout == "empty-data":
        (artifact / "index.html").write_text("dashboard", encoding="utf-8")

    result = _scan(tmp_path, "--artifact-root", str(artifact))

    assert result.returncode == 1
    assert "scan coverage incomplete" in result.stdout


def test_artifact_scan_fails_closed_for_symlinks(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    artifact = tmp_path / "dist"
    (artifact / "data").mkdir(parents=True)
    target = tmp_path / "outside.txt"
    target.write_text("safe", encoding="utf-8")
    (artifact / "data" / "linked.json").symlink_to(target)

    result = _scan(tmp_path, "--artifact-root", str(artifact))

    assert result.returncode == 1
    assert "scan coverage incomplete" in result.stdout


def test_artifact_scan_fails_closed_for_special_entries(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    artifact = tmp_path / "dist"
    (artifact / "data").mkdir(parents=True)
    os.mkfifo(artifact / "data" / "pipe.json")

    result = _scan(tmp_path, "--artifact-root", str(artifact))

    assert result.returncode == 1
    assert "scan coverage incomplete" in result.stdout
