import httpx
import pytest

from pipeline.providers.base import GuardedClient, ProviderError
from pipeline.providers.treasury import TreasuryProvider
from pipeline.settings import Settings


def feed(*rows):
    entries = "".join('<entry><content><m:properties>' + row + '</m:properties></content></entry>' for row in rows)
    return ('<feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata" '
            'xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices">'
            '<title type="text">DailyTreasuryYieldCurveRateData</title>'
            '<id>https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml-item?data=daily_treasury_yield_curve</id>'
            '<updated>2026-12-31T23:00:00Z</updated>'
            '<link rel="self" title="DailyTreasuryYieldCurveRateData" href="DailyTreasuryYieldCurveRateData" />'
            + entries + '</feed>').encode()


def row(day="2025-01-02", two="4.2", ten="4.4"):
    return f'<d:NEW_DATE>{day}T00:00:00</d:NEW_DATE><d:BC_2YEAR>{two}</d:BC_2YEAR><d:BC_10YEAR>{ten}</d:BC_10YEAR>'


def provider(body, status=200, headers=None):
    instance = TreasuryProvider(Settings(_env_file=None))
    instance._client.close()
    calls = []

    def respond(request):
        calls.append(request)
        return httpx.Response(status, content=body, headers=headers, request=request)

    instance._client = GuardedClient({"home.treasury.gov"}, transport=httpx.MockTransport(respond))
    return instance, calls


def test_daily_percent_sorted_with_separate_provenance():
    instance, calls = provider(feed(row("2025-01-03"), row()))
    result = instance.get_yield_curve(year=2025)
    assert result["series"]["DGS2"] == [{"date": "2025-01-02", "value": 4.2}, {"date": "2025-01-03", "value": 4.2}]
    assert result["series"]["DGS10"][0]["value"] == 4.4
    assert result["unit"] == "percent"
    assert result["date_semantics"] == "observation_date"
    assert result["checked_at"].endswith("+00:00")
    assert "data=daily_treasury_yield_curve" in result["source"]
    assert dict(calls[0].url.params) == {"data": "daily_treasury_yield_curve", "field_tdr_date_value": "2025"}
    assert str(calls[0].url.copy_with(query=None)) == (
        "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
    )
    assert "authorization" not in calls[0].headers


def test_missing_values_omit_only_affected_maturity():
    instance, _ = provider(feed(row(two="NaN"), row("2025-01-03", ten="")))
    result = instance.get_yield_curve(year=2025)["series"]
    assert result["DGS2"] == [{"date": "2025-01-03", "value": 4.2}]
    assert result["DGS10"] == [{"date": "2025-01-02", "value": 4.4}]


@pytest.mark.parametrize("body", [feed(), b"bad xml", b"<html/>", feed(row(), row()),
    feed(row("2024-01-02")), feed(row("2025-02-30")), feed(row("9999-01-01")),
    feed(row(two="inf")), feed(row().replace('<d:BC_2YEAR>4.2</d:BC_2YEAR>', '')),
    feed(row().replace('</d:NEW_DATE>', '</d:NEW_DATE><d:NEW_DATE>2025-01-02</d:NEW_DATE>')),
    b'<!DOCTYPE feed [<!ENTITY x SYSTEM "file:///etc/passwd">]><feed>&x;</feed>',
    '<!DOCTYPE feed [<!ENTITY x "boom">]><feed>&x;</feed>'.encode("utf-16"),
])
def test_rejects_untrustworthy_xml(body):
    instance, _ = provider(body)
    with pytest.raises(ProviderError):
        instance.get_yield_curve(year=2025)


@pytest.mark.parametrize("status", [204, 301, 302, 403, 429, 500])
def test_non_200_never_followed(status):
    instance, calls = provider(b"", status, {"location": "https://evil.test/"})
    with pytest.raises(ProviderError):
        instance.get_yield_curve(year=2025)
    assert len(calls) == 1


@pytest.mark.parametrize("year", [True, "2025", 1989, 9999])
def test_invalid_year_never_requests(year):
    instance, calls = provider(feed(row()))
    with pytest.raises(ProviderError):
        instance.get_yield_curve(year=year)
    assert not calls


def test_bounded_decoded_body_and_declared_size(monkeypatch):
    monkeypatch.setattr("pipeline.providers.treasury.MAX_RESPONSE_BYTES", 10)
    instance, _ = provider(feed(row()))
    with pytest.raises(ProviderError, match="size limit"):
        instance.get_yield_curve(year=2025)
    instance, _ = provider(b"", headers={"content-length": "99999999"})
    with pytest.raises(ProviderError, match="exceeds"):
        instance.get_yield_curve(year=2025)


def test_host_guard_blocks_before_transport():
    instance, calls = provider(feed(row()))
    with pytest.raises(ProviderError, match="allowlist"):
        instance._client.get("https://evil.test/")
    assert not calls


@pytest.fixture
def new_year(monkeypatch):
    from datetime import UTC, datetime

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2027, 1, 1, 12, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr("pipeline.providers.treasury.datetime", FrozenDatetime)


def year_provider(payloads):
    instance, calls = provider(b"")
    instance._client.close()

    def respond(request):
        year = request.url.params["field_tdr_date_value"]
        calls.append(request)
        body = payloads[year]
        if isinstance(body, Exception):
            raise body
        return httpx.Response(200, content=body, request=request)

    instance._client = GuardedClient({"home.treasury.gov"}, transport=httpx.MockTransport(respond))
    return instance, calls


def test_default_empty_new_year_uses_previous_year_provenance(new_year):
    instance, calls = year_provider({"2027": feed(), "2026": feed(row("2026-12-31"))})
    result = instance.get_yield_curve()
    assert [request.url.params["field_tdr_date_value"] for request in calls] == ["2027", "2026"]
    assert result["year"] == 2026
    assert "field_tdr_date_value=2026" in result["source"]
    assert result["checked_at"] == "2027-01-01T12:00:00+00:00"
    assert result["series"]["DGS2"] == [{"date": "2026-12-31", "value": 4.2}]


def test_explicit_empty_new_year_does_not_fallback(new_year):
    instance, calls = year_provider({"2027": feed()})
    with pytest.raises(ProviderError, match="missing or empty"):
        instance.get_yield_curve(year=2027)
    assert len(calls) == 1


@pytest.mark.parametrize("body", [b"bad xml", feed(row("2027-01-01", two="NaN")),
    feed(row("2027-01-02")), feed(row("2027-01-01"), row("2027-01-01")),
    feed('<entry xmlns="wrong"/>'), httpx.ConnectError("offline")])
def test_default_new_year_errors_are_not_masked(new_year, body):
    instance, calls = year_provider({"2027": body})
    with pytest.raises(ProviderError):
        instance.get_yield_curve()
    assert len(calls) == 1


def test_default_populated_current_year_stays_in_year(new_year):
    instance, calls = year_provider({"2027": feed(row("2027-01-01"))})
    assert instance.get_yield_curve()["year"] == 2027
    assert len(calls) == 1


def test_default_empty_both_years_stops_after_previous_year(new_year):
    instance, calls = year_provider({"2027": feed(), "2026": feed()})
    with pytest.raises(ProviderError, match="missing or empty"):
        instance.get_yield_curve()
    assert len(calls) == 2


@pytest.mark.parametrize("body", [
    b'<feed xmlns="http://www.w3.org/2005/Atom"/>',
    b'<feed xmlns="http://www.w3.org/2005/Atom"><error>unavailable</error></feed>',
    feed().replace(b'</feed>', b'<error>unavailable</error></feed>'),
    feed().replace(b'</feed>', b'<m:error>unavailable</m:error></feed>'),
    feed().replace(b'DailyTreasuryYieldCurveRateData</title>', b'Error</title>'),
    feed().replace(b'<updated>2026-12-31T23:00:00Z</updated>', b''),
    feed().replace(b'2026-12-31T23:00:00Z', b'invalid'),
    feed().replace(b'xml-item?data=daily_treasury_yield_curve', b'wrong'),
    feed().replace(b'</feed>', b'<title>DailyTreasuryYieldCurveRateData</title></feed>'),
    feed().replace(b'</feed>', b'<link rel="next" href="evil"/></feed>'),
])
def test_fake_empty_feed_never_triggers_fallback(new_year, body):
    instance, calls = year_provider({"2027": body})
    with pytest.raises(ProviderError, match="untrusted empty feed"):
        instance.get_yield_curve()
    assert len(calls) == 1
