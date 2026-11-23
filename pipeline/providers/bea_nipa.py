"""Official BEA monthly NIPA flat-file adapter for public macro series."""

from __future__ import annotations

import csv
import math
import time
from datetime import date
from typing import Any

import httpx

from pipeline.providers.base import BaseProvider, ProviderError, ProviderHealth, guarded_client
from pipeline.utils import now_utc

BEA_NIPA_MONTHLY_URL = "https://apps.bea.gov/national/Release/TXT/NipaDataM.txt"
MAX_BEA_FILE_BYTES = 50 * 1024 * 1024
SERIES_CODES = {"PCEPILFE": "BPCCRO"}


class BeaNipaProvider(BaseProvider):
    name = "bea_nipa"
    domain = "macro"
    hosts = ("apps.bea.gov",)

    def __init__(self, settings=None) -> None:
        super().__init__(settings)
        self._client = guarded_client(
            set(self.hosts), timeout=75.0, max_response_bytes=MAX_BEA_FILE_BYTES,
        )

    def health(self) -> ProviderHealth:
        started = time.monotonic()
        try:
            response = self._client.head(BEA_NIPA_MONTHLY_URL)
            if response.status_code != 200:
                raise ProviderError.from_http("BEA NIPA monthly file", response)
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

    def get_series(self, series_id: str, *, start: str, end: str) -> list[dict[str, Any]]:
        code = SERIES_CODES.get(series_id)
        if code is None:
            raise ProviderError("BEA NIPA: unsupported macro target")
        try:
            start_day, end_day = date.fromisoformat(start), date.fromisoformat(end)
        except ValueError as exc:
            raise ProviderError("BEA NIPA: invalid date range") from exc
        if start_day > end_day or (end_day - start_day).days > 6 * 366:
            raise ProviderError("BEA NIPA: date range is invalid or exceeds six years")

        fetched_at = now_utc()
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        transferred = 0
        try:
            with self._client.stream("GET", BEA_NIPA_MONTHLY_URL, follow_redirects=False) as response:
                if response.status_code != 200:
                    raise ProviderError.from_http("BEA NIPA monthly file", response)
                try:
                    declared_size = int(response.headers.get("content-length", "0"))
                except ValueError:
                    declared_size = 0
                if declared_size > MAX_BEA_FILE_BYTES:
                    raise ProviderError("BEA NIPA: monthly file exceeds response size limit")
                for line in response.iter_lines():
                    transferred += len(line.encode("utf-8")) + 1
                    if transferred > MAX_BEA_FILE_BYTES:
                        raise ProviderError("BEA NIPA: monthly file exceeds response size limit")
                    if line == "%SeriesCode,Period,Value":
                        continue
                    if not line.startswith(f"{code},"):
                        continue
                    fields = next(csv.reader([line]))
                    if len(fields) != 3 or fields[0] != code:
                        raise ValueError("invalid target row")
                    period = fields[1]
                    if len(period) != 7 or period[4] != "M":
                        raise ValueError("invalid monthly period")
                    month = date.fromisoformat(f"{period[:4]}-{period[5:]}-01")
                    observation_date = month.isoformat()
                    if observation_date in seen:
                        raise ValueError("duplicate observation")
                    seen.add(observation_date)
                    if month < start_day.replace(day=1) or month > end_day.replace(day=1):
                        continue
                    value = float(fields[2].replace(",", ""))
                    if not math.isfinite(value):
                        raise ValueError("non-finite observation")
                    rows.append({
                        "date": observation_date,
                        "period": period.replace("M", "-"),
                        "value": value,
                        "fetched_at": fetched_at,
                    })
        except ProviderError:
            raise
        except (httpx.HTTPError, csv.Error, TypeError, ValueError, OverflowError) as exc:
            raise ProviderError("BEA NIPA: monthly file could not be parsed") from exc
        rows.sort(key=lambda row: row["date"])
        if not rows:
            raise ProviderError("BEA NIPA: no observations in the requested date range")
        return rows
