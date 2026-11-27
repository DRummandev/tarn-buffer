"""Build latest.json, history rows, status.json; change detection.

App liveness must never use fetchedAt/generated_at — those are ignored when
diffing so we do not rewrite latest.json when nothing semantic changed.
Liveness comes from post times + the staleness rule + the policy block.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from .common import BAGHDAD, rfc3339
from .config import SourcesConfig
from .selector import Candidate, CardState, Decision

APP_NAME = "شكد سعر الدولار؟"


SOURCE_LABELS = {
    "tg_kifah": "بورصة الكفاح",
    "tg_dollariraqi": "الدولار العراقي",
    "info4iq": "Info4IQ",
    "iraqsm": "IraqSM",
    "dinarlive_erbil": "DinarLive Erbil",
}

MARKET_LABELS = {
    "tg_kifah": "الكفاح",
    "tg_dollariraqi": "بغداد",
    "info4iq": "الكفاح",
    "iraqsm": "الموازي",
    "dinarlive_erbil": "أربيل",
}


def paper_units(per100: float) -> float:
    """Paper units = per100 / 1000 (e.g. 168.8)."""
    return per100 / 1000.0


def per_dollar(per100: float) -> float:
    return per100 / 100.0


def format_display(price: float) -> str:
    # Trim trailing zeros but keep at least one decimal when fractional
    if float(price) == int(price) and abs(price) >= 100:
        return str(int(price))
    s = f"{price:.4f}".rstrip("0").rstrip(".")
    return s


def format_display_per100(per100: float) -> str:
    if float(per100) == int(per100):
        n = int(per100)
        return f"{n:,}"
    # e.g. 171812.5 → 171,812.5
    neg = per100 < 0
    v = abs(per100)
    whole = int(v)
    frac = v - whole
    body = f"{whole:,}"
    if frac:
        frac_s = f"{frac:.4f}"[1:].rstrip("0")  # ".5"
        body = body + frac_s
    return ("-" if neg else "") + body


def make_quote(
    *,
    source: str,
    value: float,
    bid: Optional[float],
    ask: Optional[float],
    posted_at: str,
    post_id: str = "",
    post_url: str = "",
    stale: bool = False,
    stale_since: Optional[str] = None,
    fetch_ok: bool = True,
    error_class: Optional[str] = None,
    warnings: Optional[list[str]] = None,
    credit: Optional[str] = None,
    source_label: Optional[str] = None,
    market_label: Optional[str] = None,
) -> dict[str, Any]:
    """Build a legacy+new quote object. Never includes an `error` key."""
    per100 = value
    price = paper_units(per100)
    # buy/sell in paper units; single print → both equal price
    if bid is not None and ask is not None:
        buy = paper_units(bid)
        sell = paper_units(ask)
    else:
        buy = price
        sell = price
    q: dict[str, Any] = {
        "source": source,
        "sourceLabel": source_label or SOURCE_LABELS.get(source, source),
        "market": market_label or MARKET_LABELS.get(source, source),
        "buy": buy,
        "sell": sell,
        "price": price,
        "perDollar": per_dollar(per100),
        "per100": per100,
        "display": format_display(price),
        "displayPer100": format_display_per100(per100),
        "updatedAt": posted_at,
        "value": per100,
        "bid": bid,
        "ask": ask,
        "posted_at": posted_at,
        "post_id": post_id,
        "post_url": post_url,
        "stale": stale,
        "stale_since": stale_since,
        "fetch_ok": fetch_ok,
        "error_class": error_class,
        "warnings": list(warnings or []),
        "credit": credit,
    }
    return q


def post_url_for(source: str, post_id: str) -> str:
    if not post_id:
        return ""
    if source in ("tg_kifah", "tg_dollariraqi") or "/" in post_id:
        if post_id.startswith("http"):
            return post_id
        return f"https://t.me/{post_id}"
    return ""


def semantic_strip(obj: Any) -> Any:
    """Deep copy omitting fetchedAt/generated_at for change detection."""
    if isinstance(obj, dict):
        return {
            k: semantic_strip(v)
            for k, v in obj.items()
            if k not in ("fetchedAt", "generated_at")
        }
    if isinstance(obj, list):
        return [semantic_strip(x) for x in obj]
    return obj


def latest_changed(prev: Optional[dict[str, Any]], new: dict[str, Any]) -> bool:
    if prev is None:
        return True
    return semantic_strip(prev) != semantic_strip(new)


def build_policy(cfg: SourcesConfig) -> dict[str, Any]:
    return {
        "stale_trading_hours": cfg.stale_trading_hours,
        "trading_days": list(cfg.trading_days),
        "trading_window": list(cfg.trading_window),
        "sanity_min": cfg.sanity_min,
        "sanity_max": cfg.sanity_max,
        "config_version": cfg.version,
    }


def build_latest(
    *,
    now: datetime,
    cfg: SourcesConfig,
    baghdad: Decision,
    sources_quotes: dict[str, dict[str, Any]],
    cards: list[dict[str, Any]],
) -> dict[str, Any]:
    now_iso = rfc3339(now)
    ok = bool(baghdad.ok and baghdad.value is not None)
    out: dict[str, Any] = {
        "ok": ok,
        "app": APP_NAME,
        "schema": 2,
        "fetchedAt": now_iso,
        "generated_at": now_iso,
        "unit": "IQD_per_100_USD",
        "value": baghdad.value,
        "bid": baghdad.bid,
        "ask": baghdad.ask,
        "market": "kifah",
        "source": baghdad.source,
        "source_post_id": baghdad.post_id or None,
        "source_post_url": baghdad.post_url or None,
        "posted_at": baghdad.posted_at,
        "stale": baghdad.stale,
        "stale_since": baghdad.stale_since,
        "held_reason": baghdad.held_reason,
        "warnings": list(baghdad.warnings),
        "latest": None,
        "sources": sources_quotes,
        "selector": {
            "mode": baghdad.mode,
            "order": list(baghdad.order),
            "candidates": baghdad.candidates,
        },
        "policy": build_policy(cfg),
        "cards": cards,
    }
    if ok and baghdad.value is not None and baghdad.posted_at:
        out["latest"] = make_quote(
            source=baghdad.source or "",
            value=float(baghdad.value),
            bid=baghdad.bid,
            ask=baghdad.ask,
            posted_at=baghdad.posted_at,
            post_id=baghdad.post_id,
            post_url=baghdad.post_url,
            stale=baghdad.stale,
            stale_since=baghdad.stale_since,
            fetch_ok=True,
            error_class=None,
            warnings=baghdad.warnings,
            credit=baghdad.credit,
            source_label=baghdad.source_label,
            market_label=baghdad.market_label or "الكفاح",
        )
    return out


def history_row(
    *,
    t: datetime,
    baghdad: Decision,
    source_values: dict[str, float],
) -> dict[str, Any]:
    return {
        "t": rfc3339(t),
        "p": baghdad.posted_at,
        "v": baghdad.value,
        "s": baghdad.source,
        "stale": baghdad.stale,
        "m": {k: source_values[k] for k in sorted(source_values)},
    }


def history_semantic_key(row: dict[str, Any]) -> tuple:
    """Compare without relying on t for 'real change' of shown/source map."""
    m = row.get("m") or {}
    return (row.get("v"), row.get("s"), row.get("stale"), tuple(sorted(m.items())))


def append_history_if_changed(
    history_dir: Path,
    row: dict[str, Any],
    *,
    now: datetime,
) -> bool:
    """Append one JSONL row when shown value/source or any source map entry changed.

    Never rewrite past rows. Returns True if a row was appended.
    """
    history_dir.mkdir(parents=True, exist_ok=True)
    day = now.astimezone(BAGHDAD).date().isoformat()
    day_path = history_dir / f"{day}.jsonl"
    prev_key = None
    # Find last row across index (prefer today file tail, else previous days)
    index_path = history_dir / "index.json"
    days: list[str] = []
    if index_path.exists():
        try:
            idx = json.loads(index_path.read_text(encoding="utf-8"))
            days = list(idx.get("days") or [])
        except (json.JSONDecodeError, OSError):
            days = []
    scan_days = list(days)
    if day not in scan_days:
        scan_days.append(day)
    last_row = None
    for d in reversed(scan_days):
        p = history_dir / f"{d}.jsonl"
        if not p.exists():
            continue
        lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if lines:
            try:
                last_row = json.loads(lines[-1])
            except json.JSONDecodeError:
                last_row = None
            break
    if last_row is not None and history_semantic_key(last_row) == history_semantic_key(row):
        return False
    with day_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    # Update index
    if day not in days:
        days.append(day)
        days.sort()
    first = days[0] if days else day
    index = {"days": days, "first": first, "updated": rfc3339(now)}
    index_path.write_text(
        json.dumps(index, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    return True


def build_status(
    *,
    now: datetime,
    prev_status: Optional[dict[str, Any]],
    source_results: dict[str, dict[str, Any]],
    baghdad: Decision,
) -> dict[str, Any]:
    """Operational status. first_failure_at set once on failure, cleared on recovery."""
    now_iso = rfc3339(now)
    prev_sources = (prev_status or {}).get("sources") or {}
    sources_out: dict[str, Any] = {}
    for sid, info in source_results.items():
        ok = bool(info.get("ok"))
        err = info.get("error_class")
        last_ok_post = info.get("last_ok_post_time")
        prev = prev_sources.get(sid) or {}
        first_failure = prev.get("first_failure_at")
        if ok:
            first_failure = None
        else:
            if not first_failure:
                first_failure = now_iso
        sources_out[sid] = {
            "ok": ok,
            "last_error_class": None if ok else err,
            "first_failure_at": first_failure,
            "last_ok_post_time": last_ok_post if ok else prev.get("last_ok_post_time"),
        }
        if ok and last_ok_post:
            sources_out[sid]["last_ok_post_time"] = last_ok_post
        elif ok:
            sources_out[sid]["last_ok_post_time"] = prev.get("last_ok_post_time")
    primary_ok = False
    if baghdad.order:
        pid = baghdad.order[0]
        primary_ok = bool((baghdad.candidates.get(pid) or {}).get("valid"))
    selector_has = any(
        (c or {}).get("valid") for c in (baghdad.candidates or {}).values()
    )
    return {
        "sources": sources_out,
        "primary_ok": primary_ok,
        "selector_has_candidate": selector_has,
        "open_issue": False,
    }


def status_changed(prev: Optional[dict[str, Any]], new: dict[str, Any]) -> bool:
    if prev is None:
        return True
    return prev != new


def write_json(path: Path, obj: Any, *, pretty: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if pretty:
        text = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    else:
        text = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
    path.write_text(text, encoding="utf-8")


def load_json(path: Path) -> Optional[dict[str, Any]]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def card_states_from_latest(latest: Optional[dict[str, Any]]) -> dict[str, CardState]:
    """Recover per-card previous state from committed latest.json."""
    out: dict[str, CardState] = {}
    if not latest:
        return out
    if latest.get("latest") and latest.get("value") is not None:
        q = latest["latest"]
        out["baghdad"] = CardState(
            value=latest.get("value"),
            bid=latest.get("bid"),
            ask=latest.get("ask"),
            source=latest.get("source"),
            posted_at=latest.get("posted_at"),
            post_id=str(latest.get("source_post_id") or q.get("post_id") or ""),
            post_url=str(latest.get("source_post_url") or q.get("post_url") or ""),
            stale=bool(latest.get("stale")),
            stale_since=latest.get("stale_since"),
            source_label=q.get("sourceLabel") or "",
            market_label=q.get("market") or "",
            credit=q.get("credit"),
        )
    for c in latest.get("cards") or []:
        if not isinstance(c, dict) or not c.get("key"):
            continue
        out[str(c["key"])] = CardState(
            value=c.get("value"),
            bid=c.get("bid"),
            ask=c.get("ask"),
            source=c.get("source"),
            posted_at=c.get("posted_at"),
            post_id=str(c.get("post_id") or ""),
            post_url=str(c.get("post_url") or ""),
            stale=bool(c.get("stale")),
            stale_since=c.get("stale_since"),
            source_label=c.get("source_label") or "",
            market_label=c.get("title_ar") or "",
            last_valid_at=c.get("last_valid_at"),
            first_valid_at=c.get("first_valid_at"),
        )
    return out


def decision_table_markdown(
    *,
    baghdad: Decision,
    cards: list[dict[str, Any]],
    source_results: dict[str, dict[str, Any]],
) -> str:
    lines = [
        "## Decision table",
        "",
        f"| field | value |",
        f"|---|---|",
        f"| mode | `{baghdad.mode}` |",
        f"| decision | `{baghdad.decision}` |",
        f"| value | `{baghdad.value}` |",
        f"| source | `{baghdad.source}` |",
        f"| stale | `{baghdad.stale}` |",
        f"| held_reason | `{baghdad.held_reason}` |",
        f"| warnings | `{', '.join(baghdad.warnings) or '—'}` |",
        "",
        "### Candidates",
        "",
        "| source | value | posted_at | valid | why_not |",
        "|---|---|---|---|---|",
    ]
    for sid, c in (baghdad.candidates or {}).items():
        lines.append(
            f"| `{sid}` | `{c.get('value')}` | `{c.get('posted_at')}` | "
            f"`{c.get('valid')}` | `{c.get('why_not')}` |"
        )
    lines += ["", "### Fetch", "", "| source | ok | error_class |", "|---|---|---|"]
    for sid, info in source_results.items():
        lines.append(
            f"| `{sid}` | `{info.get('ok')}` | `{info.get('error_class')}` |"
        )
    if cards:
        lines += ["", "### Cards", "", "| key | value | source | stale |", "|---|---|---|---|"]
        for c in cards:
            lines.append(
                f"| `{c.get('key')}` | `{c.get('value')}` | `{c.get('source')}` | `{c.get('stale')}` |"
            )
    return "\n".join(lines) + "\n"
