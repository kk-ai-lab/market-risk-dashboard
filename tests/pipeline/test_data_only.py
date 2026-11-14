"""Production collection and validation must work without out-of-band AI output."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import pipeline.analysis_pair as legacy_ai
import pipeline.run as run_mod
from pipeline.schemas import registry
from pipeline.settings import Settings
from pipeline.validation.ci_checks import run_all
from tests.pipeline.factories import (
    DEFAULT_NOW,
    PAYLOAD_BUILDERS,
    REMOVE,
)

LEGACY_FILES = tuple(
    filename
    for key in registry.LEGACY_AI_DATASET_KEYS
    for filename in registry.require(key).filenames
)


def _publish_production_metadata(root: Path) -> None:
    outcomes = run_mod.RunOutcomes(scope=run_mod._run_scope("full"))
    for key in run_mod.FULL_RUN_DATASETS:
        outcomes.record(key, "fresh", run_mod.FreshnessReason(code="ok"), provider="test")
    run_mod._publish_metadata(run_mod.StorageWriter(root), outcomes, {})


@pytest.mark.parametrize("legacy_state", ["missing", "malformed"])
def test_production_validators_do_not_require_legacy_ai(make_data_dir, legacy_state: str) -> None:
    root = make_data_dir(latest={name: REMOVE for name in LEGACY_FILES})
    _publish_production_metadata(root)
    if legacy_state == "malformed":
        for name in LEGACY_FILES:
            (root / "latest" / name).write_text("{", encoding="utf-8")

    report = run_all(root, now=DEFAULT_NOW)

    assert report.ok, report.errors
    assert report.warnings == []
    node = subprocess.run(
        ["node", "scripts/validate-json.mjs", "--data-dir", str(root)],
        text=True,
        capture_output=True,
        check=False,
    )
    assert node.returncode == 0, node.stdout + node.stderr


@pytest.mark.parametrize("key", sorted(set(registry.CANONICAL_KEYS) - registry.LEGACY_AI_DATASET_KEYS))
def test_node_gate_still_requires_each_production_metadata_entry(make_data_dir, key: str) -> None:
    root = make_data_dir()
    _publish_production_metadata(root)
    freshness_path = root / "metadata" / "freshness.json"
    freshness = json.loads(freshness_path.read_text(encoding="utf-8"))
    freshness["datasets"].pop(key)
    freshness_path.write_text(json.dumps(freshness), encoding="utf-8")

    node = subprocess.run(
        ["node", "scripts/validate-json.mjs", "--data-dir", str(root)],
        text=True, capture_output=True, check=False,
    )
    assert node.returncode == 1, node.stdout + node.stderr
    assert f"registered dataset {key} has no entry" in node.stdout


def test_default_validation_rejects_unknown_analysis_language(make_data_dir) -> None:
    root = make_data_dir()
    (root / "latest" / "analysis.fr.json").write_text("{}", encoding="utf-8")

    report = run_all(root, now=DEFAULT_NOW)

    assert any("analysis.fr.json: unknown language key" in issue for issue in report.errors)


def test_full_collection_keeps_source_news_without_ai(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", artifacts_dir=tmp_path / "artifacts")
    monkeypatch.setattr(run_mod, "settings", settings)
    originals = json.loads((Path(__file__).parents[1] / "fixtures" / "news-originals.json").read_text(encoding="utf-8"))

    class NewsRegistry:
        def call(self, *args, **kwargs):
            return {"result": originals, "meta": {"provider": "rss_news"}}

        def providers_for(self, domain):
            return []

    monkeypatch.setattr(run_mod, "build_registry", lambda _settings: NewsRegistry())

    def forbid_ai(*args, **kwargs):
        pytest.fail("production collection must not consume AI output")

    monkeypatch.setattr(legacy_ai, "record_ai_outcomes", forbid_ai)
    monkeypatch.setattr(run_mod, "_merge_news_translations", forbid_ai)
    payloads = {
        key: registry.require(key).model.model_fields["payload"].annotation.model_validate(builder())
        for key, builder in PAYLOAD_BUILDERS.items()
    }
    market = {
        key: payloads[key] for key in ("equities", "sectors", "crypto", "commodities")
    }
    market.update(histories={}, degraded=[], provider_status={}, data_quality=1.0)
    monkeypatch.setattr(run_mod, "MarketCollector", lambda *args: SimpleNamespace(collect=lambda: market))
    for name, key in (("MacroCollector", "macro"), ("CalendarCollector", "calendar")):
        monkeypatch.setattr(run_mod, name, lambda *args, key=key: SimpleNamespace(
            collect=lambda: (payloads[key], {"degraded": [], "provider_status": {}, "data_quality": 1.0}),
        ))

    latest = settings.data_dir / "latest"
    latest.mkdir(parents=True)
    for name in LEGACY_FILES:
        (latest / name).write_text("{", encoding="utf-8")

    assert run_mod.main(["--full"]) == 0
    for command in ("macro-only", "news-only", "fact-layer"):
        assert run_mod.main([f"--{command}"]) == 0

    published_news = json.loads((latest / "news.json").read_text(encoding="utf-8"))
    by_url = {item["url"]: item for item in published_news["payload"]["items"]}
    assert set(by_url) == {item["url"] for item in originals}
    for original in originals:
        assert {key: by_url[original["url"]][key] for key in original} == original
    freshness = json.loads((settings.data_dir / "metadata" / "freshness.json").read_text(encoding="utf-8"))
    assert not registry.LEGACY_AI_DATASET_KEYS.intersection(freshness["datasets"])
    assert not (settings.data_dir / "metadata" / "translations.json").exists()
    for name in LEGACY_FILES:
        assert (latest / name).read_text(encoding="utf-8") == "{"
    for name in ("risk.json", "dashboard.json", "facts.json"):
        assert json.loads((latest / name).read_text(encoding="utf-8"))
    node = subprocess.run(
        ["node", "scripts/validate-json.mjs", "--data-dir", str(settings.data_dir)],
        text=True, capture_output=True, check=False,
    )
    assert node.returncode == 0, node.stdout + node.stderr
