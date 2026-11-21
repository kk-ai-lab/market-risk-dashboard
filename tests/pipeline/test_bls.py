import httpx
import pytest

from pipeline.providers.base import GuardedClient, ProviderError
from pipeline.providers.bls import BLS_API, SERIES, BlsProvider
from pipeline.settings import Settings


def observation(year, month, value):
    return {"year": str(year), "period": f"M{month:02}", "value": str(value)}


def provider(payload=None, status=200):
    instance = BlsProvider(Settings(_env_file=None))
    instance._client.close()
    calls = []

    def respond(request):
        import json

        calls.append(json.loads(request.content))
        return httpx.Response(status, json=payload, request=request)

    instance._client = GuardedClient({"api.bls.gov"}, transport=httpx.MockTransport(respond))
    return instance, calls


def payload(series):
    return {"status": "REQUEST_SUCCEEDED", "Results": {"series": [
        {"seriesID": key, "data": rows} for key, rows in series.items()
    ]}}


def test_one_keyless_batch_six_years_and_transforms():
    instance, calls = provider(payload({
        SERIES["CPIAUCSL"]: [observation(2026, 1, 110), observation(2025, 1, 100)],
        SERIES["CPILFESL"]: [observation(2025, 1, 200), observation(2026, 1, 210)],
        SERIES["UNRATE"]: [observation(2026, 2, 4.1), observation(2026, 1, 4)],
        SERIES["PAYEMS"]: [observation(2026, 1, 100100), observation(2025, 12, 100000)],
    }))
    rows = instance.get_series_batch(end_year=2026)
    assert calls == [{"seriesid": list(SERIES.values()), "startyear": "2021", "endyear": "2026"}]
    assert rows["CPIAUCSL"] == [{"date": "2026-01-01", "value": pytest.approx(10)}]
    assert rows["CPILFESL"][0]["value"] == pytest.approx(5)
    assert rows["PAYEMS"] == [{"date": "2026-01-01", "value": 100}]
    assert rows["UNRATE"] == [{"date": "2026-01-01", "value": 4}, {"date": "2026-02-01", "value": 4.1}]
    assert instance.domain == "bls"


def test_missing_months_never_bridge_and_invalid_values_are_skipped():
    instance, _ = provider(payload({
        SERIES["CPIAUCSL"]: [observation(2025, 1, 0), observation(2026, 1, 100), observation(2026, 2, 105)],
        SERIES["PAYEMS"]: [observation(2026, 1, 100), observation(2026, 3, 120)],
        SERIES["UNRATE"]: [observation(2026, 13, 8), observation(2026, 1, "NaN"),
                           observation(2026, 2, "inf"), observation(2026, 3, "oops"),
                           observation(2026, 4, 4.2), {"year": "oops", "period": "M05", "value": "2"}],
    }))
    rows = instance.get_series_batch(["CPIAUCSL", "PAYEMS", "UNRATE"], end_year=2026)
    assert rows == {"CPIAUCSL": [], "PAYEMS": [], "UNRATE": [{"date": "2026-04-01", "value": 4.2}]}


@pytest.mark.parametrize("body", [
    {"status": "REQUEST_FAILED", "message": ["secret"]},
    {"status": "REQUEST_SUCCEEDED", "Results": None},
    payload({}), payload({"LNS14000000": []}), [],
])
def test_http_200_failures(body):
    instance, _ = provider(body)
    with pytest.raises(ProviderError) as error:
        instance.get_series_batch(["UNRATE"])
    assert "secret" not in str(error.value)


@pytest.mark.parametrize("status,classification", [(429, "rate_limited"), (500, "transient"), (403, "permanent")])
def test_http_errors_classified(status, classification):
    instance, _ = provider({}, status=status)
    with pytest.raises(ProviderError) as error:
        instance.get_series_batch(["UNRATE"])
    assert error.value.cls == classification


def test_batch_limit_and_unknown_series_fail_without_request():
    instance, calls = provider()
    for ids in ([], [f"series-{i}" for i in range(26)]):
        with pytest.raises(ProviderError):
            instance.get_raw_series_batch(ids)
    with pytest.raises(ProviderError):
        instance.get_series_batch(["PCEPILFE"])
    assert calls == []


def test_raw_batch_accepts_25_and_sorts():
    ids = [f"series-{i}" for i in range(25)]
    instance, calls = provider(payload({key: [observation(2026, 2, 2), observation(2026, 1, 1)] for key in ids}))
    result = instance.get_raw_series_batch(ids, end_year=2026)
    assert len(calls) == 1
    assert len(result) == 25
    assert result[ids[0]][0] == {"date": "2026-01-01", "value": 1}
    assert str(instance._client.base_url) == ""
    assert BLS_API.startswith("https://api.bls.gov/")


def test_transport_and_json_failures_are_provider_errors():
    instance, _ = provider()

    def timeout(request):
        raise httpx.ReadTimeout("timeout", request=request)

    instance._client.close()
    instance._client = GuardedClient({"api.bls.gov"}, transport=httpx.MockTransport(timeout))
    with pytest.raises(ProviderError) as error:
        instance.get_series_batch(["UNRATE"])
    assert error.value.cls == "transient"
    instance._client.close()
    instance._client = GuardedClient({"api.bls.gov"}, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text="not json", request=request),
    ))
    with pytest.raises(ProviderError):
        instance.get_series_batch(["UNRATE"])


def test_health_reports_empty_or_success():
    instance, _ = provider(payload({SERIES["UNRATE"]: [observation(2026, 1, 4)]}))
    assert instance.health().ok
    instance, _ = provider(payload({}))
    assert not instance.health().ok


def test_duplicate_months_fail_instead_of_selecting_an_arbitrary_value():
    instance, _ = provider(payload({SERIES["UNRATE"]: [
        observation(2026, 1, 4), observation(2026, 1, 5),
    ]}))
    with pytest.raises(ProviderError, match="duplicate observation"):
        instance.get_series_batch(["UNRATE"])


def test_future_observation_month_is_not_returned():
    from datetime import UTC, datetime

    current = datetime.now(UTC)
    instance, _ = provider(payload({SERIES["UNRATE"]: [
        observation(current.year + 1, 1, 9), observation(current.year, current.month, 4),
    ]}))
    rows = instance.get_series_batch(["UNRATE"], end_year=current.year + 1)
    assert [row["value"] for row in rows["UNRATE"]] == [4]


def test_post_rejects_large_decoded_response():
    import gzip

    from pipeline.providers.base import MAX_RESPONSE_BYTES

    instance = BlsProvider(Settings(_env_file=None))
    instance._client.close()
    body = gzip.compress(b"x" * (MAX_RESPONSE_BYTES + 1))
    instance._client = GuardedClient({"api.bls.gov"}, transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=body, headers={"content-encoding": "gzip"}, request=request)
    ))
    with pytest.raises(ProviderError, match="size limit"):
        instance.get_series_batch(["UNRATE"])


def test_future_month_within_current_year_is_not_returned(monkeypatch):
    from datetime import UTC, datetime

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 14, tzinfo=UTC)

    monkeypatch.setattr("pipeline.providers.bls.datetime", FixedDateTime)
    instance, _ = provider(payload({SERIES["UNRATE"]: [observation(2026, 12, 9), observation(2026, 11, 4)]}))
    assert instance.get_series_batch(["UNRATE"], end_year=2026)["UNRATE"] == [{"date": "2026-11-01", "value": 4.0}]


def test_bls_does_not_follow_redirects():
    instance = BlsProvider(Settings(_env_file=None))
    instance._client.close()
    calls = []

    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://other.example/data"}, request=request)

    instance._client = GuardedClient({"api.bls.gov"}, transport=httpx.MockTransport(respond))
    with pytest.raises(ProviderError):
        instance.get_series_batch(["UNRATE"])
    assert calls == [BLS_API]


def test_partial_batch_keeps_available_series_and_marks_missing_empty():
    instance, _ = provider(payload({SERIES["UNRATE"]: [observation(2026, 10, 4.1)]}))
    result = instance.get_series_batch(["UNRATE", "PAYEMS"], end_year=2026)
    assert result["UNRATE"] == [{"date": "2026-10-01", "value": 4.1}]
    assert result["PAYEMS"] == []
