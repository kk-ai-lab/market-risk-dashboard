"""Small provider primitives shared by all external adapters."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any

from pipeline import __version__

UA = f"market-risk-dashboard/{__version__} (+https://github.com/kk-ai-lab/market-risk-dashboard)"


def declared_user_agent(contact: str) -> str:
    return (
        f"market-risk-dashboard/{__version__} "
        f"(+https://github.com/kk-ai-lab/market-risk-dashboard; {contact})"
    )


def _f(value: Any) -> float | None:
    """Return a finite, consistently rounded provider number."""
    try:
        number = float(value)
        return None if math.isnan(number) or math.isinf(number) else round(number, 6)
    except (TypeError, ValueError):
        return None


def _today(fmt: str = "%Y-%m-%d") -> str:
    return datetime.now(UTC).strftime(fmt)
