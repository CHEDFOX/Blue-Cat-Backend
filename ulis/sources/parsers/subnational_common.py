"""Small helpers shared by the parsers of Canadian provincial and Australian state legislation."""

from __future__ import annotations

import re
from datetime import date


def ws(text: str) -> str:
    """Collapse runs of whitespace (including non-breaking spaces) to single spaces."""
    return re.sub(r"\s+", " ", text).strip()


def iso_date(value: str | None) -> date | None:
    """The date at the start of an ISO date or datetime string, or None."""
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def safe_id(value: str) -> str:
    """An identifier made of letters, digits and ``._-`` only."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")


def padded_number(number: str) -> str:
    """An Act number as it appears in the publishers' identifiers: ``5`` -> ``005``, ``48A`` -> ``048A``."""
    m = re.fullmatch(r"(\d+)([A-Za-z]?)", number.strip())
    if m is None:
        raise ValueError(f"unrecognised Act number {number!r}")
    return f"{int(m.group(1)):03d}{m.group(2).upper()}"
