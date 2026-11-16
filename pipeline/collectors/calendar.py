from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from pipeline.config.models import ApprovedCollectionPlan
from pipeline.metadata import quality_for_outcomes
from pipeline.providers.base import ProviderError, ProviderRegistry
from pipeline.schemas import CalendarDataset, CalendarEvent, CalendarSourceStatus
from pipeline.schemas.calendar import CalendarSource
from pipeline.schemas.envelope import SOURCE_POLICY_EXCLUDED_REASON_CODE
from pipeline.settings import Settings
from pipeline.utils import earnings_instant, now_utc

_FEEDS: tuple[tuple[CalendarSource, str, str], ...] = (
    ("earnings", "calendar", "get_earnings_calendar"),
    ("economic", "economic", "get_economic_calendar"),
    ("bea_calendar", "bea_calendar", "get_economic_calendar"),
)


def _event(source: CalendarSource, row: dict[str, Any], provider: str) -> CalendarEvent:
    if source == "earnings":
        symbol, day = str(row["symbol"]), str(row["date"])
        return CalendarEvent(
            id=f"earnings-{symbol}-{day}",
            type="earnings",
            title=f"{symbol} Earnings",
            country="US",
            datetime=earnings_instant(day, row.get("session")),
            importance="medium",
            actual=row.get("eps_actual"),
            forecast=row.get("eps_estimate"),
            unit="usd",
            related_assets=[symbol],
            source=provider,
        )
    return CalendarEvent(
        id=row["id"],
        type="economic",
        title=row["title"],
        country=row.get("country", "US"),
        datetime=row["datetime"],
        importance=row.get("importance", "medium"),
        source=row["source"],
    )


class CalendarCollector:
    def __init__(self, registry: ProviderRegistry, settings: Settings | None = None, *, collection_plan: ApprovedCollectionPlan | None = None) -> None:
        self.registry = registry
        self.settings = settings or Settings()
        self.collection_plan = collection_plan or (
            self.settings.load_public_source_policy().collection_plan()
            if self.settings.approved_sources_only else None
        )
        self.degraded: list[str] = []
        self.provider_status: dict[str, Any] = {}

    def _quality(self, outcomes: dict[str, dict[str, Any]]) -> float:
        return quality_for_outcomes(
            [
                bool(meta.get("degraded") or meta.get("used_fallback") or meta.get("from_cache"))
                for meta in outcomes.values()
            ],
            settings=self.settings,
        )

    def collect(self) -> tuple[CalendarDataset, dict[str, Any]]:
        self.degraded = []
        self.provider_status = {}
        if self.collection_plan is not None and not self.collection_plan.allows_dataset("calendar"):
            detail = "No approved public source configured for calendar"
            self.degraded.append(detail)
            return CalendarDataset(
                source_status=[
                    CalendarSourceStatus(source=source, state="not_configured", provider="unavailable")
                    for source, _, _ in _FEEDS
                ],
                updated_at=now_utc(),
            ), {
                "degraded": self.degraded,
                "provider_status": {},
                "provider_outcome": {"provider": "unavailable", "used_fallback": False, "from_cache": False},
                "provider_outcomes": {},
                "data_quality": round(self._quality({"public_source_policy": {"degraded": True}}), 3),
                "source_updated_at": None,
                "detail": detail,
                "error_code": SOURCE_POLICY_EXCLUDED_REASON_CODE,
            }

        today = datetime.now(UTC).date()
        horizon = self.settings.load_sources_config().operations.calendar_horizon_days
        start, end = today.isoformat(), (today + timedelta(days=horizon)).isoformat()
        events: list[CalendarEvent] = []
        outcomes: dict[str, dict[str, Any]] = {}
        source_status: list[CalendarSourceStatus] = []
        malformed = 0

        for source, domain, method in _FEEDS:
            if not self.registry.providers_for(domain):
                source_status.append(CalendarSourceStatus(
                    source=source, state="not_configured", provider="unavailable",
                ))
                continue
            checked_at = now_utc()
            try:
                response = self.registry.call(domain, method, f"{source}_{horizon}d", args=(start, end))
                checked_at = now_utc()
                meta = response["meta"]
                provider = str(meta.get("provider", "unavailable"))
                used_fallback, from_cache = bool(meta.get("used_fallback")), bool(meta.get("from_cache"))
                degraded = bool(meta.get("degraded") or used_fallback or from_cache)
                source_events: list[CalendarEvent] = []
                invalid = 0
                for row in response["result"]:
                    try:
                        source_events.append(_event(source, row, provider))
                    except (KeyError, TypeError, ValueError):
                        invalid += 1
                if invalid:
                    self.degraded.append(f"calendar/{source}: {invalid} malformed row(s)")
                    malformed += invalid
                    degraded = True
                elif degraded:
                    self.degraded.append(f"calendar/{source}: provider served degraded data")
                self.provider_status[domain] = dict(meta, degraded=degraded)
                outcomes[source] = {
                    "provider": provider,
                    "used_fallback": used_fallback,
                    "from_cache": from_cache,
                    "degraded": degraded,
                }
                source_status.append(CalendarSourceStatus(
                    source=source,
                    state="degraded" if degraded else "fresh" if source_events else "empty",
                    provider=provider,
                    checked_at=checked_at,
                    used_fallback=used_fallback,
                    from_cache=from_cache,
                ))
                events.extend(source_events)
            except ProviderError as exc:
                checked_at = now_utc()
                self.degraded.append(f"calendar/{source}: {exc}")
                failure = {"degraded": True, "error": str(exc), "provider": "unavailable"}
                self.provider_status[domain] = failure
                outcomes[source] = failure
                source_status.append(CalendarSourceStatus(
                    source=source, state="degraded", provider="unavailable", checked_at=checked_at,
                ))

        seen_ids: set[str] = set()
        economic_events: set[tuple[str, str]] = set()
        unique: list[CalendarEvent] = []
        for event in events:
            identity = (event.title, event.datetime)
            if event.id in seen_ids or (event.type == "economic" and identity in economic_events):
                continue
            seen_ids.add(event.id)
            if event.type == "economic":
                economic_events.add(identity)
            unique.append(event)
        unique.sort(key=lambda event: event.datetime)

        providers = {
            str(item.get("provider", "unavailable"))
            for item in outcomes.values()
            if item.get("provider", "unavailable") != "unavailable"
        }
        provider_outcome = {
            "provider": next(iter(providers)) if len(providers) == 1 else "mixed" if providers else "unavailable",
            "used_fallback": any(bool(item.get("used_fallback")) for item in outcomes.values()),
            "from_cache": any(bool(item.get("from_cache")) for item in outcomes.values()),
        }
        return CalendarDataset(events=unique, source_status=source_status, updated_at=now_utc()), {
            "degraded": self.degraded,
            "provider_status": self.provider_status,
            "provider_outcome": provider_outcome,
            "provider_outcomes": outcomes,
            "data_quality": round(self._quality(outcomes), 3),
            "source_updated_at": None,
            "deduped": len(events) - len(unique),
            "malformed": malformed,
        }
