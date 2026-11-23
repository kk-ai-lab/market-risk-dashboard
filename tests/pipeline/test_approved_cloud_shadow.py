from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scripts.approved_cloud_shadow import run_shadow


def test_shadow_runs_isolated_data_only_collection_and_validates_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "a" * 40)
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(tmp_path / "summary.md"))
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "output.txt"))
    monkeypatch.setenv("DATA_CLOUD_STATE_TOKEN", "must-not-reach-child")
    monkeypatch.setenv("DATA_FRED_API_KEY", "must-not-reach-child")
    observed = {}

    def collect(command, *, cwd, env, check, timeout):
        observed.update(command=command, cwd=cwd, env=env, check=check, timeout=timeout)
        root = Path(env["DATA_DATA_DIR"])
        artifacts = Path(env["DATA_ARTIFACTS_DIR"])
        (artifacts / "logs").mkdir(parents=True)
        (artifacts / "logs" / "run-report-20261115T120000Z.json").write_text(
            json.dumps(
                {
                    "ok": True,
                    "clean": False,
                    "durations_seconds": {"total": 120.0},
                    "failed_datasets": [],
                    "skipped_datasets": ["equities", "news"],
                    "risk_evidence": {
                        "state": "insufficient_evidence",
                        "effective_coverage": 0.1,
                        "score_lower_bound": 6.4,
                        "score_upper_bound": 96.4,
                        "dimensions": [
                            {
                                "key": "macro",
                                "state": "partial",
                                "coverage": 0.5,
                                "missing_indicators": ["VIXCLS"],
                            }
                        ],
                    },
                }
            ),
            encoding="utf-8",
        )
        metadata = root / "metadata"
        metadata.mkdir(parents=True)
        (metadata / "freshness.json").write_text(
            json.dumps(
                {
                    "datasets": {"crypto": {"status": "fresh"}, "equities": {"status": "missing"}},
                }
            ),
            encoding="utf-8",
        )
        (metadata / "sources.json").write_text(
            json.dumps(
                {
                    "domains": {"crypto": {"providers": [{"provider": "coinmetrics"}]}},
                }
            ),
            encoding="utf-8",
        )
        latest = root / "latest"
        latest.mkdir()
        (latest / "macro.json").write_text(
            json.dumps(
                {
                    "payload": {
                        "inflation": [{"source": "BLS"}],
                        "rates": [{"source": "Treasury"}],
                    }
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0)

    with patch(
        "scripts.approved_cloud_shadow.stage_candidate", return_value={"generation_id": "b" * 64}
    ) as stage:
        summary, root = run_shadow(run_process=collect)

    assert observed["command"][1:3] == ["-m", "pipeline.run"]
    assert observed["command"][-1] == "--full"
    assert observed["env"]["DATA_APPROVED_SOURCES_ONLY"] == "true"
    assert observed["env"]["DATA_BLS_CLOUD_ENABLED"] == "false"
    assert observed["env"]["DATA_TREASURY_CLOUD_ENABLED"] == "false"
    assert observed["env"]["DATA_DATA_DIR"] != str(Path(observed["cwd"]) / "public" / "data")
    assert "DATA_CLOUD_STATE_TOKEN" not in observed["env"]
    assert "DATA_FRED_API_KEY" not in observed["env"]
    stage.assert_called_once()
    assert stage.call_args.args[0] == Path(observed["env"]["DATA_DATA_DIR"])
    assert "insufficient_evidence" in summary
    assert "10.0%" in summary
    assert "point estimate withheld" in summary
    assert "equities, news" in summary
    assert "Providers used: BLS, coinmetrics, Treasury." in summary
    assert "License: [CC-BY-NC-4.0]" in (root / "NOTICE.md").read_text()
    assert str(root) in (tmp_path / "output.txt").read_text()
    assert "Candidate: `" + "b" * 64 in (tmp_path / "summary.md").read_text()
