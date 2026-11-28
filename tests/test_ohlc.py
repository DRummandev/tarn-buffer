"""OHLC pre-aggregate: official Kifah only, omit empty hours, week starts Sat."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from scraper.common import BAGHDAD
from scraper.ohlc import (
    aggregate_hourly,
    build_ohlc_payloads,
    extract_official_prints,
    week_start_saturday,
    write_ohlc,
)
from scraper.outputs import latest_changed


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


class TestOhlc(unittest.TestCase):
    def test_week_starts_saturday(self):
        # 2026-11-28 is Saturday; 2026-11-29 is Sunday
        self.assertEqual(
            week_start_saturday(_dt("2026-11-28T12:00:00+03:00")),
            _dt("2026-11-28T00:00:00+03:00"),
        )
        self.assertEqual(
            week_start_saturday(_dt("2026-11-29T10:00:00+03:00")),
            _dt("2026-11-28T00:00:00+03:00"),
        )
        self.assertEqual(
            week_start_saturday(_dt("2026-11-27T14:00:00+03:00")),  # Friday
            _dt("2026-11-21T00:00:00+03:00"),
        )

    def test_extract_official_only_and_dedupe(self):
        rows = [
            {
                "t": "2026-11-28T13:00:00+03:00",
                "p": "2026-11-28T12:09:51+03:00",
                "v": 167000,
                "s": "tg_kifah",
                "m": {"k": 167000, "r": 166600},
            },
            # same Kifah print, other source map change — not a new print
            {
                "t": "2026-11-28T13:05:00+03:00",
                "p": "2026-11-28T12:09:51+03:00",
                "v": 167000,
                "s": "tg_kifah",
                "m": {"k": 167000, "r": 166500},
            },
            # failover shown — excluded from official series
            {
                "t": "2026-11-28T14:00:00+03:00",
                "p": "2026-11-28T13:50:00+03:00",
                "v": 166450,
                "s": "tg_dollariraqi",
                "m": {"k": 167000, "d": 166450},
            },
            {
                "t": "2026-11-28T15:00:00+03:00",
                "p": "2026-11-28T14:30:00+03:00",
                "v": 167100,
                "s": "tg_kifah",
                "m": {"k": 167100},
            },
        ]
        prints = extract_official_prints(rows)
        self.assertEqual(len(prints), 2)
        self.assertEqual(prints[0][1], 167000.0)
        self.assertEqual(prints[1][1], 167100.0)

    def test_omit_empty_hours_close_is_last(self):
        prints = [
            (_dt("2026-11-28T10:15:00+03:00"), 168000.0),
            (_dt("2026-11-28T10:45:00+03:00"), 168200.0),
            # gap 11:00 empty — omitted
            (_dt("2026-11-28T12:05:00+03:00"), 167000.0),
        ]
        hourly = aggregate_hourly(prints)
        self.assertEqual(len(hourly), 2)
        self.assertEqual(hourly[0]["t"], "2026-11-28T10:00:00+03:00")
        self.assertEqual(hourly[0]["o"], 168000)
        self.assertEqual(hourly[0]["h"], 168200)
        self.assertEqual(hourly[0]["l"], 168000)
        self.assertEqual(hourly[0]["c"], 168200)  # last print in hour
        self.assertEqual(hourly[0]["n"], 2)
        self.assertEqual(hourly[1]["t"], "2026-11-28T12:00:00+03:00")
        self.assertEqual(hourly[1]["c"], 167000)
        # no 11:00 bucket
        self.assertTrue(all(not r["t"].startswith("2026-11-28T11:") for r in hourly))

    def test_write_ohlc_and_no_invent(self):
        with tempfile.TemporaryDirectory() as td:
            hdir = Path(td)
            (hdir / "index.json").write_text(
                json.dumps(
                    {
                        "days": ["2026-11-28"],
                        "first": "2026-11-28",
                        "updated": "2026-11-28T15:00:00+03:00",
                    }
                ),
                encoding="utf-8",
            )
            (hdir / "2026-11-28.jsonl").write_text(
                json.dumps(
                    {
                        "t": "2026-11-28T13:00:00+03:00",
                        "p": "2026-11-28T12:09:51+03:00",
                        "v": 167000,
                        "s": "tg_kifah",
                        "m": {"k": 167000},
                    },
                    separators=(",", ":"),
                )
                + "\n",
                encoding="utf-8",
            )
            now = _dt("2026-11-28T15:30:00+03:00")
            self.assertTrue(write_ohlc(hdir, now=now))
            idx = json.loads((hdir / "ohlc" / "index.json").read_text(encoding="utf-8"))
            self.assertIn("hourly", idx["levels"])
            self.assertEqual(idx["week_starts"], "Saturday")
            self.assertEqual(idx["series"], "official_kifah")
            daily = json.loads((hdir / "ohlc" / "daily.json").read_text(encoding="utf-8"))
            self.assertEqual(len(daily), 1)
            self.assertEqual(daily[0]["c"], 167000)
            self.assertTrue(daily[0].get("incomplete"))
            # second write with same prints: no churn
            self.assertFalse(write_ohlc(hdir, now=_dt("2026-11-28T15:35:00+03:00")))

    def test_last_scrape_at_ignored_in_comparator(self):
        a = {
            "ok": True,
            "value": 167000,
            "fetchedAt": "2026-11-28T15:00:00+03:00",
            "generated_at": "2026-11-28T15:00:00+03:00",
            "last_scrape_at": "2026-11-28T15:00:00+03:00",
            "source": "tg_kifah",
        }
        b = dict(a)
        b["fetchedAt"] = "2026-11-28T15:05:00+03:00"
        b["generated_at"] = "2026-11-28T15:05:00+03:00"
        b["last_scrape_at"] = "2026-11-28T15:05:00+03:00"
        self.assertFalse(latest_changed(a, b))
        b["value"] = 167100
        self.assertTrue(latest_changed(a, b))


if __name__ == "__main__":
    unittest.main()
