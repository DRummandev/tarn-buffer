"""Build latest.json cards[] — Erbil, provinces, shops (independent of Baghdad)."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from .common import BAGHDAD, rfc3339
from .config import SourcesConfig
from .model import Row
from .outputs import SOURCE_LABELS
from .selector import Candidate, CardState, Decision, merge_card_state, select_card

# Fixed province order after erbil; then any other city alphabetical.
PROVINCE_ORDER = [
    "sulaymaniyah",
    "duhok",
    "basra",
    "najaf",
    "karbala",
    "kirkuk",
    "mosul",
    "babil",
    "diyala",
    "anbar",
    "wasit",
    "qadisiyah",
]

TITLE_AR = {
    "erbil": "أربيل",
    "sulaymaniyah": "السليمانية",
    "duhok": "دهوك",
    "basra": "البصرة",
    "najaf": "النجف",
    "karbala": "كربلاء",
    "kirkuk": "كركوك",
    "mosul": "الموصل",
    "babil": "بابل",
    "diyala": "ديالى",
    "anbar": "الأنبار",
    "wasit": "واسط",
    "qadisiyah": "القادسية",
    "shops_baghdad": "صيرفات بغداد",
    "shops_erbil": "صيرفات أربيل",
}

LOCATION = {
    "erbil": "Erbil",
    "sulaymaniyah": "Sulaymaniyah",
    "duhok": "Duhok",
    "basra": "Basra",
    "najaf": "Najaf",
    "karbala": "Karbala",
    "kirkuk": "Kirkuk",
    "mosul": "Mosul",
    "babil": "Babil",
    "diyala": "Diyala",
    "anbar": "Anbar",
    "wasit": "Wasit",
    "qadisiyah": "Qadisiyah",
    "shops_baghdad": "Baghdad",
    "shops_erbil": "Erbil",
}

# Markets that belong to the Baghdad main card — not province cards
BAGHDAD_MARKETS = {"kifah", "harithiya", "samawal", "baghdad", "parallel"}
SKIP_MARKETS = BAGHDAD_MARKETS | {"shops", "shop", "unknown"}

# Province / city market → preferred source order for bursa cards
CITY_SOURCE_ORDER = ["tg_kifah", "tg_dollariraqi"]

DROP_AFTER_DAYS = 7


def _newest_ok(rows: list[Row], *, market: str, kind: Optional[str] = None) -> Optional[Row]:
    ok = [
        r
        for r in rows
        if r.parse_status == "ok"
        and r.market == market
        and r.mid is not None
        and (kind is None or r.kind == kind)
    ]
    if not ok:
        return None
    return sorted(ok, key=lambda r: r.post_time or "", reverse=True)[0]


def _row_to_candidate(
    row: Row,
    *,
    source_id: str,
    fetch_ok: bool = True,
    error_class: Optional[str] = None,
    credit: Optional[str] = None,
    source_label: str = "",
    market_label: str = "",
) -> Candidate:
    bid = row.bid
    ask = row.ask
    # Single print: both bid/ask absent or equal mid → null bid/ask in quote sense
    if bid is None and ask is None:
        pass
    elif bid is not None and ask is not None and bid == ask == row.mid:
        # still keep bid/ask as values? Brief: null if single print
        # For kifah single mid-only rows, bid/ask are None already
        pass
    # If only ask set (dollariraqi list mode), treat as single print
    if bid is None and ask is not None and row.mid == ask:
        bid_out, ask_out = None, None
    elif bid is not None and ask is not None and bid != ask:
        bid_out, ask_out = bid, ask
    elif bid is not None and ask is not None and bid == ask:
        bid_out, ask_out = None, None
    else:
        bid_out, ask_out = bid, ask

    post_url = ""
    if row.post_id and ("/" in row.post_id):
        post_url = f"https://t.me/{row.post_id}"

    return Candidate(
        source_id=source_id,
        value=float(row.mid) if row.mid is not None else None,
        bid=bid_out,
        ask=ask_out,
        posted_at=row.post_time or None,
        post_id=row.post_id or "",
        post_url=post_url,
        fetch_ok=fetch_ok,
        error_class=error_class,
        warnings=list(row.warnings or []),
        parse_ok=row.parse_status == "ok" and row.mid is not None,
        credit=credit,
        source_label=source_label or SOURCE_LABELS.get(source_id, source_id),
        market_label=market_label or TITLE_AR.get(row.market, row.market),
    )


def baghdad_candidates_from_rows(
    by_source: dict[str, dict[str, Any]],
) -> dict[str, Candidate]:
    """Extract Baghdad-card candidates from parsed source payloads."""
    out: dict[str, Candidate] = {}
    # tg_kifah → market kifah
    info = by_source.get("tg_kifah") or {}
    rows: list[Row] = list(info.get("rows") or [])
    if not info.get("fetch_ok", False):
        out["tg_kifah"] = Candidate(
            source_id="tg_kifah",
            fetch_ok=False,
            error_class=info.get("error_class") or "fetch_error",
            parse_ok=False,
            source_label=SOURCE_LABELS["tg_kifah"],
            market_label="الكفاح",
        )
    else:
        row = _newest_ok(rows, market="kifah", kind="bursa")
        if row:
            out["tg_kifah"] = _row_to_candidate(
                row,
                source_id="tg_kifah",
                source_label=SOURCE_LABELS["tg_kifah"],
                market_label="الكفاح",
            )
        else:
            out["tg_kifah"] = Candidate(
                source_id="tg_kifah",
                fetch_ok=True,
                error_class=info.get("error_class") or "no_kifah_row",
                parse_ok=False,
                source_label=SOURCE_LABELS["tg_kifah"],
                market_label="الكفاح",
            )

    info = by_source.get("tg_dollariraqi") or {}
    rows = list(info.get("rows") or [])
    if not info.get("fetch_ok", False):
        out["tg_dollariraqi"] = Candidate(
            source_id="tg_dollariraqi",
            fetch_ok=False,
            error_class=info.get("error_class") or "fetch_error",
            parse_ok=False,
            source_label=SOURCE_LABELS["tg_dollariraqi"],
            market_label="بغداد",
        )
    else:
        row = _newest_ok(rows, market="baghdad", kind="bursa")
        if row:
            out["tg_dollariraqi"] = _row_to_candidate(
                row,
                source_id="tg_dollariraqi",
                source_label=SOURCE_LABELS["tg_dollariraqi"],
                market_label="بغداد",
            )
        else:
            out["tg_dollariraqi"] = Candidate(
                source_id="tg_dollariraqi",
                fetch_ok=True,
                error_class="no_baghdad_row",
                parse_ok=False,
                source_label=SOURCE_LABELS["tg_dollariraqi"],
                market_label="بغداد",
            )

    info = by_source.get("info4iq") or {}
    rows = list(info.get("rows") or [])
    if not info.get("fetch_ok", False):
        out["info4iq"] = Candidate(
            source_id="info4iq",
            fetch_ok=False,
            error_class=info.get("error_class") or "fetch_error",
            parse_ok=False,
            source_label=SOURCE_LABELS["info4iq"],
            market_label="الكفاح",
        )
    else:
        row = _newest_ok(rows, market="kifah")
        if row:
            c = _row_to_candidate(
                row,
                source_id="info4iq",
                source_label=SOURCE_LABELS["info4iq"],
                market_label="الكفاح",
            )
            # info4iq crossed buy>sell keeps bid/ask
            if row.bid is not None and row.ask is not None:
                c.bid = row.bid
                c.ask = row.ask
            out["info4iq"] = c
        else:
            out["info4iq"] = Candidate(
                source_id="info4iq",
                fetch_ok=True,
                error_class="no_kifah_row",
                parse_ok=False,
                source_label=SOURCE_LABELS["info4iq"],
                market_label="الكفاح",
            )

    info = by_source.get("iraqsm") or {}
    rows = list(info.get("rows") or [])
    credit = "Data: IQWealth (iraqsm.com)"
    if not info.get("fetch_ok", False):
        out["iraqsm"] = Candidate(
            source_id="iraqsm",
            fetch_ok=False,
            error_class=info.get("error_class") or "fetch_error",
            parse_ok=False,
            credit=credit,
            source_label=SOURCE_LABELS["iraqsm"],
            market_label="الموازي",
        )
    else:
        row = _newest_ok(rows, market="parallel", kind="bursa")
        if row:
            c = _row_to_candidate(
                row,
                source_id="iraqsm",
                credit=credit,
                source_label=SOURCE_LABELS["iraqsm"],
                market_label="الموازي",
            )
            out["iraqsm"] = c
        else:
            out["iraqsm"] = Candidate(
                source_id="iraqsm",
                fetch_ok=True,
                error_class="no_parallel_row",
                parse_ok=False,
                credit=credit,
                source_label=SOURCE_LABELS["iraqsm"],
                market_label="الموازي",
            )
    return out


def erbil_candidates_from_rows(
    by_source: dict[str, dict[str, Any]],
) -> dict[str, Candidate]:
    out: dict[str, Candidate] = {}
    for sid, market in (
        ("tg_kifah", "erbil"),
        ("tg_dollariraqi", "erbil"),
        ("dinarlive_erbil", "erbil"),
    ):
        info = by_source.get(sid) or {}
        rows: list[Row] = list(info.get("rows") or [])
        label = SOURCE_LABELS.get(sid, sid)
        if not info.get("fetch_ok", False):
            out[sid] = Candidate(
                source_id=sid,
                fetch_ok=False,
                error_class=info.get("error_class") or "fetch_error",
                parse_ok=False,
                source_label=label,
                market_label=TITLE_AR["erbil"],
            )
            continue
        row = _newest_ok(rows, market=market)
        if row:
            out[sid] = _row_to_candidate(
                row,
                source_id=sid,
                source_label=label,
                market_label=TITLE_AR["erbil"],
            )
        else:
            out[sid] = Candidate(
                source_id=sid,
                fetch_ok=True,
                error_class="no_erbil_row",
                parse_ok=False,
                source_label=label,
                market_label=TITLE_AR["erbil"],
            )
    return out


def city_candidates(
    by_source: dict[str, dict[str, Any]],
    market: str,
) -> dict[str, Candidate]:
    out: dict[str, Candidate] = {}
    for sid in CITY_SOURCE_ORDER:
        info = by_source.get(sid) or {}
        rows: list[Row] = list(info.get("rows") or [])
        if not info.get("fetch_ok", False):
            out[sid] = Candidate(
                source_id=sid,
                fetch_ok=False,
                error_class=info.get("error_class") or "fetch_error",
                parse_ok=False,
                source_label=SOURCE_LABELS.get(sid, sid),
                market_label=TITLE_AR.get(market, market),
            )
            continue
        row = _newest_ok(rows, market=market, kind="bursa")
        if row:
            out[sid] = _row_to_candidate(
                row,
                source_id=sid,
                source_label=SOURCE_LABELS.get(sid, sid),
                market_label=TITLE_AR.get(market, market),
            )
        else:
            out[sid] = Candidate(
                source_id=sid,
                fetch_ok=True,
                error_class="no_city_row",
                parse_ok=False,
                source_label=SOURCE_LABELS.get(sid, sid),
                market_label=TITLE_AR.get(market, market),
            )
    return out


def _shop_row_baghdad(by_source: dict[str, dict[str, Any]]) -> tuple[Optional[str], Optional[Row]]:
    """Prefer dollariraqi الصيرفات / Baghdad shop, else iraqsm shops."""
    info = by_source.get("tg_dollariraqi") or {}
    if info.get("fetch_ok"):
        rows: list[Row] = list(info.get("rows") or [])
        shops = [
            r
            for r in rows
            if r.parse_status == "ok"
            and r.kind == "shop"
            and r.mid is not None
            and (
                (r.label and "الصيرفات" in r.label)
                or (r.location or "").lower() == "baghdad"
            )
        ]
        if shops:
            best = sorted(shops, key=lambda r: r.post_time or "", reverse=True)[0]
            return "tg_dollariraqi", best
    info = by_source.get("iraqsm") or {}
    if info.get("fetch_ok"):
        rows = list(info.get("rows") or [])
        row = _newest_ok(rows, market="shops", kind="shop")
        if row:
            return "iraqsm", row
    return None, None


def _shop_row_erbil(by_source: dict[str, dict[str, Any]]) -> tuple[Optional[str], Optional[Row]]:
    info = by_source.get("tg_dollariraqi") or {}
    if not info.get("fetch_ok"):
        return None, None
    rows: list[Row] = list(info.get("rows") or [])
    shops = [
        r
        for r in rows
        if r.parse_status == "ok"
        and r.kind == "shop"
        and r.mid is not None
        and (r.location or "").lower() == "erbil"
    ]
    if not shops:
        return None, None
    best = sorted(shops, key=lambda r: r.post_time or "", reverse=True)[0]
    return "tg_dollariraqi", best


def _too_old_to_keep(prev: Optional[CardState], now: datetime) -> bool:
    """Drop a card after 7 days without any valid value."""
    if prev is None or prev.value is None:
        return True
    last = prev.last_valid_at or prev.posted_at
    if not last:
        return False
    try:
        last_dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
        if last_dt.tzinfo is None:
            last_dt = last_dt.replace(tzinfo=BAGHDAD)
        return now.astimezone(BAGHDAD) - last_dt.astimezone(BAGHDAD) > timedelta(
            days=DROP_AFTER_DAYS
        )
    except ValueError:
        return False


def _card_dict(
    key: str,
    *,
    kind: str,
    value: Any,
    bid: Any,
    ask: Any,
    source: Any,
    source_label: str,
    posted_at: Any,
    stale: bool,
    stale_since: Any,
) -> dict[str, Any]:
    # Public card shape only — no last_valid_at (that would rewrite latest.json every run).
    return {
        "key": key,
        "title_ar": TITLE_AR.get(key, key),
        "location": LOCATION.get(key, key),
        "kind": kind,
        "value": value,
        "bid": bid,
        "ask": ask,
        "source": source,
        "source_label": source_label,
        "posted_at": posted_at,
        "stale": stale,
        "stale_since": stale_since,
    }


def _decision_to_card(
    key: str,
    dec: Decision,
    *,
    kind: str,
    prev: Optional[CardState],
    now: datetime,
) -> Optional[dict[str, Any]]:
    # Anchor for the 7-day drop: last time we had a fresh valid candidate
    anchor = None
    if prev is not None:
        anchor = CardState(
            value=prev.value,
            posted_at=prev.posted_at,
            last_valid_at=prev.last_valid_at or prev.posted_at,
        )

    if not dec.ok or dec.value is None:
        if anchor is None or _too_old_to_keep(anchor, now):
            return None
        return _card_dict(
            key,
            kind=kind,
            value=prev.value if prev else None,
            bid=prev.bid if prev else None,
            ask=prev.ask if prev else None,
            source=prev.source if prev else None,
            source_label=(prev.source_label if prev else "")
            or SOURCE_LABELS.get((prev.source if prev else "") or "", ""),
            posted_at=prev.posted_at if prev else None,
            stale=True,
            stale_since=(prev.stale_since if prev else None)
            or (prev.posted_at if prev else None),
        )

    # Stale hold of last-good: drop after 7 days without a fresh valid candidate
    if dec.stale:
        check = anchor or CardState(
            value=dec.value,
            posted_at=dec.posted_at,
            last_valid_at=dec.posted_at,
        )
        if _too_old_to_keep(check, now):
            return None

    return _card_dict(
        key,
        kind=kind,
        value=dec.value,
        bid=dec.bid,
        ask=dec.ask,
        source=dec.source,
        source_label=dec.source_label or SOURCE_LABELS.get(dec.source or "", ""),
        posted_at=dec.posted_at,
        stale=dec.stale,
        stale_since=dec.stale_since,
    )


def discover_city_markets(by_source: dict[str, dict[str, Any]]) -> list[str]:
    found: set[str] = set()
    for sid in CITY_SOURCE_ORDER:
        info = by_source.get(sid) or {}
        for r in info.get("rows") or []:
            if (
                isinstance(r, Row)
                and r.parse_status == "ok"
                and r.kind == "bursa"
                and r.market
                and r.market not in SKIP_MARKETS
                and not r.market.startswith("unknown")
            ):
                found.add(r.market)
    ordered = [m for m in PROVINCE_ORDER if m in found]
    extras = sorted(m for m in found if m not in PROVINCE_ORDER and m != "erbil")
    return ordered + extras


def build_cards(
    *,
    by_source: dict[str, dict[str, Any]],
    prev_states: dict[str, CardState],
    now: datetime,
    cfg: SourcesConfig,
) -> tuple[list[dict[str, Any]], dict[str, CardState], dict[str, Decision]]:
    """Return (cards, updated_states, decisions_by_key)."""
    cards: list[dict[str, Any]] = []
    states: dict[str, CardState] = {}
    decisions: dict[str, Decision] = {}
    now_iso = rfc3339(now)

    # Erbil
    erbil_cands = erbil_candidates_from_rows(by_source)
    prev_e = prev_states.get("erbil")
    dec_e = select_card("erbil", erbil_cands, prev_e, now, cfg, order=list(cfg.erbil_order))
    decisions["erbil"] = dec_e
    st_e = merge_card_state(prev_e, dec_e, now_iso)
    card_e = _decision_to_card("erbil", dec_e, kind="bursa", prev=prev_e, now=now)
    if card_e:
        cards.append(card_e)
        states["erbil"] = st_e

    # Provinces
    for market in discover_city_markets(by_source):
        key = market
        cands = city_candidates(by_source, market)
        prev = prev_states.get(key)
        dec = select_card(key, cands, prev, now, cfg, order=list(CITY_SOURCE_ORDER))
        decisions[key] = dec
        st = merge_card_state(prev, dec, now_iso)
        card = _decision_to_card(key, dec, kind="bursa", prev=prev, now=now)
        if card:
            cards.append(card)
            states[key] = st

    # Also retain known previous province cards not seen this run (stale / drop)
    known_keys = {c["key"] for c in cards}
    for key, prev in prev_states.items():
        if key in known_keys or key in ("baghdad", "erbil", "shops_baghdad", "shops_erbil"):
            continue
        if key.startswith("shops_"):
            continue
        cands = city_candidates(by_source, key)
        dec = select_card(key, cands, prev, now, cfg, order=list(CITY_SOURCE_ORDER))
        st = merge_card_state(prev, dec, now_iso)
        card = _decision_to_card(key, dec, kind="bursa", prev=prev, now=now)
        if card:
            cards.append(card)
            states[key] = st

    # Shops — separate from bursa; always bid/ask when present
    for shop_key, picker in (
        ("shops_baghdad", _shop_row_baghdad),
        ("shops_erbil", _shop_row_erbil),
    ):
        sid, row = picker(by_source)
        prev = prev_states.get(shop_key)
        cands: dict[str, Candidate] = {}
        order = ["tg_dollariraqi", "iraqsm"] if shop_key == "shops_baghdad" else ["tg_dollariraqi"]
        if sid and row:
            credit = "Data: IQWealth (iraqsm.com)" if sid == "iraqsm" else None
            c = _row_to_candidate(
                row,
                source_id=sid,
                credit=credit,
                source_label=SOURCE_LABELS.get(sid, sid),
                market_label=TITLE_AR[shop_key],
            )
            c.bid = row.bid
            c.ask = row.ask
            cands[sid] = c
            for other in order:
                if other not in cands:
                    cands[other] = Candidate(
                        source_id=other,
                        fetch_ok=True,
                        parse_ok=False,
                        error_class="no_shop_row",
                    )
        else:
            for other in order:
                info = by_source.get(other) or {}
                cands[other] = Candidate(
                    source_id=other,
                    fetch_ok=bool(info.get("fetch_ok")),
                    parse_ok=False,
                    error_class=info.get("error_class") or "no_shop_row",
                )
        dec = select_card(shop_key, cands, prev, now, cfg, order=order)
        decisions[shop_key] = dec
        st = merge_card_state(prev, dec, now_iso)
        card = _decision_to_card(shop_key, dec, kind="shop", prev=prev, now=now)
        if card:
            if card.get("bid") is None and dec.bid is not None:
                card["bid"] = dec.bid
            if card.get("ask") is None and dec.ask is not None:
                card["ask"] = dec.ask
            cards.append(card)
            states[shop_key] = st

    # Stable order: erbil, provinces (fixed + extras), shops
    def sort_key(c: dict[str, Any]) -> tuple:
        k = c["key"]
        if k == "erbil":
            return (0, 0, k)
        if k in PROVINCE_ORDER:
            return (1, PROVINCE_ORDER.index(k), k)
        if k.startswith("shops_"):
            return (3, 0 if k == "shops_baghdad" else 1, k)
        return (2, 0, k)

    cards.sort(key=sort_key)
    return cards, states, decisions
