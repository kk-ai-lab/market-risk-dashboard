from pipeline.providers.base import ProviderError
from pipeline.providers.dol_claims import DolClaimsProvider


def test_fetch_uses_current_claims_form_contract(monkeypatch) -> None:
    class Response:
        status_code = 200

        def __init__(self, content: bytes) -> None:
            self.content = content

        @property
        def text(self) -> str:
            return self.content.decode()

    class Client:
        def get(self, url: str) -> Response:
            assert url.endswith("/unemploy/claims.asp")
            return Response(b'<form name="wkclaim"><input type="hidden" name="final_yr" value="2027"></form>')

        def post(self, url: str, *, headers: dict[str, str], data: dict[str, str]) -> Response:
            assert url.endswith("/unemploy/wkclaims/report.asp")
            assert headers["Referer"].endswith("/unemploy/claims.asp")
            assert data["final_yr"] == "2027"
            return Response(
                b'<r539cyNational rundate="11/19/2026">'
                b'<week><weekEnded>11/14/2026</weekEnded>'
                b'<InitialClaims><SA>203,000</SA></InitialClaims>'
                b'<ContinuedClaims><SA>1,700,000</SA></ContinuedClaims></week>'
                b'</r539cyNational>'
            )

    provider = DolClaimsProvider()
    provider._client = Client()
    monkeypatch.setattr("pipeline.providers.dol_claims.now_utc", lambda: "2026-11-24T17:00:00Z")

    rows, released_on = provider._fetch("2026-11-01", "2026-11-24")

    assert released_on == "2026-11-19"
    assert rows["ICSA"][-1]["value"] == 203.0
    assert rows["CCSA"][-1]["value"] == 1700.0


def test_eta539_rows_survive_when_optional_advance_pdf_is_unavailable(monkeypatch) -> None:
    provider = DolClaimsProvider()
    published = {
        "ICSA": [{"date": "2026-11-14", "value": 203.0, "released_on": "2026-11-19"}],
        "CCSA": [{"date": "2026-11-07", "value": 1763.0, "released_on": "2026-11-19"}],
    }
    monkeypatch.setattr(provider, "_fetch", lambda *_args: (published, "2026-11-19"))
    monkeypatch.setattr(
        provider,
        "_fetch_advance_release",
        lambda: (_ for _ in ()).throw(ProviderError("DOL advance weekly claims: HTTP 403")),
    )

    rows = provider.get_series("ICSA", start="2026-11-01", end="2026-11-24")

    assert rows == published["ICSA"]


def test_uses_published_release_when_current_eta539_report_is_embargoed(monkeypatch) -> None:
    provider = DolClaimsProvider()
    released = {
        "ICSA": [{"date": "2026-11-14", "value": 203.0, "released_on": "2026-11-19"}],
        "CCSA": [{"date": "2026-11-07", "value": 1763.0, "released_on": "2026-11-19"}],
    }
    monkeypatch.setattr(
        provider,
        "_fetch",
        lambda *_args: (_ for _ in ()).throw(ProviderError("DOL UI claims: report is not yet publicly released")),
    )
    monkeypatch.setattr(provider, "_fetch_advance_release", lambda: released)

    rows = provider.get_series("ICSA", start="2026-11-01", end="2026-11-24")

    assert rows == released["ICSA"]


def test_does_not_mask_other_eta539_failures(monkeypatch) -> None:
    provider = DolClaimsProvider()
    monkeypatch.setattr(
        provider,
        "_fetch",
        lambda *_args: (_ for _ in ()).throw(ProviderError("DOL UI claims: HTTP 502")),
    )

    try:
        provider.get_series("ICSA", start="2026-11-01", end="2026-11-24")
    except ProviderError as exc:
        assert "HTTP 502" in str(exc)
    else:
        raise AssertionError("unrelated ETA 539 failure should propagate")
