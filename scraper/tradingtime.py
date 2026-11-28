"""Market-time maths: Sat–Thu Asia/Baghdad (UTC+03:00 fixed).

Default window 10:00–15:30; Saturday uses a shorter window (config:
trading_window_by_day.Sat, typically 10:00–12:30 from observed Kifah posts).
Friday is closed unless listed in trading_days.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Mapping, Optional, Sequence

from .common import BAGHDAD

# Python weekday: Mon=0 … Sun=6 → brief day names
_PY_TO_NAME = {
    0: "Mon",
    1: "Tue",
    2: "Wed",
    3: "Thu",
    4: "Fri",
    5: "Sat",
    6: "Sun",
}

DEFAULT_TRADING_DAYS = ("Sat", "Sun", "Mon", "Tue", "Wed", "Thu")
DEFAULT_TRADING_WINDOW = ("10:00", "15:30")
DEFAULT_TRADING_WINDOW_BY_DAY: dict[str, tuple[str, str]] = {
    "Sat": ("10:00", "12:30"),
}


def _parse_hhmm(s: str) -> tuple[int, int]:
    hh, mm = s.split(":")
    return int(hh), int(mm)


def _ensure_baghdad(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=BAGHDAD)
    return dt.astimezone(BAGHDAD)


def day_name(dt: datetime) -> str:
    return _PY_TO_NAME[_ensure_baghdad(dt).weekday()]


def resolve_trading_window(
    day: str,
    *,
    trading_window: tuple[str, str] = DEFAULT_TRADING_WINDOW,
    trading_window_by_day: Optional[Mapping[str, tuple[str, str]]] = None,
) -> tuple[str, str]:
    """Return HH:MM window for a day name; by_day overrides the default window."""
    by_day = trading_window_by_day or {}
    if day in by_day:
        w = by_day[day]
        return (w[0], w[1])
    return (trading_window[0], trading_window[1])


def market_minutes_between(
    start: datetime,
    end: datetime,
    *,
    trading_days: Sequence[str] = DEFAULT_TRADING_DAYS,
    trading_window: tuple[str, str] = DEFAULT_TRADING_WINDOW,
    trading_window_by_day: Optional[Mapping[str, tuple[str, str]]] = None,
) -> float:
    """Elapsed market minutes from start → end (0 if end <= start).

    Only minutes inside trading_days ∩ that day's window count. Days not listed
    (e.g. Friday) contribute zero.
    """
    a = _ensure_baghdad(start)
    b = _ensure_baghdad(end)
    if b <= a:
        return 0.0
    day_set = set(trading_days)
    total = 0.0
    day = a.date()
    end_day = b.date()
    one = timedelta(days=1)
    while day <= end_day:
        name = _PY_TO_NAME[day.weekday()]
        if name in day_set:
            win = resolve_trading_window(
                name,
                trading_window=trading_window,
                trading_window_by_day=trading_window_by_day,
            )
            wh0, wm0 = _parse_hhmm(win[0])
            wh1, wm1 = _parse_hhmm(win[1])
            open_dt = datetime(day.year, day.month, day.day, wh0, wm0, tzinfo=BAGHDAD)
            close_dt = datetime(day.year, day.month, day.day, wh1, wm1, tzinfo=BAGHDAD)
            seg_start = max(a, open_dt)
            seg_end = min(b, close_dt)
            if seg_end > seg_start:
                total += (seg_end - seg_start).total_seconds() / 60.0
        day = day + one
    return total


def is_stale(
    posted_at: datetime,
    now: datetime,
    *,
    stale_trading_hours: float,
    trading_days: Sequence[str],
    trading_window: tuple[str, str],
    trading_window_by_day: Optional[Mapping[str, tuple[str, str]]] = None,
) -> bool:
    """True when more than stale_trading_hours of market time have elapsed.

    Exactly 240 market-minutes is still fresh; stale only after that.
    """
    mins = market_minutes_between(
        posted_at,
        now,
        trading_days=trading_days,
        trading_window=trading_window,
        trading_window_by_day=trading_window_by_day,
    )
    return mins > float(stale_trading_hours) * 60.0
