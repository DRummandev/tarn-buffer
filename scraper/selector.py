"""Pure selector: select_card — never show a wrong number."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Optional

from .common import BAGHDAD
from .config import SourcesConfig
from .tradingtime import is_stale


@dataclass
class Candidate:
    source_id: str
    value: Optional[float] = None  # mid per 100 USD
    bid: Optional[float] = None  # per100; None if single print
    ask: Optional[float] = None
    posted_at: Optional[str] = None  # ISO +03:00
    post_id: str = ""
    post_url: str = ""
    fetch_ok: bool = True
    error_class: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    parse_ok: bool = False  # parser returned complete ok result with mid
    credit: Optional[str] = None
    source_label: str = ""
    market_label: str = ""


@dataclass
class CardState:
    """Last shown state for one card (Baghdad / Erbil / province / shop)."""

    value: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    source: Optional[str] = None
    posted_at: Optional[str] = None
    post_id: str = ""
    post_url: str = ""
    stale: bool = False
    stale_since: Optional[str] = None
    source_label: str = ""
    market_label: str = ""
    credit: Optional[str] = None
    first_valid_at: Optional[str] = None  # ISO; for 7-day drop rule
    last_valid_at: Optional[str] = None  # ISO of last time we had a valid candidate


@dataclass
class Decision:
    mode: str  # primary | failover | hold
    decision: str  # show_primary | show_failover | hold_unconfirmed_jump | hold_all_stale | no_value
    ok: bool
    value: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    source: Optional[str] = None
    posted_at: Optional[str] = None
    post_id: str = ""
    post_url: str = ""
    stale: bool = False
    stale_since: Optional[str] = None
    held_reason: Optional[str] = None
    warnings: list[str] = field(default_factory=list)
    candidates: dict[str, dict[str, Any]] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    source_label: str = ""
    market_label: str = ""
    credit: Optional[str] = None


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=BAGHDAD)
        return dt.astimezone(BAGHDAD)
    except ValueError:
        return None


def _pct_diff(a: float, b: float) -> float:
    if a == 0:
        return 100.0 if b != 0 else 0.0
    return abs(a - b) / abs(a) * 100.0


def _in_sanity(v: float, cfg: SourcesConfig) -> bool:
    return cfg.sanity_min <= v <= cfg.sanity_max


def _classify(
    cand: Candidate,
    now: datetime,
    cfg: SourcesConfig,
) -> tuple[bool, Optional[str]]:
    """Return (valid_for_show, why_not). Valid = parse ok, in range, non-stale, fetch ok."""
    if not cand.fetch_ok:
        return False, cand.error_class or "fetch_error"
    if not cand.parse_ok or cand.value is None:
        return False, cand.error_class or "parse_invalid"
    if not _in_sanity(float(cand.value), cfg):
        return False, "out_of_range"
    posted = _parse_iso(cand.posted_at)
    if posted is None:
        return False, "no_time"
    if is_stale(
        posted,
        now,
        stale_trading_hours=cfg.stale_trading_hours,
        trading_days=cfg.trading_days,
        trading_window=cfg.trading_window,
    ):
        return False, "stale"
    return True, None


def select_card(
    card: str,
    candidates: dict[str, Candidate],
    prev_state: Optional[CardState],
    now: datetime,
    cfg: SourcesConfig,
    order: Optional[list[str]] = None,
) -> Decision:
    """Select what to show for one card.

    Pure function: no I/O. Rules (§2.2):
    1. parse validity + sanity range
    2. staleness (market hours)
    3. fixed priority, no flip-flopping
    4. jump confirmation
    5. cross-check warning
    6. stale hold of last good
    7. never invent / average across sources
    """
    if order is None:
        if card == "erbil":
            order = list(cfg.erbil_order)
        else:
            order = list(cfg.baghdad_order)

    meta: dict[str, dict[str, Any]] = {}
    valid: list[tuple[str, Candidate]] = []
    why_map: dict[str, Optional[str]] = {}

    for sid in order:
        if not cfg.is_enabled(sid):
            why_map[sid] = "disabled"
            meta[sid] = {
                "value": None,
                "posted_at": None,
                "valid": False,
                "why_not": "disabled",
            }
            continue
        cand = candidates.get(sid)
        if cand is None:
            why_map[sid] = "missing"
            meta[sid] = {
                "value": None,
                "posted_at": None,
                "valid": False,
                "why_not": "missing",
            }
            continue
        ok, why = _classify(cand, now, cfg)
        why_map[sid] = why
        meta[sid] = {
            "value": cand.value,
            "posted_at": cand.posted_at,
            "valid": ok,
            "why_not": why,
        }
        if ok:
            valid.append((sid, cand))

    warnings: list[str] = []

    def from_cand(
        sid: str,
        cand: Candidate,
        *,
        mode: str,
        decision: str,
        held_reason: Optional[str],
        extra_warnings: list[str],
    ) -> Decision:
        return Decision(
            mode=mode,
            decision=decision,
            ok=True,
            value=cand.value,
            bid=cand.bid,
            ask=cand.ask,
            source=sid,
            posted_at=cand.posted_at,
            post_id=cand.post_id,
            post_url=cand.post_url,
            stale=False,
            stale_since=None,
            held_reason=held_reason,
            warnings=extra_warnings,
            candidates=meta,
            order=list(order),
            source_label=cand.source_label,
            market_label=cand.market_label,
            credit=cand.credit,
        )

    def hold_prev(
        *,
        held_reason: str,
        decision: str,
        extra_warnings: list[str],
        force_stale: bool,
    ) -> Decision:
        prev = prev_state
        if prev is None or prev.value is None:
            return Decision(
                mode="hold",
                decision="no_value",
                ok=False,
                held_reason=held_reason,
                warnings=extra_warnings,
                candidates=meta,
                order=list(order),
            )
        stale = force_stale
        stale_since = prev.posted_at if force_stale else prev.stale_since
        if force_stale and prev.posted_at:
            stale_since = prev.posted_at
        # If holding previous without forcing stale, still mark stale if its post aged out
        if not force_stale and prev.posted_at:
            pdt = _parse_iso(prev.posted_at)
            if pdt is not None and is_stale(
                pdt,
                now,
                stale_trading_hours=cfg.stale_trading_hours,
                trading_days=cfg.trading_days,
                trading_window=cfg.trading_window,
            ):
                stale = True
                stale_since = prev.posted_at
        return Decision(
            mode="hold",
            decision=decision,
            ok=True,
            value=prev.value,
            bid=prev.bid,
            ask=prev.ask,
            source=prev.source,
            posted_at=prev.posted_at,
            post_id=prev.post_id or "",
            post_url=prev.post_url or "",
            stale=stale,
            stale_since=stale_since,
            held_reason=held_reason,
            warnings=extra_warnings,
            candidates=meta,
            order=list(order),
            source_label=prev.source_label or "",
            market_label=prev.market_label or "",
            credit=prev.credit,
        )

    if not valid:
        # Rule 6: stale hold
        return hold_prev(
            held_reason="all_stale",
            decision="hold_all_stale",
            extra_warnings=warnings,
            force_stale=True,
        )

    # Rule 3: first valid in config order
    sid, cand = valid[0]
    primary_id = order[0] if order else sid
    is_primary = sid == primary_id and why_map.get(primary_id) is None

    # Failover warnings for higher-priority sources
    if sid != primary_id:
        for higher in order:
            if higher == sid:
                break
            why = why_map.get(higher) or "unavailable"
            warnings.append(f"failover_because:{higher}={why}")

    # Rule 4: jump confirmation
    prev = prev_state
    if prev is not None and prev.value is not None and cand.value is not None:
        jump = _pct_diff(float(prev.value), float(cand.value))
        if jump > cfg.jump_pct:
            confirmed = False
            for other_id, other in valid:
                if other_id == sid or other.value is None:
                    continue
                if _pct_diff(float(cand.value), float(other.value)) <= cfg.confirm_tol_pct:
                    confirmed = True
                    break
            if not confirmed:
                warnings.append(f"jump_{jump:.2f}pct_unconfirmed:{sid}")
                return hold_prev(
                    held_reason="unconfirmed_jump",
                    decision="hold_unconfirmed_jump",
                    extra_warnings=warnings,
                    force_stale=False,
                )

    # Rule 5: cross-check warning vs other valid sources
    if cand.value is not None:
        for other_id, other in valid:
            if other_id == sid or other.value is None:
                continue
            d = _pct_diff(float(cand.value), float(other.value))
            if d > cfg.diff_warn_pct:
                warnings.append(f"diff_primary_vs_{other_id}={d:.2f}pct")

    if sid == primary_id:
        return from_cand(
            sid,
            cand,
            mode="primary",
            decision="show_primary",
            held_reason=None,
            extra_warnings=warnings,
        )
    return from_cand(
        sid,
        cand,
        mode="failover",
        decision="show_failover",
        held_reason="source_failover",
        extra_warnings=warnings,
    )


def decision_to_state(dec: Decision, now_iso: str) -> CardState:
    """Update card state from a decision."""
    first = None
    last = None
    if dec.ok and dec.value is not None and not dec.stale:
        last = now_iso
        first = now_iso
    return CardState(
        value=dec.value,
        bid=dec.bid,
        ask=dec.ask,
        source=dec.source,
        posted_at=dec.posted_at,
        post_id=dec.post_id,
        post_url=dec.post_url,
        stale=dec.stale,
        stale_since=dec.stale_since,
        source_label=dec.source_label,
        market_label=dec.market_label,
        credit=dec.credit,
        first_valid_at=first,
        last_valid_at=last,
    )


def merge_card_state(prev: Optional[CardState], dec: Decision, now_iso: str) -> CardState:
    """Carry first/last valid timestamps across runs for the 7-day drop rule."""
    base = decision_to_state(dec, now_iso)
    if prev is None:
        return base
    first = prev.first_valid_at
    last = prev.last_valid_at
    if dec.ok and dec.value is not None and not dec.stale:
        if not first:
            first = now_iso
        last = now_iso
    elif dec.ok and dec.value is not None:
        # holding stale — keep timestamps
        first = prev.first_valid_at
        last = prev.last_valid_at
    return replace(base, first_valid_at=first, last_valid_at=last)
