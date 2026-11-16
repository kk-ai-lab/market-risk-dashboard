import json

import pytest

from pipeline.legacy_publication import LEGACY_REASON_CODES
from pipeline.publication import PublicationError, publish_snapshot
from scripts.install_cloud_generation import install_generation

REVISION = "a" * 40
STAMP = "2026-11-15T12:00:00Z"


def test_install_replaces_legacy_files_with_current_and_parent_generations(tmp_path):
    store = tmp_path / "store"
    first = publish_snapshot(
        store,
        {
            "latest/risk.json": {
                "generated_at": STAMP,
                "payload": {"score": 50, "dimensions": [], "evidence_state": "complete"},
            },
            "history/risk/daily.json": [],
            "metadata/freshness.json": {"updated_at": STAMP},
        },
        parent_id=None,
        producer_revision=REVISION,
        created_at=STAMP,
        validator=lambda _: True,
    )
    second = publish_snapshot(
        store,
        {
            "latest/risk.json": {
                "generated_at": STAMP,
                "payload": {"score": 51, "dimensions": [], "evidence_state": "complete"},
            },
            "history/risk/daily.json": [],
            "metadata/freshness.json": {"updated_at": STAMP},
        },
        parent_id=first["generation_id"],
        producer_revision=REVISION,
        created_at="2026-11-15T12:15:00Z",
        validator=lambda _: True,
    )
    public_data = tmp_path / "public/data"
    public_data.mkdir(parents=True)
    (public_data / "unlicensed-history.json").write_text("{}", encoding="utf-8")
    current = install_generation(store, public_data)
    assert current == second["generation_id"]
    assert {path.name for path in (public_data / "generations").iterdir()} == {
        first["generation_id"],
        second["generation_id"],
    }
    assert not (public_data / "unlicensed-history.json").exists()
    assert (public_data / "latest/risk.json").read_bytes() == (
        store / "generations" / second["generation_id"] / "latest/risk.json"
    ).read_bytes()


def test_install_preserves_risk_history_index_document(tmp_path):
    store = tmp_path / "store"
    index = {"series": "risk", "updated_at": STAMP, "count": 0}
    publish_snapshot(
        store,
        {
            "latest/risk.json": {"generated_at": STAMP, "payload": {"score": 50}},
            "history/risk/daily.json": [],
            "history/risk/index.json": index,
            "metadata/freshness.json": {"updated_at": STAMP},
        },
        parent_id=None,
        producer_revision=REVISION,
        created_at=STAMP,
        validator=lambda _: True,
    )
    public_data = tmp_path / "public/data"

    install_generation(store, public_data)

    assert json.loads((public_data / "history/risk/index.json").read_text()) == index


def test_install_keeps_legacy_urls_and_projects_new_risk_states(tmp_path):
    store = tmp_path / "store"
    risk = {
        "score": 55,
        "evidence_state": "complete",
        "dimensions": [
            {"key": "volatility", "score": 50, "trend": "rising"},
        ],
    }
    files = {
        "latest/risk.json": {
            "reason": {"code": "source_policy_excluded", "detail": "unlicensed"},
            "payload": risk,
        },
        "latest/dashboard.json": {
            "payload": {"risk": risk},
        },
        "latest/news.json": {
            "reason": {"code": "news_translation_incomplete", "detail": "partial"},
            "payload": {"items": []},
        },
        "history/risk/daily.json": [],
        "metadata/freshness.json": {
            "datasets": {
                "equities": {"reason": {"code": "source_policy_excluded"}}
            }
        },
        "metadata/sources.json": {
            "domains": {
                "market": {"reason": {"code": "source_policy_excluded"}}
            }
        },
    }
    pointer = publish_snapshot(
        store, files, parent_id=None, producer_revision=REVISION,
        created_at=STAMP, validator=lambda _: True,
    )
    public_data = tmp_path / "public/data"
    install_generation(store, public_data)
    current = store / "generations" / pointer["generation_id"]

    legacy_risk = json.loads((public_data / "latest/risk.json").read_bytes())
    legacy_dashboard = json.loads((public_data / "latest/dashboard.json").read_bytes())
    legacy_news = json.loads((public_data / "latest/news.json").read_bytes())
    legacy_freshness = json.loads((public_data / "metadata/freshness.json").read_bytes())
    legacy_sources = json.loads((public_data / "metadata/sources.json").read_bytes())
    assert legacy_risk["payload"]["score"] == 55
    assert [dim["key"] for dim in legacy_risk["payload"]["dimensions"]] == ["volatility"]
    assert legacy_dashboard["payload"]["risk"] == legacy_risk["payload"]
    assert legacy_risk["reason"]["code"] == "not_collected_this_run"
    assert legacy_news["reason"]["code"] == "input_dataset_unhealthy"
    assert legacy_freshness["datasets"]["equities"]["reason"]["code"] == "not_collected_this_run"
    assert legacy_sources["domains"]["market"]["reason"]["code"] == "not_collected_this_run"
    assert set(LEGACY_REASON_CODES.values()) <= {
        "ok", "no_rows_returned", "no_events_in_window", "provider_http_error",
        "provider_rate_limited", "provider_auth_failed", "provider_parse_error",
        "served_from_fallback", "served_from_cache", "cache_expired", "cache_invalid",
        "all_providers_failed", "not_collected_this_run", "interval_exceeded",
        "input_dataset_unhealthy",
    }
    assert json.loads((current / "latest/risk.json").read_bytes())["payload"] == risk


def test_install_withholds_legacy_risk_when_evidence_is_insufficient(tmp_path):
    store = tmp_path / "store"
    files = {
        "latest/risk.json": {
            "payload": {"score": 55, "evidence_state": "insufficient_evidence", "dimensions": []},
        },
        "latest/dashboard.json": {
            "payload": {"risk": {"score": 55, "evidence_state": "insufficient_evidence", "dimensions": []}},
        },
        "history/risk/30d.json": [
            {"date": "2026-11-13", "total_score": 48, "evidence_state": "complete"},
            {"date": "2026-11-14", "total_score": 49, "evidence_state": "partial"},
            {"date": "2026-11-15", "total_score": 55, "evidence_state": "insufficient_evidence"},
        ],
        "metadata/freshness.json": {"updated_at": STAMP},
    }
    publish_snapshot(
        store, files, parent_id=None, producer_revision=REVISION,
        created_at=STAMP, validator=lambda _: True,
    )
    public_data = tmp_path / "public/data"
    install_generation(store, public_data)

    assert not (public_data / "latest/risk.json").exists()
    assert not (public_data / "latest/dashboard.json").exists()
    assert not (public_data / "history/risk/30d.json").exists()


def test_install_withholds_legacy_risk_when_a_complete_score_has_unknown_trend(tmp_path):
    store = tmp_path / "store"
    risk = {
        "score": 55,
        "evidence_state": "complete",
        "dimensions": [{"key": "macro", "score": 55, "trend": "unknown"}],
    }
    publish_snapshot(
        store,
        {
            "latest/risk.json": {"payload": risk},
            "latest/dashboard.json": {"payload": {"risk": risk}},
            "history/risk/30d.json": [],
            "metadata/freshness.json": {"updated_at": STAMP},
        },
        parent_id=None,
        producer_revision=REVISION,
        created_at=STAMP,
        validator=lambda _: True,
    )

    public_data = tmp_path / "public/data"
    install_generation(store, public_data)

    assert not (public_data / "latest/risk.json").exists()
    assert not (public_data / "latest/dashboard.json").exists()


def test_install_withholds_legacy_crypto_when_observation_time_is_unknown(tmp_path):
    store = tmp_path / "store"
    publish_snapshot(
        store,
        {
            "latest/crypto.json": {
                "generated_at": STAMP,
                "payload": {
                    "assets": [{
                        "symbol": "BTC", "name": "Bitcoin", "price": 90000,
                        "source": "coinmetrics", "updated_at": None,
                    }],
                },
            },
            "history/crypto/30d.json": [],
            "metadata/freshness.json": {"updated_at": STAMP},
        },
        parent_id=None,
        producer_revision=REVISION,
        created_at=STAMP,
        validator=lambda _: True,
    )

    public_data = tmp_path / "public/data"
    install_generation(store, public_data)

    assert not (public_data / "latest/crypto.json").exists()


def test_install_rejects_extra_files_in_store(tmp_path):
    store = tmp_path / "store"
    publish_snapshot(
        store,
        {
            "latest/risk.json": {
                "generated_at": STAMP,
                "payload": {"score": 50, "dimensions": []},
            },
            "history/risk/daily.json": [],
            "metadata/freshness.json": {"updated_at": STAMP},
        },
        parent_id=None,
        producer_revision=REVISION,
        created_at=STAMP,
        validator=lambda _: True,
    )
    (store / "legacy.json").write_text("{}", encoding="utf-8")
    with pytest.raises(PublicationError, match="unsupported files"):
        install_generation(store, tmp_path / "public/data")
