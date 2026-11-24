from __future__ import annotations

import io
import math
import re
import time
from calendar import monthrange
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
import openpyxl

from pipeline.providers.base import (
    MAX_RESPONSE_BYTES,
    BaseProvider,
    ProviderError,
    ProviderHealth,
    guarded_client,
)
from pipeline.utils import now_utc

WORLD_BANK_COMMODITY_PAGE = "https://www.worldbank.org/en/research/commodity-markets"
WORKBOOK_NAME = "CMO-Historical-Data-Monthly.xlsx"
WORKBOOK_COLUMNS: dict[str, tuple[str, str, str]] = {
    "GC=F": ("Gold", "Gold", "$/troy oz"),
    "SI=F": ("Silver", "Silver", "$/troy oz"),
    "HG=F": ("Copper", "Copper", "$/mt"),
    "CL=F": ("Crude oil, WTI", "WTI Crude", "$/bbl"),
    "WB:BRENT": ("Crude oil, Brent", "Brent Crude", "$/bbl"),
    "WB:NATGAS_US": ("Natural gas, US", "U.S. Natural Gas", "$/mmbtu"),
    "WB:COCOA": ("Cocoa", "Cocoa", "$/kg"),
    "WB:COFFEE_ARABICA": ("Coffee, Arabica", "Arabica Coffee", "$/kg"),
    "WB:SOYBEANS": ("Soybeans", "Soybeans", "$/mt"),
    "WB:MAIZE": ("Maize", "Maize", "$/mt"),
    "WB:WHEAT_HRW": ("Wheat, US HRW", "U.S. Hard Red Winter Wheat", "$/mt"),
    "WB:ALUMINUM": ("Aluminum", "Aluminum", "$/mt"),
    "WB:IRON_ORE": ("Iron ore, cfr spot", "Iron Ore", "$/dmtu"),
}


class _WorkbookLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.hrefs.append(href)


def _period(value: Any) -> tuple[str, datetime] | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"(\d{4})M(0[1-9]|1[0-2])", value.strip())
    if not match:
        return None
    year, month = map(int, match.groups())
    return value.strip(), datetime(year, month, monthrange(year, month)[1], tzinfo=UTC)


def parse_monthly_workbook(data: bytes, symbols: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not symbols or len(symbols) != len(set(symbols)) or not set(symbols).issubset(WORKBOOK_COLUMNS):
        raise ProviderError("World Bank Pink Sheet: unsupported or duplicate commodity target")
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        sheet = workbook["Monthly Prices"]
        rows = sheet.iter_rows(values_only=True)
        required_headers = {WORKBOOK_COLUMNS[symbol][0] for symbol in symbols}
        header_row = None
        for row in rows:
            if row and required_headers.issubset({str(value).strip() for value in row if value is not None}):
                header_row = tuple(row)
                break
        if header_row is None:
            raise ValueError("Monthly Prices commodity header is absent")
        units_row = next(rows, None)
        if units_row is None:
            raise ValueError("Monthly Prices unit row is absent")
        indexes: dict[str, tuple[int, str]] = {}
        for symbol in symbols:
            header, _, unit = WORKBOOK_COLUMNS[symbol]
            try:
                index = header_row.index(header)
            except ValueError as exc:
                raise ValueError(f"series column is absent: {header}") from exc
            unit_text = str(units_row[index] or "").strip()
            normalized_unit = unit_text.removeprefix("(").removesuffix(")")
            if normalized_unit != unit:
                raise ValueError(f"series unit does not match the approved definition: {header}")
            indexes[symbol] = (index, unit)

        observations: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in symbols}
        seen_periods: set[str] = set()
        for row in rows:
            if not row:
                continue
            parsed = _period(row[0])
            if parsed is None:
                continue
            period, observed_at = parsed
            if period in seen_periods:
                raise ValueError("duplicate monthly period")
            seen_periods.add(period)
            for symbol, (index, unit) in indexes.items():
                if index >= len(row) or isinstance(row[index], bool):
                    continue
                try:
                    value = float(row[index])
                except (TypeError, ValueError):
                    continue  # Pink Sheet uses an ellipsis where a series has no value.
                if not math.isfinite(value) or value < 0:
                    raise ValueError(f"invalid value for {symbol}")
                observations[symbol].append({
                    "period": period.replace("M", "-"),
                    "date": observed_at.strftime("%Y-%m-%d"),
                    "value": value,
                    "unit": unit,
                })
        for values in observations.values():
            values.sort(key=lambda item: item["period"])
        if any(not values for values in observations.values()):
            raise ValueError("an approved commodity series has no observations")
        return {symbol: values[-2:] for symbol, values in observations.items()}
    except (KeyError, OSError, TypeError, ValueError, openpyxl.utils.exceptions.InvalidFileException) as exc:
        raise ProviderError("World Bank Pink Sheet: workbook could not be parsed") from exc


class WorldBankCommoditiesProvider(BaseProvider):
    name = "world_bank_commodities"
    domain = "commodities"
    hosts = ("www.worldbank.org", "thedocs.worldbank.org")

    def __init__(self, settings=None) -> None:
        super().__init__(settings)
        self._client = guarded_client(set(self.hosts), timeout=25.0)

    def _workbook_url(self) -> str:
        try:
            response = self._client.get(WORLD_BANK_COMMODITY_PAGE)
        except (httpx.HTTPError, ProviderError) as exc:
            raise ProviderError("World Bank Pink Sheet: catalog page request failed") from exc
        if response.status_code != 200:
            raise ProviderError.from_http("World Bank Pink Sheet catalog", response)
        parser = _WorkbookLinkParser()
        parser.feed(response.text)
        matches = [href for href in parser.hrefs if WORKBOOK_NAME in href]
        if len(matches) != 1:
            raise ProviderError("World Bank Pink Sheet: monthly workbook link was not unique")
        url = urljoin(WORLD_BANK_COMMODITY_PAGE, matches[0])
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "thedocs.worldbank.org":
            raise ProviderError("World Bank Pink Sheet: workbook link host is not approved")
        return url

    def health(self) -> ProviderHealth:
        started = time.monotonic()
        try:
            self.get_monthly_prices(list(WORKBOOK_COLUMNS))
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

    def get_monthly_prices(self, symbols: list[str]) -> dict[str, list[dict[str, Any]]]:
        url = self._workbook_url()
        try:
            response = self._client.get(url)
        except (httpx.HTTPError, ProviderError) as exc:
            raise ProviderError("World Bank Pink Sheet: workbook request failed") from exc
        if response.status_code != 200:
            raise ProviderError.from_http("World Bank Pink Sheet workbook", response)
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise ProviderError("World Bank Pink Sheet: workbook exceeds response size limit")
        return parse_monthly_workbook(response.content, symbols)
