from __future__ import annotations

import json
from datetime import date
from types import SimpleNamespace

import pytest

from pipeline import run as run_mod
from pipeline.providers.treasury import TREASURY_XML
from pipeline.report import (
    RiskInputDiagnostics,
    RunFailureCode,
    RunRejectionCode,
    TreasuryInputEvidence,
    TreasuryRefreshStatus,
    write_run_report,
)
from pipeline.storage.treasury_snapshot import Observation, TreasurySnapshot, TreasuryYear


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
        failure_code=RunFailureCode.RESULT_REJECTED,
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
    assert "rejection_code" not in json.loads(clean_path.read_text(encoding="utf-8"))
    assert "written_latest_outputs" not in json.loads(clean_path.read_text(encoding="utf-8"))


def test_run_report_serializes_successful_latest_output_receipt(tmp_path):
    report_path = write_run_report(
        tmp_path,
        command="full",
        ok=True,
        durations={},
        provider_status={},
        degraded=[],
        dataset_counts={},
        written_latest_outputs=frozenset({"latest/energy.json", "latest/risk.json"}),
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["written_latest_outputs"] == ["latest/energy.json", "latest/risk.json"]


def test_run_report_serializes_optional_rejection_code(tmp_path):
    from pipeline.report import RunRejectionCode

    report_path = write_run_report(
        tmp_path,
        command="full",
        ok=False,
        durations={},
        provider_status={},
        degraded=[],
        dataset_counts={},
        failure_code=RunFailureCode.RESULT_REJECTED,
        rejection_code=RunRejectionCode.RISK_VALIDATION_FAILED,
    )

    assert (
        json.loads(report_path.read_text(encoding="utf-8")).get("rejection_code")
        == "risk_validation_failed"
    )


def test_run_report_serializes_risk_input_diagnostics(tmp_path):
    diagnostics = RiskInputDiagnostics(
        treasury=TreasuryInputEvidence(
            status=TreasuryRefreshStatus.CHECKPOINT_UNAVAILABLE,
            dgs2_observation_count=0,
            dgs10_observation_count=0,
        ),
        macro_dgs2_has_value=False,
        macro_dgs10_has_value=False,
        usable_indicator_count=0,
        configured_indicator_count=19,
    )

    report_path = write_run_report(
        tmp_path,
        command="full",
        ok=False,
        durations={},
        provider_status={},
        degraded=[],
        dataset_counts={},
        failure_code=RunFailureCode.RESULT_REJECTED,
        risk_input_diagnostics=diagnostics,
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["risk_input_diagnostics"] == {
        "treasury": {
            "status": "checkpoint_unavailable",
            "dgs2_observation_count": 0,
            "dgs10_observation_count": 0,
        },
        "macro_dgs2_has_value": False,
        "macro_dgs10_has_value": False,
        "usable_indicator_count": 0,
        "configured_indicator_count": 19,
    }


def test_treasury_input_evidence_preserves_rows_on_failed_refresh():
    year = TreasuryYear(
        year=2026,
        source=(
            f"{TREASURY_XML}?data=daily_treasury_yield_curve"
            "&field_tdr_date_value=2026"
        ),
        checked_at="2026-11-16T00:00:00+00:00",
        date_semantics="observation_date",
        series={
            "DGS2": [Observation(date=date(2026, 11, 13), value=3.9)],
            "DGS10": [Observation(date=date(2026, 11, 13), value=4.2)],
        },
    )
    snapshot = TreasurySnapshot(
        years={2026: year},
        required_years=[2026],
        degraded=True,
        errors={2026: "provider detail must not enter diagnostics"},
        history_rechecked=False,
    )

    evidence = run_mod._treasury_input_evidence(
        snapshot,
        TreasuryRefreshStatus.REFRESH_FAILED,
    )

    assert evidence.status is TreasuryRefreshStatus.REFRESH_FAILED
    assert evidence.dgs2_observation_count == 1
    assert evidence.dgs10_observation_count == 1

    complete = run_mod._treasury_input_evidence(
        snapshot.model_copy(update={"degraded": False, "errors": {}})
    )
    assert complete.status is TreasuryRefreshStatus.COMPLETE

    failed = run_mod._treasury_input_evidence(snapshot)
    assert failed.status is TreasuryRefreshStatus.REFRESH_FAILED


def test_treasury_input_evidence_distinguishes_empty_and_unconfigured_snapshots():
    empty_snapshot = TreasurySnapshot(
        years={},
        required_years=[2026],
        degraded=False,
        errors={},
        history_rechecked=False,
    )

    empty = run_mod._treasury_input_evidence(empty_snapshot)
    unconfigured = run_mod._treasury_input_evidence(None)

    assert empty.status is TreasuryRefreshStatus.NO_OBSERVATIONS
    assert empty.dgs2_observation_count == 0
    assert empty.dgs10_observation_count == 0
    assert unconfigured.status is TreasuryRefreshStatus.NOT_CONFIGURED


def test_risk_input_diagnostics_locate_macro_and_model_boundary():
    treasury = TreasuryInputEvidence(
        status=TreasuryRefreshStatus.COMPLETE,
        dgs2_observation_count=1,
        dgs10_observation_count=1,
    )
    macro = SimpleNamespace(
        rates=[
            SimpleNamespace(key="dgs2", value=3.9),
            SimpleNamespace(key="dgs10", value=4.2),
        ]
    )
    risk_result = SimpleNamespace(
        dimensions=[
            SimpleNamespace(
                indicators=[
                    SimpleNamespace(value=None),
                    SimpleNamespace(value=None),
                ]
            )
        ]
    )

    diagnostics = run_mod._risk_input_diagnostics(
        treasury,
        macro,
        risk_result,
    )

    assert diagnostics.treasury == treasury
    assert diagnostics.macro_dgs2_has_value is True
    assert diagnostics.macro_dgs10_has_value is True
    assert diagnostics.usable_indicator_count == 0
    assert diagnostics.configured_indicator_count == 2


def test_main_full_rejection_rolls_back_before_health_and_reports_fixed_code(
    monkeypatch, tmp_path, capsys
):
    from pipeline.storage.writer import StorageWriter

    reports = []
    _configure_run(monkeypatch, tmp_path, reports)
    data_dir = tmp_path / "data"
    marker = data_dir / "latest" / "candidate.json"
    local_error = "validation failed: local-validation-secret"
    rejection = run_mod.RunRejection(
        run_mod.RunRejectionCode.RISK_VALIDATION_FAILED, local_error
    )
    writers = []

    def make_writer(path):
        writer = StorageWriter(path)
        writers.append(writer)
        return writer

    def reject_candidate(_results, writer, _command):
        writer.write_standalone("candidate", {"value": "candidate"})
        return rejection

    monkeypatch.setattr(run_mod, "StorageWriter", make_writer)
    monkeypatch.setattr(run_mod, "_run_collection", lambda _command: run_mod._empty_results())
    monkeypatch.setattr(run_mod, "_run_risk_and_write", reject_candidate)

    health = {
        "failed": ["macro"],
        "skipped": ["news"],
        "degraded": ["equities"],
    }

    def inspect_after_rollback(*_args, **_kwargs):
        assert not marker.exists()
        return health

    monkeypatch.setattr(run_mod, "dataset_health", inspect_after_rollback)

    assert run_mod.main(["--full"]) == 1
    error = capsys.readouterr().err

    assert f"[pipeline] failed: {local_error}" in error
    assert len(reports) == 1
    assert reports[0]["failure_code"] == "result_rejected"
    assert reports[0].get("rejection_code") == "risk_validation_failed"
    assert reports[0]["error"] == local_error
    assert reports[0]["failed_datasets"] == health["failed"]
    assert reports[0]["skipped_datasets"] == health["skipped"]
    assert reports[0]["degraded_datasets"] == health["degraded"]
    assert "written_latest_outputs" not in reports[0]
    assert writers[0].written_latest_outputs == frozenset()


def test_main_rejection_carries_risk_input_diagnostics_into_report(
    monkeypatch, tmp_path, capsys
):
    from pipeline.storage import StorageWriter

    reports = []
    _configure_run(monkeypatch, tmp_path, reports)
    monkeypatch.setattr(run_mod, "StorageWriter", StorageWriter)
    monkeypatch.setattr(run_mod, "_run_collection", lambda _command: run_mod._empty_results())
    health = {"failed": ["risk"], "skipped": [], "degraded": []}
    monkeypatch.setattr(run_mod, "dataset_health", lambda *_args, **_kwargs: health)
    diagnostics = RiskInputDiagnostics(
        treasury=TreasuryInputEvidence(
            status=TreasuryRefreshStatus.NO_OBSERVATIONS,
            dgs2_observation_count=0,
            dgs10_observation_count=0,
        ),
        macro_dgs2_has_value=False,
        macro_dgs10_has_value=False,
        usable_indicator_count=0,
        configured_indicator_count=19,
    )
    monkeypatch.setattr(
        run_mod,
        "_run_risk_and_write",
        lambda *_args, **_kwargs: run_mod.RunRejection(
            RunRejectionCode.RISK_NO_USABLE_INDICATORS,
            "risk calculation has no available indicators",
            diagnostics,
        ),
    )

    assert run_mod.main(["--full"]) == 1
    capsys.readouterr()

    assert len(reports) == 1
    assert reports[0]["rejection_code"] == RunRejectionCode.RISK_NO_USABLE_INDICATORS
    assert reports[0]["risk_input_diagnostics"] is diagnostics


def test_finish_run_marks_dataset_health_failure_and_keeps_report_semantics(monkeypatch, tmp_path):
    reports = []
    report_paths = []
    _configure_run(monkeypatch, tmp_path, reports)

    def write_report(artifacts_dir, **kwargs):
        reports.append(kwargs)
        report_path = write_run_report(artifacts_dir, **kwargs)
        report_paths.append(report_path)
        return report_path

    monkeypatch.setattr(run_mod, "write_run_report", write_report)
    monkeypatch.setattr(run_mod, "_print_summary", lambda *_args, **_kwargs: None)
    health = {
        "failed": ["macro"],
        "skipped": ["news"],
        "degraded": ["equities"],
    }

    exit_code = run_mod._finish_run(
        "full",
        run_mod._empty_results(),
        1.5,
        health,
        written_latest_outputs=frozenset({"latest/risk.json"}),
    )

    assert exit_code == 1
    assert reports[0]["failure_code"] == "dataset_health_failed"
    report = json.loads(report_paths[0].read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["clean"] is False
    assert report["failed_datasets"] == health["failed"]
    assert report["skipped_datasets"] == health["skipped"]
    assert report["degraded_datasets"] == health["degraded"]
    assert "written_latest_outputs" not in report


def test_partial_run_omits_latest_output_receipt(monkeypatch, tmp_path):
    reports = []
    _configure_run(monkeypatch, tmp_path, reports)
    monkeypatch.setattr(run_mod, "_print_summary", lambda *_args, **_kwargs: None)

    def write_report(artifacts_dir, **kwargs):
        reports.append(kwargs)
        return write_run_report(artifacts_dir, **kwargs)

    monkeypatch.setattr(run_mod, "write_run_report", write_report)

    assert (
        run_mod._finish_run(
            "macro-only",
            run_mod._empty_results(),
            0.5,
            {"failed": [], "skipped": [], "degraded": []},
            written_latest_outputs=frozenset({"latest/macro.json"}),
        )
        == 0
    )

    report_path = next((tmp_path / "artifacts" / "logs").glob("run-report-*.json"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert "written_latest_outputs" not in report


def test_main_initialization_exception_is_not_mislabeled_as_collection_failure(
    monkeypatch, tmp_path, capsys
):
    reports = []
    _configure_run(monkeypatch, tmp_path, reports)
    collection_calls = []

    def fail_clock():
        raise RuntimeError("clock-secret")

    monkeypatch.setattr(run_mod, "now_utc", fail_clock)
    monkeypatch.setattr(
        run_mod,
        "_run_collection",
        lambda _command: collection_calls.append(True) or run_mod._empty_results(),
    )

    assert run_mod.main(["--fact-layer"]) == 1
    capsys.readouterr()
    assert collection_calls == []
    assert reports[0]["failure_code"] == "pipeline_exception"


def test_main_configuration_failure_writes_report_and_keeps_error_output(
    monkeypatch, tmp_path, capsys
):
    reports = []
    _configure_run(monkeypatch, tmp_path, reports)
    monkeypatch.setattr(run_mod.settings, "approved_sources_only", True)

    def fail_sources(_self):
        from pipeline.config.models import ConfigError

        raise ConfigError("invalid-sources-secret")

    monkeypatch.setattr(type(run_mod.settings), "load_sources", fail_sources)
    monkeypatch.setattr(
        run_mod,
        "_run_collection",
        lambda _command: pytest.fail("collection must not run after config failure"),
    )

    assert run_mod.main(["--full"]) == 1
    error = capsys.readouterr().err
    assert "[pipeline] config loading failed: invalid-sources-secret" in error
    assert reports[0]["failure_code"] == "configuration_invalid"


def test_main_legacy_data_dir_failure_writes_report_and_keeps_error_output(
    monkeypatch, tmp_path, capsys
):
    reports = []
    _configure_run(monkeypatch, tmp_path, reports)
    project_root = tmp_path / "project"
    monkeypatch.setattr(run_mod.settings, "approved_sources_only", True)
    monkeypatch.setattr(run_mod.settings, "project_root", project_root)
    monkeypatch.setattr(run_mod.settings, "data_dir", project_root / "public" / "data")
    monkeypatch.setattr(type(run_mod.settings), "load_public_source_policy", lambda _self: None)
    monkeypatch.setattr(
        run_mod,
        "_run_collection",
        lambda _command: pytest.fail("collection must not run with the legacy data directory"),
    )

    assert run_mod.main(["--full"]) == 1
    error = capsys.readouterr().err
    assert "approved-source mode requires an isolated data_dir" in error
    assert reports[0]["failure_code"] == "isolated_data_dir_required"


@pytest.mark.parametrize("failure_path", ["configuration", "legacy_data_dir"])
@pytest.mark.parametrize("mode_flag", ["--backfill", "--dry-run"])
def test_main_no_write_early_failures_do_not_add_run_reports(
    failure_path, mode_flag, monkeypatch, tmp_path, capsys
):
    reports = []
    _configure_run(monkeypatch, tmp_path, reports)
    monkeypatch.setattr(
        run_mod.settings, "approved_sources_only", failure_path == "legacy_data_dir"
    )

    if failure_path == "configuration":

        def fail_sources(_self):
            from pipeline.config.models import ConfigError

            raise ConfigError("invalid-sources-secret")

        monkeypatch.setattr(type(run_mod.settings), "load_sources", fail_sources)
    else:
        project_root = tmp_path / "project"
        monkeypatch.setattr(run_mod.settings, "project_root", project_root)
        monkeypatch.setattr(run_mod.settings, "data_dir", project_root / "public" / "data")
        monkeypatch.setattr(type(run_mod.settings), "load_public_source_policy", lambda _self: None)

    result = run_mod.main([mode_flag])
    output = capsys.readouterr()
    if mode_flag == "--dry-run" and failure_path == "legacy_data_dir":
        assert result == 0
        assert "dry-run complete, no files written" in output.out
    else:
        assert result == 1
        if failure_path == "configuration":
            assert "[pipeline] config loading failed: invalid-sources-secret" in output.err
        else:
            assert "approved-source mode requires an isolated data_dir" in output.err
    assert reports == []
