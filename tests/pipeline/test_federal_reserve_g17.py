import pytest

from pipeline.providers.federal_reserve_data import FederalReserveDataProvider, _PackageLabelParser
from pipeline.providers.base import ProviderError
from pipeline.settings import Settings


def test_ddp_parser_reads_current_preformatted_package_options():
    parser = _PackageLabelParser()
    parser.feed('''<select id="FreqRequestList">
      <option value="rel=H10&amp;series=abc&amp;filetype=csv">G.5 Statistical Release - Monthly rates [csv]</option>
      <option value="rel=H10&amp;series=def&amp;filetype=csv">G.5 Statistical Release - Monthly Indexes [csv]</option>
    </select>''')

    assert parser.packages["option_0"] == {
        "value": "rel=H10&series=abc&filetype=csv",
        "label": "G.5 Statistical Release - Monthly rates [csv]",
    }
    assert parser.packages["option_1"] == {
        "value": "rel=H10&series=def&filetype=csv",
        "label": "G.5 Statistical Release - Monthly Indexes [csv]",
    }


def test_g17_reader_selects_approved_series_and_months(monkeypatch) -> None:
    body = '\n'.join([
        '"GMF" 2025 100.0 101.0 102.0 103.0 104.0 105.0 106.0 107.0 108.0 109.0 110.0 111.0',
        '"G3344" 2025 200.0 201.0 202.0 203.0 204.0 205.0 206.0 207.0 208.0 209.0 210.0 211.0',
        '"G3344" 2026 212.0 213.0 214.0 215.0 216.0 217.0 218.0 219.0 220.0 221.0',
    ])
    provider = FederalReserveDataProvider(Settings(_env_file=None))
    monkeypatch.setattr(provider, "_get_text", lambda _url, **_kwargs: body)

    rows = provider.get_series("IPG3344", start="2025-12-01", end="2026-03-31")

    assert [(row["date"], row["value"]) for row in rows] == [
        ("2025-12-01", 211.0),
        ("2026-01-01", 212.0),
        ("2026-02-01", 213.0),
        ("2026-03-01", 214.0),
    ]
    assert all(row["fetched_at"].endswith("Z") for row in rows)


def test_g17_rejects_duplicate_months(monkeypatch) -> None:
    provider = FederalReserveDataProvider(Settings(_env_file=None))
    monkeypatch.setattr(provider, "_get_text", lambda _url, **_kwargs: '\n'.join([
        '"G3344" 2026 1.0',
        '"G3344" 2026 2.0',
    ]))

    with pytest.raises(ProviderError, match="invalid or duplicate"):
        provider.get_series("IPG3344", start="2026-01-01", end="2026-02-28")
