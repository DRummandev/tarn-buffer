"""Envelope, history, status, and dry-run/fixture pipeline tests."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from scraper.cards import baghdad_candidates_from_rows, build_cards
from scraper.common import rfc3339
from scraper.config import ConfigError, load_sources_config
from scraper.model import Row
from scraper.outputs import (
    append_history_if_changed,
    build_latest,
    build_status,
    expand_history_m,
    format_display,
    format_display_per100,
    history_row,
    history_semantic_key,
    latest_changed,
    make_quote,
    paper_units,
    parse_history_row,
    per_dollar,
    write_json,
)
from scraper.run import compact_run_log
from scraper.run import run_pipeline
from scraper.selector import Candidate, CardState, select_card

ROOT = Path(__file__).resolve().parent.parent
CFG = load_sources_config(ROOT / "config" / "sources.json")


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


class TestEnvelopeUnits(unittest.TestCase):
    def test_consistent_for_sample_values(self):
        for per100 in (168800, 169375, 171812.5):
            q = make_quote(
                source="tg_kifah",
                value=per100,
                bid=None,
                ask=None,
                posted_at="2026-11-26T13:55:19+03:00",
            )
            self.assertEqual(q["per100"], per100)
            self.assertEqual(q["price"], paper_units(per100))
            self.assertEqual(q["perDollar"], per_dollar(per100))
            self.assertEqual(q["display"], format_display(q["price"]))
            self.assertEqual(q["displayPer100"], format_display_per100(per100))
            self.assertEqual(q["buy"], q["price"])
            self.assertEqual(q["sell"], q["price"])
            self.assertNotIn("error", q)
            self.assertTrue(q["fetch_ok"])

    def test_bid_ask_paper_units(self):
        q = make_quote(
            source="info4iq",
            value=169375,
            bid=169400,
            ask=169350,
            posted_at="2026-11-26T08:06:10+03:00",
            warnings=["buy_gt_sell"],
        )
        self.assertEqual(q["buy"], 169.4)
        self.assertEqual(q["sell"], 169.35)
        self.assertEqual(q["price"], 169.375)
        self.assertNotIn("error", q)


class TestChangeDetection(unittest.TestCase):
    def test_ignore_fetched_at(self):
        a = {
            "ok": True,
            "value": 168800,
            "fetchedAt": "2026-11-26T14:00:00+03:00",
            "generated_at": "2026-11-26T14:00:00+03:00",
            "source": "tg_kifah",
        }
        b = dict(a)
        b["fetchedAt"] = "2026-11-26T14:05:00+03:00"
        b["generated_at"] = "2026-11-26T14:05:00+03:00"
        self.assertFalse(latest_changed(a, b))
        b["value"] = 168900
        self.assertTrue(latest_changed(a, b))


class TestHistory(unittest.TestCase):
    def test_append_only_on_real_change_and_index(self):
        with tempfile.TemporaryDirectory() as td:
            hdir = Path(td)
            now1 = _dt("2026-11-26T13:55:40+03:00")
            from scraper.selector import Decision

            d1 = Decision(
                mode="primary",
                decision="show_primary",
                ok=True,
                value=168800,
                source="tg_kifah",
                posted_at="2026-11-26T13:55:19+03:00",
                stale=False,
            )
            row1 = history_row(t=now1, baghdad=d1, source_values={"tg_kifah": 168800})
            self.assertTrue(append_history_if_changed(hdir, row1, now=now1))
            # writer uses compact m keys; omits stale:false
            written = json.loads(
                (hdir / "2026-11-26.jsonl").read_text().splitlines()[0]
            )
            self.assertIn("k", written["m"])
            self.assertNotIn("tg_kifah", written["m"])
            self.assertNotIn("stale", written)
            # same semantic → no append
            now2 = _dt("2026-11-26T14:00:00+03:00")
            row2 = history_row(t=now2, baghdad=d1, source_values={"tg_kifah": 168800})
            self.assertFalse(append_history_if_changed(hdir, row2, now=now2))
            # value change → append
            d3 = Decision(
                mode="primary",
                decision="show_primary",
                ok=True,
                value=168850,
                source="tg_kifah",
                posted_at="2026-11-26T14:02:00+03:00",
                stale=False,
            )
            row3 = history_row(t=now2, baghdad=d3, source_values={"tg_kifah": 168850})
            self.assertTrue(append_history_if_changed(hdir, row3, now=now2))
            day = hdir / "2026-11-26.jsonl"
            lines = [ln for ln in day.read_text().splitlines() if ln.strip()]
            self.assertEqual(len(lines), 2)
            # midnight crossing index
            now3 = _dt("2026-11-27T10:05:00+03:00")
            d4 = Decision(
                mode="primary",
                decision="show_primary",
                ok=True,
                value=168900,
                source="tg_kifah",
                posted_at="2026-11-27T10:00:00+03:00",
                stale=False,
            )
            row4 = history_row(t=now3, baghdad=d4, source_values={"tg_kifah": 168900})
            self.assertTrue(append_history_if_changed(hdir, row4, now=now3))
            idx = json.loads((hdir / "index.json").read_text())
            self.assertEqual(idx["days"], ["2026-11-26", "2026-11-27"])
            self.assertEqual(idx["first"], "2026-11-26")
            # past rows untouched
            self.assertEqual(len(lines), 2)

    def test_parse_legacy_and_compact_history_shapes(self):
        """Both mid-file shapes are intentional; consumers must accept both."""
        legacy = {
            "t": "2026-11-27T13:15:48.255537+03:00",
            "p": "2026-11-26T13:55:19+03:00",
            "v": 168800.0,
            "s": "tg_kifah",
            "stale": False,
            "m": {
                "dinarlive_erbil": 169400.0,
                "info4iq": 169375.0,
                "iraqsm": 169000.0,
                "tg_dollariraqi": 169000.0,
                "tg_kifah": 168800.0,
            },
        }
        compact = {
            "t": "2026-11-27T14:43:14+03:00",
            "p": "2026-11-26T13:55:19+03:00",
            "v": 168800,
            "s": "tg_kifah",
            "m": {"d": 169000, "e": 169400, "i": 169375, "k": 168800, "r": 168600},
        }
        pl = parse_history_row(legacy)
        pc = parse_history_row(compact)
        self.assertFalse(pl["stale"])
        self.assertFalse(pc["stale"])  # omitted → False
        self.assertEqual(pl["m"]["tg_kifah"], 168800.0)
        self.assertEqual(pc["m"]["tg_kifah"], 168800)
        self.assertEqual(pc["m"]["iraqsm"], 168600)
        self.assertEqual(
            expand_history_m({"k": 1, "d": 2}),
            {"tg_kifah": 1, "tg_dollariraqi": 2},
        )
        # Semantic compare equates legacy full keys with compact shorts
        same_as_legacy_compact = {
            "t": "x",
            "p": legacy["p"],
            "v": 168800,
            "s": "tg_kifah",
            "m": {"d": 169000, "e": 169400, "i": 169375, "k": 168800, "r": 169000},
        }
        self.assertEqual(
            history_semantic_key(legacy),
            history_semantic_key(same_as_legacy_compact),
        )


class TestCompactLog(unittest.TestCase):
    def test_published_line_is_explicit(self):
        from scraper.selector import Decision

        dec = Decision(
            mode="primary",
            decision="show_primary",
            ok=True,
            value=168800,
            source="tg_kifah",
            posted_at="2026-11-26T13:55:19+03:00",
            stale=False,
        )
        lines = compact_run_log(
            by_source={
                "tg_kifah": {
                    "ok": True,
                    "ms": 120,
                    "last_ok_post_time": "2026-11-26T13:55:19+03:00",
                }
            },
            source_results={"tg_kifah": {"newest_value": 169999.0}},
            baghdad=dec,
            changed_latest=False,
        )
        published = [ln for ln in lines if ln.startswith("published ")]
        self.assertEqual(len(published), 1)
        self.assertIn("value=168800", published[0])
        self.assertIn("source=tg_kifah", published[0])
        # newest ok fetch row must not be mistaken for published
        self.assertTrue(any("newest=169999" in ln for ln in lines))
        self.assertFalse(any(ln.startswith("decision ") and "value=" in ln for ln in lines))


class TestSourcesMapKeepLastGood(unittest.TestCase):
    def test_09_source_down_keeps_last_good(self):
        now = _dt("2026-11-26T14:10:00+03:00")
        # First build with all good
        by1 = {
            "tg_kifah": {
                "fetch_ok": True,
                "ok": True,
                "rows": [
                    Row(
                        source="tg_kifah",
                        fetched_at=rfc3339(now),
                        market="kifah",
                        kind="bursa",
                        mid=168800,
                        post_time="2026-11-26T13:55:19+03:00",
                        post_id="borsat_alkfah/30588",
                        parse_status="ok",
                    )
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
                        post_time="2026-11-26T13:50:00+03:00",
                        post_id="dollariraqi/6612",
                        parse_status="ok",
                    )
                ],
            },
            "info4iq": {"fetch_ok": False, "ok": False, "error_class": "x", "rows": []},
            "iraqsm": {"fetch_ok": False, "ok": False, "error_class": "x", "rows": []},
            "dinarlive_erbil": {"fetch_ok": False, "ok": False, "error_class": "x", "rows": []},
        }
        cands = baghdad_candidates_from_rows(by1)
        dec = select_card("baghdad", cands, None, now, CFG)
        cards, _, _ = build_cards(by_source=by1, prev_states={}, now=now, cfg=CFG)
        from scraper.run import _sources_quotes

        quotes1 = _sources_quotes(by1, cands, None)
        latest1 = build_latest(
            now=now, cfg=CFG, baghdad=dec, sources_quotes=quotes1, cards=cards
        )
        self.assertIn("tg_dollariraqi", latest1["sources"])
        self.assertTrue(latest1["sources"]["tg_dollariraqi"]["fetch_ok"])

        # dollariraqi down — keep last good
        by2 = dict(by1)
        by2["tg_dollariraqi"] = {
            "fetch_ok": False,
            "ok": False,
            "error_class": "http_429",
            "rows": [],
        }
        cands2 = baghdad_candidates_from_rows(by2)
        dec2 = select_card("baghdad", cands2, None, now, CFG)
        quotes2 = _sources_quotes(by2, cands2, latest1)
        self.assertIn("tg_dollariraqi", quotes2)
        self.assertFalse(quotes2["tg_dollariraqi"]["fetch_ok"])
        self.assertEqual(quotes2["tg_dollariraqi"]["value"], 169000)
        self.assertEqual(
            quotes2["tg_dollariraqi"]["posted_at"], "2026-11-26T13:50:00+03:00"
        )
        self.assertNotIn("error", quotes2["tg_dollariraqi"])


class TestConfigAbort(unittest.TestCase):
    def test_10_bad_config_aborts_without_write(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "config").mkdir()
            bad = {
                "version": 1,
                "baghdad_order": ["tg_kifah", "unknown_src"],
                "erbil_order": ["tg_kifah"],
                "sanity_min": 120000,
                "sanity_max": 220000,
                "stale_trading_hours": 4,
                "jump_pct": 3,
                "confirm_tol_pct": 1,
                "diff_warn_pct": 1,
                "trading_days": ["Sun", "Mon", "Tue", "Wed", "Thu"],
                "trading_window": ["10:00", "15:30"],
                "enabled": {"tg_kifah": True},
            }
            (root / "config" / "sources.json").write_text(
                json.dumps(bad), encoding="utf-8"
            )
            shutil.copytree(ROOT / "tests" / "fixtures", root / "tests" / "fixtures")
            with self.assertRaises(ConfigError):
                run_pipeline(
                    repo_root=root,
                    dry_run=True,
                    out_dir=root / "dry-out",
                    no_network=True,
                    fixtures_dir=root / "tests" / "fixtures",
                    now=_dt("2026-11-27T00:30:00+03:00"),
                )
            self.assertFalse((root / "dry-out" / "latest.json").exists())
            self.assertFalse((root / "latest.json").exists())


class TestFixturePipeline(unittest.TestCase):
    def test_no_network_fixtures_produces_envelope(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # minimal repo: config + fixtures symlink/copy
            shutil.copytree(ROOT / "config", root / "config")
            shutil.copytree(ROOT / "tests" / "fixtures", root / "tests" / "fixtures")
            # package import uses installed scraper from workspace; only data paths matter
            out = root / "dry-out"
            result = run_pipeline(
                repo_root=root,
                dry_run=True,
                out_dir=out,
                no_network=True,
                fixtures_dir=root / "tests" / "fixtures",
                now=_dt("2026-11-27T00:30:00+03:00"),
            )
            self.assertTrue(result["ok"])
            latest = result["latest"]
            self.assertEqual(latest["schema"], 2)
            self.assertEqual(latest["app"], "شكد سعر الدولار؟")
            self.assertEqual(latest["unit"], "IQD_per_100_USD")
            self.assertIn("policy", latest)
            self.assertIn("selector", latest)
            self.assertIn("cards", latest)
            self.assertTrue(latest["ok"])
            self.assertEqual(latest["value"], 168800)
            self.assertEqual(latest["source"], "tg_kifah")
            q = latest["latest"]
            self.assertEqual(q["per100"], 168800)
            self.assertEqual(q["price"], 168.8)
            self.assertEqual(q["display"], "168.8")
            self.assertEqual(q["displayPer100"], "168,800")
            self.assertNotIn("error", q)
            self.assertTrue((out / "latest.json").exists())
            # second run same data → no semantic change vs first dry-out write baseline... 
            # compare against written latest as prev by seeding root latest
            shutil.copy(out / "latest.json", root / "latest.json")
            # bump only clock
            result2 = run_pipeline(
                repo_root=root,
                dry_run=True,
                out_dir=out / "2",
                no_network=True,
                fixtures_dir=root / "tests" / "fixtures",
                now=_dt("2026-11-27T00:35:00+03:00"),
            )
            self.assertFalse(result2["changed_latest"])

    def test_status_first_failure_sticky(self):
        now = _dt("2026-11-26T14:00:00+03:00")
        from scraper.selector import Decision

        dec = Decision(
            mode="primary",
            decision="show_primary",
            ok=True,
            value=168800,
            source="tg_kifah",
            posted_at="2026-11-26T13:55:00+03:00",
            order=["tg_kifah"],
            candidates={"tg_kifah": {"valid": True, "why_not": None}},
        )
        s1 = build_status(
            now=now,
            prev_status=None,
            source_results={
                "tg_kifah": {"ok": False, "error_class": "http_429", "last_ok_post_time": None}
            },
            baghdad=dec,
        )
        ff = s1["sources"]["tg_kifah"]["first_failure_at"]
        self.assertIsNotNone(ff)
        later = _dt("2026-11-26T14:10:00+03:00")
        s2 = build_status(
            now=later,
            prev_status=s1,
            source_results={
                "tg_kifah": {"ok": False, "error_class": "http_429", "last_ok_post_time": None}
            },
            baghdad=dec,
        )
        self.assertEqual(s2["sources"]["tg_kifah"]["first_failure_at"], ff)
        s3 = build_status(
            now=later,
            prev_status=s2,
            source_results={
                "tg_kifah": {
                    "ok": True,
                    "error_class": None,
                    "last_ok_post_time": "2026-11-26T14:08:00+03:00",
                }
            },
            baghdad=dec,
        )
        self.assertIsNone(s3["sources"]["tg_kifah"]["first_failure_at"])


class TestLatestExampleShape(unittest.TestCase):
    def test_example_fixture_if_present(self):
        path = ROOT / "tests" / "fixtures" / "latest.example.json"
        if not path.exists():
            self.skipTest("latest.example.json not generated yet")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["schema"], 2)
        self.assertIn("latest", data)
        self.assertIn("sources", data)
        self.assertIn("cards", data)
        self.assertIn("policy", data)
        q = data["latest"]
        self.assertNotIn("error", q)
        self.assertEqual(q["per100"], data["value"])


if __name__ == "__main__":
    unittest.main()
