from __future__ import annotations

import csv
import io
import math
import time
from datetime import UTC, datetime
from typing import Any

import httpx

from pipeline.providers.base import BaseProvider, ProviderError, ProviderHealth, guarded_client
from pipeline.utils import now_utc

OECD_SHARE_PRICE_URL = (
    "https://sdmx.oecd.org/public/rest/data/"
    "OECD.SDD.STES,DSD_STES@DF_FINMARK,4.0/"
)
OECD_SHARE_PRICE_COUNTRIES = frozenset({"USA", "CHN"})
OECD_SHARE_PRICE_LOOKBACK_YEARS = 3


def parse_share_price_csv(data: str, countries: set[str]) -> dict[str, list[dict[str, Any]]]:
    if not countries or not countries.issubset(OECD_SHARE_PRICE_COUNTRIES):
        raise ProviderError("OECD Financial market: unsupported share-price country")
    try:
        reader = csv.DictReader(io.StringIO(data))
        required = {
            "REF_AREA", "FREQ", "MEASURE", "UNIT_MEASURE", "METHODOLOGY",
            "TIME_PERIOD", "OBS_VALUE", "OBS_STATUS",
        }
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise ValueError("required SDMX columns are absent")
        observations: dict[str, dict[str, float]] = {country: {} for country in countries}
        for row in reader:
            if row.get("REF_AREA") not in countries:
                continue
            if (
                row.get("FREQ") != "M"
                or row.get("MEASURE") != "SHARE"
                or row.get("UNIT_MEASURE") != "IX"
                or row.get("METHODOLOGY") != "N"
                or row.get("OBS_STATUS") not in {"A", "E"}
            ):
                raise ValueError("observation does not match the approved monthly-share-index series")
            period = row.get("TIME_PERIOD", "")
            if len(period) != 7 or period[4] != "-":
                raise ValueError("observation period is not monthly")
            datetime.strptime(period, "%Y-%m")
            value = float(row.get("OBS_VALUE", ""))
            if not math.isfinite(value) or value <= 0:
                raise ValueError("share-price index is not a positive finite value")
            country = str(row["REF_AREA"])
            if period in observations[country]:
                raise ValueError("duplicate country and month")
            observations[country][period] = value
        if any(len(values) < 2 for values in observations.values()):
            raise ValueError("at least two monthly observations are required per country")
        output: dict[str, list[dict[str, Any]]] = {}
        for country, values in observations.items():
            periods = sorted(values)[-2:]
            output[country] = [
                {"period": period, "value": values[period]}
                for period in periods
            ]
        return output
    except ProviderError:
        raise
    except (csv.Error, TypeError, ValueError, OverflowError) as exc:
        raise ProviderError("OECD Financial market: share-price CSV could not be parsed") from exc


class OECDFinancialMarketProvider(BaseProvider):
    name = "oecd_financial_market"
    domain = "equities"
    hosts = ("sdmx.oecd.org",)

    def __init__(self, settings=None) -> None:
        super().__init__(settings)
        self._client = guarded_client(set(self.hosts), timeout=25.0)

    def health(self) -> ProviderHealth:
        started = time.monotonic()
        try:
            self.get_share_price_indices(sorted(OECD_SHARE_PRICE_COUNTRIES))
            return ProviderHealth(
                provider=self.name,
                ok=True,
                latency_ms=round((time.monotonic() - started) * 1000, 1),
                checked_at=now_utc(),
            )
        except (ProviderError, httpx.HTTPError) as exc:
            return ProviderHealth(
                provider=self.name,
                ok=False,
                latency_ms=round((time.monotonic() - started) * 1000, 1),
                error=str(exc)[:200],
                checked_at=now_utc(),
            )

    def get_share_price_indices(self, countries: list[str]) -> dict[str, list[dict[str, Any]]]:
        if not countries or len(countries) != len(set(countries)) or not set(countries).issubset(OECD_SHARE_PRICE_COUNTRIES):
            raise ProviderError("OECD Financial market: unsupported or duplicate country target")
        start_year = datetime.now(UTC).year - OECD_SHARE_PRICE_LOOKBACK_YEARS
        selection = "+".join(sorted(countries)) + ".M.SHARE.IX._Z._Z._Z._Z.N"
        url = (
            OECD_SHARE_PRICE_URL + selection
            + f"?startPeriod={start_year}-01&dimensionAtObservation=AllDimensions&format=csvfilewithlabels"
        )
        try:
            response = self._client.get(url)
        except (httpx.HTTPError, ProviderError) as exc:
            raise ProviderError("OECD Financial market: API request failed") from exc
        if response.status_code != 200:
            raise ProviderError.from_http("OECD Financial market share prices", response)
        return parse_share_price_csv(response.text, set(countries))
