import json

import pytest

import scripts.stage_publication as staging
from pipeline.collectors.macro import SERIES_GROUPS
from pipeline.publication import PublicationError, read_current
from pipeline.validation.ci_checks import run_all
from scripts.stage_publication import stage_candidate
from tests.pipeline.factories import DEFAULT_NOW, REMOVE
from tests.pipeline.test_data_only import LEGACY_FILES, _publish_production_metadata


@pytest.fixture(autouse=True)
def freeze_validation_clock(monkeypatch):
    monkeypatch.setattr(staging, "run_all", lambda path: run_all(path, now=DEFAULT_NOW))


def candidate(make_data_dir):
    bundles = {f"history/macro/{group}.{slice_name}.json": {} for group in SERIES_GROUPS for slice_name in ("30d", "90d")}
    root = make_data_dir(latest={name: REMOVE for name in LEGACY_FILES}, support=bundles)
    _publish_production_metadata(root)
    return root


def stage(source, store, parent=None):
    return stage_candidate(source, store, parent_id=parent, revision="fixture", created_at="2026-11-14T12:00:00Z")


def test_real_validators_accept_complete_synthetic_snapshot(make_data_dir, tmp_path):
    source = candidate(make_data_dir)
    store = tmp_path / "store"
    first = stage(source, store)
    assert read_current(store) == first
    assert stage(source, store) == first
    generation = store / "generations" / first["generation_id"]
    assert (generation / "history" / "risk" / "30d.json").is_file()
    assert not (generation / "feeds").exists()


def test_missing_client_file_preserves_existing_pointer(make_data_dir, tmp_path):
    source = candidate(make_data_dir)
    store = tmp_path / "store"
    first = stage(source, store)
    (source / "history" / "macro" / "rates.30d.json").unlink()
    with pytest.raises(PublicationError) as error:
        stage(source, store, first["generation_id"])
    assert str(error.value) == "required_client_files_missing"
    assert read_current(store) == first


def test_missing_macro_manifest_preserves_existing_pointer(make_data_dir, tmp_path):
    source = candidate(make_data_dir)
    store = tmp_path / "store"
    first = stage(source, store)
    (source / "history" / "macro" / "index.json").unlink()

    with pytest.raises(PublicationError):
        stage(source, store, first["generation_id"])

    assert read_current(store) == first


def test_macro_manifest_count_mismatch_preserves_existing_pointer(make_data_dir, tmp_path):
    source = candidate(make_data_dir)
    store = tmp_path / "store"
    first = stage(source, store)
    manifest_path = source / "history" / "macro" / "index.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["series"]["DGS10"]["count"] = 1
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(PublicationError):
        stage(source, store, first["generation_id"])

    assert read_current(store) == first


def test_zero_count_macro_series_rejects_bundle_rows_and_preserves_pointer(make_data_dir, tmp_path):
    source = candidate(make_data_dir)
    store = tmp_path / "store"
    first = stage(source, store)
    bundle_path = source / "history" / "macro" / "rates.30d.json"
    bundle_path.write_text(json.dumps({"DGS10": {"d": ["2026-11-14"], "v": [4.25]}}))

    with pytest.raises(PublicationError):
        stage(source, store, first["generation_id"])

    assert read_current(store) == first


def test_non_null_latest_macro_value_rejects_zero_count_and_preserves_pointer(make_data_dir, tmp_path):
    source = candidate(make_data_dir)
    store = tmp_path / "store"
    first = stage(source, store)
    macro_path = source / "latest" / "macro.json"
    macro = json.loads(macro_path.read_text())
    macro["payload"]["rates"][0]["value"] = 4.25
    macro["payload"]["rates"][0]["updated_at"] = "2026-11-14T12:00:00Z"
    macro_path.write_text(json.dumps(macro))

    with pytest.raises(PublicationError):
        stage(source, store, first["generation_id"])

    assert read_current(store) == first


def test_retained_macro_history_is_valid_when_latest_value_is_missing(make_data_dir, tmp_path):
    source = candidate(make_data_dir)
    manifest_path = source / "history" / "macro" / "index.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["series"]["DGS10"].update(
        count=1,
        last_observation="2026-11-14",
        next_expected_release="2026-11-16",
    )
    manifest_path.write_text(json.dumps(manifest))
    for slice_name in ("30d", "90d"):
        bundle_path = source / "history" / "macro" / f"rates.{slice_name}.json"
        bundle_path.write_text(json.dumps({"DGS10": {"d": ["2026-11-14"], "v": [4.25]}}))

    pointer = stage(source, tmp_path / "store")

    assert read_current(tmp_path / "store") == pointer


def test_unknown_macro_manifest_series_preserves_existing_pointer(make_data_dir, tmp_path):
    source = candidate(make_data_dir)
    store = tmp_path / "store"
    first = stage(source, store)
    manifest_path = source / "history" / "macro" / "index.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["series"]["EFFR"] = {
        "group": "rates",
        "last_observation": None,
        "next_expected_release": None,
        "count": 0,
    }
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(PublicationError):
        stage(source, store, first["generation_id"])

    assert read_current(store) == first


def test_malformed_macro_bundle_preserves_existing_pointer(make_data_dir, tmp_path):
    source = candidate(make_data_dir)
    store = tmp_path / "store"
    first = stage(source, store)
    bundle_path = source / "history" / "macro" / "rates.90d.json"
    bundle_path.write_text(json.dumps({"DGS10": {"d": ["2026-11-14"], "v": []}}))

    with pytest.raises(PublicationError):
        stage(source, store, first["generation_id"])

    assert read_current(store) == first


def test_invalid_dataset_does_not_publish(make_data_dir, tmp_path):
    source = candidate(make_data_dir)
    store = tmp_path / "store"
    first = stage(source, store)
    risk_path = source / "latest" / "risk.json"
    risk = json.loads(risk_path.read_text())
    risk["payload"]["total_score"] = 101
    risk_path.write_text(json.dumps(risk))
    with pytest.raises(PublicationError):
        stage(source, store, first["generation_id"])
    assert read_current(store) == first


def test_candidate_symbolic_link_is_rejected(make_data_dir, tmp_path):
    source = candidate(make_data_dir)
    (source / "history" / "external.json").symlink_to(source / "latest" / "risk.json")
    with pytest.raises(PublicationError):
        stage(source, tmp_path / "store")


def test_risk_history_missing_score_fails_frontend_contract_before_publish(make_data_dir, tmp_path):
    source = candidate(make_data_dir)
    for name in ("daily", "30d", "90d"):
        path = source / "history" / "risk" / f"{name}.json"
        rows = json.loads(path.read_text())
        del rows[-1]["total_score"]
        path.write_text(json.dumps(rows))
    assert run_all(source, now=DEFAULT_NOW).ok
    with pytest.raises(PublicationError):
        stage(source, tmp_path / "store")
    assert not (tmp_path / "store" / "current.json").exists()


def test_risk_history_rejects_scores_without_matching_dimension_evidence(make_data_dir, tmp_path):
    source = candidate(make_data_dir)
    path = source / "history" / "risk" / "daily.json"
    rows = json.loads(path.read_text())
    rows[-1]["dim_scores"] = {"macro": 42}
    rows[-1]["dim_evidence_states"] = {"macro": "insufficient_evidence"}
    path.write_text(json.dumps(rows))
    with pytest.raises(PublicationError):
        stage(source, tmp_path / "store")
    assert not (tmp_path / "store" / "current.json").exists()


def test_mismatched_risk_and_dashboard_preserve_current_generation(make_data_dir, tmp_path):
    source = candidate(make_data_dir)
    store = tmp_path / "store"
    first = stage(source, store)
    dashboard_path = source / "latest" / "dashboard.json"
    dashboard = json.loads(dashboard_path.read_text())
    dashboard["payload"]["risk"]["total_score"] = 50
    dashboard_path.write_text(json.dumps(dashboard))
    assert run_all(source, now=DEFAULT_NOW).ok
    with pytest.raises(PublicationError):
        stage(source, store, first["generation_id"])
    assert read_current(store) == first
