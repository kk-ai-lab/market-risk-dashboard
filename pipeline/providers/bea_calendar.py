"""Official BEA release schedule, collected without a data API key."""

from __future__ import annotations

import hashlib
import time
from datetime import UTC, date, datetime
from typing import Any

import httpx

from pipeline.providers._util import UA, _today
from pipeline.providers.base import (
    BaseProvider,
    EconomicRow,
    ProviderError,
    ProviderHealth,
    guarded_client,
)
from pipeline.utils import now_utc

BEA_CALENDAR_URL = "https://apps.bea.gov/API/signup/release_dates.json"
_HIGH_IMPORTANCE = frozenset({"Gross Domestic Product", "Personal Income and Outlays"})


def parse_release_schedule(payload: object, start: date, end: date) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ProviderError("BEA calendar: expected a schedule object")
    events: dict[tuple[str, str], EconomicRow] = {}
    release_families = 0
    try:
        for title, entry in payload.items():
            if title == "file_last_updated":
                continue
            if not isinstance(title, str) or not title.strip() or not isinstance(entry, dict):
                raise ValueError("invalid release family")
            dates = entry.get("release_dates")
            if not isinstance(dates, list):
                raise ValueError("release_dates must be an array")
            release_families += 1
            for raw in dates:
                if not isinstance(raw, str):
                    raise ValueError("release timestamp must be a string")
                instant = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if instant.tzinfo is None or instant.utcoffset() is None:
                    raise ValueError("release timestamp must include a UTC offset")
                instant = instant.astimezone(UTC)
                if not start <= instant.date() <= end:
                    continue
                stamp = instant.isoformat().replace("+00:00", "Z")
                identity = (title, stamp)
                digest = hashlib.sha256(f"{title}\n{stamp}".encode()).hexdigest()[:24]
                events[identity] = EconomicRow(
                    id=f"econ-bea-{digest}",
                    title=title,
                    datetime=stamp,
                    importance="high" if title in _HIGH_IMPORTANCE else "medium",
                    country="US",
                    source="bea",
                )
    except (TypeError, ValueError) as exc:
        raise ProviderError(f"BEA calendar: invalid schedule ({exc})") from exc
    if not release_families:
        raise ProviderError("BEA calendar: no release families in schedule")
    return [event.model_dump(mode="json") for event in sorted(
        events.values(), key=lambda event: (event.datetime, event.title),
    )]


class BeaCalendarProvider(BaseProvider):
    name = "bea_calendar"
    domain = "bea_calendar"
    hosts = ("apps.bea.gov",)

    def __init__(self, settings=None) -> None:
        super().__init__(settings)
        self._client = guarded_client(set(self.hosts), timeout=15.0, headers={"User-Agent": UA})

    def health(self) -> ProviderHealth:
        started = time.monotonic()
        try:
            self.get_economic_calendar(_today(), _today())
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

    def get_economic_calendar(self, start: str, end: str) -> list[dict[str, Any]]:
        response = self._client.get(BEA_CALENDAR_URL)
        if response.status_code != 200:
            raise ProviderError.from_http("BEA calendar", response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError("BEA calendar: invalid JSON response") from exc
        return parse_release_schedule(payload, date.fromisoformat(start), date.fromisoformat(end))
