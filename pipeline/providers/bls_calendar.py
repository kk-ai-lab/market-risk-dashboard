"""Official BLS release calendar iCalendar feed."""

from __future__ import annotations

import hashlib
import re
import time
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from pipeline.providers._util import _today
from pipeline.providers.base import BaseProvider, ProviderError, ProviderHealth, guarded_client
from pipeline.utils import now_utc

BLS_CALENDAR_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
_EASTERN = ZoneInfo("America/New_York")
_RELEASE_IMPORTANCE = ("Consumer Price Index", "Employment Situation", "Producer Price Index")


def _unescape_ics(value: str) -> str:
    return value.replace(r"\n", " ").replace(r"\,", ",").replace(r"\;", ";").replace(r"\\", "\\").strip()


def parse_bls_calendar(body: str, start: date, end: date) -> list[dict[str, Any]]:
    """Parse dated timed release events; all-day holidays are not release events."""
    if start > end or "BEGIN:VCALENDAR" not in body or "END:VCALENDAR" not in body:
        raise ProviderError("BLS calendar: invalid iCalendar document")
    unfolded = re.sub(r"\r?\n[ \t]", "", body)
    events: list[dict[str, Any]] = []
    seen_uids: set[str] = set()
    current: dict[str, tuple[str, str]] | None = None
    for line in unfolded.splitlines():
        if line == "BEGIN:VEVENT":
            if current is not None:
                raise ProviderError("BLS calendar: nested event")
            current = {}
            continue
        if line == "END:VEVENT":
            if current is None:
                raise ProviderError("BLS calendar: event terminator without event")
            uid = current.get("UID", ("", ""))[1]
            summary = _unescape_ics(current.get("SUMMARY", ("", ""))[1])
            raw_start, start_value = current.get("DTSTART", ("", ""))
            if not uid or not summary or not start_value or uid in seen_uids:
                raise ProviderError("BLS calendar: missing or duplicate event field")
            seen_uids.add(uid)
            if "VALUE=DATE" in raw_start or "T" not in start_value:
                current = None
                continue
            try:
                if start_value.endswith("Z"):
                    instant = datetime.strptime(start_value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
                else:
                    fmt = "%Y%m%dT%H%M%S" if len(start_value) == 15 else "%Y%m%dT%H%M"
                    instant = datetime.strptime(start_value, fmt).replace(tzinfo=_EASTERN).astimezone(UTC)
            except ValueError as exc:
                raise ProviderError("BLS calendar: invalid release timestamp") from exc
            release_day = instant.astimezone(_EASTERN).date()
            if release_day < start or release_day > end:
                current = None
                continue
            digest = hashlib.sha256(uid.encode()).hexdigest()[:24]
            events.append({
                "id": f"econ-bls-{digest}",
                "title": summary,
                "datetime": instant.isoformat().replace("+00:00", "Z"),
                "country": "US",
                "importance": "high" if any(token in summary for token in _RELEASE_IMPORTANCE) else "medium",
                "source": "bls",
            })
            current = None
            continue
        if current is None or ":" not in line:
            continue
        name_params, value = line.split(":", 1)
        name, _, params = name_params.partition(";")
        if name in current:
            raise ProviderError("BLS calendar: duplicate event property")
        current[name] = (params, value)
    if current is not None:
        raise ProviderError("BLS calendar: unterminated event")
    return sorted(events, key=lambda row: (row["datetime"], row["id"]))


class BlsCalendarProvider(BaseProvider):
    name = "bls_calendar"
    domain = "bls_calendar"
    hosts = ("www.bls.gov",)

    def __init__(self, settings=None) -> None:
        super().__init__(settings)
        self._client = guarded_client(
            set(self.hosts),
            timeout=20.0,
            headers={"User-Agent": "market-risk-dashboard (+https://github.com/kk-ai-lab/market-risk-dashboard)"},
        )

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
        response = self._client.get(BLS_CALENDAR_URL)
        if response.status_code != 200:
            raise ProviderError.from_http("BLS calendar", response)
        try:
            return parse_bls_calendar(response.text, date.fromisoformat(start), date.fromisoformat(end))
        except ValueError as exc:
            raise ProviderError("BLS calendar: invalid date range") from exc
