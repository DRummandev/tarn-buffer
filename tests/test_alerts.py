"""Phase 3: decide_alerts, keepalive gate, atomic write / jump post-check, history size."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from scraper.alerts import (
    TITLE_PARSER_DRIFT,
    TITLE_PRIMARY_FAILING,
    TITLE_SOURCES_FAILING,
    AlertAction,
    OpenIssue,
    apply_alert_actions,
    build_alert_body,
    decide_alerts,
    is_market_time,
    should_update_keepalive,
)
from scraper.common import BAGHDAD
from scraper.outputs import (
    atomic_write_json,
    estimate_history_day_bytes,
    post_check_jump,
    validate_latest_payload,
)
from scraper.config import load_sources_config

ROOT = Path(__file__).resolve().parent.parent
CFG = load_sources_config(ROOT / "config" / "sources.json")


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _status(
    *,
    sources: dict | None = None,
    selector_has_candidate: bool = True,
    primary_ok: bool = True,
    selector_no_candidate_since: str | None = None,
    parser_drift: dict | None = None,
) -> dict:
    return {
        "sources": sources or {},
        "primary_ok": primary_ok,
        "selector_has_candidate": selector_has_candidate,
        "selector_no_candidate_since": selector_no_candidate_since,
        "parser_drift": parser_drift or {"active": False, "since": None, "ok_kifah_rows": 0},
        "open_issue": False,
    }


def _failing(first_failure_at: str, err: str = "timeout") -> dict:
    return {
        "ok": False,
        "last_error_class": err,
        "first_failure_at": first_failure_at,
        "last_ok_post_time": "2026-11-26T13:00:00+03:00",
    }


def _ok(last: str = "2026-11-26T13:55:19+03:00") -> dict:
    return {
        "ok": True,
        "last_error_class": None,
        "first_failure_at": None,
        "last_ok_post_time": last,
    }


class TestDecideAlerts(unittest.TestCase):
    def test_no_failure_no_action(self):
        now = _dt("2026-11-26T14:00:00+03:00")
        status = _status(
            sources={
                "tg_kifah": _ok(),
                "tg_dollariraqi": _ok(),
                "info4iq": _ok(),
                "iraqsm": _ok(),
                "dinarlive_erbil": _ok(),
            },
            selector_has_candidate=True,
        )
        actions = decide_alerts(status, True, now, [])
        self.assertEqual(actions, [])

    def test_25_min_failure_no_open(self):
        now = _dt("2026-11-26T14:00:00+03:00")
        ff = "2026-11-26T13:35:00+03:00"  # 25 min
        status = _status(
            sources={
                "tg_kifah": _failing(ff),
                "tg_dollariraqi": _failing(ff),
                "info4iq": _failing(ff),
                "iraqsm": _failing(ff),
                "dinarlive_erbil": _failing(ff),
            },
            selector_has_candidate=False,
            selector_no_candidate_since=ff,
            primary_ok=False,
        )
        actions = decide_alerts(status, False, now, [])
        opens = [a for a in actions if a.op == "open"]
        self.assertEqual(opens, [])

    def test_30_plus_min_opens_sources_failing(self):
        now = _dt("2026-11-26T14:05:00+03:00")
        ff = "2026-11-26T13:30:00+03:00"  # 35 min
        status = _status(
            sources={
                "tg_kifah": _failing(ff),
                "tg_dollariraqi": _failing(ff),
                "info4iq": _failing(ff),
                "iraqsm": _failing(ff),
                "dinarlive_erbil": _failing(ff),
            },
            selector_has_candidate=False,
            selector_no_candidate_since=ff,
            primary_ok=False,
        )
        actions = decide_alerts(status, False, now, [])
        titles = {a.title for a in actions if a.op == "open"}
        self.assertIn(TITLE_SOURCES_FAILING, titles)
        self.assertIn(TITLE_PRIMARY_FAILING, titles)

    def test_existing_open_no_duplicate(self):
        now = _dt("2026-11-26T14:05:00+03:00")
        ff = "2026-11-26T13:30:00+03:00"
        status = _status(
            sources={
                "tg_kifah": _failing(ff),
                "tg_dollariraqi": _failing(ff),
                "info4iq": _failing(ff),
                "iraqsm": _failing(ff),
                "dinarlive_erbil": _failing(ff),
            },
            selector_has_candidate=False,
            primary_ok=False,
        )
        open_issues = [
            OpenIssue(number=7, title=TITLE_SOURCES_FAILING),
            OpenIssue(number=8, title=TITLE_PRIMARY_FAILING),
        ]
        actions = decide_alerts(status, False, now, open_issues)
        opens = [a for a in actions if a.op == "open"]
        self.assertEqual(opens, [])

    def test_recovery_closes(self):
        now = _dt("2026-11-26T14:10:00+03:00")
        status = _status(
            sources={
                "tg_kifah": _ok(),
                "tg_dollariraqi": _ok(),
                "info4iq": _ok(),
                "iraqsm": _ok(),
                "dinarlive_erbil": _ok(),
            },
            selector_has_candidate=True,
            primary_ok=True,
        )
        open_issues = [
            OpenIssue(number=7, title=TITLE_SOURCES_FAILING),
            OpenIssue(number=8, title=TITLE_PRIMARY_FAILING),
        ]
        actions = decide_alerts(status, True, now, open_issues)
        closes = {a.title: a for a in actions if a.op == "close"}
        self.assertIn(TITLE_SOURCES_FAILING, closes)
        self.assertIn(TITLE_PRIMARY_FAILING, closes)
        self.assertEqual(closes[TITLE_SOURCES_FAILING].issue_number, 7)

    def test_primary_only_failure_issue_b_only(self):
        now = _dt("2026-11-26T14:05:00+03:00")
        ff = "2026-11-26T13:30:00+03:00"
        status = _status(
            sources={
                "tg_kifah": _failing(ff),
                "tg_dollariraqi": _ok(),
                "info4iq": _ok(),
                "iraqsm": _ok(),
                "dinarlive_erbil": _ok(),
            },
            selector_has_candidate=True,  # backup healthy
            primary_ok=False,
        )
        actions = decide_alerts(status, True, now, [])
        opens = [a for a in actions if a.op == "open"]
        self.assertEqual(len(opens), 1)
        self.assertEqual(opens[0].title, TITLE_PRIMARY_FAILING)

    def test_saturday_market_window(self):
        # Sat 10:00–12:30 open; before/after closed; Friday always closed
        self.assertTrue(is_market_time(_dt("2026-11-28T11:00:00+03:00")))
        self.assertFalse(is_market_time(_dt("2026-11-28T09:59:00+03:00")))
        self.assertFalse(is_market_time(_dt("2026-11-28T12:30:00+03:00")))
        self.assertFalse(is_market_time(_dt("2026-11-27T11:00:00+03:00")))  # Friday

    def test_weekend_stale_no_alert(self):
        # Friday afternoon: outside trading days; sources ok but no candidate (stale)
        now = _dt("2026-11-27T14:00:00+03:00")  # Friday
        self.assertFalse(is_market_time(now))
        since = "2026-11-27T10:00:00+03:00"
        status = _status(
            sources={
                "tg_kifah": _ok("2026-11-26T13:55:19+03:00"),
                "tg_dollariraqi": _ok(),
                "info4iq": _ok(),
                "iraqsm": _ok(),
                "dinarlive_erbil": _ok(),
            },
            selector_has_candidate=False,
            selector_no_candidate_since=since,
            primary_ok=False,
        )
        actions = decide_alerts(status, False, now, [])
        self.assertEqual([a for a in actions if a.op == "open"], [])

    def test_parser_drift_opens_after_24_market_hours(self):
        # Enough calendar span that market minutes exceed 24h (Sun–Thu windows)
        now = _dt("2026-11-30T12:00:00+03:00")  # Mon
        since = "2026-11-19T10:00:00+03:00"  # prior Thu open
        status = _status(
            sources={"tg_kifah": _ok()},
            selector_has_candidate=True,
            parser_drift={
                "active": True,
                "since": since,
                "ok_kifah_rows": 0,
                "rate_looking": True,
            },
        )
        actions = decide_alerts(status, True, now, [])
        titles = {a.title for a in actions if a.op == "open"}
        self.assertIn(TITLE_PARSER_DRIFT, titles)
        # Short drift must not open yet
        short = dict(status)
        short["parser_drift"] = {
            "active": True,
            "since": "2026-11-30T10:30:00+03:00",
            "ok_kifah_rows": 0,
            "rate_looking": True,
        }
        early = decide_alerts(short, True, now, [])
        self.assertNotIn(TITLE_PARSER_DRIFT, {a.title for a in early if a.op == "open"})

    def test_body_contains_no_secrets(self):
        now = _dt("2026-11-26T14:05:00+03:00")
        status = _status(
            sources={"tg_kifah": _failing("2026-11-26T13:30:00+03:00")},
            selector_has_candidate=False,
        )
        with mock.patch.dict(
            os.environ,
            {
                "GITHUB_TOKEN": "ghs_SECRETTOKENVALUE1234567890",
                "GITHUB_REPOSITORY": "DRummandev/tarn-buffer",
                "GITHUB_RUN_ID": "12345",
                "GITHUB_SERVER_URL": "https://github.com",
            },
        ):
            body = build_alert_body(title=TITLE_SOURCES_FAILING, status=status, now=now)
        self.assertNotIn("ghs_SECRETTOKENVALUE1234567890", body)
        self.assertNotIn("SECRETTOKEN", body)
        self.assertIn("tg_kifah", body)
        self.assertIn("run:", body)

    def test_api_error_swallowed(self):
        actions = [
            AlertAction(op="open", title=TITLE_SOURCES_FAILING, body="x"),
        ]
        with mock.patch(
            "scraper.alerts.ensure_ingest_label",
            side_effect=RuntimeError("boom"),
        ), mock.patch(
            "scraper.alerts._gh_request",
            side_effect=Exception("network"),
        ):
            logs = apply_alert_actions(actions)
        self.assertTrue(any("error" in ln.lower() or "API" in ln for ln in logs))


class TestKeepalive(unittest.TestCase):
    def test_noop_when_last_commit_under_6_days(self):
        now = _dt("2026-11-27T03:17:00+00:00")
        last = now - timedelta(days=3)
        self.assertFalse(should_update_keepalive(last, now))

    def test_due_when_last_commit_over_6_days(self):
        now = _dt("2026-11-27T03:17:00+00:00")
        last = now - timedelta(days=6, hours=1)
        self.assertTrue(should_update_keepalive(last, now))

    def test_exactly_6_days_is_due(self):
        now = _dt("2026-11-27T03:17:00+00:00")
        last = now - timedelta(days=6)
        self.assertTrue(should_update_keepalive(last, now))


class TestAtomicAndPostCheck(unittest.TestCase):
    def test_atomic_write_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "latest.json"
            obj = {"ok": True, "value": 168800, "nested": {"a": 1}}
            atomic_write_json(path, obj, pretty=True)
            loaded = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(loaded["value"], 168800)
            # no leftover temps
            temps = list(Path(td).glob(".*.tmp*")) + list(Path(td).glob("*.tmp"))
            self.assertEqual(temps, [])

    def test_validate_latest_payload(self):
        good = {
            "ok": True,
            "value": 168800,
            "held_reason": None,
            "policy": {"sanity_min": 120000, "sanity_max": 220000},
        }
        self.assertIsNone(validate_latest_payload(good, sanity_min=120000, sanity_max=220000))
        bad = dict(good)
        bad["value"] = 50
        self.assertIsNotNone(validate_latest_payload(bad, sanity_min=120000, sanity_max=220000))

    def test_post_check_jump_aborts_without_held_reason(self):
        prev = {"value": 168800, "held_reason": None}
        new = {"value": 180000, "held_reason": None}  # > 3%
        err = post_check_jump(prev, new, jump_pct=3.0)
        self.assertEqual(err, "post_check_failed")
        held = {"value": 180000, "held_reason": "jump_unconfirmed"}
        self.assertIsNone(post_check_jump(prev, held, jump_pct=3.0))
        small = {"value": 169000, "held_reason": None}
        self.assertIsNone(post_check_jump(prev, small, jump_pct=3.0))

    def test_history_day_under_50kb_for_288_rows(self):
        nbytes = estimate_history_day_bytes(n_rows=288)
        self.assertLess(nbytes, 50 * 1024)


if __name__ == "__main__":
    unittest.main()
