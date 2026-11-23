from __future__ import annotations

import json
import math
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from pipeline.providers.base import (
    MAX_RESPONSE_BYTES,
    BaseProvider,
    ProviderError,
    ProviderHealth,
    guarded_client,
)
from pipeline.utils import now_utc

API_ROOT = "https://markets.newyorkfed.org/api/rates"
REPO_API = "https://markets.newyorkfed.org/api/rp/results/search.json"
SERIES_PATHS = {
    "DFF": "unsecured/effr",
    "SOFR": "secured/sofr",
}
SUPPORTED_SERIES = frozenset({*SERIES_PATHS, "RRPONTSYD"})


class NyfedRatesProvider(BaseProvider):
    name = "nyfed_rates"
    domain = "macro"
    hosts = ("markets.newyorkfed.org",)

    def __init__(self, settings=None) -> None:
        super().__init__(settings)
        self._client = guarded_client(set(self.hosts), timeout=20.0)

    def health(self) -> ProviderHealth:
        started = time.monotonic()
        try:
            today = datetime.now(UTC).date()
            rows = self.get_series("DFF", start=(today - timedelta(days=7)).isoformat(), end=today.isoformat())
            return ProviderHealth(
                provider=self.name,
                ok=bool(rows),
                latency_ms=round((time.monotonic() - started) * 1000, 1),
                error=None if rows else "no effective rate for today",
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

    def get_series(self, series_id: str, *, start: str, end: str) -> list[dict[str, Any]]:
        if series_id not in SUPPORTED_SERIES:
            raise ProviderError("New York Fed: unsupported reference-rate target")
        try:
            start_day, end_day = date.fromisoformat(start), date.fromisoformat(end)
        except ValueError as exc:
            raise ProviderError("New York Fed: invalid date range") from exc
        if start_day > end_day or (end_day - start_day).days > 5 * 366:
            raise ProviderError("New York Fed: date range is invalid or exceeds five years")
        if series_id == "RRPONTSYD":
            return self._get_overnight_reverse_repo(start_day=start_day, end_day=end_day)
        url = f"{API_ROOT}/{SERIES_PATHS[series_id]}/search.json"
        try:
            with self._client.stream(
                "GET", url,
                params={"startDate": start_day.isoformat(), "endDate": end_day.isoformat()},
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    response.raise_for_status()
                    raise ProviderError("New York Fed: expected HTTP 200")
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise ProviderError("New York Fed: response exceeds size limit")
                    body.extend(chunk)
        except httpx.HTTPError as exc:
            raise ProviderError.from_exception(exc, detail="New York Fed reference-rate request failed") from exc
        try:
            payload = json.loads(body)
            rates = payload["refRates"]
            if not isinstance(rates, list):
                raise ValueError("refRates is not a list")
            rows: list[dict[str, Any]] = []
            seen: set[str] = set()
            fetched_at = now_utc()
            for item in rates:
                if not isinstance(item, dict):
                    raise ValueError("invalid rate row")
                day = date.fromisoformat(str(item["effectiveDate"])).isoformat()
                value = float(item["percentRate"])
                if not math.isfinite(value) or day in seen or not start <= day <= end:
                    raise ValueError("invalid, duplicate, or out-of-range rate observation")
                seen.add(day)
                rows.append({"date": day, "value": value, "fetched_at": fetched_at})
            rows.sort(key=lambda item: item["date"])
            if not rows:
                raise ValueError("no observations in the requested date range")
            return rows
        except (KeyError, TypeError, ValueError, OverflowError, json.JSONDecodeError) as exc:
            raise ProviderError("New York Fed: reference-rate response could not be parsed") from exc

    def _get_overnight_reverse_repo(self, *, start_day: date, end_day: date) -> list[dict[str, Any]]:
        operations: list[dict[str, Any]] = []
        year = start_day.year
        while year <= end_day.year:
            chunk_start = max(start_day, date(year, 1, 1))
            chunk_end = min(end_day, date(year, 12, 31))
            operations.extend(self._fetch_repo_operations(chunk_start, chunk_end))
            year += 1

        try:
            totals: dict[str, float] = {}
            seen: set[str] = set()
            fetched_at = now_utc()
            for operation in operations:
                if operation.get("operationType") != "Reverse Repo" or str(operation.get("term", "")).casefold() != "overnight":
                    continue
                operation_id = str(operation.get("operationId", ""))
                if not operation_id or operation_id in seen:
                    raise ValueError("missing or duplicate reverse-repo operation ID")
                seen.add(operation_id)
                day = date.fromisoformat(str(operation["operationDate"]))
                if not start_day <= day <= end_day:
                    raise ValueError("operation is outside requested date range")
                amount = float(operation["totalAmtAccepted"])
                if not math.isfinite(amount) or amount < 0:
                    raise ValueError("invalid amount accepted")
                totals[day.isoformat()] = totals.get(day.isoformat(), 0.0) + amount / 1_000_000_000
            rows = [
                {"date": day, "value": round(value, 9), "fetched_at": fetched_at}
                for day, value in sorted(totals.items())
            ]
            if not rows:
                raise ValueError("no overnight reverse-repo results in the requested range")
            return rows
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ProviderError("New York Fed: reverse-repo response could not be parsed") from exc

    def _fetch_repo_operations(self, start_day: date, end_day: date) -> list[dict[str, Any]]:
        try:
            with self._client.stream(
                "GET",
                REPO_API,
                params={"startDate": start_day.isoformat(), "endDate": end_day.isoformat()},
                follow_redirects=False,
            ) as response:
                if response.status_code != 200:
                    response.raise_for_status()
                    raise ProviderError("New York Fed: expected HTTP 200")
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise ProviderError("New York Fed: repo response exceeds size limit")
                    body.extend(chunk)
        except httpx.HTTPError as exc:
            raise ProviderError.from_exception(exc, detail="New York Fed reverse-repo request failed") from exc
        try:
            payload = json.loads(body)
            operations = payload["repo"]["operations"]
            if not isinstance(operations, list) or any(not isinstance(row, dict) for row in operations):
                raise ValueError("repo operations is not a list of objects")
            return operations
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderError("New York Fed: reverse-repo response could not be parsed") from exc
