"""Official Treasury CMT adapter.

Source authorization: the exact Daily Treasury Yield Curve Rates dataset is CC0:
https://catalog.data.gov/dataset/interest-rate-statistics-daily-treasury-yield-curve-rates
This does not license unrelated Treasury or third-party material. CMTs are daily
interpolated par yields in percent, not tradable intraday quotes. NEW_DATE is an
observation date, never a release timestamp; no publication-time guarantee is made.
Treasury changed to a monotone convex spline method on December 6, 2021:
https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/treasury-yield-curve-methodology
Historical comparisons spanning that change need methodological care.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any, Iterable
from xml.etree import ElementTree as ET
from xml.parsers import expat

import httpx

from pipeline.providers.base import (
    MAX_RESPONSE_BYTES,
    BaseProvider,
    ProviderError,
    ProviderHealth,
    guarded_client,
)

TREASURY_XML = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml"
LICENSE_SOURCE = "https://catalog.data.gov/dataset/interest-rate-statistics-daily-treasury-yield-curve-rates"
SERIES = {"DGS2": "BC_2YEAR", "DGS10": "BC_10YEAR"}
ATOM = "{http://www.w3.org/2005/Atom}"
META = "{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}"
DATA = "{http://schemas.microsoft.com/ado/2007/08/dataservices}"
FEED_TITLE = "DailyTreasuryYieldCurveRateData"
FEED_ID = TREASURY_XML + "-item?data=daily_treasury_yield_curve"


def _trusted_empty_feed(root: ET.Element) -> bool:
    """Recognize only Treasury's observed Atom metadata shape for empty years."""
    required = {ATOM + name for name in ("id", "title", "updated")}
    allowed = required | {ATOM + "link"}
    if root.text and root.text.strip():
        return False
    if any(node.tag not in allowed or len(node) or (node.tail and node.tail.strip()) for node in root):
        return False
    if any(len(root.findall(tag)) != 1 for tag in required):
        return False
    if root.findtext(ATOM + "title") != FEED_TITLE or root.findtext(ATOM + "id") != FEED_ID:
        return False
    try:
        updated = datetime.fromisoformat(root.findtext(ATOM + "updated") or "")
    except ValueError:
        return False
    if updated.tzinfo is None:
        return False
    return all(
        node.get("rel") == "self" and node.get("title") == FEED_TITLE
        and node.get("href") == FEED_TITLE and not (node.text or "").strip()
        for node in root.findall(ATOM + "link")
    )


def _safe_xml(body: bytes) -> ET.Element:
    # Expat recognizes declarations even in UTF-16; byte-string scans do not.
    parser = expat.ParserCreate()

    def reject(*args: Any) -> None:
        raise ProviderError("Treasury: XML DTD/entities forbidden")

    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    try:
        parser.Parse(body, True)
        return ET.fromstring(body)
    except (expat.ExpatError, ET.ParseError, ValueError) as exc:
        raise ProviderError("Treasury: malformed XML") from exc


class TreasuryProvider(BaseProvider):
    name = "treasury"
    domain = "treasury"
    hosts = ("home.treasury.gov",)

    def __init__(self, settings=None) -> None:
        super().__init__(settings)
        self._client = guarded_client(set(self.hosts), timeout=20.0)

    def health(self) -> ProviderHealth:
        try:
            self.get_yield_curve()
            return ProviderHealth(provider=self.name, ok=True)
        except ProviderError as exc:
            return ProviderHealth(provider=self.name, ok=False, error=str(exc))

    def get_yield_curve(self, *, year: int | None = None, series_ids: Iterable[str] | None = None) -> dict[str, Any]:
        """Fetch one calendar year; retain provenance and retrieval time separately.

        Missing/nonfinite yield fields are omitted individually, without filling dates.
        Duplicate dates and invalid/future dates reject the whole response. A requested
        year must contain at least one usable observation for each maturity. With no
        explicit year, an empty current-year feed falls back once to the previous
        year. Errors or nonempty feeds with unusable data never trigger fallback.
        """
        now = datetime.now(UTC)
        selected = list(dict.fromkeys(SERIES if series_ids is None else series_ids))
        if not selected or any(series_id not in SERIES for series_id in selected):
            raise ProviderError("Treasury: unsupported or empty series selection")
        default_year = year is None
        year = now.year if default_year else year
        if isinstance(year, bool) or not isinstance(year, int) or not 1990 <= year <= now.year:
            raise ProviderError("Treasury: invalid year")
        result = self._get_year(year, now, selected)
        if result is None and default_year and year > 1990:
            result = self._get_year(year - 1, now, selected)
        if result is None:
            raise ProviderError("Treasury: requested maturity missing or empty")
        return result

    def _get_year(self, year: int, now: datetime, series_ids: list[str]) -> dict[str, Any] | None:
        params = {"data": "daily_treasury_yield_curve", "field_tdr_date_value": str(year)}
        try:
            with self._client.stream("GET", TREASURY_XML, params=params, follow_redirects=False) as response:
                if response.status_code != 200:
                    response.raise_for_status()
                    raise ProviderError("Treasury: expected HTTP 200")
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise ProviderError("Treasury: response exceeds size limit")
                    body.extend(chunk)
                source_url = str(response.url)
        except httpx.HTTPError as exc:
            raise ProviderError.from_exception(exc, detail="Treasury: request failed") from exc
        checked_at = datetime.now(UTC).isoformat()
        root = _safe_xml(bytes(body))
        if root.tag != ATOM + "feed":
            raise ProviderError("Treasury: unexpected XML feed")
        entries = root.findall(ATOM + "entry")
        if not entries:
            if not _trusted_empty_feed(root):
                raise ProviderError("Treasury: untrusted empty feed")
            return None
        output: dict[str, list[dict[str, Any]]] = {key: [] for key in series_ids}
        seen: set[str] = set()
        for entry in entries:
            properties = entry.find(ATOM + "content/" + META + "properties")
            if properties is None:
                raise ProviderError("Treasury: missing observation properties")
            fields = {node.tag: node for node in properties}
            if len(fields) != len(properties):
                raise ProviderError("Treasury: duplicate observation field")
            raw_date = fields.get(DATA + "NEW_DATE")
            try:
                parsed = datetime.fromisoformat(raw_date.text or "") if raw_date is not None else None
                observation = parsed.date() if parsed is not None else None
                if observation is None or observation.year != year or observation > now.date():
                    raise ValueError("invalid date")
            except ValueError as exc:
                raise ProviderError("Treasury: invalid observation date") from exc
            key = observation.isoformat()
            if key in seen:
                raise ProviderError("Treasury: duplicate observation date")
            seen.add(key)
            for series_id in series_ids:
                field = SERIES[series_id]
                node = fields.get(DATA + field)
                if node is None or node.get(META + "null") == "true":
                    continue
                try:
                    value = float(node.text or "")
                except ValueError:
                    continue
                if math.isfinite(value):
                    output[series_id].append({"date": key, "value": value})
        if any(not rows for rows in output.values()):
            raise ProviderError("Treasury: requested maturity missing or empty")
        for rows in output.values():
            rows.sort(key=lambda row: row["date"])
        return {"series": output, "source": source_url, "year": year, "checked_at": checked_at,
                "license_source": LICENSE_SOURCE, "frequency": "daily", "unit": "percent",
                "date_semantics": "observation_date"}

    def get_series_batch(self, *, year: int | None = None) -> dict[str, list[dict[str, Any]]]:
        """Convenience series view; use get_yield_curve to retain retrieval provenance."""
        return self.get_yield_curve(year=year)["series"]
