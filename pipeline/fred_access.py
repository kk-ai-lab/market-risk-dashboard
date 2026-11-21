"""Typed FRED access, parsed once at the settings boundary.

A missing or blank key is a state, not an error to discover mid-fetch. With
:class:`NotConfigured` no provider is registered and no request is sent.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import SecretStr


@dataclass(frozen=True)
class Configured:
    key: SecretStr


@dataclass(frozen=True)
class NotConfigured:
    reason: Literal["missing_key"] = "missing_key"


FredAccess = Configured | NotConfigured


def fred_access(settings) -> FredAccess:
    """Return the configured key, or ``NotConfigured`` for ``None``, ``""`` or whitespace."""
    key = getattr(settings, "fred_api_key", None)
    if key is None:
        return NotConfigured()
    raw = key.get_secret_value() if isinstance(key, SecretStr) else str(key)
    if not raw.strip():
        return NotConfigured()
    return Configured(key if isinstance(key, SecretStr) else SecretStr(raw))
