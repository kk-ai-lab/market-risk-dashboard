"""Economic calendar provider: FRED release dates + the Federal Reserve's FOMC calendar (#94).

Chosen in #83 (§3A/§3C): FRED is the primary for US macro release *dates* — the key is
already in the repo, the data is US-government (free to redistribute), every release row
has a stable id (`release_id` + `date`), and the API's `realtime_start/end` bounds the
window server-side (verified live: 1 call per release, ~8 calls per run). FOMC meeting
dates come from the official page (2026/2027 both published, no key, public domain).

Two traps from #83, encoded here: `release_id=101` (FOMC Press Release) is NOT the FOMC
meeting calendar — FOMC comes from the federalreserve.gov page; and the FRED list is
driven from an explicit `release_id` allowlist, never the unfiltered `/releases/dates`.

FRED gives *dates only*.
"""

from __future__ import annotations

import re
import time
from datetime import date
from typing import Any

import httpx

from pipeline.providers._util import UA, _today
from pipeline.providers.base import (
    PERMANENT,
    RATE_LIMITED,
    TRANSIENT,
    BaseProvider,
    EconomicRow,
    PartialProviderResult,
    ProviderError,
    ProviderHealth,
)
from pipeline.utils import et_instant

FRED_BASE = "https://api.stlouisfed.org/fred"
FOMC_CALENDAR_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
#: release_id allowlist (verified live 2026-08-07) → (name, ET release time, importance).
#: The names are stable public release titles; the ids are immutable.
RELEASES: dict[int, tuple[str, str, str]] = {
    10: ("Consumer Price Index", "08:30", "high"),
    50: ("Employment Situation", "08:30", "high"),
    54: ("Personal Income and Outlays", "08:30", "high"),
    46: ("Producer Price Index", "08:30", "high"),
    53: ("Gross Domestic Product", "08:30", "high"),
    9: ("Advance Retail Sales", "08:30", "high"),
    192: ("Job Openings and Labor Turnover Survey", "10:00", "medium"),
}

_MONTHS = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12,
}
_ERROR_CLASS_PRIORITY = {PERMANENT: 0, TRANSIENT: 1, RATE_LIMITED: 2}


class FredCalendarProvider(BaseProvider):
    name = "fred_calendar"
    domain = "economic"
    hosts = ("api.stlouisfed.org", "www.federalreserve.gov")

    def __init__(self, settings=None) -> None:
        super().__init__(settings)
        self.api_key = self.settings.fred_api_key
        from pipeline.providers.base import guarded_client

        self._client = guarded_client(set(self.hosts), timeout=15.0, headers={"User-Agent": UA})

    def health(self) -> ProviderHealth:
        if not self.api_key:
            return ProviderHealth(provider=self.name, ok=False, error="missing DATA_FRED_API_KEY", checked_at=None)
        started = time.monotonic()
        try:
            # Cheap probe: ONE release for today's window — not the full 8-call economic
            # calendar (the per-call limiter is for the run, not for health checks).
            rows = self._fred_release_dates(10, _today(), _today())
            ok = isinstance(rows, list)
            return ProviderHealth(
                provider=self.name, ok=ok,
                latency_ms=round((time.monotonic() - started) * 1000, 1),
                error=None if ok else "bad payload", checked_at=None,
            )
        except Exception as exc:  # noqa: BLE001
            return ProviderHealth(
                provider=self.name, ok=False,
                latency_ms=round((time.monotonic() - started) * 1000, 1),
                error=str(exc)[:200], checked_at=None,
            )

    def get_economic_calendar(
        self, start: str, end: str
    ) -> list[dict[str, Any]] | PartialProviderResult[list[dict[str, Any]]]:
        rows: list[dict[str, Any]] = []
        errors: list[str] = []
        failures: list[ProviderError] = []
        successful_requests = 0
        for release_id in RELEASES:
            try:
                release_rows = self._fred_release_dates(release_id, start, end)
            except ProviderError as exc:
                errors.append(f"FRED release {release_id} failed ({exc.cls})")
                failures.append(exc)
            else:
                successful_requests += 1
                rows.extend(release_rows)
        try:
            fomc_rows = self._fomc_meetings(start, end)
        except ProviderError as exc:
            errors.append(f"FOMC schedule failed ({exc.cls})")
            failures.append(exc)
        else:
            successful_requests += 1
            rows.extend(fomc_rows)
        rows.sort(key=lambda r: r["datetime"])
        if successful_requests == 0:
            error_class = max(
                (failure.cls for failure in failures),
                key=lambda cls: _ERROR_CLASS_PRIORITY.get(cls, 0),
                default=PERMANENT,
            )
            retry_after = max(
                (
                    failure.retry_after
                    for failure in failures
                    if failure.cls == RATE_LIMITED and failure.retry_after is not None
                ),
                default=None,
            )
            raise ProviderError(
                "FRED calendar all sources failed: " + "; ".join(errors[:3]),
                cls=error_class,
                retry_after=retry_after,
            )
        if errors:
            return PartialProviderResult(result=rows, errors=tuple(errors))
        return rows

    # ---- FRED release dates ----

    def _fred_release_dates(self, release_id: int, start: str, end: str) -> list[dict[str, Any]]:
        if not self.api_key:
            raise ProviderError("FRED calendar: missing DATA_FRED_API_KEY (local .env)")
        name, time_et, importance = RELEASES[release_id]
        try:
            resp = self._client.get(
                f"{FRED_BASE}/release/dates",
                params={
                    "release_id": release_id,
                    "api_key": self.api_key,
                    "file_type": "json",
                    "include_release_dates_with_no_data": "true",
                    "sort_order": "asc",
                    # #83: the realtime window bounds the *release dates* server-side — without
                    # it the API returns the full 1949→today history for a release.
                    "realtime_start": start,
                    "realtime_end": end,
                },
            )
        except httpx.TransportError as exc:
            raise ProviderError.from_exception(
                exc, detail=f"FRED release {release_id} request failed"
            ) from exc
        if resp.status_code != 200:
            # #103/S-1: one error boundary — classification + redaction (from_http).
            raise ProviderError.from_http(f"FRED release {release_id}", resp)
        try:
            data = resp.json()
            rows = data.get("release_dates") if isinstance(data, dict) else None
            if not isinstance(rows, list):
                raise TypeError("release_dates must be a list")
            out: list[dict[str, Any]] = []
            for row in rows:
                if not isinstance(row, dict):
                    raise TypeError("release date row must be an object")
                day = str(row.get("date", ""))
                if not day:
                    continue
                out.append(EconomicRow(
                    id=f"econ-fred-{release_id}-{day}",
                    title=name,
                    datetime=et_instant(day, time_et),
                    importance=importance,
                    country="US",
                    source="fred",
                ).model_dump(mode="json"))
        except (AttributeError, KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ProviderError(f"FRED release {release_id}: invalid response") from exc
        return out

    # ---- FOMC meetings (federalreserve.gov, official) ----

    def _fomc_meetings(self, start: str, end: str) -> list[dict[str, Any]]:
        """Parse the official FOMC calendar page into decision-day events.

        Statement lands 14:00 ET on the **second** day of each meeting (#83 §3C); the
        ``*`` press-conference marker is the presence of a ``fomcpressconf`` link.
        """
        try:
            resp = self._client.get(FOMC_CALENDAR_URL)
        except httpx.TransportError as exc:
            raise ProviderError.from_exception(exc, detail="FOMC calendar request failed") from exc
        if resp.status_code != 200:
            # #103/S-1: one error boundary — classification + redaction (from_http).
            raise ProviderError.from_http("FOMC calendar", resp)
        html = resp.text
        if not isinstance(html, str):
            raise ProviderError("FOMC calendar returned an invalid document")
        try:
            start_day, end_day = date.fromisoformat(start), date.fromisoformat(end)
        except ValueError as exc:
            raise ProviderError("FOMC calendar received an invalid date range") from exc
        if end_day < start_day:
            raise ProviderError("FOMC calendar received an invalid date range")
        requested_years = set(range(start_day.year, end_day.year + 1))
        out: list[dict[str, Any]] = []
        sections = _year_sections(html)
        if not sections:
            raise ProviderError("FOMC calendar parse returned no year sections")
        available_years = {year for _, year in sections}
        missing_years = requested_years - available_years
        if missing_years:
            missing = ", ".join(str(year) for year in sorted(missing_years))
            raise ProviderError(f"FOMC calendar requested year coverage missing: {missing}")
        valid_meeting_years: set[int] = set()
        for idx, (section_start, year) in enumerate(sections):
            section_end = sections[idx + 1][0] if idx + 1 < len(sections) else len(html)
            section = html[section_start:section_end]
            months = list(re.finditer(r"fomc-meeting__month[^>]*>\s*<strong>(\w+)</strong>", section))
            for midx, m in enumerate(months):
                month = _MONTHS.get(m.group(1))
                if month is None:
                    continue
                block_end = months[midx + 1].start() if midx + 1 < len(months) else len(section)
                month_block = section[m.end():block_end]
                date_m = re.search(r"fomc-meeting__date[^>]*>\s*([\d-]+)\s*</div>", month_block)
                if date_m is None:
                    continue
                day_range = date_m.group(1)
                days = [int(d) for d in re.findall(r"\d+", day_range)]
                if not days:
                    continue
                decision_day = days[1] if len(days) >= 2 else days[0]  # statement on the SECOND day
                press_conf = "fomcpressconf" in month_block[date_m.end():]
                try:
                    meeting_day = date(year, month, decision_day)
                except ValueError:
                    continue  # e.g. a past year's malformed row — skip, never crash the run
                valid_meeting_years.add(year)
                if not (start_day <= meeting_day <= end_day):
                    continue
                day = meeting_day.isoformat()
                title = "FOMC Meeting" + (" (press conference)" if press_conf else "")
                out.append(EconomicRow(
                    id=f"econ-fomc-{day}",
                    title=title,
                    datetime=et_instant(day, "14:00"),
                    importance="high",
                    country="US",
                    source="fomc",
                ).model_dump(mode="json"))
        invalid_years = requested_years - valid_meeting_years
        if invalid_years:
            invalid = ", ".join(str(year) for year in sorted(invalid_years))
            raise ProviderError(f"FOMC calendar has no meeting rows for requested years: {invalid}")
        return out


def _year_sections(html: str) -> list[tuple[int, int]]:
    """``(section_start, year)`` for each ``YYYY FOMC Meetings`` panel heading."""
    out: list[tuple[int, int]] = []
    for m in re.finditer(r"<h4><a id=\"\d+\">(\d{4}) FOMC Meetings</a></h4>", html):
        out.append((m.end(), int(m.group(1))))
    return out
