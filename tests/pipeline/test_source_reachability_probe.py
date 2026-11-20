from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from scripts.probe_source_reachability import format_summary, main, run_probe

FRED_CSV = "observation_date,VIXCLS\n2026-11-19,20.1\n"
NASDAQ_JSON = {"data": {"primaryData": {"symbol": "COMP", "lastSalePrice": "$20,000"}}}


class Frame:
    def __init__(self, rows: int = 5):
        self.rows = rows
        self.empty = rows == 0

    def __len__(self) -> int:
        return self.rows


def transport(calls: list[httpx.Request], *, nasdaq=None, graph=None, api=None):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        host = request.url.host
        if host == "api.nasdaq.com":
            return nasdaq if isinstance(nasdaq, httpx.Response) else httpx.Response(
                200, json=NASDAQ_JSON if nasdaq is None else nasdaq, request=request)
        if host == "fred.stlouisfed.org":
            return graph if isinstance(graph, httpx.Response) else httpx.Response(
                200, text=FRED_CSV if graph is None else graph, request=request)
        if host == "api.stlouisfed.org":
            return api if isinstance(api, httpx.Response) else httpx.Response(
                200, json={"observations": []}, request=request)
        raise AssertionError(f"unexpected host {host}")

    return handler


class SourceReachabilityProbeTests(unittest.TestCase):
    def run_probe(self, *, key="secret", download=None, handler=None):
        calls: list[httpx.Request] = []
        handler = handler or transport(calls)
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            results = run_probe(
                client=client,
                download=download or (lambda *args, **kwargs: Frame()),
                fred_api_key=key,
            )
        return results, calls

    def test_all_hosts_pass_and_one_request_each(self):
        results, calls = self.run_probe()
        self.assertEqual([(host, verdict) for host, verdict, _ in results], [
            ("yahoo", "PASS"), ("nasdaq", "PASS"),
            ("fred_graph", "PASS"), ("fred_api", "PASS"),
        ])
        self.assertEqual(len(calls), 3)
        self.assertEqual(calls[0].url.host, "api.nasdaq.com")
        self.assertIn("Chrome", calls[0].headers["user-agent"])
        self.assertEqual(calls[2].url.params["series_id"], "VIXCLS")
        self.assertEqual(calls[2].url.params["api_key"], "secret")

    def test_fred_api_skipped_without_key_sends_no_request(self):
        for key in (None, "", "   "):
            with self.subTest(key=key):
                results, calls = self.run_probe(key=key)
                verdicts = {host: (verdict, detail) for host, verdict, detail in results}
                self.assertEqual(verdicts["fred_api"], ("SKIP", "not_configured"))
                self.assertEqual([call.url.host for call in calls],
                                 ["api.nasdaq.com", "fred.stlouisfed.org"])

    def test_empty_yfinance_frame_is_rate_limited(self):
        results, _ = self.run_probe(download=lambda *args, **kwargs: Frame(rows=0))
        self.assertEqual(results[0],
                         ("yahoo", "FAIL", "empty frame (rate limited)"))

    def test_yfinance_exception_is_reported_without_secret_detail(self):
        def boom(*args, **kwargs):
            raise RuntimeError("proxy said no")

        results, _ = self.run_probe(download=boom)
        self.assertEqual(results[0][0], "yahoo")
        self.assertEqual(results[0][1], "FAIL")
        self.assertIn("RuntimeError", results[0][2])

    def test_http_and_payload_failures_are_reported(self):
        cases = [
            ("nasdaq", {"nasdaq": httpx.Response(429)}),
            ("nasdaq", {"nasdaq": {"data": {}}}),
            ("fred_graph", {"graph": httpx.Response(500)}),
            ("fred_graph", {"graph": "wrong,header\n"}),
            ("fred_api", {"api": httpx.Response(403)}),
        ]
        for host, responses in cases:
            with self.subTest(host=host, responses=responses):
                calls: list[httpx.Request] = []
                handler = transport(calls, **responses)
                results, _ = self.run_probe(handler=handler)
                verdicts = {name: verdict for name, verdict, _ in results}
                self.assertEqual(verdicts[host], "FAIL")

    def test_summary_table_has_one_row_per_host(self):
        results, _ = self.run_probe()
        summary = format_summary(results)
        self.assertIn("| yahoo | PASS |", summary)
        self.assertIn("| fred_api | PASS |", summary)

    def test_main_exits_zero_and_saves_the_summary(self):
        results = [("yahoo", "FAIL", "blocked"), ("nasdaq", "PASS", "ok"),
                   ("fred_graph", "PASS", "ok"), ("fred_api", "SKIP", "not_configured")]
        with tempfile.TemporaryDirectory() as directory:
            summary_path = Path(directory, "summary.md")
            with patch("scripts.probe_source_reachability.run_probe", return_value=results), \
                 patch.dict("os.environ", {"GITHUB_STEP_SUMMARY": str(summary_path)}), \
                 patch("builtins.print"):
                self.assertEqual(main(), 0)
            self.assertIn("| yahoo | FAIL | blocked |", summary_path.read_text())

    def test_workflow_is_read_only_and_non_deploying(self):
        text = Path(".github/workflows/source-reachability-probe.yml").read_text()
        self.assertIn("pull_request:", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("DATA_FRED_API_KEY: ${{ secrets.DATA_FRED_API_KEY }}", text)
        for forbidden in ("deploy", "pages", "write", "cloud-state", "id-token"):
            self.assertNotIn(forbidden, text)
        actions = re.findall(r"uses: (\S+)", text)
        self.assertEqual(len(actions), 2)
        for action in actions:
            self.assertRegex(action, r"^actions/(checkout|setup-python)@[0-9a-f]{40}$")


if __name__ == "__main__":
    unittest.main()
