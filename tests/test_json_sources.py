"""JSON source parsers (§3.7)."""

from __future__ import annotations

import json
import unittest
from datetime import datetime
from pathlib import Path

from scraper.common import BAGHDAD
from scraper.dinarlive import parse_dinarlive
from scraper.info4iq import parse_info4iq
from scraper.iraqsm import parse_iraqsm
from scraper.model import SourceShapeError

FIX = Path(__file__).parent / "fixtures"


class TestInfo4iq(unittest.TestCase):
    def test_bom_crlf_and_scale(self):
        body = (FIX / "info4iq.json").read_bytes()
        rows = parse_info4iq(body, datetime.now(BAGHDAD))
        self.assertTrue(rows)
        kifah = next(r for r in rows if r.market == "kifah")
        self.assertEqual(kifah.parse_status, "ok")
        self.assertEqual(kifah.ask, 169350)
        self.assertEqual(kifah.bid, 169400)
        self.assertEqual(kifah.mid, 169375)
        self.assertIn("buy_gt_sell", kifah.warnings)

    def test_crossed_warning(self):
        raw = (
            '\r\n\ufeff[{"ID":"1","sell":"169.00","buy":"169.50",'
            '"market_name":"الكفاح","time":"2026-11-26 08:06:10","marketOffered":"169.00"}]'
        ).encode("utf-8")
        rows = parse_info4iq(raw, datetime.now(BAGHDAD))
        self.assertIn("buy_gt_sell", rows[0].warnings)
        self.assertEqual(rows[0].mid, 169250)

    def test_bad_json_raises(self):
        with self.assertRaises(SourceShapeError):
            parse_info4iq(b"not-json", datetime.now(BAGHDAD))


class TestIraqsm(unittest.TestCase):
    def test_unit_and_scale(self):
        body = (FIX / "iraqsm.json").read_bytes()
        rows = parse_iraqsm(body, datetime.now(BAGHDAD))
        parallel = next(r for r in rows if r.market == "parallel")
        self.assertEqual(parallel.parse_status, "ok")
        self.assertEqual(parallel.bid, 169000)
        self.assertEqual(parallel.ask, 169000)
        self.assertEqual(parallel.mid, 169000)
        shops = [r for r in rows if r.market == "shops"]
        self.assertTrue(shops)
        self.assertEqual(shops[0].ask, 169000)
        self.assertEqual(shops[0].bid, 168600)

    def test_changed_unit_raises(self):
        d = json.loads((FIX / "iraqsm.json").read_text(encoding="utf-8"))
        d["unit"] = "IQD per 100 USD"
        with self.assertRaises(SourceShapeError):
            parse_iraqsm(json.dumps(d).encode(), datetime.now(BAGHDAD))


class TestDinarlive(unittest.TestCase):
    def test_value_and_tz_warning(self):
        body = (FIX / "dinarlive.json").read_bytes()
        rows = parse_dinarlive(body, datetime.now(BAGHDAD))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].mid, 169400)
        self.assertEqual(rows[0].parse_status, "ok")
        self.assertIn("created_at_tz_assumed_baghdad", rows[0].warnings)
        self.assertTrue(rows[0].post_time.endswith("+03:00"))

    def test_bad_shape_raises(self):
        with self.assertRaises(SourceShapeError):
            parse_dinarlive(b'{"oops":1}', datetime.now(BAGHDAD))


if __name__ == "__main__":
    unittest.main()
