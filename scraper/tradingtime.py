"""Market-time maths: Sun–Thu 10:00–15:30 Asia/Baghdad (UTC+03:00 fixed)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Sequence

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


def _parse_hhmm(s: str) -> tuple[int, int]:
    hh, mm = s.split(":")
    return int(hh), int(mm)


def _ensure_baghdad(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=BAGHDAD)
    return dt.astimezone(BAGHDAD)


def market_minutes_between(
    start: datetime,
    end: datetime,
    *,
    trading_days: Sequence[str] = ("Sun", "Mon", "Tue", "Wed", "Thu"),
    trading_window: tuple[str, str] = ("10:00", "15:30"),
) -> float:
    """Elapsed market minutes from start → end (0 if end <= start).

    Only minutes inside trading_days ∩ trading_window count. Fri/Sat (when not
    listed) contribute zero.
    """
    a = _ensure_baghdad(start)
    b = _ensure_baghdad(end)
    if b <= a:
        return 0.0
    wh0, wm0 = _parse_hhmm(trading_window[0])
    wh1, wm1 = _parse_hhmm(trading_window[1])
    day_set = set(trading_days)
    total = 0.0
    day = a.date()
    end_day = b.date()
    one = timedelta(days=1)
    while day <= end_day:
        name = _PY_TO_NAME[day.weekday()]
        if name in day_set:
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
) -> bool:
    """True when more than stale_trading_hours of market time have elapsed.

    Exactly 240 market-minutes is still fresh; stale only after that
    (Thu 13:55 → stale after Sun 12:25:00).
    """
    mins = market_minutes_between(
        posted_at,
        now,
        trading_days=trading_days,
        trading_window=trading_window,
    )
    return mins > float(stale_trading_hours) * 60.0
