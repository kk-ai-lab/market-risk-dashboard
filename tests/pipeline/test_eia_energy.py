from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from pydantic import SecretStr

from pipeline.collectors.energy import EnergyCollector
from pipeline.config.models import PublicSourcePolicy
from pipeline.providers.base import ProviderError
from pipeline.providers.eia import EiaProvider
from pipeline.schemas import EnergyDataset, EnergyEnvelope, EnergyObservation
from pipeline.settings import Settings
from pipeline.storage.writer import StorageWriter
from pipeline.utils import now_utc


class StubRegistry:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def call(self, domain, method, key):
        self.calls.append((domain, method, key))
        if self.error:
            raise self.error
        return self.response


def settings_at(tmp_path, *, api_key="test-eia-key"):
    return Settings(
        _env_file=None,
        eia_api_key=SecretStr(api_key) if api_key is not None else None,
        project_root=tmp_path,
        config_dir=Settings(_env_file=None).config_dir,
        data_dir=tmp_path / "data",
        artifacts_dir=tmp_path / "artifacts",
    )


def previous_envelope(*, week_ending: date | None = None) -> EnergyEnvelope:
    attempt = datetime.now(UTC).replace(microsecond=0)
    week = week_ending or (attempt.date() - timedelta(days=7))
    payload = EnergyDataset(
        observations=[EnergyObservation(week_ending=week, value=420_000.0)],
        collection_status="available",
        observation_age="within_window" if (attempt.date() - week).days <= 14 else "overdue",
        last_attempt_at=attempt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        last_successful_fetch_at=(attempt - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    return EnergyEnvelope(
        generated_at=attempt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        schema_version="1.0.0",
        source="eia",
        freshness_status="fresh",
        data_quality=1.0,
        provenance={"provider": "eia", "used_fallback": False, "from_cache": False},
        payload=payload,
    )


def test_energy_policy_grants_only_the_public_wce_series(tmp_path):
    settings = settings_at(tmp_path)
    policy = settings.load_public_source_policy()
    grant = policy.collection_plan().energy_grant_for("WCESTUS1")

    assert grant is not None
    assert grant.datasets == frozenset({"energy"})
    assert grant.energy_series == frozenset({"WCESTUS1"})
    assert grant.permitted_use == "public-domain"

    raw = policy.model_dump()
    raw["sources"]["eia_weekly_crude_inventory"]["energy_series"] = ["WCESTUS1", "WCESTUS2"]
    with pytest.raises(ValueError, match="unsupported"):
        PublicSourcePolicy.model_validate(raw)


def test_eia_provider_returns_only_sanitized_observations_even_when_response_echoes_key(tmp_path):
    api_key = "never-publish-this-eia-key"
    provider = EiaProvider(settings_at(tmp_path, api_key=api_key))
    captured = {}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "response": {"data": [{"period": "2026-11-06", "value": "417000"}]},
                "request": {"params": {"api_key": api_key}},
            }

    def fake_get(url, *, params):
        captured.update(url=url, params=params)
        return Response()

    provider._client.get = fake_get
    rows = provider.get_weekly_inventory()

    assert captured["url"] == "https://api.eia.gov/v2/seriesid/WCESTUS1"
    assert captured["params"]["api_key"] == api_key
    assert rows == [{"date": "2026-11-06", "value": 417000.0}]
    assert api_key not in repr(rows)


def test_eia_provider_does_not_put_key_or_response_body_in_provider_error(tmp_path):
    api_key = "never-publish-this-eia-key"
    provider = EiaProvider(settings_at(tmp_path, api_key=api_key))

    class Response:
        status_code = 500
        request = None

    provider._client.get = lambda *_args, **_kwargs: Response()
    with pytest.raises(ProviderError) as error:
        provider.get_weekly_inventory()

    assert api_key not in str(error.value)
    assert "response" not in str(error.value).lower()


def test_missing_key_publishes_not_configured_without_provider_request(tmp_path):
    registry = StubRegistry()
    result = EnergyCollector(registry, settings_at(tmp_path, api_key=None)).collect(attempted_at=now_utc())

    assert result["payload"].collection_status == "not_configured"
    assert result["payload"].last_attempt_at
    assert result["payload"].last_successful_fetch_at is None
    assert result["payload"].observations == []
    assert registry.calls == []


def test_collector_caps_history_sorts_weeks_and_keeps_attempt_times_distinct(tmp_path):
    today = date.today()
    rows = [
        {"date": (today - timedelta(weeks=index)).isoformat(), "value": 500_000 - index}
        for index in range(265)
    ]
    registry = StubRegistry(
        response={"result": rows, "meta": {"provider": "eia", "from_cache": False, "degraded": False}}
    )
    attempt = now_utc()

    result = EnergyCollector(registry, settings_at(tmp_path)).collect(attempted_at=attempt)
    payload = result["payload"]

    assert len(payload.observations) == 260
    assert [row.week_ending for row in payload.observations] == sorted(row.week_ending for row in payload.observations)
    assert payload.observations[-1].week_ending == today
    assert payload.collection_status == "available"
    assert payload.last_attempt_at == attempt
    assert payload.last_successful_fetch_at == attempt
    assert payload.observation_age == "within_window"


def test_provider_failure_preserves_only_valid_prior_energy_payload(tmp_path):
    previous = previous_envelope()
    registry = StubRegistry(error=ProviderError("upstream unavailable"))
    attempt = now_utc()

    result = EnergyCollector(registry, settings_at(tmp_path)).collect(previous.model_dump(mode="json"), attempted_at=attempt)
    payload = result["payload"]

    assert payload.collection_status == "degraded"
    assert payload.observations == previous.payload.observations
    assert payload.last_successful_fetch_at == previous.payload.last_successful_fetch_at
    assert payload.last_attempt_at == attempt
    assert result["degraded"] is True


def test_invalid_old_contract_is_not_reused_on_provider_failure(tmp_path):
    previous = previous_envelope().model_dump(mode="json")
    previous["payload"]["observation_age"] = "overdue"
    result = EnergyCollector(
        StubRegistry(error=ProviderError("upstream unavailable")), settings_at(tmp_path)
    ).collect(previous)

    assert result["payload"].collection_status == "degraded"
    assert result["payload"].observations == []
    assert result["payload"].last_successful_fetch_at is None


def test_optional_energy_health_does_not_fail_full_run(tmp_path):
    from pipeline.run import FULL_RUN_DATASETS, dataset_health

    writer = StorageWriter(tmp_path / "data")
    now = "2026-11-16T15:00:00Z"
    writer.write_freshness_metadata(
        {
            "schema_version": "1.0.0",
            "updated_at": now,
            "datasets": {
                name: {
                    "status": "missing" if name == "energy" else "fresh",
                    "reason": {"code": "provider_auth_failed", "detail": "not configured"},
                    "updated_at": now,
                }
                for name in FULL_RUN_DATASETS
            },
        }
    )

    health = dataset_health(writer, "full", run_started_at="2026-11-16T14:00:00Z")

    assert health["failed"] == []
    assert health["degraded"] == ["energy"]
    assert health["skipped"] == []


@pytest.mark.parametrize("api_key", ["eia-secret-value", None])
def test_cloud_refresh_forwards_eia_secret_only_to_collection_child(tmp_path, monkeypatch, api_key):
    import pipeline.cloud_refresh as cloud_refresh

    settings = Settings(
        _env_file=None,
        eia_api_key=SecretStr(api_key) if api_key else None,
        cloud_state_repo="owner/repo",
        cloud_state_token=SecretStr("cloud-state-token"),
        project_root=tmp_path,
        config_dir=Settings(_env_file=None).config_dir,
        data_dir=tmp_path / "data",
        artifacts_dir=tmp_path / "artifacts",
    )
    captured = {}

    def fake_run(*args, **kwargs):
        captured.update(args=args, kwargs=kwargs)
        return type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(cloud_refresh.subprocess, "run", fake_run)
    cloud_refresh._collect_approved(settings)
    child_env = captured["kwargs"]["env"]
    assert ("DATA_EIA_API_KEY" in child_env) is bool(api_key)
    if api_key:
        assert child_env["DATA_EIA_API_KEY"] == api_key
        assert api_key not in repr(captured["args"])
    assert child_env["DATA_CLOUD_STATE_TOKEN"] == "cloud-state-token"


def test_actions_exposes_eia_secret_only_to_the_collection_step():
    workflow_path = Path(__file__).parents[2] / ".github/workflows/deploy-pages.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    matches = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if "DATA_EIA_API_KEY" in step.get("env", {})
    ]

    assert len(matches) == 1
    assert matches[0]["name"] == "Collect and validate approved sources"
    assert matches[0]["env"]["DATA_EIA_API_KEY"] == "${{ secrets.EIA_API_KEY }}"


def test_energy_is_not_an_input_to_risk_context_or_score_coverage():
    import inspect

    from pipeline.risk_context import build_risk_context

    assert "energy" not in inspect.signature(build_risk_context).parameters
