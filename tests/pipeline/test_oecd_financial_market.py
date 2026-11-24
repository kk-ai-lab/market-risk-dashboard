from __future__ import annotations

import pytest

from pipeline.providers.base import ProviderError
from pipeline.providers.oecd_financial_market import parse_share_price_csv

CSV = """REF_AREA,FREQ,MEASURE,UNIT_MEASURE,METHODOLOGY,TIME_PERIOD,OBS_VALUE,OBS_STATUS
USA,M,SHARE,IX,N,2026-09,226.1003,A
USA,M,SHARE,IX,N,2026-10,225.0328,A
CHN,M,SHARE,IX,N,2026-09,105.7402,A
CHN,M,SHARE,IX,N,2026-10,101.3747,A
"""


def test_parse_only_requested_monthly_national_share_indices() -> None:
    parsed = parse_share_price_csv(CSV, {"USA", "CHN"})

    assert parsed == {
        "USA": [
            {"period": "2026-09", "value": 226.1003},
            {"period": "2026-10", "value": 225.0328},
        ],
        "CHN": [
            {"period": "2026-09", "value": 105.7402},
            {"period": "2026-10", "value": 101.3747},
        ],
    }


@pytest.mark.parametrize("field,value", [("FREQ", "D"), ("OBS_STATUS", "P"), ("UNIT_MEASURE", "USD")])
def test_parse_rejects_observations_outside_approved_definition(field: str, value: str) -> None:
    lines = CSV.splitlines()
    columns = lines[0].split(",")
    rows = [line.split(",") for line in lines[1:]]
    rows[0][columns.index(field)] = value
    altered = "\n".join([lines[0], *(",".join(row) for row in rows)])

    with pytest.raises(ProviderError):
        parse_share_price_csv(altered, {"USA", "CHN"})


def test_parse_rejects_duplicate_monthly_observations() -> None:
    with pytest.raises(ProviderError):
        parse_share_price_csv(CSV + "USA,M,SHARE,IX,N,2026-10,225.0328,A\n", {"USA", "CHN"})
