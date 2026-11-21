"""Production collection and validation must work without out-of-band AI output."""

from __future__ import annotations

import json
import subprocess
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import pipeline.run as run_mod
from pipeline.collectors.news import NewsCollector
from pipeline.schemas import (
    NewsDataset,
    NewsItem,
    NewsTranslation,
    NewsTranslationsDataset,
    registry,
)
from pipeline.settings import Settings
from pipeline.storage.writer import StorageWriter
from pipeline.validation.ci_checks import run_all
from tests.pipeline.factories import (
    DEFAULT_NOW,
    PAYLOAD_BUILDERS,
    REMOVE,
    TODAY_ISO_DATE,
    make_macro_roster_payload,
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


def test_legacy_ai_command_is_not_available() -> None:
    with pytest.raises(SystemExit) as error:
        run_mod.build_parser().parse_args(["--analysis-only"])

    assert error.value.code == 2


def test_news_collection_merges_an_existing_translation_sidecar(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    article = NewsItem(
        id="news-1", title="Fed holds rates", source="Example", url="https://example.com/news",
        published_at=DEFAULT_NOW.isoformat().replace("+00:00", "Z"), importance=50, summary="The Fed held rates unchanged.",
    )
    payload = NewsDataset(items=[article], total=1, updated_at=DEFAULT_NOW.isoformat().replace("+00:00", "Z"))

    class Collector:
        def __init__(self, registry, settings):
            pass

        def collect(self):
            return payload, {"degraded": [], "provider": "rss_news", "data_quality": 1.0}

        def merge_translations(self, news, translations):
            return NewsCollector.merge_translations(self, news, translations)


    sidecar = NewsTranslationsDataset(items=[NewsTranslation(
        id="news-1", title_zh="美联储维持利率", summary_zh="美联储维持利率不变。",
    )], updated_at=DEFAULT_NOW.isoformat().replace("+00:00", "Z"))
    StorageWriter(tmp_path).write_standalone("news.zh-translations", sidecar.model_dump(mode="json"))
    monkeypatch.setattr(run_mod, "settings", settings)
    monkeypatch.setattr(run_mod, "build_registry", lambda _settings: object())
    monkeypatch.setattr(run_mod.AssetUniverse, "load", lambda _settings: object())
    monkeypatch.setattr(run_mod, "NewsCollector", Collector)

    results = run_mod._run_collection("news-only")

    assert results["news"].items[0].title_zh == "美联储维持利率"
    assert not results["news_degraded"]
    assert results["news_meta"]["translation_record"]["status"] == "merged"


def test_news_collection_rejects_translation_sidecar_from_an_older_batch(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    article = NewsItem(
        id="news-2", title="Fed holds rates", source="Example", url="https://example.com/news",
        published_at=(DEFAULT_NOW + timedelta(days=1)).isoformat().replace("+00:00", "Z"),
        importance=50, summary="The Fed held rates unchanged.",
    )
    payload = NewsDataset(items=[article], total=1, updated_at=DEFAULT_NOW.isoformat().replace("+00:00", "Z"))

    class Collector:
        def __init__(self, registry, settings):
            pass

        def collect(self):
            return payload, {"degraded": [], "provider": "rss_news", "data_quality": 1.0}

        def merge_translations(self, news, translations):
            return NewsCollector.merge_translations(self, news, translations)

    sidecar = NewsTranslationsDataset(items=[NewsTranslation(
        id="news-2", title_zh="美联储维持利率", summary_zh="美联储维持利率不变。",
    )], updated_at=DEFAULT_NOW.isoformat().replace("+00:00", "Z"))
    StorageWriter(tmp_path).write_standalone("news.zh-translations", sidecar.model_dump(mode="json"))
    monkeypatch.setattr(run_mod, "settings", settings)
    monkeypatch.setattr(run_mod, "build_registry", lambda _settings: object())
    monkeypatch.setattr(run_mod.AssetUniverse, "load", lambda _settings: object())
    monkeypatch.setattr(run_mod, "NewsCollector", Collector)

    results = run_mod._run_collection("news-only")

    assert results["news"].items[0].title_zh is None
    assert results["news_degraded"]
    assert results["news_meta"]["translation_record"]["status"] == "stale"


def test_news_collection_marks_missing_translation_coverage_degraded(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    article = NewsItem(
        id="news-1", title="Fed holds rates", source="Example", url="https://example.com/news",
        published_at=DEFAULT_NOW.isoformat().replace("+00:00", "Z"), importance=50, summary="The Fed held rates unchanged.",
    )
    payload = NewsDataset(items=[article], total=1, updated_at=DEFAULT_NOW.isoformat().replace("+00:00", "Z"))

    class Collector:
        def __init__(self, registry, settings):
            pass

        def collect(self):
            return payload, {"degraded": [], "provider": "rss_news", "data_quality": 1.0}

        def merge_translations(self, news, translations):
            return NewsCollector.merge_translations(self, news, translations)

    monkeypatch.setattr(run_mod, "settings", settings)
    monkeypatch.setattr(run_mod, "build_registry", lambda _settings: object())
    monkeypatch.setattr(run_mod.AssetUniverse, "load", lambda _settings: object())
    monkeypatch.setattr(run_mod, "NewsCollector", Collector)

    results = run_mod._run_collection("news-only")

    assert results["news_degraded"]
    assert results["news_meta"]["error_code"] == "news_translation_incomplete"
    assert results["news_meta"]["translation_record"]["status"] == "skipped"


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

    payloads = {
        key: registry.require(key).model.model_fields["payload"].annotation.model_validate(builder())
        for key, builder in PAYLOAD_BUILDERS.items()
    }
    from pipeline.collectors.macro import DEFAULT_SERIES

    payloads["macro"] = registry.require("macro").model.model_fields["payload"].annotation.model_validate(
        make_macro_roster_payload(include_live_rate=True)
    )
    series_history = {
        series_id.lower(): ([{"date": TODAY_ISO_DATE, "value": 4.25}] if series_id == "DGS10" else [])
        for series_id in DEFAULT_SERIES
    }
    market = {
        key: payloads[key] for key in ("equities", "sectors", "crypto", "commodities")
    }
    market.update(histories={}, degraded=[], provider_status={}, data_quality=1.0)
    monkeypatch.setattr(run_mod, "MarketCollector", lambda *args: SimpleNamespace(collect=lambda: market))
    monkeypatch.setattr(run_mod, "MacroCollector", lambda *args, **kwargs: SimpleNamespace(
        collect=lambda: (payloads["macro"], {
            "degraded": [], "provider_status": {}, "data_quality": 1.0, "series_history": series_history,
        }),
    ))
    monkeypatch.setattr(run_mod, "CalendarCollector", lambda *args: SimpleNamespace(
        collect=lambda: (payloads["calendar"], {"degraded": [], "provider_status": {}, "data_quality": 1.0}),
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
    translations = json.loads((settings.data_dir / "metadata" / "translations.json").read_text(encoding="utf-8"))
    assert translations["last_merge"]["status"] == "skipped"
    assert "bilingual translations cover 0/2" in translations["last_merge"]["reason"]
    for name in LEGACY_FILES:
        assert (latest / name).read_text(encoding="utf-8") == "{"
    for name in ("risk.json", "dashboard.json", "facts.json"):
        assert json.loads((latest / name).read_text(encoding="utf-8"))
    node = subprocess.run(
        ["node", "scripts/validate-json.mjs", "--data-dir", str(settings.data_dir)],
        text=True, capture_output=True, check=False,
    )
    assert node.returncode == 0, node.stdout + node.stderr
    previous_files = {p.relative_to(settings.data_dir): p.read_bytes() for p in settings.data_dir.rglob("*.json")}
    empty_market = {key: type(payloads[key])() for key in ("equities", "sectors", "crypto", "commodities")}
    empty_market.update(histories={}, degraded=["all market sources unavailable"], provider_status={}, data_quality=0.0)
    monkeypatch.setattr(run_mod, "MarketCollector", lambda *args: SimpleNamespace(collect=lambda: empty_market))
    monkeypatch.setattr(run_mod, "MacroCollector", lambda *args: SimpleNamespace(collect=lambda: (
        type(payloads["macro"])(), {"degraded": ["macro unavailable"], "provider_status": {}, "data_quality": 0.0}
    )))
    assert run_mod.main(["--full"]) != 0
    assert {p.relative_to(settings.data_dir): p.read_bytes() for p in settings.data_dir.rglob("*.json")} == previous_files
