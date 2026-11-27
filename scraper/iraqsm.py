"""iraqsm fx.json parser."""

from __future__ import annotations

import json
from datetime import datetime

from .common import in_range, rfc3339
from .model import Row, SourceShapeError, UNIT

EXPECTED_UNIT = "IQD per 1 USD"


def parse_iraqsm(body_bytes: bytes, fetched_at: datetime) -> list[Row]:
    try:
        d = json.loads(body_bytes.decode("utf-8", errors="replace"))
    except json.JSONDecodeError as e:
        raise SourceShapeError(f"iraqsm: unexpected JSON: {e}") from e
    if not isinstance(d, dict):
        raise SourceShapeError("iraqsm: root not an object")
    unit = d.get("unit")
    if unit != EXPECTED_UNIT:
        raise SourceShapeError(f"iraqsm: unit string changed: {unit!r}")
    parallel = d.get("parallel") or {}
    if not isinstance(parallel, dict):
        raise SourceShapeError("iraqsm: missing parallel")
    buy = float(parallel.get("buy") or 0)
    sell = float(parallel.get("sell") or 0)
    if buy == 0 and sell == 0:
        raise SourceShapeError("iraqsm: no parallel prices")

    fa = rfc3339(fetched_at)

    def mk(market: str, kind: str, b: float, s: float, pub: str, stale: bool) -> Row:
        r = Row(
            source="iraqsm",
            fetched_at=fa,
            unit=UNIT,
            market=market,
            location="Baghdad",
            kind=kind,
            label="free tier; credit IQWealth (iraqsm.com); precision 10 IQD per USD",
        )
        r.bid = b * 100
        r.ask = s * 100
        r.mid = (r.bid + r.ask) / 2
        if pub:
            try:
                from datetime import timezone

                raw = pub.replace("Z", "+00:00")
                t = datetime.fromisoformat(raw)
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                r.post_time = rfc3339(t)
            except ValueError:
                r.warnings.append("bad_time")
        else:
            r.warnings.append("bad_time")
        if stale:
            r.warnings.append("source_flag_stale")
        r.parse_status = "ok"
        if b <= 0 or s <= 0:
            r.parse_status = "ambiguous"
        elif not in_range(r.bid) or not in_range(r.ask):
            r.parse_status = "out_of_range"
        return r

    rows = [
        mk(
            "parallel",
            "bursa",
            buy,
            sell,
            str(parallel.get("publishedAt") or ""),
            bool(parallel.get("stale")),
        )
    ]
    shops = d.get("shops") or {}
    if isinstance(shops, dict) and float(shops.get("sell") or 0) > 0:
        rows.append(
            mk(
                "shops",
                "shop",
                float(shops.get("buy") or 0),
                float(shops.get("sell") or 0),
                str(shops.get("publishedAt") or ""),
                False,
            )
        )
    return rows
