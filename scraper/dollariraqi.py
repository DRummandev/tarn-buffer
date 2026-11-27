"""tg_dollariraqi parser (bursa city list + shop blocks)."""

from __future__ import annotations

import re
from datetime import datetime

from .common import LOC_LABEL, clip, in_range, market_of, norm, num_val, parse_text_core, rfc3339
from .model import Row, SourceShapeError, UNIT
from .tg import parse_tg

_SHOP_RE = re.compile(
    r"💱\s*(\S+)\s*[–\-—]\s*(\S+)[^\d]*?البيع:?\s*(\d[\d,.]*\d)[^\d]*?الشراء:?\s*(\d[\d,.]*\d)"
)


def parse_dollariraqi_page(html: str, fetched_at: datetime) -> tuple[list[Row], int]:
    """Parse t.me/s/dollariraqi widget HTML. Raises SourceShapeError if zero posts."""
    posts = parse_tg(html)
    if not posts:
        raise SourceShapeError("tg_dollariraqi: zero tgme_widget_message_wrap posts")
    fa = rfc3339(fetched_at)
    rows: list[Row] = []
    for p in posts:
        base = Row(
            source="tg_dollariraqi",
            fetched_at=fa,
            post_id=p.id,
            post_time=rfc3339(p.time),
            unit=UNIT,
            text=clip(p.text, 200),
            kind="bursa",
        )
        txt = norm(p.text)
        if not txt.strip() or "بغداد" not in txt:
            r = Row(**{**base.__dict__})
            r.parse_status = "not_a_rate"
            rows.append(r)
            continue

        for m in _SHOP_RE.finditer(txt):
            sell = num_val(m.group(3))
            buy = num_val(m.group(4))
            loc = market_of(m.group(1))
            r = Row(
                source="tg_dollariraqi",
                fetched_at=fa,
                post_id=p.id,
                post_time=rfc3339(p.time),
                unit=UNIT,
                text=clip(p.text, 200),
                kind="shop",
                label=f"{m.group(1)} - {m.group(2)}",
                market="shop",
                location=LOC_LABEL.get(loc, m.group(1)) if loc else m.group(1),
            )
            if sell is None or buy is None:
                r.parse_status = "ambiguous"
            else:
                r.bid = float(buy)
                r.ask = float(sell)
                r.mid = float(buy + sell) / 2
                r.parse_status = "ok"
                for v in (r.bid, r.ask):
                    if v is not None and not in_range(v):
                        r.parse_status = "out_of_range"
                if r.parse_status == "ok" and r.ask is not None and r.bid is not None and r.ask < r.bid:
                    r.parse_status = "crossed"
            rows.append(r)

        clean = _SHOP_RE.sub(" ", txt)
        cut = len(clean)
        for mk in ("🟡", "مثقال", "🇪🇺", "💷", "الجنيه", "اليورو"):
            i = clean.find(mk)
            if 0 <= i < cut:
                cut = i
        clean = clean[:cut]
        hits, ps = parse_text_core(clean, list_mode=True)
        if not hits:
            r = Row(**{**base.__dict__})
            r.parse_status = ps
            rows.append(r)
        for h in hits:
            rows.append(
                Row(
                    source="tg_dollariraqi",
                    fetched_at=fa,
                    post_id=p.id,
                    post_time=rfc3339(p.time),
                    market=h.market,
                    location=LOC_LABEL.get(h.market, ""),
                    kind="bursa",
                    bid=None,
                    ask=h.ask or h.mid or None,
                    mid=h.mid or None,
                    unit=UNIT,
                    parse_status=h.status,
                    warnings=list(h.warnings or []),
                    text=clip(p.text, 200),
                )
            )
    return rows, len(posts)
