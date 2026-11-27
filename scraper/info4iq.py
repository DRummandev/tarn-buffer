"""info4iq JSON parser."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .common import LOC_LABEL, in_range, market_of, norm, parse_baghdad_local, rfc3339
from .model import Row, SourceShapeError, UNIT


def _to_f(v: Any) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return 0.0
    return 0.0


def parse_info4iq(body_bytes: bytes, fetched_at: datetime) -> list[Row]:
    """Parse get_prem_markets_prices.php body (CRLF/BOM tolerant)."""
    text = body_bytes.decode("utf-8", errors="replace")
    if text.startswith("\ufeff"):
        text = text[1:]
    text = text.lstrip("\r\n").strip()
    if text.startswith("\ufeff"):
        text = text[1:].strip()
    try:
        arr = json.loads(text)
    except json.JSONDecodeError as e:
        raise SourceShapeError(f"info4iq: unexpected JSON: {e}") from e
    if not isinstance(arr, list) or not arr:
        raise SourceShapeError("info4iq: empty or non-array payload")

    fa = rfc3339(fetched_at)
    rows: list[Row] = []
    for it in arr:
        if not isinstance(it, dict):
            raise SourceShapeError("info4iq: array item not an object")
        name = str(it.get("market_name") or it.get("name") or "")
        sell = _to_f(it.get("sell"))
        buy = _to_f(it.get("buy"))
        offered = _to_f(it.get("marketOffered"))
        fields = (norm(name) + " x").split()
        mk = market_of(fields[0]) if fields else ""
        r = Row(
            source="info4iq",
            fetched_at=fa,
            unit=UNIT,
            kind="bursa",
            label=name,
            market=mk or f"unknown:{name}",
            location=LOC_LABEL.get(mk, ""),
        )
        ts = it.get("time")
        if isinstance(ts, str) and ts:
            try:
                r.post_time = rfc3339(parse_baghdad_local(ts))
            except ValueError:
                r.warnings.append("bad_time")
        else:
            r.warnings.append("no_time")
        pid = it.get("ID")
        if pid is not None:
            r.post_id = str(pid)
        # thousands of IQD per 100 USD
        r.bid = buy * 1000
        r.ask = sell * 1000
        if offered > 0 and offered != sell:
            r.warnings.append("sell_ne_marketOffered")
        r.mid = (r.bid + r.ask) / 2
        r.parse_status = "ok"
        if r.bid <= 0 or r.ask <= 0:
            r.parse_status = "ambiguous"
        elif not in_range(r.bid) or not in_range(r.ask):
            r.parse_status = "out_of_range"
        if r.bid > r.ask and r.parse_status == "ok":
            r.warnings.append("buy_gt_sell")
        rows.append(r)
    return rows
