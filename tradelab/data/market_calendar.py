"""NYSE trading calendar: full-day holidays, next session, and rebalance-period ends.

Rules-based (no data download). Covers the regular holiday schedule; one-off closures (e.g. national
days of mourning) and early closes are not modelled. Used only to look *forward* (next session,
whether a date is the last session of its week/month); history comes from the price data itself.
"""

from __future__ import annotations

import datetime as dt
from functools import lru_cache

from dateutil.easter import easter

Date = dt.date


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> Date:
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(weekday - d.weekday()) % 7)
    return d + dt.timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> Date:
    d = dt.date(year + (month == 12), month % 12 + 1, 1) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - weekday) % 7)


def _observed(d: Date) -> Date:
    """Saturday holidays move to Friday, Sunday holidays to Monday."""
    return (
        d - dt.timedelta(days=1)
        if d.weekday() == 5
        else d + dt.timedelta(days=1) if d.weekday() == 6 else d
    )


@lru_cache(maxsize=None)
def nyse_holidays(year: int) -> frozenset[Date]:
    h = {
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        easter(year) - dt.timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial Day
        _observed(dt.date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed(dt.date(year, 12, 25)),
    }
    new_year = dt.date(year, 1, 1)
    if (
        new_year.weekday() != 5
    ):  # NYSE does not observe a Saturday New Year's Day on the prior Friday
        h.add(_observed(new_year))
    if year >= 2022:
        h.add(_observed(dt.date(year, 6, 19)))  # Juneteenth
    return frozenset(h)


def is_session(d: Date) -> bool:
    return d.weekday() < 5 and d not in nyse_holidays(d.year)


def next_session(d: Date) -> Date:
    d += dt.timedelta(days=1)
    while not is_session(d):
        d += dt.timedelta(days=1)
    return d


def prev_session(d: Date) -> Date:
    d -= dt.timedelta(days=1)
    while not is_session(d):
        d -= dt.timedelta(days=1)
    return d


def _period(d: Date, rule: str) -> tuple[int, ...]:
    if rule in ("W", "W-FRI"):
        return tuple(d.isocalendar()[:2])
    if rule in ("M", "ME"):
        return (d.year, d.month)
    if rule in ("Q", "QE"):
        return (d.year, (d.month - 1) // 3)
    raise ValueError(f"unsupported rebalance rule for calendar lookups: {rule!r}")


def is_period_end(d: Date, rule: str) -> bool:
    """True if ``d`` is the last NYSE session of its rebalance period ("D", "W", "M", "Q")."""
    if rule == "D":
        return True
    return _period(next_session(d), rule) != _period(d, rule)


def next_period_end(d: Date, rule: str) -> Date:
    """First session after ``d`` that ends a rebalance period."""
    s = next_session(d)
    while not is_period_end(s, rule):
        s = next_session(s)
    return s


def sessions(start: Date, end: Date) -> list[Date]:
    """Every NYSE session from ``start`` to ``end`` inclusive."""
    out, d = [], start
    while d <= end:
        if is_session(d):
            out.append(d)
        d += dt.timedelta(days=1)
    return out
