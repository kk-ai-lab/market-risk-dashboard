"""Report whether approved data hosts answer from the current runner.

The probe is read-only by design. It never publishes data, touches Pages or the
cloud-state checkpoint, and it always exits 0 so a blocked host stays a report,
not a merge gate.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Callable, Literal

ProbeHost = Literal["yahoo", "nasdaq", "fred_graph", "fred_api"]
ProbeVerdict = Literal["PASS", "FAIL", "SKIP"]
ProbeResult = tuple[ProbeHost, ProbeVerdict, str]

NASDAQ_URL = "https://api.nasdaq.com/api/quote/COMP/info?assetclass=index"
FRED_GRAPH_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=VIXCLS"
FRED_API_URL = "https://api.stlouisfed.org/fred/series/observations"
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
    ),
    "Accept": "application/json, text/csv, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

Download = Callable[..., object]


def _detail(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"[:200].replace("|", "/").replace("\n", " ")


def probe_yahoo(download: Download) -> ProbeResult:
    try:
        frame = download("^GSPC", period="5d", interval="1d", threads=False, progress=False)
    except Exception as exc:  # noqa: BLE001 - a probe reports the failure
        return ("yahoo", "FAIL", _detail(exc))
    if frame is None or getattr(frame, "empty", True):
        return ("yahoo", "FAIL", "empty frame (rate limited)")
    rows = len(frame)
    return ("yahoo", "PASS", f"{rows} rows")


def probe_nasdaq(client) -> ProbeResult:
    try:
        response = client.get(NASDAQ_URL, headers=BROWSER_HEADERS)
    except Exception as exc:  # noqa: BLE001
        return ("nasdaq", "FAIL", _detail(exc))
    if response.status_code != 200:
        return ("nasdaq", "FAIL", f"HTTP {response.status_code}")
    try:
        payload = response.json()
    except Exception as exc:  # noqa: BLE001
        return ("nasdaq", "FAIL", f"invalid JSON: {_detail(exc)}")
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict) or not data.get("primaryData"):
        return ("nasdaq", "FAIL", "missing data.primaryData")
    return ("nasdaq", "PASS", "data.primaryData present")


def probe_fred_graph(client) -> ProbeResult:
    try:
        response = client.get(FRED_GRAPH_URL)
    except Exception as exc:  # noqa: BLE001
        return ("fred_graph", "FAIL", _detail(exc))
    if response.status_code != 200:
        return ("fred_graph", "FAIL", f"HTTP {response.status_code}")
    header = response.text.splitlines()[0].strip() if response.text else ""
    if header != "observation_date,VIXCLS":
        return ("fred_graph", "FAIL", f"unexpected header: {header[:80]}")
    return ("fred_graph", "PASS", "keyless CSV header")


def probe_fred_api(client, api_key: str | None) -> ProbeResult:
    key = (api_key or "").strip()
    if not key:
        return ("fred_api", "SKIP", "not_configured")
    try:
        response = client.get(
            FRED_API_URL,
            params={"series_id": "VIXCLS", "limit": 1, "file_type": "json", "api_key": key},
        )
    except Exception as exc:  # noqa: BLE001
        return ("fred_api", "FAIL", _detail(exc))
    if response.status_code != 200:
        return ("fred_api", "FAIL", f"HTTP {response.status_code}")
    return ("fred_api", "PASS", "series/observations reachable")


def run_probe(*, client, download: Download, fred_api_key: str | None) -> list[ProbeResult]:
    return [
        probe_yahoo(download),
        probe_nasdaq(client),
        probe_fred_graph(client),
        probe_fred_api(client, fred_api_key),
    ]


def format_summary(results: list[ProbeResult]) -> str:
    lines = [
        "## Source reachability probe",
        "",
        "Read-only. A FAIL means this runner's egress could not reach the host.",
        "It does not change the data or block a merge.",
        "",
        "| Host | Verdict | Detail |",
        "|---|---|---|",
    ]
    for host, verdict, detail in results:
        lines.append(f"| {host} | {verdict} | {detail} |")
    return "\n".join(lines) + "\n"


def main() -> int:
    import httpx
    import yfinance

    results = run_probe(
        client=httpx.Client(timeout=20.0, follow_redirects=True),
        download=yfinance.download,
        fred_api_key=os.environ.get("DATA_FRED_API_KEY"),
    )
    for host, verdict, detail in results:
        print(f"{host} {verdict} {detail}")
    summary = format_summary(results)
    destination = os.environ.get("GITHUB_STEP_SUMMARY")
    if destination:
        with Path(destination).open("a", encoding="utf-8") as handle:
            handle.write(summary)
    print(summary, file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
