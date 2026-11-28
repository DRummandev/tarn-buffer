"""Build latest.json, history rows, status.json; change detection.

App liveness must never use fetchedAt/generated_at/last_scrape_at — those
heartbeat fields are ignored when diffing so we do not rewrite latest.json
(or spam 5-minute commits) when nothing semantic changed.
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


# Heartbeat-only fields: updated every scrape but must not force a git commit.
_HEARTBEAT_KEYS = frozenset({"fetchedAt", "generated_at", "last_scrape_at"})


def semantic_strip(obj: Any) -> Any:
    """Deep copy omitting heartbeat timestamps for change detection."""
    if isinstance(obj, dict):
        return {
            k: semantic_strip(v)
            for k, v in obj.items()
            if k not in _HEARTBEAT_KEYS
        }
    if isinstance(obj, list):
        return [semantic_strip(x) for x in obj]
    return obj


def latest_changed(prev: Optional[dict[str, Any]], new: dict[str, Any]) -> bool:
    if prev is None:
        return True
    return semantic_strip(prev) != semantic_strip(new)


def build_policy(cfg: SourcesConfig) -> dict[str, Any]:
    policy: dict[str, Any] = {
        "stale_trading_hours": cfg.stale_trading_hours,
        "trading_days": list(cfg.trading_days),
        "trading_window": list(cfg.trading_window),
        "sanity_min": cfg.sanity_min,
        "sanity_max": cfg.sanity_max,
        "config_version": cfg.version,
    }
    if cfg.trading_window_by_day:
        policy["trading_window_by_day"] = {
            k: list(v) for k, v in sorted(cfg.trading_window_by_day.items())
        }
    return policy


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
        "last_scrape_at": now_iso,
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


# Short keys inside history `m` so a worst-case 288-row day stays < 50KB.
HIST_M_KEYS = {
    "tg_kifah": "k",
    "tg_dollariraqi": "d",
    "info4iq": "i",
    "iraqsm": "r",
    "dinarlive_erbil": "e",
}
HIST_M_KEYS_REV = {v: k for k, v in HIST_M_KEYS.items()}


def _compact_num(v: Any) -> Any:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return v
    if f == int(f) and abs(f) < 1e15:
        return int(f)
    return f


def _compact_iso(s: Optional[str]) -> Optional[str]:
    """Strip sub-second fraction to keep history/ small (≤50KB / 288-row day)."""
    if not s or not isinstance(s, str):
        return s
    # 2026-11-27T13:15:48.255537+03:00 → 2026-11-27T13:15:48+03:00
    if "." in s and ("+" in s[10:] or s.endswith("Z")):
        head, rest = s.split(".", 1)
        # rest = 255537+03:00 or 255537Z
        tz_i = rest.find("+")
        if tz_i < 0:
            tz_i = rest.find("-")
        if tz_i < 0 and rest.endswith("Z"):
            return head + "Z"
        if tz_i >= 0:
            return head + rest[tz_i:]
    return s


def history_row(
    *,
    t: datetime,
    baghdad: Decision,
    source_values: dict[str, float],
) -> dict[str, Any]:
    """Compact JSONL row. Omit stale:false; ints when whole; short m keys; no sub-second."""
    m: dict[str, Any] = {}
    for sid, val in source_values.items():
        key = HIST_M_KEYS.get(sid, sid)
        m[key] = _compact_num(val)
    row: dict[str, Any] = {
        "t": _compact_iso(rfc3339(t)),
        "p": _compact_iso(baghdad.posted_at),
        "v": _compact_num(baghdad.value),
        "s": baghdad.source,
        "m": {k: m[k] for k in sorted(m)},
    }
    if baghdad.stale:
        row["stale"] = True
    return row


def _normalize_hist_m(m: dict[str, Any]) -> tuple:
    """Normalize long or short history m keys for semantic comparison."""
    out: dict[str, Any] = {}
    for k, v in (m or {}).items():
        long_k = HIST_M_KEYS_REV.get(k, k)
        # Prefer canonical short key for stable compare
        short = HIST_M_KEYS.get(long_k, k)
        out[short] = _compact_num(v)
    return tuple(sorted(out.items()))


def history_semantic_key(row: dict[str, Any]) -> tuple:
    """Compare without relying on t for 'real change' of shown/source map."""
    # Treat omitted stale as False (compact rows omit stale:false)
    stale = bool(row.get("stale"))
    return (row.get("v"), row.get("s"), stale, _normalize_hist_m(row.get("m") or {}))


def expand_history_m(m: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Expand history ``m`` keys to full source ids (accepts compact or legacy)."""
    out: dict[str, Any] = {}
    for k, v in (m or {}).items():
        long_k = HIST_M_KEYS_REV.get(k, k)
        out[long_k] = v
    return out


def parse_history_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a history JSONL row for consumers.

    Accepts both shapes intentionally present in day files:
    - legacy: full source ids in ``m``, always includes ``stale``
    - compact (writer going forward): short ``m`` keys ``d/e/i/k/r``,
      omits ``stale`` when false

    Past rows are never rewritten; apps must accept both.
    """
    return {
        "t": raw.get("t"),
        "p": raw.get("p"),
        "v": raw.get("v"),
        "s": raw.get("s"),
        "stale": bool(raw.get("stale")),
        "m": expand_history_m(raw.get("m") if isinstance(raw.get("m"), dict) else {}),
    }


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
    parser_drift: Optional[dict[str, Any]] = None,
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
    prev_no_cand = (prev_status or {}).get("selector_no_candidate_since")
    if selector_has:
        no_cand_since = None
    else:
        no_cand_since = prev_no_cand or now_iso

    # Parser drift: sticky since timestamp while active; clear when ok Kifah rows return
    prev_drift = (prev_status or {}).get("parser_drift") or {}
    drift_in = dict(parser_drift or {})
    active = bool(drift_in.get("active"))
    ok_kifah = int(drift_in.get("ok_kifah_rows") or 0)
    if active and ok_kifah == 0:
        since = prev_drift.get("since") if prev_drift.get("active") else now_iso
        if not since:
            since = now_iso
        drift_out: dict[str, Any] = {
            "active": True,
            "since": since,
            "ok_kifah_rows": ok_kifah,
            "rate_looking": bool(drift_in.get("rate_looking")),
            "http_ok": bool(drift_in.get("http_ok", True)),
        }
    else:
        drift_out = {
            "active": False,
            "since": None,
            "ok_kifah_rows": ok_kifah,
            "rate_looking": bool(drift_in.get("rate_looking", False)),
            "http_ok": bool(drift_in.get("http_ok", False)),
        }

    return {
        "sources": sources_out,
        "primary_ok": primary_ok,
        "selector_has_candidate": selector_has,
        "selector_no_candidate_since": no_cand_since,
        "parser_drift": drift_out,
        "open_issue": False,
    }


def status_changed(prev: Optional[dict[str, Any]], new: dict[str, Any]) -> bool:
    if prev is None:
        return True
    return prev != new


def write_json(path: Path, obj: Any, *, pretty: bool = True) -> None:
    """Write JSON atomically (temp + rename) so latest.json is never half-written."""
    atomic_write_json(path, obj, pretty=pretty)


def atomic_write_json(path: Path, obj: Any, *, pretty: bool = True) -> None:
    """Write to a same-directory temp file, fsync, then os.replace."""
    import os
    import tempfile

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if pretty:
        text = json.dumps(obj, ensure_ascii=False, indent=2) + "\n"
    else:
        text = json.dumps(obj, ensure_ascii=False, separators=(",", ":")) + "\n"
    # Round-trip sanity before touching disk destination
    roundtrip = json.loads(text)
    if roundtrip != json.loads(json.dumps(obj, ensure_ascii=False)):
        # Allow float/int normalisation differences by comparing re-serialised forms
        if json.dumps(roundtrip, sort_keys=True) != json.dumps(
            json.loads(json.dumps(obj)), sort_keys=True
        ):
            raise ValueError("JSON round-trip mismatch before atomic write")
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


def validate_latest_payload(
    latest: dict[str, Any],
    *,
    sanity_min: float,
    sanity_max: float,
) -> Optional[str]:
    """Return error string if payload fails JSON/sanity checks; else None."""
    try:
        text = json.dumps(latest, ensure_ascii=False)
        loaded = json.loads(text)
    except (TypeError, ValueError) as e:
        return f"json_invalid:{e}"
    if not isinstance(loaded, dict):
        return "json_not_object"
    value = loaded.get("value")
    if value is not None:
        try:
            v = float(value)
        except (TypeError, ValueError):
            return "value_not_numeric"
        if not (sanity_min <= v <= sanity_max):
            return f"value_out_of_range:{v}"
    return None


def post_check_jump(
    prev: Optional[dict[str, Any]],
    new: dict[str, Any],
    *,
    jump_pct: float,
) -> Optional[str]:
    """If value jumped by > jump_pct and held_reason is unset, return post_check_failed."""
    if not prev or prev.get("value") is None or new.get("value") is None:
        return None
    if new.get("held_reason"):
        return None
    try:
        old_v = float(prev["value"])
        new_v = float(new["value"])
    except (TypeError, ValueError):
        return "post_check_failed"
    if old_v == 0:
        return None
    pct = abs(new_v - old_v) / abs(old_v) * 100.0
    if pct > float(jump_pct):
        return "post_check_failed"
    return None


def estimate_history_day_bytes(*, n_rows: int = 288) -> int:
    """Worst-case size of a day JSONL file (compact rows) for size assertion."""
    sample = {
        "t": "2026-11-26T15:30:00+03:00",
        "p": "2026-11-26T15:29:59+03:00",
        "v": 168800,
        "s": "tg_kifah",
        "stale": True,
        "m": {"d": 169000, "e": 168500, "i": 169375, "k": 168800, "r": 169000},
    }
    line = json.dumps(sample, ensure_ascii=False, separators=(",", ":")) + "\n"
    return len(line.encode("utf-8")) * int(n_rows)


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
