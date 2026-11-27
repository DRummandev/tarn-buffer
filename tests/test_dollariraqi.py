"""dollariraqi parser tests (§3.7)."""

from __future__ import annotations

import unittest
from datetime import datetime

from scraper.common import BAGHDAD, parse_text_core
from scraper.dollariraqi import parse_dollariraqi_page
from scraper.model import SourceShapeError


def _wrap(post_id: str, text: str, when: str = "2026-11-26T20:24:32+00:00") -> str:
    return f"""
<div class="tgme_widget_message_wrap">
  <div class="tgme_widget_message" data-post="{post_id}">
    <div class="tgme_widget_message_text js-message_text">{text}</div>
    <a class="tgme_widget_message_date"><time datetime="{when}"></time></a>
  </div>
</div>
"""


EXPECTED_BAGHDAD = {
    "dollariraqi/6593": 170400,
    "dollariraqi/6594": 171550,
    "dollariraqi/6601": 170100,
    "dollariraqi/6610": 169350,
    "dollariraqi/6611": 168800,
    "dollariraqi/6612": 169000,
}


class TestDollariraqi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import json
        from pathlib import Path

        samples = json.loads(
            (Path(__file__).parent / "fixtures" / "real_samples.json").read_text(
                encoding="utf-8"
            )
        )
        cls.posts = samples["dollariraqi_posts"]

    def test_expected_baghdad_from_fixture(self):
        for p in self.posts:
            pid = p["id"]
            html = _wrap(pid, p["text"], p["time_utc"])
            rows, n = parse_dollariraqi_page(html, datetime.now(BAGHDAD))
            self.assertEqual(n, 1)
            bg = [
                r
                for r in rows
                if r.market == "baghdad" and r.kind == "bursa" and r.parse_status == "ok"
            ]
            self.assertTrue(bg, f"no baghdad bursa for {pid}")
            self.assertEqual(bg[0].mid, EXPECTED_BAGHDAD[pid], pid)

    def test_shop_block_never_bursa(self):
        p = self.posts[0]  # has shop then list
        html = _wrap(p["id"], p["text"], p["time_utc"])
        rows, _ = parse_dollariraqi_page(html, datetime.now(BAGHDAD))
        shops = [r for r in rows if r.kind == "shop" and r.parse_status == "ok"]
        self.assertTrue(shops)
        for s in shops:
            self.assertEqual(s.market, "shop")
            # shop mids differ from bursa baghdad
        bg = next(r for r in rows if r.market == "baghdad" and r.kind == "bursa")
        self.assertEqual(bg.mid, 170400)
        # shop sell/buy for Baghdad block was 171000/170500
        bag_shop = next(r for r in shops if "بغداد" in (r.label or "") or r.location == "Baghdad")
        self.assertNotEqual(bag_shop.mid, bg.mid)

    def test_gold_euro_do_not_leak_erbil(self):
        # synthetic: gold after list with absurd Erbil gold figure
        text = (
            "🏛 بغداد 168,800 🏛 أربيل 169,050 "
            "🟡 سعر مثقال الذهب أربيل 943,000 دينار 🇪🇺 اليورو 1,250"
        )
        html = _wrap("dollariraqi/syn1", text)
        rows, _ = parse_dollariraqi_page(html, datetime.now(BAGHDAD))
        erbil = [r for r in rows if r.market == "erbil" and r.kind == "bursa" and r.parse_status == "ok"]
        self.assertTrue(erbil)
        self.assertEqual(erbil[0].mid, 169050)

    def test_evening_close_parses(self):
        p = next(x for x in self.posts if x["id"] == "dollariraqi/6612")
        html = _wrap(p["id"], p["text"], p["time_utc"])
        rows, _ = parse_dollariraqi_page(html, datetime.now(BAGHDAD))
        bg = next(r for r in rows if r.market == "baghdad" and r.kind == "bursa")
        self.assertEqual(bg.parse_status, "ok")
        self.assertEqual(bg.mid, 169000)

    def test_zero_posts_raises(self):
        with self.assertRaises(SourceShapeError):
            parse_dollariraqi_page("<html></html>", datetime.now(BAGHDAD))

    def test_list_mode_ignores_bid_ask_caption(self):
        # الأسعار عرض is a caption — list_mode should still take city numbers
        hits, ps = parse_text_core(
            "بغداد 168800 اربيل 169050 الاسعار عرض", list_mode=True
        )
        self.assertEqual(ps, "rate_post")
        m = {h.market: h.mid for h in hits}
        self.assertEqual(m.get("baghdad"), 168800)


if __name__ == "__main__":
    unittest.main()
