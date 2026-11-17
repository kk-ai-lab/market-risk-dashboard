from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from math import fsum, isfinite

MONTHS_PER_AVERAGE = 3
PRIOR_AVERAGE_COUNT = 12
REQUIRED_OBSERVATION_MONTHS = MONTHS_PER_AVERAGE + PRIOR_AVERAGE_COUNT


@dataclass(frozen=True)
class MonthlyUnemploymentRate:
    period: date
    rate_pct: float

    def __post_init__(self) -> None:
        if self.period.day != 1:
            raise ValueError("unemployment observations must identify a month")
        if isinstance(self.rate_pct, bool) or not isfinite(self.rate_pct) or not 0.0 <= self.rate_pct <= 100.0:
            raise ValueError("unemployment rate must be a finite percentage")


@dataclass(frozen=True)
class MonthlyLaborStress:
    period: date
    value_pp: float


def derive_sahm_style_labor_stress(
    observations: Sequence[MonthlyUnemploymentRate],
    *,
    as_of: date | None = None,
) -> tuple[MonthlyLaborStress, ...]:
    eligible = [observation for observation in observations if as_of is None or observation.period <= as_of]
    counts = Counter(observation.period for observation in eligible)
    rates_by_month = {
        observation.period.year * 12 + observation.period.month - 1: observation.rate_pct
        for observation in eligible
        if counts[observation.period] == 1
    }
    results: list[MonthlyLaborStress] = []
    for current_month in sorted(rates_by_month):
        required_months = range(
            current_month - REQUIRED_OBSERVATION_MONTHS + 1,
            current_month + 1,
        )
        if any(month not in rates_by_month for month in required_months):
            continue
        current_average = _three_month_average_ending(rates_by_month, current_month)
        prior_averages = (
            _three_month_average_ending(rates_by_month, ending_month)
            for ending_month in range(current_month - PRIOR_AVERAGE_COUNT, current_month)
        )
        year, month_number = divmod(current_month, 12)
        results.append(MonthlyLaborStress(
            period=date(year, month_number + 1, 1),
            value_pp=round(current_average - min(prior_averages), 6),
        ))
    return tuple(results)


def _three_month_average_ending(rates_by_month: Mapping[int, float], ending_month: int) -> float:
    first_month = ending_month - MONTHS_PER_AVERAGE + 1
    months = range(first_month, ending_month + 1)
    return fsum(rates_by_month[month] for month in months) / MONTHS_PER_AVERAGE
