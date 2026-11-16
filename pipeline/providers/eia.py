"""EIA API v2 adapter restricted to the public WCESTUS1 weekly series."""

from __future__ import annotations

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

EIA_WEEKLY_INVENTORY_ENDPOINT = "https://api.eia.gov/v2/seriesid/WCESTUS1"
EIA_SERIES_ID = "WCESTUS1"


class EiaProvider(BaseProvider):
    name = "eia"
    domain = "energy"
    hosts = ("api.eia.gov",)
    requires_api_key = True

    def __init__(self, settings=None) -> None:
        super().__init__(settings)
        self.api_key = self.settings.eia_api_key
        self._client = guarded_client(set(self.hosts), timeout=20.0)

    def health(self) -> ProviderHealth:
        configured = bool(self.api_key and self.api_key.get_secret_value().strip())
        return ProviderHealth(
            provider=self.name,
            ok=configured,
            error=None if configured else "missing DATA_EIA_API_KEY",
            checked_at=None,
        )

    def get_weekly_inventory(self) -> list[dict[str, Any]]:
        """Fetch a bounded, sanitized list; never return the EIA response envelope."""
        api_key = self.api_key.get_secret_value().strip() if self.api_key else ""
        if not api_key:
            raise ProviderError("EIA weekly inventory is not configured")

        try:
            response = self._client.get(
                EIA_WEEKLY_INVENTORY_ENDPOINT,
                params={
                    "api_key": api_key,
                    "frequency": "weekly",
                    "sort[0][column]": "period",
                    "sort[0][direction]": "desc",
                    "length": "260",
                },
            )
        except httpx.TimeoutException as exc:
            raise ProviderError("EIA weekly inventory request timed out", cls=TRANSIENT) from exc
        except httpx.TransportError as exc:
            raise ProviderError("EIA weekly inventory transport failed", cls=TRANSIENT) from exc
        except ProviderError as exc:
            # GuardedClient errors can contain the full request URL; retain only its class.
            raise ProviderError("EIA weekly inventory request was blocked") from exc

        if response.status_code != 200:
            raise ProviderError.from_http("EIA weekly inventory", response)

        try:
            body = response.json()
            rows = body["response"]["data"]
            if not isinstance(rows, list):
                raise ValueError("invalid rows")
            safe_rows: list[dict[str, Any]] = []
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("invalid row")
                week_ending = date.fromisoformat(str(row["period"])).isoformat()
                value = float(row["value"])
                if not math.isfinite(value) or value < 0:
                    raise ValueError("invalid value")
                safe_rows.append({"date": week_ending, "value": value})
            safe_rows.sort(key=lambda item: item["date"])
            return safe_rows[-260:]
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            # Do not include the response, request params, or exception body in this message.
            raise ProviderError("EIA weekly inventory response could not be parsed", cls=PERMANENT) from exc
