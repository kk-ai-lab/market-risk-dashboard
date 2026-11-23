"""Standalone, unauthenticated BLS adapter, not registered with the production pipeline.

Dates identify monthly observation periods, never release or availability dates.
CPI series use seasonally adjusted indexes, matching the dashboard's FRED series.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime
from typing import Any

import httpx

from pipeline.providers.base import (
    MAX_RESPONSE_BYTES,
    BaseProvider,
    ProviderError,
    ProviderHealth,
    guarded_client,
)

BLS_API = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
SERIES = {
    "CPIAUCSL": "CUSR0000SA0",
    "CPILFESL": "CUSR0000SA0L1E",
    "UNRATE": "LNS14000000",
    "PAYEMS": "CES0000000001",
    "CIVPART": "LNS11300000",
}
MAX_BATCH_SIZE = 25


class BlsProvider(BaseProvider):
    name = "bls"
    domain = "bls"
    hosts = ("api.bls.gov",)

    def __init__(self, settings=None) -> None:
        super().__init__(settings)
        self._client = guarded_client(set(self.hosts), timeout=20.0)

    def health(self) -> ProviderHealth:
        try:
            rows = self.get_series_batch(["UNRATE"])
            ok = bool(rows["UNRATE"])
            return ProviderHealth(provider=self.name, ok=ok, error=None if ok else "empty series")
        except ProviderError as exc:
            return ProviderHealth(provider=self.name, ok=False, error=str(exc))

    def get_raw_series_batch(
        self, series_ids: list[str], *, end_year: int | None = None, allow_partial: bool = False,
    ) -> dict[str, list[dict[str, Any]]]:
        """Fetch up to 25 BLS IDs over six calendar years, oldest observations first.

        The extra year supplies denominators for five calendar years of YoY data.
        No registration key is read or sent. Partial batches are opt-in.
        """
        ids = list(dict.fromkeys(series_ids))
        if not ids or len(ids) > MAX_BATCH_SIZE:
            raise ProviderError("BLS: request must contain 1 to 25 unique series")
        year = end_year if end_year is not None else datetime.now(UTC).year
        if isinstance(year, bool) or not isinstance(year, int) or not 1905 <= year <= 9999:
            raise ProviderError("BLS: invalid end year")
        try:
            with self._client.stream("POST", BLS_API, json={
                "seriesid": ids, "startyear": str(year - 5), "endyear": str(year),
            }, follow_redirects=False) as response:
                response.raise_for_status()
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise ProviderError("BLS: response exceeds size limit")
                    body.extend(chunk)
                payload = json.loads(body)
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError.from_exception(exc, detail="BLS: request failed") from exc
        if not isinstance(payload, dict) or payload.get("status") != "REQUEST_SUCCEEDED":
            raise ProviderError("BLS: API request failed")
        results = payload.get("Results")
        series = results.get("series") if isinstance(results, dict) else None
        if not isinstance(series, list):
            raise ProviderError("BLS: malformed series response")
        output: dict[str, list[dict[str, Any]]] = {}
        current_month = datetime.now(UTC).date().replace(day=1)
        for item in series:
            if not isinstance(item, dict) or item.get("seriesID") not in ids:
                continue
            series_id = item["seriesID"]
            if series_id in output or not isinstance(item.get("data"), list):
                raise ProviderError("BLS: malformed series response")
            months: dict[str, float] = {}
            for obs in item["data"]:
                if not isinstance(obs, dict):
                    continue
                period = obs.get("period", "")
                if not isinstance(period, str) or period not in {f"M{month:02}" for month in range(1, 13)}:
                    continue
                try:
                    observation_year = int(obs["year"])
                    observation_date = date(observation_year, int(period[1:]), 1)
                    raw_value = obs["value"]
                    if isinstance(raw_value, bool):
                        continue
                    value = float(raw_value)
                except (ValueError, TypeError, KeyError, OverflowError):
                    continue
                if not math.isfinite(value) or not year - 5 <= observation_year <= year:
                    continue
                if observation_date > current_month:
                    continue
                key = observation_date.isoformat()
                if key in months:
                    raise ProviderError("BLS: duplicate observation month")
                months[key] = value
            output[series_id] = [{"date": key, "value": months[key]} for key in sorted(months)]
        if not any(output.values()) or (not allow_partial and (set(output) != set(ids) or any(not rows for rows in output.values()))):
            raise ProviderError("BLS: requested series missing or empty")
        for series_id in ids:
            output.setdefault(series_id, [])
        return output

    def get_series_batch(
        self, series_ids: list[str] | None = None, *, end_year: int | None = None,
    ) -> dict[str, list[dict[str, Any]]]:
        """Return dashboard IDs with YoY CPI %, monthly payroll change (thousands), and UNRATE %."""
        ids = list(dict.fromkeys(SERIES if series_ids is None else series_ids))
        if not ids or any(series_id not in SERIES for series_id in ids):
            raise ProviderError("BLS: unsupported dashboard series")
        raw = self.get_raw_series_batch([SERIES[series_id] for series_id in ids], end_year=end_year, allow_partial=True)
        result: dict[str, list[dict[str, Any]]] = {}
        for series_id in ids:
            rows = raw[SERIES[series_id]]
            values = {row["date"]: row["value"] for row in rows}
            transformed = []
            for row in rows:
                year, month = map(int, row["date"][:7].split("-"))
                value = row["value"]
                if series_id in {"CPIAUCSL", "CPILFESL"}:
                    previous = values.get(f"{year - 1:04}-{month:02}-01")
                    if previous is None or previous == 0:
                        continue
                    value = (value / previous - 1) * 100
                elif series_id == "PAYEMS":
                    previous_month = date(year - (month == 1), 12 if month == 1 else month - 1, 1)
                    previous = values.get(previous_month.isoformat())
                    if previous is None:
                        continue
                    value -= previous
                if math.isfinite(value):
                    transformed.append({"date": row["date"], "value": value})
            result[series_id] = transformed
        return result
