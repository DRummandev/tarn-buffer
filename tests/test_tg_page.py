"""Telegram page HTML parsing with synthetic wraps + live fixture files if present."""

from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path

from scraper.common import BAGHDAD
from scraper.kifah import parse_kifah_page
from scraper.tg import parse_tg

FIX = Path(__file__).parent / "fixtures"


class TestTGParse(unittest.TestCase):
    def test_parse_wraps_newest_order_preserved(self):
        html = """
        <div class="tgme_widget_message_wrap">
          <div class="tgme_widget_message" data-post="borsat_alkfah/1">
            <div class="tgme_widget_message_text js-message_text">كفاح 168,100</div>
            <time datetime="2026-11-04T07:25:06+00:00"></time>
          </div>
        </div>
        <div class="tgme_widget_message_wrap">
          <div class="tgme_widget_message" data-post="borsat_alkfah/2">
            <div class="tgme_widget_message_text js-message_text">كفاح 168,800</div>
            <time datetime="2026-11-26T10:55:19+00:00"></time>
          </div>
        </div>
        """
        posts = parse_tg(html)
        self.assertEqual(len(posts), 2)
        rows, n = parse_kifah_page(html, datetime.now(BAGHDAD))
        self.assertEqual(n, 2)
        ok = [r for r in rows if r.parse_status == "ok" and r.market == "kifah"]
        self.assertEqual(len(ok), 2)
        self.assertEqual(ok[0].mid, 168100)
        self.assertEqual(ok[1].mid, 168800)

    def test_live_kifah_fixture_if_present(self):
        path = FIX / "kifah_page.html"
        if not path.exists():
            self.skipTest("kifah_page.html not captured yet")
        rows, n = parse_kifah_page(path.read_text(encoding="utf-8", errors="replace"), datetime.now(BAGHDAD))
        self.assertGreaterEqual(n, 10)
        self.assertTrue(any(r.parse_status == "ok" for r in rows))


if __name__ == "__main__":
    unittest.main()
