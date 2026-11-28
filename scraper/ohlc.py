"""Pre-aggregate official Kifah OHLC from history/*.jsonl.

Series = shown rows where source is tg_kifah (official market=kifah + kind=bursa
via the selector). Never invent a close; omit empty hour buckets; no forward-fill.
Week starts Saturday (Asia/Baghdad). Chart consumers plot ``c``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from .common import BAGHDAD, rfc3339
from .outputs import _compact_num, expand_history_m, load_json, write_json

OFFICIAL_SOURCE = "tg_kifah"
LEVELS = ("hourly", "daily", "weekly", "monthly", "yearly")


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s or not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=BAGHDAD)
    return dt.astimezone(BAGHDAD)


def _ensure_baghdad(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=BAGHDAD)
    return dt.astimezone(BAGHDAD)


def load_history_rows(history_dir: Path) -> list[dict[str, Any]]:
    """Load all day JSONL rows in chronological order (by ``t``)."""
    index_path = history_dir / "index.json"
    days: list[str] = []
    if index_path.exists():
        try:
            idx = json.loads(index_path.read_text(encoding="utf-8"))
            days = list(idx.get("days") or [])
        except (json.JSONDecodeError, OSError):
            days = []
    if not days:
        days = sorted(
            p.stem
            for p in history_dir.glob("????-??-??.jsonl")
            if p.is_file()
        )
    rows: list[dict[str, Any]] = []
    for day in days:
        path = history_dir / f"{day}.jsonl"
        if not path.exists():
            continue
        for ln in path.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            try:
                rows.append(json.loads(ln))
            except json.JSONDecodeError:
                continue
    rows.sort(key=lambda r: r.get("t") or "")
    return rows


def extract_official_prints(rows: list[dict[str, Any]]) -> list[tuple[datetime, float]]:
    """Unique official Kifah prints from history rows.

    A print is ``(post_time, value)`` when ``s == tg_kifah`` and ``v`` is set.
    Deduped by ``(p, v)`` so map-only updates that re-append the same Kifah
    print do not create extra buckets. Never invents a price.
    """
    seen: set[tuple[str, float]] = set()
    prints: list[tuple[datetime, float]] = []
    for raw in rows:
        if raw.get("s") != OFFICIAL_SOURCE:
            continue
        v = raw.get("v")
        if v is None:
            # Fallback: compact m.k when shown source is kifah but v missing
            m = expand_history_m(raw.get("m") if isinstance(raw.get("m"), dict) else {})
            v = m.get("tg_kifah")
        if v is None:
            continue
        try:
            price = float(v)
        except (TypeError, ValueError):
            continue
        p = raw.get("p")
        if not p or not isinstance(p, str):
            continue
        key = (p, price)
        if key in seen:
            continue
        seen.add(key)
        dt = _parse_iso(p)
        if dt is None:
            continue
        prints.append((dt, price))
    prints.sort(key=lambda x: x[0])
    return prints


def _ohlc_from_prints(
    prints: list[tuple[datetime, float]],
    *,
    period_start: datetime,
    incomplete: bool = False,
) -> Optional[dict[str, Any]]:
    if not prints:
        return None
    prices = [p for _, p in prints]
    rec: dict[str, Any] = {
        "t": _compact_iso_period(period_start),
        "o": _compact_num(prices[0]),
        "h": _compact_num(max(prices)),
        "l": _compact_num(min(prices)),
        "c": _compact_num(prices[-1]),
        "n": len(prices),
    }
    if incomplete:
        rec["incomplete"] = True
    return rec


def _compact_iso_period(dt: datetime) -> str:
    """Period start as Asia/Baghdad ISO without sub-seconds."""
    d = _ensure_baghdad(dt).replace(microsecond=0)
    return rfc3339(d).replace(".000000", "")


def hour_start(dt: datetime) -> datetime:
    d = _ensure_baghdad(dt)
    return d.replace(minute=0, second=0, microsecond=0)


def day_start(dt: datetime) -> datetime:
    d = _ensure_baghdad(dt)
    return d.replace(hour=0, minute=0, second=0, microsecond=0)


def week_start_saturday(dt: datetime) -> datetime:
    """Week starts Saturday 00:00 Asia/Baghdad."""
    d = day_start(dt)
    # Mon=0 … Sat=5 → days since Saturday = (weekday + 2) % 7
    since_sat = (d.weekday() + 2) % 7
    return d - timedelta(days=since_sat)


def month_start(dt: datetime) -> datetime:
    d = day_start(dt)
    return d.replace(day=1)


def year_start(dt: datetime) -> datetime:
    d = day_start(dt)
    return d.replace(month=1, day=1)


def aggregate_hourly(prints: list[tuple[datetime, float]]) -> list[dict[str, Any]]:
    """One record per Baghdad hour that has ≥1 official print. Omit empty hours."""
    buckets: dict[datetime, list[tuple[datetime, float]]] = {}
    for t, price in prints:
        key = hour_start(t)
        buckets.setdefault(key, []).append((t, price))
    out: list[dict[str, Any]] = []
    for key in sorted(buckets):
        rec = _ohlc_from_prints(buckets[key], period_start=key)
        if rec:
            out.append(rec)
    return out


def aggregate_by(
    prints: list[tuple[datetime, float]],
    *,
    period_fn,
    now: datetime,
    current_period_start: datetime,
) -> list[dict[str, Any]]:
    buckets: dict[datetime, list[tuple[datetime, float]]] = {}
    for t, price in prints:
        key = period_fn(t)
        buckets.setdefault(key, []).append((t, price))
    out: list[dict[str, Any]] = []
    for key in sorted(buckets):
        incomplete = key == current_period_start
        rec = _ohlc_from_prints(
            buckets[key], period_start=key, incomplete=incomplete
        )
        if rec:
            out.append(rec)
    return out


def build_ohlc_payloads(
    history_dir: Path,
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Build all OHLC level payloads from history/. Empty prints → empty levels."""
    now = _ensure_baghdad(now or datetime.now(tz=BAGHDAD))
    rows = load_history_rows(history_dir)
    prints = extract_official_prints(rows)

    hourly = aggregate_hourly(prints)
    daily = aggregate_by(
        prints,
        period_fn=day_start,
        now=now,
        current_period_start=day_start(now),
    )
    weekly = aggregate_by(
        prints,
        period_fn=week_start_saturday,
        now=now,
        current_period_start=week_start_saturday(now),
    )
    monthly = aggregate_by(
        prints,
        period_fn=month_start,
        now=now,
        current_period_start=month_start(now),
    )
    yearly = aggregate_by(
        prints,
        period_fn=year_start,
        now=now,
        current_period_start=year_start(now),
    )

    levels_present = [
        name
        for name, data in (
            ("hourly", hourly),
            ("daily", daily),
            ("weekly", weekly),
            ("monthly", monthly),
            ("yearly", yearly),
        )
        if data
    ]
    first: dict[str, str] = {}
    last: dict[str, str] = {}
    for name, data in (
        ("hourly", hourly),
        ("daily", daily),
        ("weekly", weekly),
        ("monthly", monthly),
        ("yearly", yearly),
    ):
        if data:
            first[name] = data[0]["t"]
            last[name] = data[-1]["t"]

    index = {
        "levels": levels_present,
        "first": first,
        "last": last,
        "updated_at": rfc3339(now),
        "series": "official_kifah",
        "week_starts": "Saturday",
    }
    return {
        "index": index,
        "hourly": hourly,
        "daily": daily,
        "weekly": weekly,
        "monthly": monthly,
        "yearly": yearly,
    }


def _ohlc_semantic(payloads: dict[str, Any]) -> Any:
    """Compare without updated_at so heartbeat-only rebuilds do not rewrite."""
    idx = dict(payloads.get("index") or {})
    idx.pop("updated_at", None)
    return {
        "index": idx,
        "hourly": payloads.get("hourly"),
        "daily": payloads.get("daily"),
        "weekly": payloads.get("weekly"),
        "monthly": payloads.get("monthly"),
        "yearly": payloads.get("yearly"),
    }


def write_ohlc(
    history_dir: Path,
    *,
    now: Optional[datetime] = None,
) -> bool:
    """Write history/ohlc/* from jsonl. Returns True if any file changed."""
    now = _ensure_baghdad(now or datetime.now(tz=BAGHDAD))
    payloads = build_ohlc_payloads(history_dir, now=now)
    ohlc_dir = history_dir / "ohlc"
    ohlc_dir.mkdir(parents=True, exist_ok=True)

    prev_index = load_json(ohlc_dir / "index.json")
    prev_hourly = _load_jsonl_records(ohlc_dir / "hourly.jsonl")
    prev_daily = _load_json_array(ohlc_dir / "daily.json")
    prev_weekly = _load_json_array(ohlc_dir / "weekly.json")
    prev_monthly = _load_json_array(ohlc_dir / "monthly.json")
    prev_yearly = _load_json_array(ohlc_dir / "yearly.json")
    prev = {
        "index": prev_index or {},
        "hourly": prev_hourly,
        "daily": prev_daily,
        "weekly": prev_weekly,
        "monthly": prev_monthly,
        "yearly": prev_yearly,
    }
    if prev_index is not None and _ohlc_semantic(prev) == _ohlc_semantic(payloads):
        return False

    write_json(ohlc_dir / "index.json", payloads["index"], pretty=True)
    _write_jsonl(ohlc_dir / "hourly.jsonl", payloads["hourly"])
    write_json(ohlc_dir / "daily.json", payloads["daily"], pretty=False)
    write_json(ohlc_dir / "weekly.json", payloads["weekly"], pretty=False)
    write_json(ohlc_dir / "monthly.json", payloads["monthly"], pretty=False)
    write_json(ohlc_dir / "yearly.json", payloads["yearly"], pretty=False)
    return True


def _load_jsonl_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    for ln in path.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        try:
            out.append(json.loads(ln))
        except json.JSONDecodeError:
            continue
    return out


def _load_json_array(path: Path) -> list[Any]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    return data if isinstance(data, list) else []


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    import os
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(
        json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n" for r in records
    )
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
