"""Manual shadow probe of the actual approved provider implementations only."""
from __future__ import annotations

import json
import os
import time
from datetime import UTC, date, datetime
from pathlib import Path

from pipeline.providers.base import ProviderError
from pipeline.providers.coinmetrics import CM_URL as CM
from pipeline.providers.coinmetrics import CoinMetricsProvider
from pipeline.providers.treasury import TREASURY_XML, TreasuryProvider
from pipeline.settings import Settings


def run(*, coinmetrics=None, treasury=None, clock=None) -> tuple[list[dict], str]:
    clock = clock or (lambda: datetime.now(UTC))
    settings = Settings(_env_file=None, coinmetrics_enabled=True)
    coinmetrics = coinmetrics or CoinMetricsProvider(settings)
    treasury = treasury or TreasuryProvider(settings)
    sources = [("Coin Metrics", CM, coinmetrics.get_crypto_market),
               ("Treasury CMT", TREASURY_XML, treasury.get_yield_curve)]
    results = []
    summary = ["## Approved shadow probe", "", "Read-only sample; no publication or deployment. Coin Metrics attribution: [Community API](https://coinmetrics.io/) (CC BY-NC 4.0, noncommercial). Treasury CMT: [CC0 dataset](https://catalog.data.gov/dataset/interest-rate-statistics-daily-treasury-yield-curve-rates).", "", "| Source | Coverage | Retrieved UTC | Request + validation ms | Observation / age | Status |", "|---|---|---|---:|---|---|"]
    for name, url, collect in sources:
        started = time.monotonic()
        result = {"source": name, "source_url": url}
        try:
            payload = collect()
            completed_at = clock()
            result["retrieved_at"] = completed_at.isoformat()
            if name == "Coin Metrics":
                result["samples"] = [{"series": row["symbol"], "value": row["price"],
                                      "observed_at": row["updated_at"], "time_basis": "provider timestamp"}
                                     for row in payload["assets"]]
            else:
                result["source_url"] = payload["source"]
                result["retrieved_at"] = payload["checked_at"]
                result["samples"] = [{"series": key, "value": rows[-1]["value"],
                                      "observed_at": rows[-1]["date"], "time_basis": "observation date only"}
                                     for key, rows in payload["series"].items()]
            result["ok"] = True
        except ProviderError as exc:
            detail = str(exc).replace("|", "/").replace("\n", " ")[:240]
            result.update(ok=False, error=f"{exc.cls}: {detail}")
        except (ValueError, TypeError, KeyError):
            result.update(ok=False, error="malformed provider payload or observation")
        if "retrieved_at" not in result:
            result["retrieved_at"] = clock().isoformat()
        result["duration_ms"] = round((time.monotonic() - started) * 1000, 1)
        observations = []
        for sample in result.get("samples", []):
            raw = sample["observed_at"]
            if sample["time_basis"] == "provider timestamp":
                age = f"{(completed_at - datetime.fromisoformat(raw.replace("Z", "+00:00"))).total_seconds():.0f}s old"
            else:
                age = f"{(completed_at.date() - date.fromisoformat(raw)).days} calendar days old; release time unknown"
            observations.append(f"{sample['series']}: {raw} ({age})")
        summary.append(f"| {name} | {len(result.get('samples', []))}/2 | {result['retrieved_at']} | {result['duration_ms']} | {'; '.join(observations) or 'unknown'} | {result.get('error', 'OK')} |")
        results.append(result)
    summary.extend(["", "Treasury is daily CMT, not an intraday quote. Observation age is not publication latency. Request duration measures this runner only. Coin Metrics makes one request. Treasury makes one current-year request, with at most one previous-year request only for an explicitly valid empty current-year feed; malformed/partial feeds fail without fallback."])
    return results, "\n".join(summary) + "\n"


def main() -> int:
    results, summary = run()
    destination = os.environ.get("GITHUB_STEP_SUMMARY")
    if destination:
        with Path(destination).open("a", encoding="utf-8") as handle:
            handle.write(summary)
    print(summary)
    Path(os.environ.get("RUNNER_TEMP", "/tmp"), "approved-cloud-probe.json").write_text(
        json.dumps(results, indent=2), encoding="utf-8")
    return 0 if all(result["ok"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
