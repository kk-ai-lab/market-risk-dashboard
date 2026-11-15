"""Macro collector (architecture §3.7 MacroCollector).

FRED series + FedWatch probabilities (Yahoo ZQ futures + EFFR anchor + local snapshot accumulation).
Any Provider failure → degradation chain → degraded, does not interrupt the pipeline.

#96 (uses #84): the 27-series roster across 7 groups (incl. a new `volatility` group for
VIXCLS), every request bounded to the 5y window and memoised, units transformed
server-side (pc1/chg), frequency-aware change/status, per-series history archived for the
risk model's percentile windows. Refresh cadence: FRED runs on `--full`/`--macro-only`
(2 runs/day against the automation schedule); 27 bounded requests ≈ 2 MB/run — payload
size, not rate limits, was the constraint (#84 §4).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

from pipeline.fedwatch import (
    FedWatchInput,
    compute_fedwatch,
    enrich_with_history,
    insufficient_data_snapshot,
    load_history,
    meeting_date_for_contract,
    next_contract_codes,
    save_history,
)
from pipeline.metadata import oldest_source_timestamp, quality_for_outcomes
from pipeline.config.models import ApprovedCollectionPlan
from pipeline.providers.base import ProviderError, ProviderRegistry
from pipeline.providers.bls import SERIES as BLS_SERIES
from pipeline.providers.fred import DEFAULT_SERIES_META, SERIES_CATALOG
from pipeline.providers.treasury import SERIES as TREASURY_SERIES
from pipeline.schemas import FedWatchSnapshot, MacroDataset, MacroIndicator
from pipeline.schemas.envelope import SOURCE_POLICY_EXCLUDED_REASON_CODE
from pipeline.settings import Settings
from pipeline.storage.bls_snapshot import BlsSnapshot
from pipeline.storage.treasury_snapshot import TreasurySnapshot

# FRED series → group (#96, uses #84 §1 roster). VIXCLS moved to its own `volatility`
# group (it is an equity implied-volatility index, not a rate); T10YIE moved to
# `inflation` (an expectation measure, not a rate).
SERIES_GROUPS: dict[str, list[str]] = {
    "rates": ["DGS10", "DGS2", "DFII10", "DFF"],
    "credit": ["BAMLH0A0HYM2", "BAMLC0A0CM"],
    "volatility": ["VIXCLS"],
    "inflation": ["CPIAUCSL", "CPILFESL", "PCEPILFE", "T5YIFR", "T10YIE"],
    "labor": ["PAYEMS", "UNRATE", "ICSA", "CCSA", "CIVPART"],
    "liquidity": ["WALCL", "WRESBAL", "WTREGEN", "RRPONTSYD", "SOFR"],
    "fx": ["DTWEXBGS", "DTWEXAFEGS", "DEXUSEU", "DEXJPUS", "DEXCHUS"],
}

# #84 §0: the four empty categories were a wiring gap between SERIES_GROUPS (15 ids) and
# DEFAULT_SERIES (8 ids) that drifted apart. One list now: the fetch loop iterates the
# grouping table, so a new series added to a group is fetched by construction.
DEFAULT_SERIES: list[str] = [s for group in SERIES_GROUPS.values() for s in group]

#: Frequency spec — one table, three consumers (#84 §6a/§6b): change_1m lookback in ROWS
#: (21 rows is ~1 month only for daily series; a monthly series would show 21 months
#: labelled "1m"), staleness threshold in DAYS since the last observation (a row-count
#: "fresh" is not a freshness measure), and the history manifest's next-release estimate.
#: Three parallel maps would drift; one table cannot.
FREQ_SPEC: dict[str, dict[str, int]] = {
    "daily": {"lookback": 21, "stale_days": 7, "next_days": 2},
    "weekly": {"lookback": 4, "stale_days": 21, "next_days": 8},
    "monthly": {"lookback": 1, "stale_days": 75, "next_days": 32},
}

#: FRED fetch window (#84 §4): bound every request to 5y — the full-history download was
#: 7.26 MB/run for 8 series (DGS10 alone 1563 KB back to 1962); the 27-series roster with
#: observation_start ≈ 2 MB.
MACRO_LOOKBACK_DAYS = 5 * 365


class MacroCollector:
    def __init__(self, registry: ProviderRegistry, settings: Settings | None = None, *, collection_plan: ApprovedCollectionPlan | None = None, bls_snapshot: BlsSnapshot | None = None, treasury_snapshot: TreasurySnapshot | None = None, source_errors: dict[str, str] | None = None) -> None:
        self.registry = registry
        self.settings = settings or Settings()
        self.collection_plan = collection_plan or (
            self.settings.load_public_source_policy().collection_plan()
            if self.settings.approved_sources_only else None
        )
        self.bls_snapshot = bls_snapshot
        self.treasury_snapshot = treasury_snapshot
        self.source_errors = source_errors or {}
        self.degraded: list[str] = []
        self.provider_status: dict[str, Any] = {}
        # Full series history (Fix P0-2: for the risk model 5Y percentile window)
        self.series_history: dict[str, list[dict[str, Any]]] = {}
        self._fred_failures = 0
        self._fedwatch_failed = False
        # #84 §4: memoise _fred_series — DFF used to be fetched twice per run (DEFAULT_SERIES
        # + the FedWatch anchor), a wasted 2.4 MB.
        self._fred_cache: dict[str, list[dict[str, Any]]] = {}
        #: Domain → provider outcome of the most recent successful call (#65).
        self._provider_outcomes: dict[str, dict[str, Any]] = {}
        self._degraded_sources: set[str] = set()

    # ---- FRED ----

    def _fred_series(self, series_id: str) -> list[dict[str, Any]]:
        if series_id in self._fred_cache:
            return self._fred_cache[series_id]
        catalog = SERIES_CATALOG.get(series_id, {})
        # #84 §4: observation_start bounds every request to the 5y window (the risk model's
        # percentile window) — the full-history download was ~7 MB/run for 8 series.
        start = (datetime.now(timezone.utc).date() - timedelta(days=MACRO_LOOKBACK_DAYS)).isoformat()
        units = catalog.get("units", "lin")
        try:
            out = self.registry.call(
                "macro", "get_series", f"fred_{series_id}",
                args=(series_id,), kwargs={"start": start, "units": units},
            )
            if "macro" not in self.provider_status or out["meta"].get("degraded"):
                self.provider_status["macro"] = out["meta"]
            self._provider_outcomes["macro"] = out["meta"]
            if out["meta"].get("degraded"):
                self._degraded_sources.add("fred")
                self.degraded.append(f"FRED {series_id}: provider served degraded data")
            rows = out["result"]
            # Normalize to lowercase keys: the risk model 5Y percentile looks up by the lowercase
            # series name of the indicator key
            self.series_history[series_id.lower()] = rows
            self._fred_cache[series_id] = rows
            return rows
        except ProviderError as exc:
            self.degraded.append(str(exc))
            self._fred_failures += 1
            self._degraded_sources.add("fred")
            self.provider_status["macro"] = {"degraded": True, "error": str(exc)}
            return []

    def _collect_macro(self) -> MacroDataset:
        groups: dict[str, list[MacroIndicator]] = {g: [] for g in SERIES_GROUPS}
        for source_id, error in self.source_errors.items():
            self._degraded_sources.add(source_id)
            self.degraded.append(f"{source_id}: {error}")
            self.provider_status[source_id] = {"provider": source_id, "degraded": True, "error": error}
        for series_id in DEFAULT_SERIES:
            grant = (
                self.collection_plan.macro_grant_for(series_id)
                if self.collection_plan is not None else None
            )
            if self.collection_plan is None:
                bls = self.bls_snapshot.series[series_id] if self.bls_snapshot is not None and series_id in BLS_SERIES else None
                treasury = self.treasury_snapshot if series_id in TREASURY_SERIES else None
            else:
                bls = (
                    self.bls_snapshot.series.get(series_id)
                    if grant is not None and grant.provider == "bls" and self.bls_snapshot is not None
                    else None
                )
                treasury = (
                    self.treasury_snapshot
                    if grant is not None and grant.provider == "treasury"
                    else None
                )
            if treasury is not None:
                rows = [
                    {"date": row.date.isoformat(), "value": row.value, "source": "Treasury", "fetched_at": datetime.fromisoformat(year.checked_at).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")}
                    for number in sorted(treasury.required_years)
                    if (year := treasury.years.get(number)) is not None
                    for row in year.series.get(series_id, [])
                ]
                self.series_history[series_id.lower()] = rows
                if treasury.degraded:
                    self._degraded_sources.add("treasury")
                    self.degraded.append(f"Treasury {series_id}: degraded snapshot")
            elif self.collection_plan is not None and bls is None:
                rows = []
            else:
                rows = [row.model_dump(mode="json") for row in bls.rows] if bls is not None else self._fred_series(series_id)
            if bls is not None:
                self.series_history[series_id.lower()] = rows
                if bls.status != "fresh":
                    self._degraded_sources.add("bls")
                    self.degraded.append(f"BLS {series_id}: {bls.status}")
            if not rows:
                if self.collection_plan is not None:
                    catalog = SERIES_CATALOG[series_id]
                    source_label = (
                        "BLS" if grant is not None and grant.provider == "bls"
                        else "Treasury" if grant is not None and grant.provider == "treasury"
                        else "unavailable"
                    )
                    self.series_history[series_id.lower()] = []
                    self._degraded_sources.add(grant.provider if grant is not None else "public_source_policy")
                    self.degraded.append(f"No approved public observation for {series_id}")
                    groups[_group_of(series_id)].append(MacroIndicator(
                        key=series_id.lower(), label=catalog["label"], source=source_label, status="missing",
                        unit=_unit(catalog.get("unit", "level")),
                    ))
                elif bls is not None or treasury is not None:
                    catalog = SERIES_CATALOG[series_id]
                    groups[_group_of(series_id)].append(MacroIndicator(
                        key=series_id.lower(), label=catalog["label"], source="Treasury" if treasury is not None else "BLS", status="missing",
                        unit=_unit(catalog.get("unit", "level")),
                    ))
                continue
            catalog = SERIES_CATALOG.get(series_id, {**DEFAULT_SERIES_META, "label": series_id})
            frequency = catalog.get("frequency", "daily")
            indicator = MacroIndicator(
                key=series_id.lower(),
                label=catalog["label"],
                value=rows[-1]["value"],
                previous=rows[-2]["value"] if len(rows) > 1 else None,
                # #84 §6a: the change lookback derives from the series' frequency — 21
                # rows is one month only for daily series.
                change_1m=_change(rows, FREQ_SPEC.get(frequency, FREQ_SPEC["daily"])["lookback"]),
                unit=_unit(catalog.get("unit", "level")),
                source="Treasury" if treasury is not None else "BLS" if bls is not None else "FRED",
                updated_at=None if bls is not None or treasury is not None else _utc_from_date(rows[-1]["date"]),
                observation_date=rows[-1]["date"] if treasury is not None else None,
                observation_period=rows[-1]["date"][:7] if frequency == "monthly" else None,
                fetched_at=rows[-1]["fetched_at"] if treasury is not None else bls.fetched_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if bls is not None and bls.fetched_at is not None else None,
                freshness_policy=_freshness_policy(frequency),
                # #84 §6b: per-indicator status is judged against the series' own cadence
                # (a monthly series unchanged for 30 days is fresh; a daily series 10 days
                # behind is stale), not a row count.
                status="degraded" if (treasury is not None and treasury.degraded) or (bls is not None and bls.status == "degraded") else _series_status(rows[-1]["date"], frequency),
            )
            group = _group_of(series_id)
            groups[group].append(indicator)
        return MacroDataset(
            rates=groups["rates"],
            credit=groups["credit"],
            volatility=groups["volatility"],
            inflation=groups["inflation"],
            labor=groups["labor"],
            liquidity=groups["liquidity"],
            fx=groups["fx"],
            fedwatch=self._collect_fedwatch() if self.collection_plan is None else None,
        )

    # ---- FedWatch ----

    def _collect_fedwatch(self) -> FedWatchSnapshot | None:
        # EFFR anchor (DFF preferred, fallback EFFR)
        effr: float | None = None
        for series_id in ("DFF", "EFFR"):
            rows = self._fred_series(series_id)
            if rows:
                effr = rows[-1]["value"]
                break
        if effr is None:
            self._fedwatch_failed = True
            self._degraded_sources.add("fedwatch")
            self.degraded.append("FedWatch: no EFFR anchor")
            return self._accumulate(insufficient_data_snapshot(None))

        codes = next_contract_codes()
        prices: dict[str, float | None] = {}
        errors: list[str] = []
        outcomes: list[dict[str, Any]] = []
        for code in codes:
            try:
                out = self.registry.call(
                    "fedwatch", "get_contract_prices", f"fedwatch_{code}", args=([code],)
                )
                prices.update(out["result"])
                outcomes.append(out["meta"])
            except ProviderError as exc:
                errors.append(f"{code}: {exc}")
        if outcomes:
            first = outcomes[0]
            fedwatch_degraded = bool(errors) or any(
                meta.get("degraded") or meta.get("used_fallback") or meta.get("from_cache")
                for meta in outcomes
            )
            self.provider_status["fedwatch"] = {
                **first,
                "used_fallback": any(meta.get("used_fallback") for meta in outcomes),
                "from_cache": all(meta.get("from_cache") for meta in outcomes),
                "degraded": fedwatch_degraded,
                "errors": errors,
            }
        if errors:
            self._degraded_sources.add("fedwatch")
            self.degraded.append("FedWatch: " + "; ".join(errors[:3]))
        elif outcomes and fedwatch_degraded:
            self._degraded_sources.add("fedwatch")
            self.degraded.append("FedWatch: provider served degraded data")
        if not outcomes:
            self.provider_status["fedwatch"] = {"degraded": True, "errors": errors}

        if not any(v is not None for v in prices.values()):
            self._fedwatch_failed = True
            self._degraded_sources.add("fedwatch")
            self.degraded.append("FedWatch: ZQ futures unavailable")
            return self._accumulate(insufficient_data_snapshot(effr))

        snapshot = compute_fedwatch(
            FedWatchInput(
                current_contract_price=prices.get(codes[0]) if len(codes) > 0 else None,
                next_contract_price=prices.get(codes[1]) if len(codes) > 1 else None,
                effr=effr,
                meeting_date=meeting_date_for_contract(codes[0]) if len(codes) > 0 else None,
            )
        )
        if snapshot is None:
            return self._accumulate(insufficient_data_snapshot(effr))
        return self._accumulate(snapshot)

    def _accumulate(self, snapshot: FedWatchSnapshot) -> FedWatchSnapshot:
        """Local daily snapshot accumulation (architecture §1.6/review P0-1): build the accumulation file regardless of success."""
        history_path = self.settings.data_dir / "feeds" / "fedwatch-history.json"
        history = load_history(history_path)
        enriched = enrich_with_history(snapshot, history, history_path)
        save_history(history_path, history)
        return enriched
    # ---- Summary ----

    def _quality(self) -> float:
        """Data quality degrades by the configured factor per failed source.

        FRED partial failure counts as one source, FedWatch failure counts as one.
        """
        sources = ["fred", "fedwatch", "bls", "treasury"]
        if self.collection_plan is not None:
            sources.append("public_source_policy")
        return quality_for_outcomes(
            [source in self._degraded_sources for source in sources],
            settings=self.settings,
        )

    def collect(self) -> tuple[MacroDataset, dict[str, Any]]:
        dataset = self._collect_macro()
        quality = self._quality()
        indicator_times = [
            indicator.updated_at
            for group in SERIES_GROUPS
            for indicator in getattr(dataset, group)
        ]
        source_updated_at = (
            oldest_source_timestamp(indicator_times)
            if (
                len(indicator_times) == len(DEFAULT_SERIES)
                and not self._degraded_sources
                # FedWatch combines FRED with futures whose adapter result has no
                # trustworthy upstream observation timestamp.
                and dataset.fedwatch is None
            )
            else None
        )
        # #64: return payload + provider outcome; the caller assembles the envelope and
        # finalizes freshness through the single assembly path.
        outcome = self._provider_outcomes.get("macro") or self.registry.resolved_provider("macro")
        if outcome is None:
            outcome = {"provider": "unavailable", "used_fallback": False, "from_cache": False}
        provider = str(outcome.get("provider", "unavailable"))
        from_cache = bool(outcome.get("from_cache", False))
        used_fallback = bool(outcome.get("used_fallback", False))
        if self.bls_snapshot is not None or self.treasury_snapshot is not None:
            sources = {indicator.source for group in SERIES_GROUPS for indicator in getattr(dataset, group) if indicator.value is not None}
            provider = "mixed" if len(sources) > 1 else next(iter(sources)).lower() if sources else "unavailable"
            from_cache = from_cache if sources == {"FRED"} else False
            used_fallback = used_fallback if "FRED" in sources else False
        missing_series = [
            indicator.key.upper()
            for group in SERIES_GROUPS
            for indicator in getattr(dataset, group)
            if indicator.status == "missing"
        ]
        policy_excluded_series = [
            series_id
            for series_id in DEFAULT_SERIES
            if self.collection_plan is not None
            and self.collection_plan.macro_grant_for(series_id) is None
        ]
        detail_parts = []
        if self.source_errors:
            detail_parts.append("Source checkpoints degraded: " + ", ".join(sorted(self.source_errors)))
        if policy_excluded_series:
            names = ", ".join(policy_excluded_series[:8])
            if len(policy_excluded_series) > 8:
                names += f", and {len(policy_excluded_series) - 8} more"
            detail_parts.append(f"No approved public source configured for {names}")
        unavailable_observations = sorted(set(missing_series) - set(policy_excluded_series))
        if unavailable_observations:
            detail_parts.append(f"No observation returned for {', '.join(unavailable_observations)}")
        return dataset, {
            "degraded": self.degraded,
            "provider_status": self.provider_status,
            "series_history": self.series_history,
            "series_sources": {**({key.lower(): "BLS" for key in BLS_SERIES} if self.bls_snapshot is not None else {}), **({key.lower(): "Treasury" for key in TREASURY_SERIES} if self.treasury_snapshot is not None else {})},
            "data_quality": round(quality, 3),
            "source_updated_at": source_updated_at,
            "detail": "; ".join(detail_parts)[:200],
            "error_code": SOURCE_POLICY_EXCLUDED_REASON_CODE if policy_excluded_series else None,
            "provider": {
                "provider": provider,
                "used_fallback": used_fallback,
                "from_cache": from_cache,
            },
        }


def _change(rows: list[dict], lookback: int) -> float | None:
    """Change over exactly `lookback` periods, consistent with `momentum` (#70).

    The base is `lookback` periods before the latest (`rows[-1 - lookback]`); the previous
    off-by-one used `rows[-lookback]`, which is one period closer and understated the span.
    """
    if len(rows) < 2:
        return None
    periods = min(lookback, len(rows) - 1)
    return round(rows[-1]["value"] - rows[-1 - periods]["value"], 6)


def _unit(unit: str) -> str:
    if unit == "pct":
        return "pct"
    if unit == "usd":
        return "usd"
    if unit == "index":
        return "index"
    return "level"


def _group_of(series_id: str) -> str:
    for group, series_list in SERIES_GROUPS.items():
        if series_id in series_list:
            return group
    # #84 §5: an unmapped series used to misfile itself under "rates" silently. With the
    # roster now derived from this table an unmapped id is a wiring mistake — fail loud.
    raise KeyError(f"macro: {series_id!r} is not mapped in SERIES_GROUPS")


def _series_status(last_date: str, frequency: str) -> str:
    """Per-indicator status against the series' OWN cadence (#84 §6b).

    A row-count "fresh" is not a freshness measure: a discontinued series with thousands
    of rows ending in 2019 reported `fresh`. A monthly series unchanged for 30 days IS
    fresh; a daily series 10 days behind is stale. Thresholds are a grace multiple of the
    frequency. (The full release-calendar model — next_expected_release per series in the
    history manifest — is future work; this is the minimal honest version.)
    """
    try:
        # §8.2: compare against UTC, not the host's local date.
        age_days = (datetime.now(timezone.utc).date() - date.fromisoformat(last_date)).days
    except ValueError:
        return "stale"
    threshold = FREQ_SPEC.get(frequency, FREQ_SPEC["daily"])["stale_days"]
    return "fresh" if age_days <= threshold else "stale"


def _freshness_policy(frequency: str) -> dict[str, int]:
    stale_after_minutes = FREQ_SPEC.get(frequency, FREQ_SPEC["daily"])["stale_days"] * 24 * 60
    return {
        "delayed_after_minutes": stale_after_minutes // 2,
        "stale_after_minutes": stale_after_minutes,
    }


def _utc_from_date(date_str: str) -> str | None:
    if not date_str:
        return None
    return f"{date_str}T00:00:00Z"
