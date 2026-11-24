from pipeline.providers.base import ProviderError
from pipeline.providers.dol_claims import DolClaimsProvider


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
