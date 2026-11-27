"""tg_kifah parser + shared parse_text entry point."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .common import LOC_LABEL, Hit, clip, parse_text_core, rfc3339
from .model import Row, SourceShapeError, UNIT
from .tg import parse_tg


def parse_text(text: str, list_mode: bool = False) -> tuple[list[dict[str, Any]], str]:
    """Parse a single post's plain text. Returns (hits, post_status)."""
    hits, ps = parse_text_core(text, list_mode=list_mode)
    out: list[dict[str, Any]] = []
    for h in hits:
        d: dict[str, Any] = {
            "market": h.market,
            "status": h.status,
            "mid": h.mid,
            "warnings": list(h.warnings or []),
        }
        if h.bid:
            d["bid"] = h.bid
        if h.ask:
            d["ask"] = h.ask
        out.append(d)
    return out, ps


def _row_from_hit(
    *,
    source: str,
    fetched_at: str,
    post_id: str,
    post_time: str,
    text: str,
    kind: str,
    h: Hit,
) -> Row:
    return Row(
        source=source,
        fetched_at=fetched_at,
        post_id=post_id,
        post_time=post_time,
        market=h.market,
        location=LOC_LABEL.get(h.market, ""),
        kind=kind,
        bid=h.bid or None,
        ask=h.ask or None,
        mid=h.mid or None,
        unit=UNIT,
        parse_status=h.status,
        warnings=list(h.warnings or []),
        text=clip(text, 200),
    )


def parse_kifah_page(html: str, fetched_at: datetime) -> tuple[list[Row], int]:
    """Parse t.me/s/borsat_alkfah widget HTML. Raises SourceShapeError if zero posts."""
    posts = parse_tg(html)
    if not posts:
        raise SourceShapeError("tg_kifah: zero tgme_widget_message_wrap posts")
    fa = rfc3339(fetched_at)
    rows: list[Row] = []
    for p in posts:
        hits, ps = parse_text_core(p.text)
        base_time = rfc3339(p.time)
        if not hits:
            rows.append(
                Row(
                    source="tg_kifah",
                    fetched_at=fa,
                    post_id=p.id,
                    post_time=base_time,
                    kind="bursa",
                    unit=UNIT,
                    parse_status=ps,
                    text=clip(p.text, 200),
                )
            )
            continue
        for h in hits:
            rows.append(
                _row_from_hit(
                    source="tg_kifah",
                    fetched_at=fa,
                    post_id=p.id,
                    post_time=base_time,
                    text=p.text,
                    kind="bursa",
                    h=h,
                )
            )
    return rows, len(posts)
