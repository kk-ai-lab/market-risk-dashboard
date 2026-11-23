from __future__ import annotations

import json
import re
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import httpx

from pipeline.providers.coinmetrics import CoinMetricsProvider
from pipeline.providers.treasury import FEED_ID, FEED_TITLE, TreasuryProvider
from pipeline.settings import Settings
from scripts.approved_cloud_probe import main, run

NOW = datetime(2026, 11, 15, 12, tzinfo=UTC)
CRYPTO = json.dumps({"data": [
    {"asset": "btc", "ReferenceRateUSD": "95000", "time": "2026-11-15T11:59:00Z"},
    {"asset": "eth", "ReferenceRateUSD": "3000", "time": "2026-11-15T11:59:00Z"},
]}).encode()
XML = f'''<feed xmlns="http://www.w3.org/2005/Atom"
xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata"
xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices">
<id>{FEED_ID}</id><title>{FEED_TITLE}</title><updated>2026-11-14T18:00:00Z</updated>
<link rel="self" title="{FEED_TITLE}" href="{FEED_TITLE}" />
<entry><content><m:properties><d:NEW_DATE>2026-11-13T00:00:00</d:NEW_DATE>
<d:BC_2YEAR>3.5</d:BC_2YEAR><d:BC_10YEAR>4.1</d:BC_10YEAR>
</m:properties></content></entry></feed>'''.encode()


class ApprovedCloudProbeTests(unittest.TestCase):
    def probe(self, cm=CRYPTO, treasury=XML):
        calls = []
        def transport(request):
            calls.append(request)
            body = cm if "coinmetrics" in request.url.host else treasury
            if isinstance(body, Exception):
                raise body
            return httpx.Response(200, content=body, request=request)
        settings = Settings(_env_file=None)
        coin = CoinMetricsProvider(settings)
        treas = TreasuryProvider(settings)
        coin._client.close()
        treas._client.close()
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW
        with httpx.Client(transport=httpx.MockTransport(transport)) as client, \
             patch("pipeline.providers.coinmetrics.datetime", Clock), \
             patch("pipeline.providers.treasury.datetime", Clock):
            coin._client = treas._client = client
            results, summary = run(coinmetrics=coin, treasury=treas, clock=lambda: NOW)
        return results, summary, calls

    def test_actual_provider_success_and_separate_times(self):
        results, summary, calls = self.probe()
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(row["ok"] for row in results))
        self.assertEqual([s["series"] for s in results[0]["samples"]], ["BTC", "ETH"])
        self.assertEqual([s["series"] for s in results[1]["samples"]], ["DGS2", "DGS10"])
        self.assertIn("60s old", summary)
        self.assertIn("release time unknown", summary)
        self.assertIn("retrieved_at", results[0])
        self.assertIn("duration_ms", results[0])
        self.assertIn("noncommercial", summary)
        self.assertNotIn("95000", summary)
        self.assertEqual(set(calls[0].url.params["assets"].split(",")), {"btc", "eth"})

    def test_bad_data_still_probes_other_source_without_retry(self):
        for payload in (b"{}", b"not json", b'{"data":[]}',
                        CRYPTO.replace(b'"95000"', b'"NaN"'),
                        CRYPTO.replace(b'"eth"', b'"btc"')):
            with self.subTest(payload=payload):
                results, _, calls = self.probe(cm=payload)
                self.assertEqual(len(calls), 2)
                self.assertFalse(results[0]["ok"])
                self.assertTrue(results[1]["ok"])

    def test_partial_treasury_and_malformed_empty_fail_without_fallback(self):
        for payload in (XML.replace(b'<d:BC_10YEAR>4.1</d:BC_10YEAR>', b''),
                        b'<feed xmlns="http://www.w3.org/2005/Atom"/>',
                        b'<!DOCTYPE feed [<!ENTITY x "x">]><feed/>'):
            results, _, calls = self.probe(treasury=payload)
            self.assertFalse(results[1]["ok"])
            self.assertEqual(len(calls), 2)

    def test_trusted_empty_current_year_allows_only_one_previous_year_request(self):
        payload = (f'<feed xmlns="http://www.w3.org/2005/Atom"><id>{FEED_ID}</id>'
                   f'<title>{FEED_TITLE}</title><updated>2026-01-01T00:00:00Z</updated></feed>').encode()
        results, summary, calls = self.probe(treasury=payload)
        self.assertFalse(results[1]["ok"])
        self.assertEqual(len(calls), 3)
        self.assertEqual(int(calls[2].url.params["field_tdr_date_value"]),
                         int(calls[1].url.params["field_tdr_date_value"]) - 1)
        self.assertIn("previous-year", summary)

    def test_age_uses_each_response_completion_across_utc_midnight(self):
        coin = unittest.mock.Mock()
        coin.get_crypto_market.return_value = {"assets": [
            {"symbol": "BTC", "price": 1, "updated_at": "2026-11-15T23:59:59Z"},
            {"symbol": "ETH", "price": 2, "updated_at": "2026-11-15T23:59:59Z"}]}
        treasury = unittest.mock.Mock()
        treasury.get_yield_curve.return_value = {
            "source": "https://home.treasury.gov/example", "checked_at": "2026-11-16T00:01:01Z",
            "series": {"DGS2": [{"date": "2026-11-15", "value": 3}],
                       "DGS10": [{"date": "2026-11-15", "value": 4}]}}
        times = iter([datetime(2026, 11, 16, 0, 0, 1, tzinfo=UTC),
                      datetime(2026, 11, 16, 0, 1, 1, tzinfo=UTC)])
        results, summary = run(coinmetrics=coin, treasury=treasury, clock=lambda: next(times))
        self.assertTrue(all(row["ok"] for row in results))
        self.assertIn("2s old", summary)
        self.assertIn("1 calendar days old", summary)
        self.assertNotIn("-", summary.split("BTC:")[1].split("old")[0].split("(")[-1])

    def test_network_failure_and_nonzero_exit(self):
        results, summary, calls = self.probe(cm=httpx.ConnectError("secret detail"))
        self.assertEqual(len(calls), 2)
        self.assertFalse(results[0]["ok"])
        self.assertNotIn("secret detail", summary)
        with patch("scripts.approved_cloud_probe.run", return_value=(results, summary)), \
             patch("scripts.approved_cloud_probe.Path.write_text"), \
             patch.dict("os.environ", {}, clear=True), patch("builtins.print"):
            self.assertEqual(main(), 1)

    def test_workflow_manual_minimal_pinned_and_no_other_collections(self):
        text = Path(".github/workflows/cloud-collection-probe.yml").read_text()
        self.assertRegex(text, r"on:\s+workflow_dispatch:\s+permissions:")
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn("persist-credentials: false", text)
        self.assertIn("python -m scripts.approved_cloud_probe", text)
        self.assertIn("python -m scripts.approved_cloud_shadow", text)
        self.assertIn("retention-days: 5", text)
        actions = re.findall(r"uses: (\S+)", text)
        self.assertEqual(len(actions), 4)
        allowed = {"checkout", "setup-python", "setup-node", "upload-artifact"}
        for action in actions:
            self.assertRegex(action, rf"^actions/({'|'.join(allowed)})@[0-9a-f]{{40}}$")
        for forbidden in ("schedule:", "secrets.", "write", "deploy", "pipeline.run --publish"):
            self.assertNotIn(forbidden, text)

if __name__ == "__main__":
    unittest.main()
