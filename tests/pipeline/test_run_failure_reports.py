from __future__ import annotations

import json

import pytest

from pipeline import run as run_mod
from pipeline.report import write_run_report


def _configure_run(monkeypatch, tmp_path, reports):
    for name in ("load_universe", "load_risk_model", "load_sources", "load_news_sources"):
        monkeypatch.setattr(type(run_mod.settings), name, lambda _self: {})
    monkeypatch.setattr(run_mod.settings, "approved_sources_only", False)
    monkeypatch.setattr(run_mod.settings, "data_dir", tmp_path / "data")
    monkeypatch.setattr(run_mod.settings, "artifacts_dir", tmp_path / "artifacts")
    monkeypatch.setattr(run_mod, "StorageWriter", lambda _path: object())
    monkeypatch.setattr(run_mod, "write_run_report", lambda _path, **kwargs: reports.append(kwargs))


@pytest.mark.parametrize(
    ("failure_path", "expected_code"),
    [
        ("collection_exception", "collection_exception"),
        ("pipeline_exception", "pipeline_exception"),
        ("result_rejected", "result_rejected"),
    ],
)
def test_main_failure_reports_use_control_path_code_and_preserve_rejection_health(
    failure_path, expected_code, monkeypatch, tmp_path, capsys
):
    reports = []
    _configure_run(monkeypatch, tmp_path, reports)
    health = {
        "failed": ["macro"],
        "skipped": ["news"],
        "degraded": ["equities"],
    }
    monkeypatch.setattr(run_mod, "dataset_health", lambda *_args, **_kwargs: health)

    if failure_path == "collection_exception":

        def fail_collection(_command):
            raise RuntimeError("collection-secret")

        monkeypatch.setattr(run_mod, "_run_collection", fail_collection)
    else:
        monkeypatch.setattr(run_mod, "_run_collection", lambda _command: run_mod._empty_results())
        if failure_path == "pipeline_exception":

            def fail_pipeline():
                raise RuntimeError("pipeline-secret")

            monkeypatch.setattr(run_mod, "_run_fact_layer_only", fail_pipeline)
        else:
            monkeypatch.setattr(run_mod, "_run_fact_layer_only", lambda: (False, "rejected"))

    assert run_mod.main(["--fact-layer"]) == 1
    capsys.readouterr()
    assert len(reports) == 1
    report = reports[0]
    assert report["failure_code"] == expected_code
    if failure_path == "result_rejected":
        assert report["failed_datasets"] == health["failed"]
        assert report["skipped_datasets"] == health["skipped"]
        assert report["degraded_datasets"] == health["degraded"]
    else:
        assert report["failed_datasets"] == []
        assert report["skipped_datasets"] == []
        assert report["degraded_datasets"] == []


def test_run_report_serializes_failure_code_and_omits_it_when_absent(tmp_path):
    report_path = write_run_report(
        tmp_path / "with-code",
        command="full",
        ok=False,
        durations={},
        provider_status={},
        degraded=[],
        dataset_counts={},
        failure_code="result_rejected",
    )
    assert json.loads(report_path.read_text(encoding="utf-8"))["failure_code"] == "result_rejected"

    clean_path = write_run_report(
        tmp_path / "without-code",
        command="full",
        ok=True,
        durations={},
        provider_status={},
        degraded=[],
        dataset_counts={},
    )
    assert "failure_code" not in json.loads(clean_path.read_text(encoding="utf-8"))
