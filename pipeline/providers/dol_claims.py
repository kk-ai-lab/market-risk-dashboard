"""U.S. Department of Labor non-embargoed weekly UI claims series."""

from __future__ import annotations

import math
import re
import time
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from typing import Any
from xml.etree import ElementTree as ET

import httpx
from pypdf import PdfReader

from pipeline.providers.base import (
    MAX_RESPONSE_BYTES,
    BaseProvider,
    ProviderError,
    ProviderHealth,
    guarded_client,
)
from pipeline.utils import now_utc

DOL_CLAIMS_URL = "https://oui.doleta.gov/unemploy/wkclaims/report.asp"
DOL_ADVANCE_CLAIMS_URL = "https://www.dol.gov/ui/data.pdf"
SERIES_PATHS = {"ICSA": ("InitialClaims", "SA"), "CCSA": ("ContinuedClaims", "SA")}


def _release_week_end(token: str, release_date: date) -> date:
    for days_ago in range(1, 36):
        candidate = release_date - timedelta(days=days_ago)
        if candidate.weekday() != 5:
            continue
        try:
            if datetime.strptime(f"{token} {candidate.year}", "%B %d %Y").date() == candidate:
                return candidate
        except ValueError:
            break
    raise ValueError("weekly claims release contains an unknown week-ending date")


def parse_advance_claims_text(text: str, *, fetched_at: str) -> dict[str, list[dict[str, Any]]]:
    normalized = " ".join(text.split())
    release_match = re.search(
        r"(?:Monday|Tuesday|Wednesday|Thursday|Friday),\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})",
        normalized,
    )
    initial_match = re.search(
        r"In the week ending\s+([A-Za-z]+\s+\d{1,2}),\s+the advance figure for seasonally adjusted initial claims was\s+([\d,]+)",
        normalized,
    )
    continued_match = re.search(
        r"advance number for seasonally adjusted insured unemployment during the week ending\s+([A-Za-z]+\s+\d{1,2}) was\s+([\d,]+)",
        normalized,
    )
    if not release_match or not initial_match or not continued_match:
        raise ValueError("weekly claims release is missing its date or national series values")
    release_date = datetime.strptime(release_match.group(1), "%B %d, %Y").date()
    initial_date = _release_week_end(initial_match.group(1), release_date)
    continued_date = _release_week_end(continued_match.group(1), release_date)
    if continued_date != initial_date - timedelta(days=7):
        raise ValueError("weekly claims release periods are inconsistent")

    def row(day: date, raw_value: str) -> dict[str, Any]:
        return {
            "date": day.isoformat(),
            "value": int(raw_value.replace(",", "")) / 1000.0,
            "fetched_at": fetched_at,
            "released_on": release_date.isoformat(),
        }

    output: dict[str, list[dict[str, Any]]] = {
        "ICSA": [row(initial_date, initial_match.group(2))],
        "CCSA": [row(continued_date, continued_match.group(2))],
    }
    previous_initial = re.search(
        r"previous week's level was revised\s+(?:up|down) by\s+[\d,]+ from\s+[\d,]+ to\s+([\d,]+)",
        normalized[initial_match.end():continued_match.start()],
    )
    previous_continued = re.search(
        r"previous week's level was revised\s+(?:up|down) by\s+[\d,]+ from\s+[\d,]+ to\s+([\d,]+)",
        normalized[continued_match.end():],
    )
    if previous_initial:
        output["ICSA"].insert(0, row(initial_date - timedelta(days=7), previous_initial.group(1)))
    if previous_continued:
        output["CCSA"].insert(0, row(continued_date - timedelta(days=7), previous_continued.group(1)))
    return output


class DolClaimsProvider(BaseProvider):
    name = "dol_claims"
    domain = "macro"
    hosts = ("oui.doleta.gov", "www.dol.gov")

    def __init__(self, settings=None) -> None:
        super().__init__(settings)
        self._client = guarded_client(set(self.hosts), timeout=25.0)
        self._cache: dict[tuple[str, str], tuple[dict[str, list[dict[str, Any]]], str]] = {}
        self._advance_cache: dict[str, list[dict[str, Any]]] | None = None

    def health(self) -> ProviderHealth:
        started = time.monotonic()
        try:
            today = datetime.now(UTC).date()
            result = self.get_series("ICSA", start=(today.replace(year=today.year - 1)).isoformat(), end=today.isoformat())
            return ProviderHealth(
                provider=self.name,
                ok=bool(result),
                latency_ms=round((time.monotonic() - started) * 1000, 1),
                error=None if result else "no recent initial claims observations",
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

    def _fetch(self, start: str, end: str) -> tuple[dict[str, list[dict[str, Any]]], str]:
        key = (start, end)
        if key in self._cache:
            return self._cache[key]
        start_day, end_day = date.fromisoformat(start), date.fromisoformat(end)
        response = self._client.post(
            DOL_CLAIMS_URL,
            data={
                "level": "us",
                "strtdate": str(start_day.year),
                "enddate": str(end_day.year),
                "filetype": "xml",
                "submit": "Submit",
            },
        )
        if response.status_code != 200:
            raise ProviderError.from_http("DOL UI claims", response)
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ProviderError("DOL UI claims: XML exceeds response size limit")
        try:
            root = ET.fromstring(response.content)
            if root.tag != "r539cyNational":
                raise ValueError("unexpected ETA 539 report root")
            release_date = datetime.strptime(root.attrib["rundate"], "%m/%d/%Y").date().isoformat()
            fetched_at = now_utc()
            output: dict[str, list[dict[str, Any]]] = {series: [] for series in SERIES_PATHS}
            seen: set[str] = set()
            for week in root.findall("week"):
                week_ended = datetime.strptime(
                    week.findtext("weekEnded") or "", "%m/%d/%Y"
                ).date()
                day = week_ended.isoformat()
                if day in seen:
                    raise ValueError("duplicate claims week")
                seen.add(day)
                if not start_day <= week_ended <= end_day:
                    continue
                for series_id, (section, field) in SERIES_PATHS.items():
                    raw = week.findtext(f"{section}/{field}")
                    if raw is None or raw.strip() in {"", "-", "."}:
                        continue
                    value = float(raw.replace(",", "")) / 1000.0
                    if not math.isfinite(value) or value < 0:
                        raise ValueError("invalid claims value")
                    output[series_id].append({
                        "date": day,
                        "value": value,
                        "fetched_at": fetched_at,
                        "released_on": release_date,
                    })
            if any(not rows for rows in output.values()):
                raise ValueError("one or more claims series are empty")
            for rows in output.values():
                rows.sort(key=lambda row: row["date"])
            self._cache[key] = (output, release_date)
            return output, release_date
        except (ET.ParseError, KeyError, TypeError, ValueError, OverflowError) as exc:
            raise ProviderError("DOL UI claims: ETA 539 XML could not be parsed") from exc

    def get_series(self, series_id: str, *, start: str, end: str) -> list[dict[str, Any]]:
        if series_id not in SERIES_PATHS:
            raise ProviderError("DOL UI claims: unsupported macro target")
        try:
            start_day, end_day = date.fromisoformat(start), date.fromisoformat(end)
        except ValueError as exc:
            raise ProviderError("DOL UI claims: invalid date range") from exc
        if start_day > end_day or (end_day - start_day).days > 6 * 366:
            raise ProviderError("DOL UI claims: date range is invalid or exceeds six years")
        rows, _ = self._fetch(start_day.isoformat(), end_day.isoformat())
        combined = {row["date"]: row for row in rows[series_id]}
        for row in self._fetch_advance_release()[series_id]:
            if start_day <= date.fromisoformat(row["date"]) <= end_day:
                combined[row["date"]] = row
        return [combined[day] for day in sorted(combined)]

    def _fetch_advance_release(self) -> dict[str, list[dict[str, Any]]]:
        if self._advance_cache is not None:
            return self._advance_cache
        try:
            response = self._client.get(DOL_ADVANCE_CLAIMS_URL, follow_redirects=False)
            if response.status_code != 200:
                raise ProviderError.from_http("DOL advance weekly claims", response)
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise ProviderError("DOL advance weekly claims: PDF exceeds response size limit")
            reader = PdfReader(BytesIO(response.content), strict=True)
            text = "\n".join(page.extract_text() or "" for page in reader.pages[:2])
            parsed = parse_advance_claims_text(text, fetched_at=now_utc())
            self._advance_cache = parsed
            return parsed
        except ProviderError:
            raise
        except (httpx.HTTPError, OSError, ValueError, TypeError) as exc:
            raise ProviderError("DOL advance weekly claims: official release could not be parsed") from exc
