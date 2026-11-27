"""Selector unit tests + scenario replay harness.

Scenario JSON format (tests/scenarios/*.json)
============================================
A scenario file is a JSON object:

{
  "name": "human label",
  "config_overrides": { ... optional keys from config/sources.json ... },
  "runs": [
    {
      "now": "2026-11-29T11:00:00+03:00",
      "sources": {
        "<source_id>": {
          "fetch_ok": true,
          "error_class": null,
          "rows": [
            {
              "source": "tg_kifah",
              "market": "kifah",
              "kind": "bursa",
              "mid": 168800,
              "bid": null,
              "ask": null,
              "post_time": "2026-11-26T13:55:19+03:00",
              "post_id": "borsat_alkfah/30588",
              "parse_status": "ok",
              "warnings": []
            }
          ]
        }
      },
      "expect": {
        "card": "baghdad",
        "decision": "show_primary",
        "value": 168800,
        "stale": false,
        "held_reason": null,
        "warnings_contain": []
      }
    }
  ]
}

The harness feeds each run through select_card (and carries CardState between
runs). `decision` must match Decision.decision. `warnings_contain` is a list of
substrings that must appear in the decision warnings.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from scraper.cards import (
    baghdad_candidates_from_rows,
    build_cards,
    city_candidates,
    erbil_candidates_from_rows,
)
from scraper.common import BAGHDAD, parse_baghdad_local, rfc3339
from scraper.config import (
    ConfigError,
    apply_overrides,
    load_sources_config,
    validate_config_dict,
)
from scraper.model import Row
from scraper.outputs import (
    build_latest,
    format_display,
    format_display_per100,
    latest_changed,
    make_quote,
    paper_units,
    per_dollar,
)
from scraper.selector import Candidate, CardState, select_card
from scraper.tradingtime import is_stale, market_minutes_between

ROOT = Path(__file__).resolve().parent.parent
CFG = load_sources_config(ROOT / "config" / "sources.json")
SCENARIOS = Path(__file__).resolve().parent / "scenarios"


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _cand(
    sid: str,
    value: float,
    posted_at: str,
    *,
    fetch_ok: bool = True,
    parse_ok: bool = True,
    error_class=None,
    bid=None,
    ask=None,
    post_id: str = "",
) -> Candidate:
    return Candidate(
        source_id=sid,
        value=value,
        bid=bid,
        ask=ask,
        posted_at=posted_at,
        post_id=post_id or f"{sid}/1",
        post_url=f"https://t.me/{sid}/1" if post_id or True else "",
        fetch_ok=fetch_ok,
        error_class=error_class,
        parse_ok=parse_ok and fetch_ok,
        source_label=sid,
        market_label=sid,
    )


def _fail(sid: str, err: str = "http_429") -> Candidate:
    return Candidate(
        source_id=sid,
        fetch_ok=False,
        error_class=err,
        parse_ok=False,
    )


class TestTradingTime(unittest.TestCase):
    def test_weekend_not_counted(self):
        # Thu 13:55 → Sun 10:30 = 95 (Thu) + 30 (Sun) = 125 minutes
        post = _dt("2026-11-26T13:55:00+03:00")  # Thursday
        fri = _dt("2026-11-27T12:00:00+03:00")
        sat = _dt("2026-11-28T12:00:00+03:00")
        sun1030 = _dt("2026-11-29T10:30:00+03:00")
        self.assertEqual(market_minutes_between(post, fri), 95.0)
        self.assertEqual(market_minutes_between(post, sat), 95.0)
        self.assertEqual(market_minutes_between(post, sun1030), 125.0)
        self.assertFalse(
            is_stale(
                post,
                sun1030,
                stale_trading_hours=4,
                trading_days=CFG.trading_days,
                trading_window=CFG.trading_window,
            )
        )
        # after Sunday 12:25 → stale
        sun1225 = _dt("2026-11-29T12:25:00+03:00")
        self.assertEqual(market_minutes_between(post, sun1225), 240.0)
        self.assertFalse(
            is_stale(
                post,
                sun1225,
                stale_trading_hours=4,
                trading_days=CFG.trading_days,
                trading_window=CFG.trading_window,
            )
        )
        sun1225_01 = _dt("2026-11-29T12:25:01+03:00")
        self.assertTrue(
            is_stale(
                post,
                sun1225_01,
                stale_trading_hours=4,
                trading_days=CFG.trading_days,
                trading_window=CFG.trading_window,
            )
        )


class TestSelectorCore(unittest.TestCase):
    def test_01_normal_primary(self):
        now = _dt("2026-11-26T14:00:00+03:00")
        cands = {
            "tg_kifah": _cand("tg_kifah", 168800, "2026-11-26T13:55:19+03:00", bid=168750, ask=168850),
            "tg_dollariraqi": _cand("tg_dollariraqi", 169000, "2026-11-26T13:50:00+03:00"),
            "info4iq": _cand("info4iq", 169375, "2026-11-26T08:06:10+03:00"),
            "iraqsm": _cand("iraqsm", 169000, "2026-11-26T13:00:00+03:00"),
        }
        # info4iq may be stale at 14:00 depending on market hours from 08:06
        dec = select_card("baghdad", cands, None, now, CFG)
        self.assertEqual(dec.decision, "show_primary")
        self.assertEqual(dec.value, 168800)
        self.assertEqual(dec.source, "tg_kifah")
        self.assertFalse(dec.stale)
        self.assertIsNone(dec.held_reason)
        # mid from bid/ask preserved on candidate
        self.assertEqual(cands["tg_kifah"].bid, 168750)
        self.assertEqual(cands["tg_kifah"].ask, 168850)

    def test_01b_single_print_mid(self):
        now = _dt("2026-11-26T14:00:00+03:00")
        cands = {
            "tg_kifah": _cand("tg_kifah", 168800, "2026-11-26T13:55:19+03:00"),
            "tg_dollariraqi": _fail("tg_dollariraqi"),
            "info4iq": _fail("info4iq"),
            "iraqsm": _fail("iraqsm"),
        }
        dec = select_card("baghdad", cands, None, now, CFG)
        self.assertEqual(dec.decision, "show_primary")
        self.assertEqual(dec.value, 168800)
        self.assertIsNone(dec.bid)
        self.assertIsNone(dec.ask)

    def test_02_failover_fetch_error(self):
        now = _dt("2026-11-26T14:00:00+03:00")
        cands = {
            "tg_kifah": _fail("tg_kifah", "http_429"),
            "tg_dollariraqi": _cand("tg_dollariraqi", 169000, "2026-11-26T13:50:00+03:00"),
            "info4iq": _fail("info4iq"),
            "iraqsm": _fail("iraqsm"),
        }
        dec = select_card("baghdad", cands, None, now, CFG)
        self.assertEqual(dec.decision, "show_failover")
        self.assertEqual(dec.value, 169000)
        self.assertEqual(dec.source, "tg_dollariraqi")
        self.assertEqual(dec.held_reason, "source_failover")
        self.assertTrue(any(w.startswith("failover_because:tg_kifah=") for w in dec.warnings))

    def test_03_failover_stale_and_return_no_flipflop(self):
        # Primary posted Thu 10:00; at Thu 15:00 still fresh (<4h)
        post_old = "2026-11-26T10:00:00+03:00"
        now1 = _dt("2026-11-26T15:20:00+03:00")  # 5h20 clock but only 5h20 market from 10:00 → stale (>4h)
        # From 10:00 to 15:20 = 320 market minutes > 240 → stale
        cands = {
            "tg_kifah": _cand("tg_kifah", 168800, post_old),
            "tg_dollariraqi": _cand("tg_dollariraqi", 169000, "2026-11-26T15:00:00+03:00"),
            "info4iq": _fail("info4iq"),
            "iraqsm": _fail("iraqsm"),
        }
        dec = select_card("baghdad", cands, None, now1, CFG)
        self.assertEqual(dec.decision, "show_failover")
        self.assertEqual(dec.source, "tg_dollariraqi")
        self.assertTrue(any("tg_kifah=stale" in w for w in dec.warnings))

        # Return to primary when fresh again
        now2 = _dt("2026-11-26T15:25:00+03:00")
        cands2 = {
            "tg_kifah": _cand("tg_kifah", 168850, "2026-11-26T15:20:00+03:00"),
            "tg_dollariraqi": _cand("tg_dollariraqi", 169500, "2026-11-26T15:22:00+03:00"),
            "info4iq": _fail("info4iq"),
            "iraqsm": _fail("iraqsm"),
        }
        prev = CardState(value=169000, source="tg_dollariraqi", posted_at="2026-11-26T15:00:00+03:00")
        dec2 = select_card("baghdad", cands2, prev, now2, CFG)
        self.assertEqual(dec2.decision, "show_primary")
        self.assertEqual(dec2.source, "tg_kifah")
        # No flip-flop: primary stays even if backup is newer
        self.assertEqual(dec2.value, 168850)

    def test_04_weekend_freshness(self):
        post = "2026-11-26T13:55:00+03:00"
        for now_s in (
            "2026-11-27T12:00:00+03:00",
            "2026-11-28T12:00:00+03:00",
            "2026-11-29T10:30:00+03:00",
        ):
            cands = {
                "tg_kifah": _cand("tg_kifah", 168800, post),
                "tg_dollariraqi": _fail("tg_dollariraqi"),
                "info4iq": _fail("info4iq"),
                "iraqsm": _fail("iraqsm"),
            }
            dec = select_card("baghdad", cands, None, _dt(now_s), CFG)
            self.assertEqual(dec.decision, "show_primary", msg=now_s)
            self.assertFalse(dec.stale, msg=now_s)
        # After Sun 12:25 → stale → hold if no backup
        cands = {
            "tg_kifah": _cand("tg_kifah", 168800, post),
            "tg_dollariraqi": _fail("tg_dollariraqi"),
            "info4iq": _fail("info4iq"),
            "iraqsm": _fail("iraqsm"),
        }
        prev = CardState(value=168800, source="tg_kifah", posted_at=post)
        dec = select_card("baghdad", cands, prev, _dt("2026-11-29T12:30:00+03:00"), CFG)
        self.assertEqual(dec.decision, "hold_all_stale")
        self.assertTrue(dec.stale)
        self.assertEqual(dec.stale_since, post)

    def test_05_out_of_range_never_shown(self):
        now = _dt("2026-11-26T14:00:00+03:00")
        cands = {
            "tg_kifah": _cand("tg_kifah", 16880, "2026-11-26T13:55:00+03:00"),
            "tg_dollariraqi": _cand("tg_dollariraqi", 1_688_000, "2026-11-26T13:55:00+03:00"),
            "info4iq": _fail("info4iq"),
            "iraqsm": _fail("iraqsm"),
        }
        dec = select_card("baghdad", cands, None, now, CFG)
        self.assertEqual(dec.decision, "no_value")
        self.assertFalse(dec.ok)
        self.assertEqual(dec.candidates["tg_kifah"]["why_not"], "out_of_range")
        self.assertEqual(dec.candidates["tg_dollariraqi"]["why_not"], "out_of_range")

    def test_06_jump_unconfirmed_and_confirmed(self):
        now = _dt("2026-11-26T14:00:00+03:00")
        prev = CardState(value=168800, source="tg_kifah", posted_at="2026-11-26T13:00:00+03:00")
        # 5% jump from dollariraqi alone
        cands = {
            "tg_kifah": _fail("tg_kifah"),
            "tg_dollariraqi": _cand("tg_dollariraqi", 177240, "2026-11-26T13:50:00+03:00"),  # ~5%
            "info4iq": _fail("info4iq"),
            "iraqsm": _fail("iraqsm"),
        }
        dec = select_card("baghdad", cands, prev, now, CFG)
        self.assertEqual(dec.decision, "hold_unconfirmed_jump")
        self.assertEqual(dec.value, 168800)
        self.assertEqual(dec.held_reason, "unconfirmed_jump")
        self.assertTrue(any("unconfirmed" in w for w in dec.warnings))

        # Confirmed by iraqsm within 1%
        cands2 = {
            "tg_kifah": _fail("tg_kifah"),
            "tg_dollariraqi": _cand("tg_dollariraqi", 177240, "2026-11-26T13:50:00+03:00"),
            "info4iq": _fail("info4iq"),
            "iraqsm": _cand("iraqsm", 177000, "2026-11-26T13:50:00+03:00"),
        }
        dec2 = select_card("baghdad", cands2, prev, now, CFG)
        self.assertEqual(dec2.decision, "show_failover")
        self.assertEqual(dec2.value, 177240)

    def test_07_diff_warn(self):
        now = _dt("2026-11-26T14:00:00+03:00")
        cands = {
            "tg_kifah": _cand("tg_kifah", 168800, "2026-11-26T13:55:00+03:00"),
            "tg_dollariraqi": _cand("tg_dollariraqi", 171000, "2026-11-26T13:50:00+03:00"),  # ~1.3%
            "info4iq": _fail("info4iq"),
            "iraqsm": _fail("iraqsm"),
        }
        dec = select_card("baghdad", cands, None, now, CFG)
        self.assertEqual(dec.decision, "show_primary")
        self.assertEqual(dec.value, 168800)
        self.assertTrue(any(w.startswith("diff_primary_vs_tg_dollariraqi=") for w in dec.warnings))

    def test_08_all_stale_hold_and_no_value(self):
        now = _dt("2026-11-26T15:30:00+03:00")
        old = "2026-11-26T10:00:00+03:00"  # >4 market hours by 15:30
        cands = {
            "tg_kifah": _cand("tg_kifah", 168800, old),
            "tg_dollariraqi": _cand("tg_dollariraqi", 169000, old),
            "info4iq": _cand("info4iq", 169375, old),
            "iraqsm": _cand("iraqsm", 169000, old),
        }
        prev = CardState(value=168800, source="tg_kifah", posted_at=old)
        dec = select_card("baghdad", cands, prev, now, CFG)
        self.assertEqual(dec.decision, "hold_all_stale")
        self.assertTrue(dec.ok)
        self.assertTrue(dec.stale)
        self.assertEqual(dec.stale_since, old)
        self.assertEqual(dec.value, 168800)

        dec2 = select_card("baghdad", cands, None, now, CFG)
        self.assertEqual(dec2.decision, "no_value")
        self.assertFalse(dec2.ok)

    def test_10_invalid_config_aborts(self):
        with self.assertRaises(ConfigError):
            validate_config_dict(
                {
                    "version": 1,
                    "baghdad_order": ["tg_kifah", "not_a_source"],
                    "erbil_order": ["tg_kifah"],
                    "sanity_min": 120000,
                    "sanity_max": 220000,
                    "stale_trading_hours": 4,
                    "jump_pct": 3,
                    "confirm_tol_pct": 1,
                    "diff_warn_pct": 1,
                    "trading_days": ["Sun"],
                    "trading_window": ["10:00", "15:30"],
                    "enabled": {"tg_kifah": True},
                }
            )
        # Changing order in file changes next selection
        cfg2 = apply_overrides(CFG, {"baghdad_order": ["iraqsm", "tg_kifah", "tg_dollariraqi", "info4iq"]})
        now = _dt("2026-11-26T14:00:00+03:00")
        cands = {
            "tg_kifah": _cand("tg_kifah", 168800, "2026-11-26T13:55:00+03:00"),
            "tg_dollariraqi": _cand("tg_dollariraqi", 169000, "2026-11-26T13:50:00+03:00"),
            "info4iq": _fail("info4iq"),
            "iraqsm": _cand("iraqsm", 169100, "2026-11-26T13:50:00+03:00"),
        }
        dec = select_card("baghdad", cands, None, now, cfg2)
        self.assertEqual(dec.source, "iraqsm")
        self.assertEqual(dec.value, 169100)


class TestCards(unittest.TestCase):
    def test_11_erbil_province_shops(self):
        now = _dt("2026-11-26T14:10:00+03:00")
        by_source = {
            "tg_kifah": {
                "fetch_ok": True,
                "ok": True,
                "rows": [
                    Row(
                        source="tg_kifah",
                        fetched_at=rfc3339(now),
                        market="erbil",
                        kind="bursa",
                        mid=169000,
                        post_time="2026-11-26T13:56:19+03:00",
                        post_id="borsat_alkfah/30591",
                        parse_status="ok",
                    ),
                    Row(
                        source="tg_kifah",
                        fetched_at=rfc3339(now),
                        market="basra",
                        kind="bursa",
                        mid=168800,
                        post_time="2026-11-26T13:55:53+03:00",
                        post_id="borsat_alkfah/30590",
                        parse_status="ok",
                    ),
                    Row(
                        source="tg_kifah",
                        fetched_at=rfc3339(now),
                        market="kifah",
                        kind="bursa",
                        mid=168800,
                        post_time="2026-11-26T13:55:19+03:00",
                        post_id="borsat_alkfah/30588",
                        parse_status="ok",
                    ),
                ],
            },
            "tg_dollariraqi": {
                "fetch_ok": True,
                "ok": True,
                "rows": [
                    Row(
                        source="tg_dollariraqi",
                        fetched_at=rfc3339(now),
                        market="baghdad",
                        kind="bursa",
                        mid=169000,
                        ask=169000,
                        post_time="2026-11-26T13:50:00+03:00",
                        post_id="dollariraqi/6612",
                        parse_status="ok",
                    ),
                    Row(
                        source="tg_dollariraqi",
                        fetched_at=rfc3339(now),
                        market="basra",
                        kind="bursa",
                        mid=169100,
                        ask=169100,
                        post_time="2026-11-26T13:50:00+03:00",
                        post_id="dollariraqi/6612",
                        parse_status="ok",
                    ),
                    Row(
                        source="tg_dollariraqi",
                        fetched_at=rfc3339(now),
                        market="shop",
                        kind="shop",
                        location="Baghdad",
                        label="بغداد - الصيرفات",
                        bid=168600,
                        ask=169000,
                        mid=168800,
                        post_time="2026-11-26T13:50:00+03:00",
                        post_id="dollariraqi/6612",
                        parse_status="ok",
                    ),
                ],
            },
            "info4iq": {"fetch_ok": False, "ok": False, "error_class": "http_429", "rows": []},
            "iraqsm": {"fetch_ok": False, "ok": False, "error_class": "http_429", "rows": []},
            "dinarlive_erbil": {"fetch_ok": False, "ok": False, "error_class": "timeout", "rows": []},
        }
        cards, states, decs = build_cards(
            by_source=by_source, prev_states={}, now=now, cfg=CFG
        )
        keys = [c["key"] for c in cards]
        self.assertIn("erbil", keys)
        self.assertEqual(cards[0]["key"], "erbil")
        self.assertEqual(decs["erbil"].source, "tg_kifah")
        self.assertIn("basra", keys)
        basra = next(c for c in cards if c["key"] == "basra")
        # Kifah preferred over dollariraqi for province
        self.assertEqual(basra["source"], "tg_kifah")
        self.assertEqual(basra["value"], 168800)
        shops = [c for c in cards if c["key"] == "shops_baghdad"]
        self.assertEqual(len(shops), 1)
        self.assertEqual(shops[0]["kind"], "shop")
        self.assertIsNotNone(shops[0]["bid"])
        self.assertIsNotNone(shops[0]["ask"])
        # shops never leak into baghdad bursa candidates
        bag = baghdad_candidates_from_rows(by_source)
        self.assertEqual(bag["tg_kifah"].value, 168800)
        self.assertNotEqual(bag["tg_kifah"].value, shops[0]["bid"])

        # Retain stale then drop after 7 days
        prev = states["erbil"]
        prev.last_valid_at = "2026-11-01T10:00:00+03:00"
        prev.posted_at = "2026-11-01T10:00:00+03:00"
        empty = {
            "tg_kifah": {"fetch_ok": True, "ok": True, "rows": []},
            "tg_dollariraqi": {"fetch_ok": True, "ok": True, "rows": []},
            "info4iq": {"fetch_ok": False, "ok": False, "rows": [], "error_class": "x"},
            "iraqsm": {"fetch_ok": False, "ok": False, "rows": [], "error_class": "x"},
            "dinarlive_erbil": {"fetch_ok": False, "ok": False, "rows": [], "error_class": "x"},
        }
        cards2, _, _ = build_cards(
            by_source=empty,
            prev_states={"erbil": prev},
            now=_dt("2026-11-26T14:10:00+03:00"),
            cfg=CFG,
        )
        self.assertNotIn("erbil", [c["key"] for c in cards2])


class TestScenarioReplay(unittest.TestCase):
    def _row_from_dict(self, d: dict) -> Row:
        return Row(
            source=d.get("source", ""),
            fetched_at=d.get("fetched_at", ""),
            market=d.get("market", ""),
            location=d.get("location", ""),
            kind=d.get("kind", "bursa"),
            label=d.get("label", ""),
            bid=d.get("bid"),
            ask=d.get("ask"),
            mid=d.get("mid"),
            post_time=d.get("post_time", ""),
            post_id=d.get("post_id", ""),
            parse_status=d.get("parse_status", "ok"),
            warnings=list(d.get("warnings") or []),
            text=d.get("text", ""),
        )

    def _run_scenario(self, path: Path) -> list[str]:
        data = json.loads(path.read_text(encoding="utf-8"))
        cfg = apply_overrides(CFG, data.get("config_overrides") or {})
        prev: CardState | None = None
        lines = []
        for i, run in enumerate(data["runs"]):
            now = _dt(run["now"])
            by_source: dict = {}
            for sid, payload in (run.get("sources") or {}).items():
                rows = [self._row_from_dict(r) for r in (payload.get("rows") or [])]
                by_source[sid] = {
                    "fetch_ok": bool(payload.get("fetch_ok", True)),
                    "ok": bool(payload.get("fetch_ok", True)),
                    "error_class": payload.get("error_class"),
                    "rows": rows,
                }
            # fill missing sources as down
            for sid in cfg.baghdad_order + cfg.erbil_order:
                if sid not in by_source:
                    by_source[sid] = {
                        "fetch_ok": False,
                        "ok": False,
                        "error_class": "missing_in_scenario",
                        "rows": [],
                    }
            expect = run["expect"]
            card = expect.get("card", "baghdad")
            if card == "baghdad":
                cands = baghdad_candidates_from_rows(by_source)
                order = list(cfg.baghdad_order)
            elif card == "erbil":
                cands = erbil_candidates_from_rows(by_source)
                order = list(cfg.erbil_order)
            else:
                cands = city_candidates(by_source, card)
                order = ["tg_kifah", "tg_dollariraqi"]
            dec = select_card(card, cands, prev, now, cfg, order=order)
            actual = {
                "decision": dec.decision,
                "value": dec.value,
                "stale": dec.stale,
                "held_reason": dec.held_reason,
            }
            lines.append(
                f"{path.name}#{i} expect={expect.get('decision')} actual={dec.decision} "
                f"value={dec.value} warnings={dec.warnings}"
            )
            self.assertEqual(dec.decision, expect["decision"], msg=lines[-1])
            if "value" in expect:
                self.assertEqual(dec.value, expect["value"], msg=lines[-1])
            if "stale" in expect:
                self.assertEqual(dec.stale, expect["stale"], msg=lines[-1])
            if "held_reason" in expect:
                self.assertEqual(dec.held_reason, expect["held_reason"], msg=lines[-1])
            for sub in expect.get("warnings_contain") or []:
                self.assertTrue(
                    any(sub in w for w in dec.warnings),
                    msg=f"{lines[-1]} missing warning substring {sub!r} in {dec.warnings}",
                )
            # carry state
            if dec.ok and dec.value is not None:
                prev = CardState(
                    value=dec.value,
                    bid=dec.bid,
                    ask=dec.ask,
                    source=dec.source,
                    posted_at=dec.posted_at,
                    post_id=dec.post_id,
                    post_url=dec.post_url,
                    stale=dec.stale,
                    stale_since=dec.stale_since,
                )
        return lines

    def test_all_scenarios(self):
        files = sorted(SCENARIOS.glob("*.json"))
        self.assertGreaterEqual(len(files), 8, "need ≥8 scenario files")
        table = ["| run | expected | actual | value | warnings |", "|---|---|---|---|---|"]
        for f in files:
            lines = self._run_scenario(f)
            for ln in lines:
                # ln format already human; also build summary cells
                table.append(f"| {ln} | | | | |")
        print("\n".join(table))


if __name__ == "__main__":
    unittest.main()
