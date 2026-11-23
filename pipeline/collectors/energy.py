from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pipeline.providers.base import RATE_LIMITED, ProviderError
from pipeline.schemas import EnergyDataset, EnergyEnvelope, EnergyObservation
from pipeline.schemas.energy import ENERGY_OBSERVATION_WINDOW_DAYS
from pipeline.utils import now_utc

SERIES_ID = "WCESTUS1"


def _observation_age(observations: list[EnergyObservation], today: date) -> str:
    if not observations:
        return "unknown"
    days_old = (today - observations[-1].week_ending).days
    if 0 <= days_old <= ENERGY_OBSERVATION_WINDOW_DAYS:
        return "within_window"
    if days_old > ENERGY_OBSERVATION_WINDOW_DAYS:
        return "overdue"
    return "unknown"


class EnergyCollector:
    """Fetch EIA WCESTUS1 and preserve only a validated prior energy envelope on failure."""

    def __init__(self, registry, settings) -> None:
        self.registry = registry
        self.settings = settings

    def collect(self, previous: Any = None, *, attempted_at: str | None = None) -> dict[str, Any]:
        attempt = attempted_at or now_utc()
        attempt_date = datetime.fromisoformat(attempt.replace("Z", "+00:00")).date()
        previous_payload: EnergyDataset | None = None
        previous_provider = "unavailable"
        try:
            old = EnergyEnvelope.model_validate(previous) if previous is not None else None
            if old is not None:
                previous_payload = old.payload
                previous_provider = old.provenance.provider
        except (TypeError, ValueError):
            previous_payload = None

        try:
            response = self.registry.call("energy", "get_weekly_inventory", "weekly_inventory")
            if response["meta"].get("from_cache"):
                raise ProviderError("EIA provider failed; cached response not used")
            observations = [
                EnergyObservation(
                    week_ending=date.fromisoformat(row["date"]),
                    released_on=date.fromisoformat(row["release_date"]),
                    value=float(row["value"]),
                )
                for row in response["result"]
            ]
            observations.sort(key=lambda item: item.week_ending)
            observations = observations[-260:]
            payload = EnergyDataset(
                observations=observations,
                collection_status="available" if observations else "empty",
                observation_age=_observation_age(observations, attempt_date),
                last_attempt_at=attempt,
                last_successful_fetch_at=attempt,
            )
            meta = response["meta"]
            return {
                "payload": payload,
                "degraded": bool(meta.get("degraded", False)),
                "provider": str(meta.get("provider", "eia")),
                "from_cache": False,
                "error_code": None,
                "detail": "",
                # The WPSR CSV gives a release *date*, not an ISO-8601 timestamp. Keep
                # that date on each observation instead of inventing a midnight UTC time.
                "source_updated_at": None,
            }
        except ProviderError as exc:
            return self._degraded_previous(
                previous_payload,
                previous_provider,
                attempt,
                attempt_date,
                "provider_rate_limited" if exc.cls == RATE_LIMITED else "provider_http_error",
            )
        except (KeyError, TypeError, ValueError, OverflowError):
            return self._degraded_previous(
                previous_payload,
                previous_provider,
                attempt,
                attempt_date,
                "provider_parse_error",
            )

    @staticmethod
    def _degraded_previous(
        previous: EnergyDataset | None,
        provider: str,
        attempt: str,
        today: date,
        error_code: str,
    ) -> dict[str, Any]:
        observations = list(previous.observations) if previous is not None else []
        last_success = previous.last_successful_fetch_at if previous is not None else None
        payload = EnergyDataset(
            observations=observations,
            collection_status="degraded",
            observation_age=_observation_age(observations, today),
            last_attempt_at=attempt,
            last_successful_fetch_at=last_success,
        )
        return {
            "payload": payload,
            "degraded": True,
            "provider": provider if provider != "unavailable" else "eia",
            "from_cache": False,
            "error_code": error_code,
            "source_updated_at": None,
            "detail": (
                "EIA inventory refresh failed; retained validated prior observations"
                if previous is not None
                else "EIA inventory refresh failed; no valid prior observations were available"
            ),
        }
