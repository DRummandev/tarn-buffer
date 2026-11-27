"""Live-captured dollariraqi page fixture."""

from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path

from scraper.common import BAGHDAD
from scraper.dollariraqi import parse_dollariraqi_page

FIX = Path(__file__).parent / "fixtures"


class TestDollariraqiPageFixture(unittest.TestCase):
    def test_live_page(self):
        path = FIX / "dollariraqi_page.html"
        if not path.exists():
            self.skipTest("dollariraqi_page.html missing")
        rows, n = parse_dollariraqi_page(
            path.read_text(encoding="utf-8", errors="replace"), datetime.now(BAGHDAD)
        )
        self.assertGreaterEqual(n, 5)
        bg = [
            r
            for r in rows
            if r.parse_status == "ok" and r.market == "baghdad" and r.kind == "bursa"
        ]
        shop = [r for r in rows if r.parse_status == "ok" and r.kind == "shop"]
        self.assertGreaterEqual(len(bg), 1)
        self.assertGreaterEqual(len(shop), 1)
        for r in bg:
            self.assertGreaterEqual(r.mid, 150000)
            self.assertLessEqual(r.mid, 200000)


if __name__ == "__main__":
    unittest.main()
