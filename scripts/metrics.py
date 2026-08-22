"""Conservative parsers for marketplace metric displays."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
import math
import re


_COUNT_RE = re.compile(
    r"^\+?(?P<number>(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"\s*(?P<suffix>k|mil)?\s*\+?\s*(?P<label>sold|vendidos?)?$",
    re.IGNORECASE,
)
_RATING_RE = re.compile(r"^(?P<rating>\d+(?:\.\d+)?)(?:\s*(?:de|/)\s*5)?$", re.IGNORECASE)


def parse_count(displayed: str | int | float | None) -> int | None:
    """Return an unambiguous non-negative count, or ``None``."""
    if displayed is None or isinstance(displayed, bool):
        return None
    if isinstance(displayed, int):
        return displayed if displayed >= 0 else None
    if isinstance(displayed, float):
        return int(displayed) if math.isfinite(displayed) and displayed >= 0 and displayed.is_integer() else None
    if not isinstance(displayed, str):
        return None

    match = _COUNT_RE.fullmatch(displayed.strip())
    if match is None:
        return None
    raw_number = match.group("number")
    suffix = match.group("suffix")
    if "." in raw_number and suffix is None:
        return None
    try:
        number = Decimal(raw_number.replace(",", ""))
    except InvalidOperation:
        return None
    multiplier = Decimal(1000) if suffix else Decimal(1)
    total = number * multiplier
    if total < 0 or total != total.to_integral_value():
        return None
    return int(total)


def parse_rating(displayed: str | int | float | None) -> float | None:
    """Return a finite rating in the inclusive range 0..5, or ``None``."""
    if displayed is None or isinstance(displayed, bool):
        return None
    if isinstance(displayed, (int, float)):
        rating = float(displayed)
    elif isinstance(displayed, str):
        match = _RATING_RE.fullmatch(displayed.strip())
        if match is None:
            return None
        rating = float(match.group("rating"))
    else:
        return None
    return rating if math.isfinite(rating) and 0 <= rating <= 5 else None
