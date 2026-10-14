"""Small text helpers for the operator commands (plain-text tables, durations, ages)."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime


def format_table(headers: Sequence[str], rows: Sequence[Sequence[object]], right: Iterable[int] = ()) -> str:
    """A fixed-width table; columns listed in ``right`` are right-aligned (numbers)."""
    right_set = set(right)
    cells = [[str(c) for c in row] for row in rows]
    widths = [max([len(h)] + [len(r[i]) for r in cells]) for i, h in enumerate(headers)]

    def line(values: Sequence[str]) -> str:
        parts = [v.rjust(widths[i]) if i in right_set else v.ljust(widths[i]) for i, v in enumerate(values)]
        return "  ".join(parts).rstrip()

    return "\n".join([line(list(headers)), line(["-" * w for w in widths]), *(line(r) for r in cells)])


def human_duration(seconds: float) -> str:
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s" if total else f"{seconds:.1f}s"


def human_age(now: datetime, then: datetime | None) -> str:
    if then is None:
        return "never"
    seconds = max(0.0, (now - then).total_seconds())
    if seconds < 90:
        return "just now"
    if seconds < 3600 * 2:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400 * 2:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def human_bytes(size: float) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"
