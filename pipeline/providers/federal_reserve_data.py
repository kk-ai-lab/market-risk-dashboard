from __future__ import annotations

import csv
import io
import math
import re
import time
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urljoin, urlparse

import httpx

from pipeline.providers.base import (
    MAX_RESPONSE_BYTES,
    BaseProvider,
    ProviderError,
    ProviderHealth,
    guarded_client,
)
from pipeline.utils import now_utc

DDP_ROOT = "https://www.federalreserve.gov/datadownload/"
DDP_CHOICES = {
    "H41": ("Choose.aspx?rel=H41", {"table1": "Table 1", "table5": "Table 5"}),
    "H10": ("Choose.aspx?rel=H10", {"monthly_index": "Monthly Indexes", "monthly_rates": "Monthly rates"}),
}
H41_COLUMNS = {
    "WALCL": ("table5", "Assets: Total Assets: Total assets (Less eliminations from consolidation): Wednesday level"),
    "WRESBAL": ("table1", "Liabilities and Capital: Other Factors Draining Reserve Balances: Reserve balances with Federal Reserve Banks: Wednesday level"),
    "WTREGEN": ("table1", "Liabilities and Capital: Liabilities: Deposits with F.R. Banks, other than reserve balances: U.S. Treasury, General Account: Wednesday level"),
}
H10_COLUMNS = {
    "DTWEXBGS": ("monthly_index", "Nominal Broad Dollar Index"),
    "DTWEXAFEGS": ("monthly_index", "Nominal Advanced Foreign Economies Dollar Index"),
    "DEXUSEU": ("monthly_rates", "SPOT EXCHANGE RATE - EURO AREA"),
    "DEXCHUS": ("monthly_rates", "CHINA -- SPOT EXCHANGE RATE, YUAN/US$"),
    "DEXJPUS": ("monthly_rates", "JAPAN -- SPOT EXCHANGE RATE, YEA/US$"),
}
SUPPORTED_SERIES = frozenset(H41_COLUMNS) | frozenset(H10_COLUMNS)


class _PackageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.packages: dict[str, dict[str, str]] = {}
        self.download_href: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key: value or "" for key, value in attrs}
        if tag == "input" and re.fullmatch(r"FreqRequest_\d+", attributes.get("id", "")):
            self.packages[attributes["id"]] = {
                "value": attributes.get("value", ""),
                "for": attributes["id"],
            }
        if tag == "a" and attributes.get("id") == "DataURL":
            self.download_href = attributes.get("href")

    def handle_data(self, data: str) -> None:
        # DDP's labels are the sibling <label for="FreqRequest_n"> element.
        if self.packages:
            for package in self.packages.values():
                if package.get("for") == getattr(self, "active_label", None):
                    package["label"] = (package.get("label", "") + data).strip()


class _PackageLabelParser(_PackageParser):
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        super().handle_starttag(tag, attrs)
        attributes = dict(attrs)
        if tag == "label":
            self.active_label = attributes.get("for")
        elif tag not in {"label"}:
            self.active_label = None


class FederalReserveDataProvider(BaseProvider):
    name = "federal_reserve_data"
    domain = "macro"
    hosts = ("www.federalreserve.gov",)

    def __init__(self, settings=None) -> None:
        super().__init__(settings)
        self._client = guarded_client(set(self.hosts), timeout=30.0)
        self._parsed: dict[str, dict[str, list[dict[str, Any]]]] = {}

    def health(self) -> ProviderHealth:
        started = time.monotonic()
        today = datetime.now(UTC).date()
        try:
            start = (today - timedelta(days=5 * 365)).isoformat()
            rows = self.get_series("WALCL", start=start, end=today.isoformat())
            return ProviderHealth(
                provider=self.name,
                ok=bool(rows),
                latency_ms=round((time.monotonic() - started) * 1000, 1),
                error=None if rows else "no H.4.1 observations",
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

    def _get_text(self, url: str, *, params: dict[str, str] | None = None) -> str:
        try:
            with self._client.stream("GET", url, params=params, follow_redirects=False) as response:
                if response.status_code != 200:
                    response.raise_for_status()
                    raise ProviderError("Federal Reserve DDP: expected HTTP 200")
                body = bytearray()
                for chunk in response.iter_bytes(chunk_size=64 * 1024):
                    if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise ProviderError("Federal Reserve DDP: response exceeds size limit")
                    body.extend(chunk)
                content_type = response.headers.get("content-type", "").lower()
        except httpx.HTTPError as exc:
            raise ProviderError.from_exception(exc, detail="Federal Reserve DDP request failed") from exc
        if not content_type.startswith(("text/csv", "text/html")):
            raise ProviderError("Federal Reserve DDP: unexpected content type")
        try:
            return body.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ProviderError("Federal Reserve DDP: response was not UTF-8") from exc

    def _package_csv(self, release: str, package_key: str) -> list[dict[str, Any]]:
        if release not in DDP_CHOICES or package_key not in DDP_CHOICES[release][1]:
            raise ProviderError("Federal Reserve DDP: unsupported package")
        if package_key in self._parsed:
            return self._parsed[package_key]["rows"]
        choose_path, package_labels = DDP_CHOICES[release]
        choose_html = self._get_text(urljoin(DDP_ROOT, choose_path))
        parser = _PackageLabelParser()
        parser.feed(choose_html)
        package = next(
            (
                item for item in parser.packages.values()
                if (
                    re.search(r"\bTable 1 \[", item.get("label", ""), re.IGNORECASE)
                    if package_key == "table1"
                    else package_labels[package_key].lower() in item.get("label", "").lower()
                )
            ),
            None,
        )
        if package is None:
            raise ProviderError("Federal Reserve DDP: approved preformatted package was not found")
        parameters = parse_qs(package["value"], keep_blank_values=True)
        if parameters.get("rel") != [release] or parameters.get("filetype") != ["csv"]:
            raise ProviderError("Federal Reserve DDP: package identity was unexpected")
        parameters["lastobs"] = ["300" if release == "H41" else "120"]
        download_html = self._get_text(urljoin(DDP_ROOT, "Download.aspx"), params=dict(
            (key, values[0]) for key, values in parameters.items()
        ))
        output_parser = _PackageParser()
        output_parser.feed(download_html)
        if not output_parser.download_href:
            raise ProviderError("Federal Reserve DDP: direct CSV link was absent")
        output_url = urljoin(DDP_ROOT, output_parser.download_href)
        parsed_url = urlparse(output_url)
        output_query = parse_qs(parsed_url.query, keep_blank_values=True)
        if (
            parsed_url.scheme != "https" or parsed_url.hostname not in self.hosts
            or parsed_url.path != "/datadownload/Output.aspx"
            or output_query.get("rel") != [release]
            or output_query.get("filetype") != ["csv"]
        ):
            raise ProviderError("Federal Reserve DDP: direct CSV host or path was not approved")
        text = self._get_text(output_url)
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames or "Series Description" not in reader.fieldnames:
            raise ProviderError("Federal Reserve DDP: CSV columns were invalid")
        rows = [row for row in reader if row.get("Series Description") not in (None, "", "Unit:")]
        self._parsed[package_key] = {"rows": rows, "source": output_url}
        return rows

    def get_series(self, series_id: str, *, start: str, end: str) -> list[dict[str, Any]]:
        if series_id not in SUPPORTED_SERIES:
            raise ProviderError("Federal Reserve Board: unsupported macro target")
        try:
            start_day, end_day = date.fromisoformat(start), date.fromisoformat(end)
        except ValueError as exc:
            raise ProviderError("Federal Reserve Board: invalid date range") from exc
        if start_day > end_day or (end_day - start_day).days > 5 * 366:
            raise ProviderError("Federal Reserve Board: date range is invalid or exceeds five years")
        package_key, column = (H41_COLUMNS if series_id in H41_COLUMNS else H10_COLUMNS)[series_id]
        release = "H41" if series_id in H41_COLUMNS else "H10"
        fetched_at = now_utc()
        output: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in self._package_csv(release, package_key):
            period = str(row.get("Series Description", "")).strip()
            try:
                observed = date.fromisoformat(period) if release == "H41" else datetime.strptime(period, "%Y-%m").date().replace(day=1)
                key = observed.isoformat()
                raw = row[column]
                value = float(raw)
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
            if key in seen or not math.isfinite(value):
                raise ProviderError("Federal Reserve Board: invalid or duplicate observation")
            seen.add(key)
            if start_day <= observed <= end_day:
                output.append({"date": key, "value": value, "fetched_at": fetched_at})
        output.sort(key=lambda item: item["date"])
        if not output:
            raise ProviderError(f"Federal Reserve Board: no approved observation for {series_id}")
        return output
