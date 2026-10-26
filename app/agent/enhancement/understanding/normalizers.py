"""Deterministic normalization for user-supplied routing parameters."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

_SAFE_ORDER_ID_FULL = re.compile(
    r"^\s*ORD[\s_-]?(\d{1,3})\s*$",
    re.IGNORECASE,
)
_SAFE_ORDER_ID_IN_TEXT = re.compile(
    r"(?<![A-Z0-9])ORD[\s_-]?(\d{1,3})(?![A-Z0-9])",
    re.IGNORECASE,
)
_POSSIBLE_ORDER_ID_TYPO_IN_TEXT = re.compile(
    r"(?<![A-Z0-9])ODR[\s_-]?(\d{1,3})(?![A-Z0-9])",
    re.IGNORECASE,
)
_NUMERIC_DATE = re.compile(
    r"^\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\s*$"
)
_STAGE_ALIASES = {
    "KNITTING": "KNITTING",
    "针织": "KNITTING",
    "ASSEMBLY": "ASSEMBLY",
    "装配": "ASSEMBLY",
    "WASHING": "WASHING",
    "水洗": "WASHING",
    "PACKING": "PACKING",
    "PACKAGING": "PACKING",
    "包装": "PACKING",
}


def normalize_order_id(value: Any) -> str | None:
    """Canonicalize safe order-ID formatting variants without guessing typos."""

    if not isinstance(value, str):
        return None
    match = _SAFE_ORDER_ID_FULL.fullmatch(value)
    if match is None:
        return None
    return f"ORD-{int(match.group(1)):03d}"


def normalize_order_ids_in_text(text: str) -> str:
    """Canonicalize safe order-ID variants embedded in a user message."""

    if not isinstance(text, str):
        raise TypeError("text must be a string")

    return _SAFE_ORDER_ID_IN_TEXT.sub(
        lambda match: f"ORD-{int(match.group(1)):03d}",
        text,
    )


def find_order_id_typo_candidates(text: str) -> list[tuple[str, str]]:
    """Return conservative ODR-to-ORD candidates that require confirmation."""

    if not isinstance(text, str):
        raise TypeError("text must be a string")

    return [
        (match.group(0), f"ORD-{int(match.group(1)):03d}")
        for match in _POSSIBLE_ORDER_ID_TYPO_IN_TEXT.finditer(text)
    ]


def normalize_upper_token(value: Any) -> str | None:
    """Normalize stage and category tokens while preserving their meaning."""

    if not isinstance(value, str):
        return None
    cleaned = value.strip().upper()
    return cleaned or None


def normalize_stage(value: Any) -> str | None:
    """Map supported English and Chinese stage names to canonical values."""

    if not isinstance(value, str):
        return None
    return _STAGE_ALIASES.get(value.strip().upper())


def normalize_explicit_date(value: Any) -> str | None:
    """Normalize explicit numeric dates; relative expressions remain unresolved."""

    if not isinstance(value, str):
        return None
    match = _NUMERIC_DATE.fullmatch(value)
    if match is None:
        return None
    try:
        parsed = date(*(int(part) for part in match.groups()))
    except ValueError:
        return None
    return parsed.isoformat()


def normalize_integer(value: Any) -> int | None:
    """Return an integer without treating booleans or decimals as quantities."""

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        cleaned = value.strip()
        if re.fullmatch(r"[+-]?\d+", cleaned):
            return int(cleaned)
    return None


def normalize_positive_integer(value: Any) -> int | None:
    """Return a strictly positive integer for counts and ranking limits."""

    normalized = normalize_integer(value)
    if normalized is None or normalized <= 0:
        return None
    return normalized
