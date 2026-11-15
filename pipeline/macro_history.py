"""Pure date-window operations for macro observation rows."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any


def treasury_window_rows(
    rows: list[dict[str, Any]],
    *,
    years: int,
    as_of_date: date | None = None,
    source: str | None = None,
) -> list[dict[str, Any]]:
    if years < 1:
        raise ValueError("percentile_window_years must be positive")
    today = as_of_date or datetime.now(UTC).date()
    try:
        cutoff = today.replace(year=today.year - years)
    except ValueError:
        cutoff = today.replace(year=today.year - years, day=28)

    kept = []
    for row in rows:
        if (source or row.get("source")) != "Treasury":
            kept.append(row)
            continue
        try:
            observed = date.fromisoformat(str(row.get("date", "")))
        except ValueError:
            continue
        if cutoff <= observed <= today:
            kept.append(row)
    return kept
