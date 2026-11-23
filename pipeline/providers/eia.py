from __future__ import annotations

import csv
import io
import math
from datetime import date
from typing import Any

import httpx

from pipeline.providers.base import (
    PERMANENT,
    TRANSIENT,
    BaseProvider,
    ProviderError,
    ProviderHealth,
    guarded_client,
)

EIA_WEEKLY_INVENTORY_CSV = "https://ir.eia.gov/wpsr/psw00.csv"
EIA_CSV_SERIES_ID = "WCESTUS1"


class EiaProvider(BaseProvider):
    name = "eia"
    domain = "energy"
    hosts = ("ir.eia.gov",)

    def __init__(self, settings=None) -> None:
        super().__init__(settings)
        self.api_key = self.settings.eia_api_key
        self._client = guarded_client(set(self.hosts), timeout=20.0)

    def health(self) -> ProviderHealth:
        try:
            rows = self.get_weekly_inventory()
            return ProviderHealth(provider=self.name, ok=bool(rows), error=None if rows else "empty inventory")
        except ProviderError as exc:
            return ProviderHealth(provider=self.name, ok=False, error=str(exc))

    def get_weekly_inventory(self) -> list[dict[str, Any]]:
        try:
            response = self._client.get(EIA_WEEKLY_INVENTORY_CSV)
        except httpx.TimeoutException as exc:
            raise ProviderError("EIA WPSR CSV request timed out", cls=TRANSIENT) from exc
        except httpx.TransportError as exc:
            raise ProviderError("EIA WPSR CSV transport failed", cls=TRANSIENT) from exc
        except ProviderError as exc:
            raise ProviderError("EIA WPSR CSV request was blocked") from exc

        if response.status_code != 200:
            raise ProviderError.from_http("EIA WPSR CSV", response)

        try:
            reader = csv.DictReader(io.StringIO(response.text))
            required = {"Release_Date", "Date", "Sourcekey", "Value", "Suppression_Flag"}
            if not reader.fieldnames or not required.issubset(reader.fieldnames):
                raise ValueError("unexpected WPSR CSV columns")
            safe_rows: list[dict[str, Any]] = []
            seen_dates: set[str] = set()
            for row in reader:
                if row.get("Sourcekey") != EIA_CSV_SERIES_ID:
                    continue
                if row.get("Suppression_Flag", "").strip():
                    continue
                release_date = date.fromisoformat(str(row["Release_Date"])).isoformat()
                week_ending = date.fromisoformat(str(row["Date"])).isoformat()
                value = float(row["Value"])
                if not math.isfinite(value) or value < 0 or week_ending in seen_dates:
                    raise ValueError("invalid value")
                seen_dates.add(week_ending)
                safe_rows.append({"date": week_ending, "release_date": release_date, "value": value})
            safe_rows.sort(key=lambda item: item["date"])
            if not safe_rows:
                raise ValueError("no unsuppressed WCESTUS1 rows")
            return safe_rows[-260:]
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ProviderError("EIA WPSR CSV could not be parsed", cls=PERMANENT) from exc
