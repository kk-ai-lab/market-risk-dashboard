"""Official Treasury nominal and real CMT adapter.

Source authorization: the Daily Treasury nominal and real yield-curve datasets are CC0:
https://catalog.data.gov/dataset/interest-rate-statistics-daily-treasury-yield-curve-rates
https://catalog.data.gov/dataset/daily-treasury-real-yield-curve-rates
This does not license unrelated Treasury or third-party material. CMTs are daily
interpolated par yields in percent, not tradable intraday quotes. NEW_DATE is an
observation date, never a release timestamp; no publication-time guarantee is made.
Treasury changed to a monotone convex spline method on December 6, 2021:
https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics/treasury-yield-curve-methodology
Historical comparisons spanning that change need methodological care.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any
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
REAL_LICENSE_SOURCE = "https://catalog.data.gov/dataset/daily-treasury-real-yield-curve-rates"
SERIES = {
    "DGS2": "BC_2YEAR", "DGS5": "BC_5YEAR", "DGS10": "BC_10YEAR",
    "DFII5": "TC_5YEAR", "DFII10": "TC_10YEAR", "T5YIFR": None, "T10YIE": None,
}
DEFAULT_CURVE_SERIES = ("DGS2", "DGS10")
NOMINAL_SERIES = {"DGS2", "DGS5", "DGS10"}
REAL_SERIES = {"DFII5", "DFII10"}
ATOM = "{http://www.w3.org/2005/Atom}"
META = "{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}"
DATA = "{http://schemas.microsoft.com/ado/2007/08/dataservices}"
FEED_TITLE = "DailyTreasuryYieldCurveRateData"
FEED_ID = TREASURY_XML + "-item?data=daily_treasury_yield_curve"
REAL_FEED_TITLE = "DailyTreasuryRealYieldCurveRateData"
REAL_FEED_ID = TREASURY_XML + "-item?data=daily_treasury_real_yield_curve"


def _trusted_empty_feed(root: ET.Element, title: str, feed_id: str) -> bool:
    """Recognize only Treasury's observed Atom metadata shape for empty years."""
    required = {ATOM + name for name in ("id", "title", "updated")}
    allowed = required | {ATOM + "link"}
    if root.text and root.text.strip():
        return False
    if any(node.tag not in allowed or len(node) or (node.tail and node.tail.strip()) for node in root):
        return False
    if any(len(root.findall(tag)) != 1 for tag in required):
        return False
    if root.findtext(ATOM + "title") != title or root.findtext(ATOM + "id") != feed_id:
        return False
    try:
        updated = datetime.fromisoformat(root.findtext(ATOM + "updated") or "")
    except ValueError:
        return False
    if updated.tzinfo is None:
        return False
    return all(
        node.get("rel") == "self" and node.get("title") == title
        and node.get("href") == title and not (node.text or "").strip()
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
        selected = list(dict.fromkeys(DEFAULT_CURVE_SERIES if series_ids is None else series_ids))
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
        requested = set(series_ids)
        nominal_ids = requested & NOMINAL_SERIES
        real_ids = requested & REAL_SERIES
        if "T10YIE" in requested:
            nominal_ids.add("DGS10")
            real_ids.add("DFII10")
        if "T5YIFR" in requested:
            nominal_ids.update({"DGS5", "DGS10"})
            real_ids.update({"DFII5", "DFII10"})
        nominal = self._get_year_feed(year, now, sorted(nominal_ids), real=False) if nominal_ids else None
        real = self._get_year_feed(year, now, sorted(real_ids), real=True) if real_ids else None
        if nominal_ids and nominal is None or real_ids and real is None:
            return None
        combined: dict[str, list[dict[str, Any]]] = {}
        if nominal is not None:
            combined.update(nominal["series"])
        if real is not None:
            combined.update(real["series"])
        if "T10YIE" in requested:
            nominal_by_date = {row["date"]: row["value"] for row in combined["DGS10"]}
            real_by_date = {row["date"]: row["value"] for row in combined["DFII10"]}
            combined["T10YIE"] = [
                {"date": day, "value": nominal_by_date[day] - real_by_date[day]}
                for day in sorted(nominal_by_date.keys() & real_by_date.keys())
            ]
        if "T5YIFR" in requested:
            nominal_5 = {row["date"]: row["value"] for row in combined["DGS5"]}
            nominal_10 = {row["date"]: row["value"] for row in combined["DGS10"]}
            real_5 = {row["date"]: row["value"] for row in combined["DFII5"]}
            real_10 = {row["date"]: row["value"] for row in combined["DFII10"]}
            shared_dates = nominal_5.keys() & nominal_10.keys() & real_5.keys() & real_10.keys()
            forward_values = []
            for day in sorted(shared_dates):
                breakeven_5 = (nominal_5[day] - real_5[day]) / 100
                breakeven_10 = (nominal_10[day] - real_10[day]) / 100
                if 1 + breakeven_5 <= 0 or 1 + breakeven_10 <= 0:
                    raise ProviderError("Treasury: invalid breakeven inputs for T5YIFR")
                forward = (((1 + breakeven_10) ** 10 / (1 + breakeven_5) ** 5) ** 0.2 - 1) * 100
                forward_values.append({"date": day, "value": forward})
            combined["T5YIFR"] = forward_values
        output = {series_id: combined.get(series_id, []) for series_id in requested}
        if any(not rows for rows in output.values()):
            raise ProviderError("Treasury: requested maturity missing or empty")
        return {
            "series": output,
            "source": nominal["source"] if nominal is not None else real["source"],
            "real_source": real["source"] if real is not None and nominal is not None else None,
            "year": year,
            "checked_at": datetime.now(UTC).isoformat(),
            "license_source": LICENSE_SOURCE,
            "real_license_source": REAL_LICENSE_SOURCE if real is not None else None,
            "frequency": "daily",
            "unit": "percent",
            "date_semantics": "observation_date",
        }

    def _get_year_feed(
        self, year: int, now: datetime, series_ids: list[str], *, real: bool,
    ) -> dict[str, Any] | None:
        data_name = "daily_treasury_real_yield_curve" if real else "daily_treasury_yield_curve"
        title = REAL_FEED_TITLE if real else FEED_TITLE
        feed_id = REAL_FEED_ID if real else FEED_ID
        fields_by_series = {series_id: SERIES[series_id] for series_id in series_ids}
        params = {"data": data_name, "field_tdr_date_value": str(year)}
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
            if not _trusted_empty_feed(root, title, feed_id):
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
                field = fields_by_series[series_id]
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
        return {"series": output, "source": source_url, "year": year, "checked_at": checked_at}

    def get_series_batch(self, *, year: int | None = None) -> dict[str, list[dict[str, Any]]]:
        """Convenience series view; use get_yield_curve to retain retrieval provenance."""
        return self.get_yield_curve(year=year)["series"]
