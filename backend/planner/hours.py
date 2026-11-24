"""Tiny parser for the common subset of OSM `opening_hours`, evaluated for Saturday only.

Returns None whenever the string is anything fancier than day ranges + time ranges,
so callers treat the hours as "unknown" rather than guessing.
"""

from __future__ import annotations

import re

DAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]
SAT = 5
_TIME_RANGE = re.compile(r"^(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\+?$")
_DAY_TOKEN = re.compile(r"^(Mo|Tu|We|Th|Fr|Sa|Su)(?:\s*-\s*(Mo|Tu|We|Th|Fr|Sa|Su))?$")


def _days_cover_saturday(days: str) -> bool | None:
    hit = False
    for token in days.split(","):
        token = token.strip()
        if token == "PH":
            continue
        m = _DAY_TOKEN.match(token)
        if not m:
            return None
        a = DAYS.index(m.group(1))
        b = DAYS.index(m.group(2)) if m.group(2) else a
        span = range(a, b + 1) if a <= b else list(range(a, 7)) + list(range(b + 1))
        hit = hit or SAT in span
    return hit


def saturday_intervals(spec: str | None) -> list[tuple[int, int]] | None:
    """Open intervals on Saturday in minutes from midnight; [] means closed; None means unknown."""
    if not spec:
        return None
    spec = spec.strip()
    if spec == "24/7":
        return [(0, 24 * 60)]
    result: list[tuple[int, int]] | None = None
    for rule in spec.split(";"):
        rule = rule.strip()
        if not rule:
            continue
        m = re.match(r"^((?:(?:Mo|Tu|We|Th|Fr|Sa|Su|PH)(?:\s*-\s*(?:Mo|Tu|We|Th|Fr|Sa|Su))?\s*,?\s*)+)?(.*)$", rule)
        if not m:
            return None
        days, times = (m.group(1) or "").strip().rstrip(","), m.group(2).strip()
        if days:
            covers = _days_cover_saturday(days)
            if covers is None:
                return None
            if not covers:
                continue
        if times.lower() in ("off", "closed"):
            result = []
            continue
        intervals = []
        for part in times.split(","):
            tm = _TIME_RANGE.match(part.strip())
            if not tm:
                return None
            start = int(tm.group(1)) * 60 + int(tm.group(2))
            end = int(tm.group(3)) * 60 + int(tm.group(4))
            if end <= start:
                end += 24 * 60  # past midnight
            intervals.append((start, end))
        result = intervals  # later rules override earlier ones
    return result


def open_during(spec: str | None, start_min: int, end_min: int) -> bool | None:
    intervals = saturday_intervals(spec)
    if intervals is None:
        return None
    return any(a <= start_min and end_min <= b for a, b in intervals)
